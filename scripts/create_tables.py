#!/usr/bin/env python3
"""
Create all database tables from SQLAlchemy models.
Run from the project root: python scripts/create_tables.py
"""
import sys
import os

# Make sure app package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env before importing settings
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, inspect, text
from app.core.config import settings

# Import all models so their metadata is registered
from app.models.base import Base
import app.models.agent
import app.models.phone
import app.models.label
import app.models.contact
import app.models.chat
import app.models.message
import app.models.ticket
import app.models.note
import app.models.quick_reply
import app.models.automation_rule
import app.models.knowledge_item
import app.models.bulk_message_job
import app.models.activity_log

EXPECTED_TABLES = [
    "agents",
    "phones",
    "labels",
    "contacts",
    "contact_labels",
    "chats",
    "chat_labels",
    "messages",
    "tickets",
    "ticket_labels",
    "notes",
    "quick_replies",
    "automation_rules",
    "knowledge_items",
    "bulk_message_jobs",
    "activity_logs",
]


def main():
    print(f"\n{'='*60}")
    print("  Hyperscope — Database Setup")
    print(f"{'='*60}")
    print(f"  URL : {settings.database_url}")
    print()

    engine = create_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
    )

    # Test connection
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT VERSION()"))
            version = result.fetchone()[0]
            print(f"  [OK] Connected to MySQL {version}")
    except Exception as e:
        print(f"  [FAIL] Connection error: {e}")
        sys.exit(1)

    # Create all tables (CREATE TABLE IF NOT EXISTS)
    print("\n  Creating tables...")
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
        print("  [OK] Table creation complete")
    except Exception as e:
        print(f"  [FAIL] Table creation error: {e}")
        sys.exit(1)

    # Verify tables exist and show columns
    inspector = inspect(engine)
    existing = inspector.get_table_names()

    print(f"\n  {'Table':<25} {'Status':<10} {'Columns'}")
    print(f"  {'-'*65}")

    all_ok = True
    for table in EXPECTED_TABLES:
        if table in existing:
            cols = [c["name"] for c in inspector.get_columns(table)]
            fks = inspector.get_foreign_keys(table)
            fk_info = ", ".join(
                f"{fk['constrained_columns'][0]}→{fk['referred_table']}.{fk['referred_columns'][0]}"
                for fk in fks
            ) if fks else "—"
            print(f"  {'✓ '+table:<25} {'OK':<10} {len(cols)} cols  FK: {fk_info}")
        else:
            print(f"  {'✗ '+table:<25} {'MISSING':<10}")
            all_ok = False

    extra = [t for t in existing if t not in EXPECTED_TABLES]
    if extra:
        print(f"\n  Extra tables (unexpected): {extra}")

    print(f"\n  {'='*60}")
    if all_ok:
        print("  All tables created successfully!")
    else:
        print("  WARNING: Some tables are missing.")
    print(f"  {'='*60}\n")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
