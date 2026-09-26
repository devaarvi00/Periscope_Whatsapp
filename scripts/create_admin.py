#!/usr/bin/env python3
"""Create an admin agent, or reset an existing one (idempotent).

Usage:
    python scripts/create_admin.py --email admin@example.com --name "Admin"
        # prompts for the password (never echoed)

    python scripts/create_admin.py --email admin@example.com --password '...'

    # Non-interactive (e.g. docker compose exec -T app ...):
    ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD='...' python scripts/create_admin.py

If an agent with the email exists it is promoted to admin, re-activated, and
its password is replaced. There is no default password.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

# Allow running as `python scripts/create_admin.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password  # noqa: E402
from app.db.init_db import init_db  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.agent import Agent, AgentRole  # noqa: E402
from app.schemas.auth import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or reset a Hyperscope admin agent.")
    parser.add_argument("--email", default=os.getenv("ADMIN_EMAIL"), help="Admin email (or ADMIN_EMAIL)")
    parser.add_argument("--name", default=os.getenv("ADMIN_NAME"), help="Display name (or ADMIN_NAME; defaults to email prefix)")
    parser.add_argument(
        "--password",
        default=None,
        help="Password (or ADMIN_PASSWORD). Omit to be prompted — preferred, keeps it out of shell history.",
    )
    return parser.parse_args()


def _read_password(cli_value: str | None) -> str:
    password = cli_value or os.getenv("ADMIN_PASSWORD")
    if password:
        return password
    if not sys.stdin.isatty():
        sys.exit("error: no password given; pass --password, set ADMIN_PASSWORD, or run interactively")
    password = getpass.getpass("Admin password: ")
    if password != getpass.getpass("Confirm password: "):
        sys.exit("error: passwords do not match")
    return password


def _validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        sys.exit(f"error: password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password.encode()) > PASSWORD_MAX_LENGTH:
        sys.exit(f"error: password must be at most {PASSWORD_MAX_LENGTH} bytes")


def main() -> int:
    args = _parse_args()
    email = (args.email or "").strip().lower()
    if not email or "@" not in email:
        sys.exit("error: a valid --email (or ADMIN_EMAIL) is required")
    name = (args.name or "").strip() or email.split("@", 1)[0]

    password = _read_password(args.password)
    _validate_password(password)

    init_db()  # ensure tables exist even if the app has never started
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.email == email).first()
        if agent:
            agent.password_hash = hash_password(password)
            agent.role = AgentRole.ADMIN
            agent.is_active = True
            if args.name:
                agent.name = name
            action = "Updated"
        else:
            agent = Agent(
                email=email,
                name=name,
                password_hash=hash_password(password),
                role=AgentRole.ADMIN,
                is_active=True,
            )
            db.add(agent)
            action = "Created"
        db.commit()
        db.refresh(agent)
    finally:
        db.close()

    print(f"{action} admin agent #{agent.id} <{agent.email}>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
