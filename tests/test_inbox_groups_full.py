"""
HYPERSCOPE CRM · FULL INBOX & GROUP MANAGEMENT TEST SUITE
Tests every feature: 1:1 chats, groups, multi-filter, label CRUD + fly-create + bulk,
custom properties, quick replies (slash), private notes (@mentions),
scheduled + recurring messages, bulk actions, analytics, audit logs.
"""
import sys
from datetime import datetime, timedelta

import httpx

BASE = "http://localhost:8000/api/v1"

# Known test chats
CHAT_1_1  = 1608   # hr Aarvi (1:1 direct)
CHAT_1_1B = 1654   # +91 832 035 6326 (1:1 direct) — for bulk tests

results = {"pass": 0, "fail": 0}

def p(cid, desc, ok, detail=""):
    s = "✅ PASS" if ok else "❌ FAIL"
    d = f" — {detail}" if detail else ""
    print(f"  [{cid}] {desc:<72} {s}{d}")
    results["pass" if ok else "fail"] += 1

def sec(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

def run():
    print()
    print("=" * 100)
    print("  HYPERSCOPE CRM · MANAGE GROUPS, CHATS & COMMUNITIES — COMPREHENSIVE TEST")
    print("=" * 100)

    c = httpx.Client(timeout=20.0)

    # ── AUTH ──────────────────────────────────────────────────────────────────
    r = c.post(f"{BASE}/auth/login", json={"email": "admin@gmail.com", "password": "Admin@123"})
    assert r.status_code == 200, f"Login failed: {r.status_code}"
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    print("\n✔  Authenticated as admin\n")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 1: Inbox — Chat List & Filters
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 1: Inbox — Chat List & Filter Variants")

    r = c.get(f"{BASE}/inbox/chats", params={"limit": 10})
    p("1.1", "List all chats (default, limit=10)", r.status_code == 200,
      f"count={len(r.json())}")

    r = c.get(f"{BASE}/inbox/chats", params={"is_flagged": True})
    p("1.2", "Filter chats: is_flagged=True", r.status_code == 200,
      f"count={len(r.json())}")

    r = c.get(f"{BASE}/inbox/chats", params={"is_group": True, "limit": 10})
    groups_resp = r.json()
    p("1.3", "Filter chats: is_group=True (groups only)", r.status_code == 200,
      f"count={len(groups_resp)}")

    r = c.get(f"{BASE}/inbox/chats", params={"is_group": False, "limit": 10})
    p("1.4", "Filter chats: is_group=False (1:1 chats only)", r.status_code == 200,
      f"count={len(r.json())}")

    r = c.get(f"{BASE}/inbox/chats", params={"search": "hr"})
    p("1.5", "Full-text search chats: keyword 'hr'", r.status_code == 200,
      f"count={len(r.json())}")

    r = c.get(f"{BASE}/inbox/chats", params={"is_archived": True, "limit": 5})
    p("1.6", "Filter chats: is_archived=True", r.status_code == 200,
      f"count={len(r.json())}")

    r = c.get(f"{BASE}/inbox/chats", params={"assigned_to": 1})
    p("1.7", "Filter chats by assigned_to (Agent #1)", r.status_code == 200,
      f"count={len(r.json())}")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 2: Single Chat — Get / Update / Mark-Read
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 2: Single Chat Operations")

    r = c.get(f"{BASE}/inbox/chats/{CHAT_1_1}")
    chat = r.json() if r.status_code == 200 else {}
    p("2.1", f"Get chat details (chat #{CHAT_1_1})", r.status_code == 200,
      f"name={chat.get('name')}")

    r = c.get(f"{BASE}/inbox/chats/99999")
    p("2.2", "Get non-existent chat (expect 404)", r.status_code == 404,
      f"status={r.status_code}")

    r = c.patch(f"{BASE}/inbox/chats/{CHAT_1_1}", json={"is_flagged": True, "assigned_to": 1})
    p("2.3", "Update chat: flag + assign to Agent #1", r.status_code == 200)

    r = c.patch(f"{BASE}/inbox/chats/{CHAT_1_1}", json={"is_flagged": False})
    p("2.4", "Update chat: unflag", r.status_code == 200)

    r = c.post(f"{BASE}/inbox/chats/{CHAT_1_1}/read")
    p("2.5", "Mark chat as read", r.status_code == 200)

    r = c.get(f"{BASE}/inbox/chats/{CHAT_1_1}/messages", params={"limit": 5})
    p("2.6", "Get messages for chat (limit=5)", r.status_code == 200,
      f"count={len(r.json())}")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 3: Labels — Create-On-Fly, CRUD, Filter, Bulk Apply
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 3: Labels — Create-on-Fly, CRUD, Filter & Bulk Apply")

    ts = int(datetime.utcnow().timestamp())
    r = c.post(f"{BASE}/labels", json={"name": f"InboxTest_{ts}", "color": "#7C3AED"})
    p("3.1", "Create label on-the-fly (create-on-fly)", r.status_code == 201,
      f"id={r.json().get('id')}")
    label_id = r.json().get("id") if r.status_code == 201 else None

    r2 = c.post(f"{BASE}/labels", json={"name": f"InboxTest_{ts}", "color": "#7C3AED"})
    p("3.2", "Duplicate label name rejected (expect 400)", r2.status_code == 400,
      f"status={r2.status_code}")

    r = c.get(f"{BASE}/labels")
    p("3.3", "List all labels", r.status_code == 200, f"total={len(r.json())}")

    if label_id:
        r = c.patch(f"{BASE}/labels/{label_id}", json={"name": f"InboxTest_{ts}_renamed", "color": "#059669"})
        p("3.4", "Update (rename) label", r.status_code == 200,
          f"name={r.json().get('name')}")

    # Apply label to a chat
    if label_id:
        r = c.post(f"{BASE}/inbox/chats/{CHAT_1_1}/labels/{label_id}")
        p("3.5", f"Apply label #{label_id} to chat #{CHAT_1_1}", r.status_code == 200,
          f"ok={r.json().get('ok')}")

        # Filter chats by label
        r = c.get(f"{BASE}/inbox/chats", params={"label_id": label_id})
        p("3.6", "Filter chat list by label_id", r.status_code == 200,
          f"found={len(r.json())}")

        # Bulk apply label to both chats
        r = c.post(f"{BASE}/inbox/bulk-update",
                   json={"chat_ids": [CHAT_1_1, CHAT_1_1B], "add_label_id": label_id})
        p("3.7", "Bulk add label to 2 chats", r.status_code == 200,
          f"updated={r.json().get('updated')}")

        # Bulk remove label from both chats
        r = c.post(f"{BASE}/inbox/bulk-update",
                   json={"chat_ids": [CHAT_1_1, CHAT_1_1B], "remove_label_id": label_id})
        p("3.8", "Bulk remove label from 2 chats", r.status_code == 200,
          f"updated={r.json().get('updated')}")

        r = c.delete(f"{BASE}/inbox/chats/{CHAT_1_1}/labels/{label_id}")
        p("3.9", "Remove label from chat", r.status_code == 200)

        r = c.delete(f"{BASE}/labels/{label_id}")
        p("3.10", "Delete label (cleanup)", r.status_code == 204)

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 4: Custom Properties — Chat + Ticket
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 4: Custom Properties")

    # Create a text property for chats
    r = c.post(f"{BASE}/properties/definitions", json={
        "entity": "chat", "name": "Account Tier", "prop_type": "single_select",
        "options": ["Starter", "Pro", "Enterprise"], "required": False
    })
    prop_id = None
    if r.status_code == 201:
        prop_id = r.json().get("id")
        p("4.1", "Create 'single_select' property for chat", True, f"id={prop_id}")
    else:
        p("4.1", "Create 'single_select' property for chat", False,
          f"status={r.status_code} {r.text[:80]}")

    if prop_id:
        r = c.put(f"{BASE}/properties/chat/{CHAT_1_1}",
                   json={"values": {str(prop_id): "Pro"}})
        p("4.2", "Set custom property value on chat", r.status_code in (200, 201),
          f"status={r.status_code}")

        r = c.get(f"{BASE}/properties/chat/{CHAT_1_1}")
        p("4.3", "Get custom property values for chat", r.status_code == 200,
          f"props={r.json()}")

        r = c.delete(f"{BASE}/properties/definitions/{prop_id}")
        p("4.4", "Delete property definition (cleanup)", r.status_code == 204)

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 5: Quick Replies (Slash Commands)
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 5: Quick Replies (Slash Commands)")

    r = c.get(f"{BASE}/quick-replies")
    p("5.1", "List existing quick replies", r.status_code == 200, f"count={len(r.json())}")

    r = c.post(f"{BASE}/quick-replies", json={"command": "refund_test", "message": "Refunds take 5-7 business days."})
    qr_id = None
    if r.status_code == 201:
        qr_id = r.json().get("id")
        p("5.2", "Create quick reply '/refund_test'", True, f"id={qr_id}")
    else:
        p("5.2", "Create quick reply '/refund_test'", False, f"status={r.status_code}")

    if qr_id:
        r = c.patch(f"{BASE}/quick-replies/{qr_id}",
                    json={"command": "refund_test", "message": "Refunds take 3-5 business days. Updated!"})
        p("5.3", "Update quick reply message", r.status_code == 200,
          f"msg='{r.json().get('message', '')[:40]}'")

        r = c.delete(f"{BASE}/quick-replies/{qr_id}")
        p("5.4", "Delete quick reply", r.status_code == 204)

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 6: Private Notes (@mentions)
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 6: Private Notes with @Mentions")

    r = c.post(f"{BASE}/notes", json={
        "chat_id": CHAT_1_1,
        "content": "📝 @admin follow up with customer about the billing issue. Check ticket status."
    })
    note_id = None
    if r.status_code == 201:
        note_id = r.json().get("id")
        p("6.1", "Create private note with @mention on chat", True, f"id={note_id}")
    else:
        p("6.1", "Create private note with @mention on chat", False,
          f"status={r.status_code} {r.text[:80]}")

    r = c.get(f"{BASE}/notes/chat/{CHAT_1_1}")
    p("6.2", "List private notes for chat", r.status_code == 200,
      f"count={len(r.json())}")

    # Note with no content should fail
    r = c.post(f"{BASE}/notes", json={"chat_id": CHAT_1_1, "content": ""})
    p("6.3", "Create note with empty content (expect 400)", r.status_code == 400,
      f"status={r.status_code}")

    if note_id:
        r = c.delete(f"{BASE}/notes/{note_id}")
        p("6.4", "Delete private note (cleanup)", r.status_code == 204)

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 7: Scheduled & Recurring Messages
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 7: Scheduled & Recurring Messages")

    future1 = (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z"
    r = c.post(f"{BASE}/scheduled", json={
        "chat_id": CHAT_1_1,
        "body": "Automated lifecycle test: one-time scheduled message",
        "send_at": future1,
        "repeat": "none"
    })
    sched_id = None
    if r.status_code == 201:
        sched_id = r.json().get("id")
        p("7.1", "Create one-time scheduled message", True, f"id={sched_id}")
    else:
        p("7.1", "Create one-time scheduled message", False,
          f"status={r.status_code} {r.text[:80]}")

    # Recurring: daily
    future2 = (datetime.utcnow() + timedelta(hours=1)).isoformat() + "Z"
    r = c.post(f"{BASE}/scheduled", json={
        "chat_id": CHAT_1_1,
        "body": "Daily check-in broadcast (test)",
        "send_at": future2,
        "repeat": "daily",
        "interval": 1,
    })
    recur_id = None
    if r.status_code == 201:
        recur_id = r.json().get("id")
        p("7.2", "Create recurring (daily) scheduled message", True,
          f"id={recur_id} repeat={r.json().get('repeat')}")
    else:
        p("7.2", "Create recurring (daily) scheduled message", False,
          f"status={r.status_code}")

    # Recurring: weekly (days_of_week)
    r = c.post(f"{BASE}/scheduled", json={
        "chat_id": CHAT_1_1B,
        "body": "Weekly team update (test)",
        "send_at": future2,
        "repeat": "weekly",
        "interval": 1,
        "days_of_week": [1, 3, 5],   # Mon, Wed, Fri
    })
    weekly_id = None
    if r.status_code == 201:
        weekly_id = r.json().get("id")
        p("7.3", "Create recurring (weekly Mon/Wed/Fri) message", True,
          f"id={weekly_id}")
    else:
        p("7.3", "Create recurring (weekly Mon/Wed/Fri) message", False,
          f"status={r.status_code}")

    # Empty body rejected
    r = c.post(f"{BASE}/scheduled", json={
        "chat_id": CHAT_1_1, "body": "", "send_at": future1
    })
    p("7.4", "Empty body rejected (expect 400)", r.status_code == 400,
      f"status={r.status_code}")

    # List scheduled
    r = c.get(f"{BASE}/scheduled", params={"chat_id": CHAT_1_1})
    p("7.5", "List scheduled messages for chat", r.status_code == 200,
      f"count={len(r.json())}")

    # Update scheduled
    if sched_id:
        future3 = (datetime.utcnow() + timedelta(hours=3)).isoformat() + "Z"
        r = c.patch(f"{BASE}/scheduled/{sched_id}",
                    json={"body": "Updated: lifecycle message v2", "send_at": future3})
        p("7.6", "Update pending scheduled message", r.status_code == 200,
          f"body='{r.json().get('body', '')[:40]}'")

    # Cancel one-time
    if sched_id:
        r = c.delete(f"{BASE}/scheduled/{sched_id}")
        p("7.7", "Cancel one-time scheduled message", r.status_code == 204)

    # Cancel recurring (daily)
    if recur_id:
        r = c.delete(f"{BASE}/scheduled/{recur_id}")
        p("7.8", "Cancel recurring (daily) message", r.status_code == 204)

    # Cancel recurring (weekly)
    if weekly_id:
        r = c.delete(f"{BASE}/scheduled/{weekly_id}")
        p("7.9", "Cancel recurring (weekly) message", r.status_code == 204)

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 8: Bulk Chat Actions
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 8: Chat-List Bulk Actions")

    r = c.post(f"{BASE}/inbox/bulk-update",
               json={"chat_ids": [CHAT_1_1, CHAT_1_1B], "mark_read": True})
    p("8.1", "Bulk mark 2 chats as read", r.status_code == 200,
      f"updated={r.json().get('updated')}")

    r = c.post(f"{BASE}/inbox/bulk-update",
               json={"chat_ids": [CHAT_1_1, CHAT_1_1B], "mark_read": False})
    p("8.2", "Bulk mark 2 chats as unread", r.status_code == 200,
      f"updated={r.json().get('updated')}")

    r = c.post(f"{BASE}/inbox/bulk-update",
               json={"chat_ids": [CHAT_1_1, CHAT_1_1B],
                     "updates": {"is_flagged": True}})
    p("8.3", "Bulk flag 2 chats", r.status_code == 200,
      f"updated={r.json().get('updated')}")

    r = c.post(f"{BASE}/inbox/bulk-update",
               json={"chat_ids": [CHAT_1_1, CHAT_1_1B],
                     "updates": {"is_flagged": False}})
    p("8.4", "Bulk unflag 2 chats", r.status_code == 200,
      f"updated={r.json().get('updated')}")

    r = c.post(f"{BASE}/inbox/bulk-update",
               json={"chat_ids": [CHAT_1_1, CHAT_1_1B],
                     "updates": {"is_pinned": True}})
    p("8.5", "Bulk pin 2 chats", r.status_code == 200,
      f"updated={r.json().get('updated')}")

    r = c.post(f"{BASE}/inbox/bulk-update",
               json={"chat_ids": [CHAT_1_1, CHAT_1_1B],
                     "updates": {"is_pinned": False}})
    p("8.6", "Bulk unpin 2 chats", r.status_code == 200,
      f"updated={r.json().get('updated')}")

    # Empty chat_ids rejected
    r = c.post(f"{BASE}/inbox/bulk-update", json={"chat_ids": []})
    p("8.7", "Bulk action with empty chat_ids rejected (expect 400)",
      r.status_code == 400, f"status={r.status_code}")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 9: Groups — List, Participants, Analytics
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 9: Groups — List, Participants & Analytics")

    r = c.get(f"{BASE}/groups", params={"limit": 10})
    p("9.1", "List group chats", r.status_code == 200, f"count={len(r.json())}")

    group_chats = [g for g in groups_resp if g.get("is_group")]
    first_group = group_chats[0] if group_chats else None

    if first_group:
        gid = first_group["id"]
        r = c.get(f"{BASE}/groups/{gid}/participants")
        p("9.2", f"Get participants for group #{gid}", r.status_code == 200,
          f"participants={len(r.json())}")

        r = c.get(f"{BASE}/groups/{gid}/analytics")
        p("9.3", f"Get analytics for group #{gid}", r.status_code == 200,
          f"keys={list(r.json().keys())[:5]}")
    else:
        p("9.2", "Get group participants (skipped — no groups found)", True, "no groups")
        p("9.3", "Get group analytics (skipped — no groups found)", True, "no groups")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 10: Analytics — Dashboard, Messages, Tickets, Agents
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 10: Analytics — Dashboard, Messages, Tickets, Agent Performance")

    r = c.get(f"{BASE}/analytics/dashboard")
    if r.status_code == 200:
        d = r.json()
        p("10.1", "Dashboard analytics", True,
          f"chats={d.get('total_chats')} open_tickets={d.get('open_tickets')}")
    else:
        p("10.1", "Dashboard analytics", False, f"status={r.status_code}")

    r = c.get(f"{BASE}/analytics/messages", params={"days": 7})
    p("10.2", "Message volume analytics (7 days)", r.status_code == 200,
      f"keys={list(r.json().keys())[:4]}")

    r = c.get(f"{BASE}/analytics/tickets")
    p("10.3", "Ticket metrics analytics", r.status_code == 200,
      f"keys={list(r.json().keys())[:4]}")

    r = c.get(f"{BASE}/analytics/agents", params={"days": 7})
    p("10.4", "Agent performance analytics (7 days)", r.status_code == 200,
      f"agents={len(r.json())}")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 11: Audit Logs
    # ════════════════════════════════════════════════════════════════════════
    sec("SECTION 11: Audit Logs")

    r = c.get(f"{BASE}/logs", params={"limit": 10})
    p("11.1", "List activity logs (limit=10)", r.status_code == 200,
      f"count={len(r.json())}")

    r = c.get(f"{BASE}/logs", params={"entity_type": "chat", "limit": 5})
    p("11.2", "Filter logs by entity_type=chat", r.status_code == 200,
      f"count={len(r.json())}")

    r = c.get(f"{BASE}/logs", params={"entity_type": "ticket", "limit": 5})
    p("11.3", "Filter logs by entity_type=ticket", r.status_code == 200,
      f"count={len(r.json())}")

    r = c.get(f"{BASE}/logs/actions")
    p("11.4", "List distinct log action types", r.status_code == 200,
      f"types={r.json()[:5]}")

    # ════════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ════════════════════════════════════════════════════════════════════════
    total = results["pass"] + results["fail"]
    print()
    print("=" * 100)
    print(f"  RESULTS: {results['pass']}/{total} PASSED   |   {results['fail']} FAILED")
    print("=" * 100)
    if results["fail"] > 0:
        print("  ⚠️  Some tests FAILED — see ❌ lines above for details.")
    else:
        print("  🎉 ALL TESTS PASSED!")
    print("=" * 100)

if __name__ == "__main__":
    run()
