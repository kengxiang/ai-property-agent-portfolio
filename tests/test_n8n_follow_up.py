"""Opt-in real n8n Docker integration. Credentials and database are ephemeral."""

import json
import os
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from automation.outbox_worker import Settings, process_one
from backend.database.lead_operations_models import OutboxEventRecord
from test_follow_up_postgres import confirm_event, tasks


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FILE = ROOT / "automation/n8n_workflows/lead_created_follow_up.json"
RUN_N8N = os.getenv("RUN_POSTGRES_TESTS") == "1" and os.getenv("RUN_N8N_TESTS") == "1"
SIGNATURE = "public.process_lead_follow_up(text, text, text, text)"


@dataclass(frozen=True)
class RunningN8n:
    url: str
    secret: str = field(repr=False)
    role: str
    container: str


def test_workflow_export_requires_local_credentials():
    workflow = json.loads(WORKFLOW_FILE.read_text())
    nodes = {node["name"]: node for node in workflow["nodes"]}
    assert all("credentials" not in node for node in nodes.values())
    assert workflow["active"] is False
    assert nodes["LeadCreated Webhook"]["parameters"]["authentication"] == "headerAuth"
    assert workflow["settings"]["saveDataSuccessExecution"] == "none"
    assert workflow["settings"]["saveDataErrorExecution"] == "none"
    assert all(
        connection["node"] in nodes
        for source in workflow["connections"].values()
        for output in source["main"] for connection in output
    )


def docker(*args, stdin=None):
    result = subprocess.run(["docker", *args], input=stdin, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f"Docker {args[0]} failed: {result.stderr.strip()[:400]}")
    return result.stdout.strip()


@pytest.fixture
def n8n(database):
    suffix = uuid4().hex[:12]
    name = f"p204b-n8n-{suffix}"
    network = f"p204b-net-{suffix}"
    volume = f"p204b-data-{suffix}"
    role = f"follow_up_{suffix}"
    password = secrets.token_hex(24)
    secret = secrets.token_hex(24)
    pg_container = os.environ["N8N_TEST_PG_CONTAINER"]
    image = os.getenv("N8N_TEST_IMAGE", "docker.n8n.io/n8nio/n8n:latest")

    with database.begin() as connection:
        connection.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        connection.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {role}")
    cleanups = []
    try:
        docker("network", "create", network)
        cleanups.append(("network", "rm", network))
        docker("network", "connect", "--alias", "property-db", network, pg_container)
        cleanups.append(("network", "disconnect", network, pg_container))
        docker("volume", "create", volume)
        cleanups.append(("volume", "rm", volume))
        docker(
            "run", "-d", "--name", name, "--network", network,
            "-p", "127.0.0.1::5678", "-v", f"{volume}:/home/node/.n8n",
            "-e", "N8N_DIAGNOSTICS_ENABLED=false", "-e", "N8N_VERSION_NOTIFICATIONS_ENABLED=false",
            "-e", "N8N_TEMPLATES_ENABLED=false", "--entrypoint", "sh", image,
            "-c", "while [ ! -f /tmp/start-n8n ]; do sleep 1; done; exec n8n start",
        )
        cleanups.append(("rm", "-f", name))
        workflow = json.loads(WORKFLOW_FILE.read_text())
        workflow["id"] = "P204BLeadFollowUp"
        credentials = [
            {"id": "P204BTestHeader", "name": "Test Header", "type": "httpHeaderAuth",
             "data": {"name": "X-Webhook-Secret", "value": secret}},
            {"id": "P204BTestPostgres", "name": "Test PostgreSQL", "type": "postgres",
             "data": {"host": "property-db", "port": 5432, "database": database.url.database,
                      "user": role, "password": password, "ssl": "disable"}},
        ]
        for node_name, credential in zip(("LeadCreated Webhook", "Persist or Verify Follow-up"), credentials):
            node = next(node for node in workflow["nodes"] if node["name"] == node_name)
            node["credentials"] = {credential["type"]: {"id": credential["id"], "name": credential["name"]}}
        for filename, data in (("credentials", credentials), ("workflow", workflow)):
            docker("exec", "-i", name, "sh", "-c", f"umask 077; cat >/tmp/{filename}.json",
                   stdin=json.dumps(data))
        docker("exec", name, "n8n", "import:credentials", "--input=/tmp/credentials.json")
        docker("exec", name, "rm", "/tmp/credentials.json")
        docker("exec", name, "n8n", "import:workflow", "--input=/tmp/workflow.json")
        docker("exec", name, "n8n", "publish:workflow", f"--id={workflow['id']}")
        docker("exec", name, "touch", "/tmp/start-n8n")
        port = docker("port", name, "5678/tcp").rsplit(":", 1)[1]
        base_url = f"http://127.0.0.1:{port}"
        deadline = monotonic() + 45
        with httpx.Client(trust_env=False, timeout=2) as client:
            while monotonic() < deadline:
                try:
                    if client.post(f"{base_url}/webhook/lead-created", json={}).status_code in (401, 403):
                        break
                except httpx.RequestError:
                    pass
                sleep(0.3)
            else:
                pytest.fail(f"n8n did not register its authenticated webhook: {docker('logs', '--tail', '20', name)}")
        yield RunningN8n(f"{base_url}/webhook/lead-created", secret, role, name)
    finally:
        try:
            for command in reversed(cleanups):
                docker(*command)
        finally:
            with database.begin() as connection:
                connection.exec_driver_sql(f"REVOKE EXECUTE ON FUNCTION {SIGNATURE} FROM {role}")
                connection.exec_driver_sql(f"DROP ROLE {role}")


@pytest.mark.skipif(not RUN_N8N, reason="requires RUN_POSTGRES_TESTS=1 and RUN_N8N_TESTS=1 with local n8n Docker image")
def test_confirm_worker_and_real_n8n(database, monkeypatch, n8n):
    url, secret, role = n8n.url, n8n.secret, n8n.role
    event = confirm_event(database, monkeypatch)
    event_id = event["event_id"]
    settings = Settings(url, secret, 5, 1, 3, 20)
    headers = {"X-Webhook-Secret": secret}
    with httpx.Client(trust_env=False, timeout=10) as client:
        for unauthorized in ({}, {"X-Webhook-Secret": ""}, {"X-Webhook-Secret": "wrong"}):
            assert client.post(url, json=event, headers=unauthorized).status_code in (401, 403)
        assert client.post(url, json={**event, "event_type": "Other"}, headers=headers).status_code == 400
        assert client.post(url, content="{", headers={**headers, "Content-Type": "application/json"}).status_code == 422
        assert client.post(url, json={**event, "event_id": str(uuid4())}, headers=headers).status_code == 404
        assert client.post(url, json={**event, "lead_id": str(uuid4())}, headers=headers).status_code == 409
        assert not tasks(database, event_id)

        for query in ("SELECT phone FROM leads", "UPDATE leads SET status = 'OTHER'",
                      "SELECT * FROM follow_up_tasks"):
            with pytest.raises(ProgrammingError) as denied, database.begin() as connection:
                connection.exec_driver_sql(f"SET LOCAL ROLE {role}")
                connection.exec_driver_sql(query)
            assert denied.value.orig.sqlstate == "42501"

        monkeypatch.setenv("N8N_WEBHOOK_URL", url)
        monkeypatch.setenv("N8N_WEBHOOK_SECRET", secret)
        worker = subprocess.run(
            [sys.executable, "-m", "automation.outbox_worker", "--once"],
            capture_output=True, text=True, timeout=20,
        )
        assert worker.returncode == 0, worker.stderr
        with database.connect() as connection:
            assert connection.scalar(select(OutboxEventRecord.delivery_status).filter_by(event_id=event_id)) == "DELIVERED"
        original, = tasks(database, event_id)
        assert (original["lead_id"], original["property_id"], original["status"]) == (event["lead_id"], "P001", "PENDING")
        replay = client.post(url, headers=headers, json=event)
        assert replay.status_code == 200 and replay.json() == {"event_id": event_id, "accepted": True}
        assert client.post(url, headers=headers, json={**event, "property_id": "P999"}).status_code == 409
        assert tasks(database, event_id) == [original]
        assert not process_one(database, client, settings)

        second = confirm_event(database, monkeypatch)
        with ThreadPoolExecutor(max_workers=2) as workers:
            responses = list(workers.map(lambda _: client.post(url, headers=headers, json=second), range(2)))
        assert all(response.status_code == 200 and response.json() == {
            "event_id": second["event_id"], "accepted": True,
        } for response in responses)
        assert len(tasks(database, second["event_id"])) == 1
        assert process_one(database, client, settings)

        third = confirm_event(database, monkeypatch)
        with database.begin() as connection:
            connection.exec_driver_sql(f"REVOKE EXECUTE ON FUNCTION {SIGNATURE} FROM {role}")
        try:
            failed = client.post(url, headers=headers, json=third)
            assert failed.status_code == 503 and failed.json()["accepted"] is False
            assert not tasks(database, third["event_id"])
        finally:
            with database.begin() as connection:
                connection.exec_driver_sql(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {role}")

        docker("stop", n8n.container)
        assert process_one(database, client, settings)
        with database.connect() as connection:
            row = connection.execute(select(
                OutboxEventRecord.delivery_status, OutboxEventRecord.last_error,
            ).filter_by(event_id=third["event_id"])).one()
        assert row == ("RETRY", "CONNECTION_ERROR")
        assert not tasks(database, third["event_id"])
