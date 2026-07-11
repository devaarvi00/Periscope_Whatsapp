"""
HYPERSCOPE CRM MASTER PRODUCTION READY CHECK
Tests all 4 core modules:
1. Groups, Chats, & Communities
2. Tickets, Tasks, & Reminders
3. Bulk Messaging
4. AI Agent & Gemini Integration

Runs full lifecycles on the provided test chat ID (1608).
"""
import sys
sys.path.append(".")
from datetime import datetime, timedelta
import httpx
from app.db.session import SessionLocal

BASE_URL = "http://localhost:8000/api/v1"
CHAT_ID = 1608  # hr Aarvi

results = {"pass": 0, "fail": 0}

def print_test(module, name, ok, detail=""):
    status = "✅ PASS" if ok else "❌ FAIL"
    det = f" - {detail}" if detail else ""
    print(f"[{module:<10}] {name:<65} {status}{det}")
    results["pass" if ok else "fail"] += 1

def header(text):
    print("\n" + "=" * 90)
    print(f"  {text}")
    print("=" * 90)

def run_master_check():
    print()
    print("=" * 90)
    print("   🚀 RUNNING HYPERSCOPE CRM MASTER E2E LIFECYCLE PRODUCTION CHECK")
    print("=" * 90)

    client = httpx.Client(timeout=30.0)

    # 0. AUTHENTICATION
    r = client.post(f"{BASE_URL}/auth/login", json={"email": "admin@gmail.com", "password": "Admin@123"})
    if r.status_code != 200:
        print_test("AUTH", "Admin Authentication", False, f"Status: {r.status_code}")
        sys.exit(1)
    
    token = r.json().get("access_token")
    client.headers.update({"Authorization": f"Bearer {token}"})
    print_test("AUTH", "Admin Authentication & JWT Verification", True)

    # ════════════════════════════════════════════════════════════════════════
    # MODULE 1: Manage Groups, 1:1 Chats & Communities
    # ════════════════════════════════════════════════════════════════════════
    header("MODULE 1: Manage Groups, 1:1 Chats & Communities")

    # 1.1 List Chats with Limit and Flags
    r = client.get(f"{BASE_URL}/inbox/chats", params={"limit": 5})
    print_test("CHATS", "List Chats", r.status_code == 200, f"Found: {len(r.json())}")

    # 1.2 Group filtering
    r = client.get(f"{BASE_URL}/inbox/chats", params={"is_group": True, "limit": 5})
    groups = r.json()
    print_test("CHATS", "Filter Group Chats", r.status_code == 200, f"Groups: {len(groups)}")

    # 1.3 Label CRUD on the fly
    ts = int(datetime.utcnow().timestamp())
    r = client.post(f"{BASE_URL}/labels", json={"name": f"MasterLabel_{ts}", "color": "#E11D48"})
    label_id = r.json().get("id") if r.status_code == 201 else None
    print_test("CHATS", "Create custom label on-the-fly", r.status_code == 201, f"Label ID: {label_id}")

    # 1.4 Apply and remove label
    if label_id:
        r = client.post(f"{BASE_URL}/inbox/chats/{CHAT_ID}/labels/{label_id}")
        print_test("CHATS", "Apply label to Chat", r.status_code == 200)
        
        # Filter chats by label
        r = client.get(f"{BASE_URL}/inbox/chats", params={"label_id": label_id})
        print_test("CHATS", "Filter Chat list by Label ID", r.status_code == 200, f"Count: {len(r.json())}")

        r = client.delete(f"{BASE_URL}/inbox/chats/{CHAT_ID}/labels/{label_id}")
        print_test("CHATS", "Remove label from Chat", r.status_code == 200)

        r = client.delete(f"{BASE_URL}/labels/{label_id}")
        print_test("CHATS", "Delete custom label", r.status_code == 204)

    # 1.5 Custom Properties definition and value assignment
    r = client.post(f"{BASE_URL}/properties/definitions", json={
        "entity": "chat", "name": f"MasterProp_{ts}", "prop_type": "text", "required": False
    })
    prop_id = r.json().get("id") if r.status_code == 201 else None
    print_test("CHATS", "Create custom property definition", r.status_code == 201, f"Prop ID: {prop_id}")

    if prop_id:
        r = client.put(f"{BASE_URL}/properties/chat/{CHAT_ID}", json={"values": {str(prop_id): "Master Value"}})
        print_test("CHATS", "Set custom property value on Chat", r.status_code == 200)

        r = client.get(f"{BASE_URL}/properties/chat/{CHAT_ID}")
        val = r.json().get("custom_properties", {}).get(str(prop_id))
        print_test("CHATS", "Verify custom property value persisted", val == "Master Value", f"Val: {val}")

        r = client.delete(f"{BASE_URL}/properties/definitions/{prop_id}")
        print_test("CHATS", "Delete custom property definition", r.status_code == 204)

    # 1.6 Private Notes with mentions
    r = client.post(f"{BASE_URL}/notes", json={"chat_id": CHAT_ID, "content": "📝 @admin Master check note."})
    note_id = r.json().get("id") if r.status_code == 201 else None
    print_test("CHATS", "Create private note with @mention", r.status_code == 201)

    if note_id:
        r = client.get(f"{BASE_URL}/notes/chat/{CHAT_ID}")
        print_test("CHATS", "List notes for chat", r.status_code == 200, f"Notes count: {len(r.json())}")

        r = client.delete(f"{BASE_URL}/notes/{note_id}")
        print_test("CHATS", "Delete private note", r.status_code == 204)

    # ════════════════════════════════════════════════════════════════════════
    # MODULE 2: Tickets, Tasks & Reminders
    # ════════════════════════════════════════════════════════════════════════
    header("MODULE 2: Tickets, Tasks & Reminders")

    # 2.1 Create Ticket with SLA breach date
    past_due = (datetime.utcnow() - timedelta(hours=2)).isoformat() + "Z"
    ticket_payload = {
        "title": "Master Lifecycle Ticket",
        "description": "Production validation check ticket",
        "priority": "urgent",
        "chat_id": CHAT_ID,
        "due_date": past_due
    }
    r = client.post(f"{BASE_URL}/tickets", json=ticket_payload)
    ticket_id = r.json().get("id") if r.status_code == 201 else None
    print_test("CRM", "Create Ticket with past-due SLA date", r.status_code == 201, f"Ticket ID: {ticket_id}")

    # 2.2 Trigger SLA breach via background task execution
    if ticket_id:
        # Before SLA breach check
        r = client.get(f"{BASE_URL}/tickets/{ticket_id}")
        print_test("CRM", "Initial SLA check status", r.json().get("sla_breached") is False)

        # Run background worker for SLA breaches
        from app.workers.tasks import check_sla_breaches
        import asyncio
        try:
            asyncio.run(check_sla_breaches())
            print_test("CRM", "Executed check_sla_breaches() background task", True)
        except Exception as ex:
            print_test("CRM", "Executed check_sla_breaches() background task", False, str(ex))

        # Re-check ticket state
        r = client.get(f"{BASE_URL}/tickets/{ticket_id}")
        print_test("CRM", "Verify ticket SLA updated to BREACHED", r.json().get("sla_breached") is True)

    # 2.3 Create Task and Reminder alert
    task_id = None
    if ticket_id:
        past_remind = (datetime.utcnow() - timedelta(minutes=10)).isoformat() + "Z"
        task_payload = {
            "ticket_id": ticket_id,
            "title": "Master Verification Task",
            "description": "Must be done now",
            "assigned_to": 1,
            "reminder_at": past_remind
        }
        r = client.post(f"{BASE_URL}/tasks", json=task_payload)
        task_id = r.json().get("id") if r.status_code == 201 else None
        print_test("CRM", "Create Task with past-due reminder time", r.status_code == 201, f"Task ID: {task_id}")

    # 2.4 Trigger Task Reminder alerting
    if task_id:
        # Run background worker for Task Reminders
        from app.workers.tasks import check_task_reminders
        import asyncio
        try:
            asyncio.run(check_task_reminders())
            print_test("CRM", "Executed check_task_reminders() background task", True)
        except Exception as ex:
            print_test("CRM", "Executed check_task_reminders() background task", False, str(ex))

        # Verify task reminder status directly from DB
        from app.models.task import Task
        db = SessionLocal()
        t_obj = db.query(Task).filter(Task.id == task_id).first()
        reminder_sent = t_obj.reminder_sent if t_obj else False
        db.close()
        print_test("CRM", "Verify task reminder marked sent (reminder_sent = True)", reminder_sent is True)

    # 2.5 Resolve entities
    if task_id:
        r = client.patch(f"{BASE_URL}/tasks/{task_id}", json={"status": "done"})
        print_test("CRM", "Complete Task", r.status_code == 200)

    if ticket_id:
        r = client.patch(f"{BASE_URL}/tickets/{ticket_id}", json={"status": "resolved"})
        print_test("CRM", "Resolve Ticket", r.status_code == 200)

    # ════════════════════════════════════════════════════════════════════════
    # MODULE 3: Bulk Messaging
    # ════════════════════════════════════════════════════════════════════════
    header("MODULE 3: Bulk Messaging")

    # 3.1 Check monthly bulk message credits
    r = client.get(f"{BASE_URL}/bulk/credits")
    credits = r.json()
    print_test("BULK", "Retrieve Bulk Message Credits", r.status_code == 200, f"Remaining: {credits.get('remaining')}")

    # 3.2 Create message template
    r = client.post(f"{BASE_URL}/bulk/templates", json={"name": f"MasterTemplate_{ts}", "body": "Hello {{name}}, this is a production check."})
    tpl_id = r.json().get("id") if r.status_code == 201 else None
    print_test("BULK", "Create Bulk Message Template", r.status_code == 201, f"Tpl ID: {tpl_id}")

    # 3.3 Create Saved Chat List
    r = client.post(f"{BASE_URL}/bulk/chat-lists", json={"name": f"MasterChatList_{ts}", "chat_ids": [CHAT_ID]})
    list_id = r.json().get("id") if r.status_code == 201 else None
    print_test("BULK", "Create Saved Chat List", r.status_code == 201, f"List ID: {list_id}")

    # 3.4 Create Bulk Job targeting CHAT_ID
    job_payload = {
        "name": f"Master Bulk Campaign {ts}",
        "message": "Hello hr Aarvi, this is an automated Hyperscope master production test.",
        "phone_id": 1,
        "recipient_chat_ids": [str(CHAT_ID)],
        "message_type": "text",
        "delay_seconds": 1,
        "repeat": "none"
    }
    r = client.post(f"{BASE_URL}/bulk/jobs", json=job_payload)
    job_id = r.json().get("id") if r.status_code == 201 else None
    print_test("BULK", "Create Bulk Campaign Job", r.status_code == 201, f"Job ID: {job_id}")

    # 3.5 Execute/Simulate Bulk Campaign Job
    if job_id:
        from app.services.bulk_service import BulkService
        db = SessionLocal()
        try:
            # We execute it synchronously to verify the entire pipeline without async background delay
            import asyncio
            asyncio.run(BulkService(db).execute_job(job_id))
            db.commit()
            print_test("BULK", "Execute Bulk Campaign synchronously", True)
        except Exception as ex:
            print_test("BULK", "Execute Bulk Campaign synchronously", False, str(ex))
        finally:
            db.close()

        # 3.6 Check job logs
        r = client.get(f"{BASE_URL}/bulk/jobs/{job_id}/logs")
        logs_resp = r.json()
        logs = logs_resp.get("logs", [])
        print_test("BULK", "Verify Bulk Job delivery logs", r.status_code == 200, f"Delivery records: {len(logs)}")
        if logs:
            print_test("BULK", "  Delivery Status check", logs[0].get("status") in ("sent", "failed"), f"Status: {logs[0].get('status')}")

    # Cleanup Template & Saved List
    if tpl_id:
        client.delete(f"{BASE_URL}/bulk/templates/{tpl_id}")
    if list_id:
        client.delete(f"{BASE_URL}/bulk/chat-lists/{list_id}")

    # ════════════════════════════════════════════════════════════════════════
    # MODULE 4: AI Agent & Gemini Integration
    # ════════════════════════════════════════════════════════════════════════
    header("MODULE 4: AI Agent & Gemini Integration")

    # 4.1 Update settings
    r = client.put(f"{BASE_URL}/ai/settings", json={"enabled": True, "personality": "friendly", "agent_name": "HyperBot"})
    print_test("AI", "Update AI Agent Settings", r.status_code == 200)

    # 4.2 Activate AI on chat
    r = client.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/activate")
    print_test("AI", "Activate AI on target chat 1608", r.status_code == 200)

    # 4.3 Request Reply Suggestion (Gemini Reply generator)
    r = client.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/suggest-reply")
    if r.status_code == 200:
        reply = r.json().get("reply", "")
        print_test("AI", "Suggest reply for chat", True, f"Reply: '{reply[:50]}...'")
    else:
        print_test("AI", "Suggest reply for chat", False, f"Status: {r.status_code}")

    # 4.4 Summarize Chat (Gemini Summarizer)
    r = client.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/summarize")
    if r.status_code == 200:
        summary = r.json().get("summary", "")
        print_test("AI", "Summarize Chat", True, f"Summary: '{summary[:50]}...'")
    else:
        print_test("AI", "Summarize Chat", False, f"Status: {r.status_code}")

    # 4.5 Polish Draft Tone (Gemini Polisher)
    r = client.post(f"{BASE_URL}/ai/polish", json={"text": "tell them we will reply soon", "tone": "professional"})
    if r.status_code == 200:
        polished = r.json().get("polished", "")
        print_test("AI", "Polish draft reply", True, f"Polished: '{polished[:50]}...'")
    else:
        print_test("AI", "Polish draft reply", False, f"Status: {r.status_code}")

    # 4.6 Translation (Gemini Translator)
    r = client.post(f"{BASE_URL}/ai/translate", json={"text": "Good morning", "target_language": "French"})
    if r.status_code == 200:
        translated = r.json().get("translated", "")
        print_test("AI", "Translate text", True, f"Translated: '{translated[:50]}...'")
    else:
        print_test("AI", "Translate text", False, f"Status: {r.status_code}")

    # 4.7 Assistant recipes (Chat scope)
    r = client.post(f"{BASE_URL}/ai/assistant", json={"chat_id": CHAT_ID, "recipe": "sentiment"})
    if r.status_code == 200:
        ans = r.json().get("answer", "")
        print_test("AI", "Chat Assistant recipe: sentiment", True, f"Ans: '{ans[:50]}...'")
    else:
        print_test("AI", "Chat Assistant recipe: sentiment", False, f"Status: {r.status_code}")

    # 4.8 Assistant recipes (Org scope)
    r = client.post(f"{BASE_URL}/ai/assistant", json={"recipe": "stale_tickets"})
    if r.status_code == 200:
        ans = r.json().get("answer", "")
        print_test("AI", "Org Assistant recipe: stale_tickets", True, f"Ans: '{ans[:50]}...'")
    else:
        print_test("AI", "Org Assistant recipe: stale_tickets", False, f"Status: {r.status_code}")

    # 4.9 Deactivate AI
    r = client.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/deactivate")
    print_test("AI", "Deactivate AI on chat 1608", r.status_code == 200)

    # ════════════════════════════════════════════════════════════════════════
    # MODULE 5: Audit Log Verifications
    # ════════════════════════════════════════════════════════════════════════
    header("MODULE 5: Audit Logs Verification")
    r = client.get(f"{BASE_URL}/logs", params={"limit": 20})
    logs = r.json()
    print_test("LOGS", "Retrieve latest activity logs", r.status_code == 200, f"Found: {len(logs)}")
    
    actions = [l.get("action") for l in logs]
    critical_actions = ["ticket_created", "ticket_updated", "task_created", "task_updated", "bulk_job_created"]
    for act in critical_actions:
        found = act in actions
        print_test("LOGS", f"Verify log action type '{act}' recorded", found)

    # ════════════════════════════════════════════════════════════════════════
    # FINAL RESULTS
    # ════════════════════════════════════════════════════════════════════════
    total = results["pass"] + results["fail"]
    print("\n" + "=" * 90)
    print(f"  FINAL SUMMARY: {results['pass']}/{total} PASSED  |  {results['fail']} FAILED")
    print("=" * 90)
    if results["fail"] > 0:
        print("  ⚠️  System has regression issues. Check the log details above.")
        sys.exit(1)
    else:
        print("  🎉 ALL CRM, CHATS, BULK AND AI AGENT PIPELINES ARE 100% PRODUCTION READY!")
    print("=" * 90)

if __name__ == "__main__":
    run_master_check()
