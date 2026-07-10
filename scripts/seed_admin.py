#!/usr/bin/env python3
"""
Create the first admin user.
Run: python scripts/seed_admin.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import SessionLocal
from app.models.agent import Agent, AgentRole
from app.core.security import hash_password


def seed():
    db = SessionLocal()
    try:
        existing = db.query(Agent).filter(Agent.email == "admin@periskope.local").first()
        if existing:
            print(f"\n  Admin already exists: {existing.email}  (role: {existing.role.value})")
            return

        admin = Agent(
            email="admin@periskope.local",
            name="Admin",
            password_hash=hash_password("admin123"),
            role=AgentRole.ADMIN,
            is_active=True,
            avatar_color="#0D8C7C",
        )
        db.add(admin)
        db.commit()

        print("\n  ✓ Admin user created:")
        print("    Email    : admin@periskope.local")
        print("    Password : admin123")
        print("    Role     : admin")
        print("\n  Change the password after first login!\n")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
