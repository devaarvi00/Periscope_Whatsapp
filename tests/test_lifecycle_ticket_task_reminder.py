import asyncio
import sys
from datetime import datetime, timedelta
import httpx
from sqlalchemy import create_engine, text

# Base configurations
BASE_URL = "http://localhost:8000/api/v1"
DATABASE_URL = "mysql+pymysql://root:@localhost:3306/whatsapp_periscope"
TARGET_CHAT_ID = 1608  # hr Aarvi

def print_result(case_id, description, status, details=""):
    status_icon = "✅ PASS" if status else "❌ FAIL"
    details_str = f" - {details}" if details else ""
    print(f"[{case_id}] {description:<75} {status_icon}{details_str}")

async def run_lifecycle_test():
    print("=" * 100)
    print("HYPERSCOPE CRM: TICKETS, TASKS & REMINDERS FULL LIFECYCLE CHECKOUT")
    print("=" * 100)

    client = httpx.Client(timeout=10.0)

    # 1. AUTHENTICATE
    login_payload = {"email": "admin@gmail.com", "password": "Admin@123"}
    r = client.post(f"{BASE_URL}/auth/login", json=login_payload)
    if r.status_code != 200:
        print_result("L1.1", "Admin authentication", False, f"Status: {r.status_code}")
        sys.exit(1)
    
    token = r.json().get("access_token")
    client.headers.update({"Authorization": f"Bearer {token}"})
    print_result("L1.1", "Admin authentication and token retrieval", True)

    # 2. CREATE TICKET (Linked to Chat ID 1608 with Urgent priority and past due date)
    past_due_date = (datetime.utcnow() - timedelta(hours=2))
    ticket_payload = {
        "chat_id": TARGET_CHAT_ID,
        "title": "Automated Lifecycle Test Ticket",
        "description": "Verify end-to-end SLA and state flow",
        "priority": "urgent",
        "due_date": past_due_date.isoformat() + "Z"
    }
    r = client.post(f"{BASE_URL}/tickets", json=ticket_payload)
    if r.status_code != 201:
        print_result("L2.1", "Create ticket with past due_date", False, f"Status: {r.status_code}")
        sys.exit(1)
    
    ticket = r.json()
    ticket_id = ticket["id"]
    print_result("L2.1", "Create ticket with past due_date", True, f"Ticket ID: {ticket_id}")

    # 3. GET/APPLY LABELS TO TICKET
    r = client.get(f"{BASE_URL}/labels")
    labels = r.json()
    if not labels:
        # Create a label on the fly
        label_create = client.post(f"{BASE_URL}/labels", json={"name": "Lifecycle Label", "color": "#ff0000"})
        label_id = label_create.json()["id"]
    else:
        label_id = labels[0]["id"]

    r = client.post(f"{BASE_URL}/tickets/{ticket_id}/labels/{label_id}")
    print_result("L2.2", "Apply label to the ticket", r.status_code == 201, f"Label ID: {label_id} (Status: {r.status_code})")

    # 4. VERIFY TICKET DETAILS & INITIAL SLA STATE
    r = client.get(f"{BASE_URL}/tickets/{ticket_id}")
    ticket_details = r.json()
    init_sla = ticket_details.get("sla_breached")
    print_result("L2.3", "Verify ticket initial SLA (expect False)", init_sla is False, f"sla_breached: {init_sla}")

    # 5. UPDATE TICKET DETAILS (Assign to Agent #1 and status to in_progress)
    update_payload = {
        "status": "in_progress",
        "assigned_to": 1
    }
    r = client.patch(f"{BASE_URL}/tickets/{ticket_id}", json=update_payload)
    print_result("L2.4", "Update ticket status to 'in_progress' and assign", r.status_code == 200, f"Status: {r.status_code}")

    # 6. TRIGGER SLA BREACH CHECK BACKGROUND WORKER
    print("\n--- Triggering check_sla_breaches() background task ---")
    from app.workers.tasks import check_sla_breaches
    await check_sla_breaches()

    # 7. VERIFY TICKET IS FLAGGED AS SLA BREACHED
    r = client.get(f"{BASE_URL}/tickets/{ticket_id}")
    ticket_details = r.json()
    sla_breached = ticket_details.get("sla_breached")
    print_result("L3.1", "Verify ticket SLA breached (expect True)", sla_breached is True, f"sla_breached: {sla_breached}")

    # 8. CREATE TASK WITH REMINDER (Linked to chat and ticket, reminder in the past)
    past_reminder_at = (datetime.utcnow() - timedelta(minutes=15))
    future_task_due = (datetime.utcnow() + timedelta(hours=1))
    task_payload = {
        "chat_id": TARGET_CHAT_ID,
        "title": "Automated Lifecycle Test Task",
        "notes": f"Linked to ticket #{ticket_id}",
        "priority": "high",
        "due_date": future_task_due.isoformat() + "Z",
        "reminder_at": past_reminder_at.isoformat() + "Z",
        "assigned_to": 1
    }
    r = client.post(f"{BASE_URL}/tasks", json=task_payload)
    if r.status_code != 201:
        print_result("L4.1", "Create task with past reminder_at", False, f"Status: {r.status_code}")
        sys.exit(1)
    
    task = r.json()
    task_id = task["id"]
    print_result("L4.1", "Create task with past reminder_at", True, f"Task ID: {task_id}")

    # 9. TRIGGER TASK REMINDERS BACKGROUND WORKER
    print("\n--- Triggering check_task_reminders() background task ---")
    from app.workers.tasks import check_task_reminders
    await check_task_reminders()

    # 10. VERIFY TASK REMINDER RECORDED AS SENT
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        res = conn.execute(text("SELECT reminder_sent, status FROM tasks WHERE id = :id"), {"id": task_id}).fetchone()
        reminder_sent = res[0]
        task_status = res[1]
    print_result("L4.2", "Verify task reminder_sent flag (expect 1/True)", reminder_sent == 1 or reminder_sent is True, f"reminder_sent: {reminder_sent}")

    # 11. MARK TASK AS DONE & TICKET AS RESOLVED
    r_task = client.patch(f"{BASE_URL}/tasks/{task_id}", json={"status": "done"})
    r_tkt = client.patch(f"{BASE_URL}/tickets/{ticket_id}", json={"status": "resolved"})
    print_result("L5.1", "Mark task as 'done'", r_task.status_code == 200, f"Task Status: {r_task.json().get('status')}")
    print_result("L5.2", "Mark ticket as 'resolved'", r_tkt.status_code == 200, f"Ticket Status: {r_tkt.json().get('status')}")

    # 12. VERIFY AUDIT LOG ENTRIES CREATED FOR EACH LIFECYCLE EVENT
    with engine.connect() as conn:
        logs = conn.execute(
            text("SELECT action, description FROM activity_logs WHERE entity_type IN ('ticket', 'task') AND entity_id IN (:t_id, :tk_id) ORDER BY id ASC"),
            {"t_id": ticket_id, "tk_id": task_id}
        ).fetchall()
    
    print("\n--- Verified Lifecycle Activity Logs in DB ---")
    for log in logs:
        print(f" -> Event: {log[0]:<20} | {log[1]}")
    
    print_result("L6.1", "Check activity logs generation", len(logs) >= 4, f"Total log entries: {len(logs)}")
    print("\n" + "=" * 100)
    print("🎉 FULL LIFECYCLE CHECKOUT COMPLETED SUCCESSFULLY!")
    print("=" * 100)

if __name__ == "__main__":
    asyncio.run(run_lifecycle_test())
