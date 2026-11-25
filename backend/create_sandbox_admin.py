"""Create a Sandbox administrator interactively, without putting a password in shell history."""

import argparse
from getpass import getpass
import os
import re
import sys

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.database.admin_models import AdminAccount
from backend.database.connection import get_engine
from backend.services.admin_ingestion import hash_password


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("admin", "staff"), default="admin")
    role = parser.parse_args().role
    if (
        os.getenv("ADMIN_INGESTION_ENABLED") != "1"
        or os.getenv("ADMIN_SANDBOX_PROJECT") != "ai-property-portfolio"
        or not sys.stdin.isatty()
    ):
        raise SystemExit("Run interactively inside the enabled Sandbox backend.")
    engine = get_engine()
    try:
        if engine.url.host != "postgres":
            raise SystemExit("Expected the Sandbox Compose database; see the setup guide.")
        with Session(engine) as session, session.begin():
            if session.scalar(text("SELECT to_regclass('public.admin_accounts')")) is None:
                raise SystemExit("Admin migration is missing")
            username = input("New administrator username: ").strip().lower()
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", username):
                raise SystemExit("Use 3–64 letters, digits, dots, underscores or hyphens.")
            if session.get(AdminAccount, username):
                raise SystemExit("Username already exists.")
            password = getpass("New password: ")
            if not 14 <= len(password) <= 256 or getpass("Repeat password: ") != password:
                raise SystemExit("Passwords must match and contain 14–256 characters.")
            session.add(AdminAccount(username=username, password_hash=hash_password(password), role=role))
        print("Administrator created.")
    except SQLAlchemyError:
        raise SystemExit("Could not create the administrator.") from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
