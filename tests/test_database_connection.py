from backend.database import connection


def test_compose_database_settings_accept_password_punctuation(monkeypatch, tmp_path):
    monkeypatch.setattr(connection, "ENV_PATH", tmp_path / "missing")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key, value in {
        "POSTGRES_HOST": "postgres", "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "demo", "POSTGRES_USER": "demo",
        "POSTGRES_PASSWORD": "demo:p@ss/word",
    }.items():
        monkeypatch.setenv(key, value)

    url = connection.get_database_url()
    assert (url.host, url.port, url.database, url.username) == ("postgres", 5432, "demo", "demo")
    assert url.password == "demo:p@ss/word"


def test_standalone_defaults_and_explicit_url(monkeypatch, tmp_path):
    env_path = tmp_path / ".env.postgres"
    env_path.write_text("POSTGRES_DB=demo\nPOSTGRES_USER=demo\nPOSTGRES_PASSWORD=synthetic\n")
    monkeypatch.setattr(connection, "ENV_PATH", env_path)
    for key in ("DATABASE_URL", "POSTGRES_HOST", "POSTGRES_PORT",
                "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    url = connection.get_database_url()
    assert (url.host, url.port, url.database) == ("127.0.0.1", 5433, "demo")

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://other@example.test/override")
    assert connection.get_database_url().database == "override"
