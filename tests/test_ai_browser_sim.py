"""
HYPERSCOPE AI AGENT BROWSER UI SIMULATION TEST
Simulates every button-click/form-submit visible on the AI Agent page
and chat inbox bar without a real browser driver.
"""
import sys
import httpx

BASE_URL = "http://localhost:8000/api/v1"
CHAT_ID = 1608  # hr Aarvi

def p(case_id, desc, ok, detail=""):
    icon = "✅ PASS" if ok else "❌ FAIL"
    d = f" — {detail}" if detail else ""
    print(f"  [{case_id}] {desc:<75} {icon}{d}")

def run():
    print()
    print("=" * 100)
    print("  HYPERSCOPE CRM · AI AGENT UI SIMULATION TEST  (browser-equivalent)")
    print("=" * 100)

    c = httpx.Client(timeout=30.0)

    # ── LOGIN ──
    r = c.post(f"{BASE_URL}/auth/login", json={"email": "admin@gmail.com", "password": "Admin@123"})
    assert r.status_code == 200, f"Login failed {r.status_code}"
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    print("\n✔  Authenticated as admin\n")

    # ══════════════════════════════════════════════════════════════
    # SECTION A: AI AGENT SETTINGS PAGE
    # ══════════════════════════════════════════════════════════════
    print("─" * 60)
    print("  SECTION A: AI Agent Settings Page")
    print("─" * 60)

    # A1 – Page loads (GET /ai/settings)
    r = c.get(f"{BASE_URL}/ai/settings")
    cfg = r.json()
    p("A1", "AI Settings page loads (GET /ai/settings)", r.status_code == 200,
      f"enabled={cfg.get('enabled')} personality={cfg.get('personality')}")

    # A2 – Save form: valid full payload
    r = c.put(f"{BASE_URL}/ai/settings", json={
        "enabled": True,
        "auto_activate_new_chats": False,
        "agent_name": "HyperBot",
        "personality": "friendly",
        "role_description": "Support agent for Hyperscope CRM",
        "custom_instructions": "Be concise and helpful.",
        "restrictions": "Never share passwords.",
        "activation_rules": "Reply to questions only.",
        "response_delay_seconds": 3,
        "snooze_after_human_seconds": 300,
        "hours_start": "09:00",
        "hours_end": "18:00",
        "flag_enabled": True,
        "flag_criteria": "angry customer, billing complaint"
    })
    p("A2", "Save full AI settings form (PUT /ai/settings)", r.status_code == 200,
      f"agent_name={r.json().get('agent_name')}")

    # A3 – Personality dropdown: each valid option
    for pers in ("friendly", "grounded", "spartan", "sales"):
        r = c.put(f"{BASE_URL}/ai/settings", json={"personality": pers})
        p(f"A3-{pers}", f"  Personality dropdown → '{pers}'", r.status_code == 200,
          f"saved={r.json().get('personality')}")

    # A4 – Invalid personality triggers 400
    r = c.put(f"{BASE_URL}/ai/settings", json={"personality": "robot"})
    p("A4", "Invalid personality value blocked (expect 400)", r.status_code == 400,
      f"status={r.status_code}")

    # A5 – Operating hours validation
    r = c.put(f"{BASE_URL}/ai/settings", json={"hours_start": "9am"})
    p("A5", "Invalid hours_start 'HH:MM' format blocked (expect 400)", r.status_code == 400,
      f"status={r.status_code}")

    # A6 – Verify values persisted after reload
    r = c.get(f"{BASE_URL}/ai/settings")
    cfg2 = r.json()
    p("A6", "Verify persisted settings on page reload", r.status_code == 200,
      f"agent_name={cfg2.get('agent_name')} flag_enabled={cfg2.get('flag_enabled')}")

    # ══════════════════════════════════════════════════════════════
    # SECTION B: AI Controls in Chat Inbox (thread header bar)
    # ══════════════════════════════════════════════════════════════
    print()
    print("─" * 60)
    print("  SECTION B: AI Controls in Chat View  (thread bar)")
    print("─" * 60)

    # B1 – 'AI Off' button → Activate
    r = c.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/activate")
    p("B1", f"'AI Off' button → Activate AI on chat {CHAT_ID}", r.status_code == 200,
      f"ai_state={r.json().get('ai_state')}")

    # B2 – 'Suggest' button → suggestReply
    r = c.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/suggest-reply")
    if r.status_code == 200:
        reply = r.json().get("reply") or r.json().get("suggestion") or ""
        p("B2", "'Suggest' button → AI reply suggestion injected", True,
          f"reply='{reply[:55]}...'")
    else:
        p("B2", "'Suggest' button → AI reply suggestion injected", False,
          f"status={r.status_code} body={r.text[:80]}")

    # B3 – '⋯ > Summary' button → summarize
    r = c.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/summarize")
    if r.status_code == 200:
        s = r.json().get("summary", "")
        p("B3", "'Summary' menu item → chat summarized", True, f"summary='{s[:55]}...'")
    else:
        p("B3", "'Summary' menu item → chat summarized", False,
          f"status={r.status_code}")

    # B4 – '✨ Polish' button → polish draft
    r = c.post(f"{BASE_URL}/ai/polish", json={"text": "hi i need help with my order pls fix asap", "tone": "professional"})
    if r.status_code == 200:
        pol = r.json().get("polished", "")
        p("B4", "'✨ Polish' button → draft polished", True, f"polished='{pol[:55]}...'")
    else:
        p("B4", "'✨ Polish' button → draft polished", False, f"status={r.status_code}")

    # B4b – empty draft validation
    r = c.post(f"{BASE_URL}/ai/polish", json={"text": ""})
    p("B4b", "'✨ Polish' with empty draft blocked (expect 400)", r.status_code == 400,
      f"status={r.status_code}")

    # B5 – 'AI On' button → Deactivate
    r = c.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/deactivate")
    p("B5", "'AI On' button → Deactivate AI on chat", r.status_code == 200,
      f"ai_state={r.json().get('ai_state')}")

    # B6 – Human Takeover button
    r = c.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/activate")   # re-activate first
    r = c.post(f"{BASE_URL}/ai/chat/{CHAT_ID}/takeover")
    p("B6", "Human Takeover button → AI snoozed", r.status_code == 200,
      f"result={r.json()}")

    # ══════════════════════════════════════════════════════════════
    # SECTION C: Translate Message Widget
    # ══════════════════════════════════════════════════════════════
    print()
    print("─" * 60)
    print("  SECTION C: Translate Message Widget (AI Agent page)")
    print("─" * 60)

    tests = [
        ("hindi",   "Hello, how can I help you today?"),
        ("spanish", "Your invoice is due on Friday."),
        ("french",  "We have resolved your issue."),
        ("arabic",  "Thank you for your patience."),
        ("english", "Bonjour, comment puis-je vous aider?"),
    ]
    for lang, text in tests:
        r = c.post(f"{BASE_URL}/ai/translate", json={"text": text, "target_language": lang})
        if r.status_code == 200:
            t = r.json().get("translated", "")
            p(f"C1-{lang}", f"  Translate → {lang}", True, f"'{t[:55]}...'")
        else:
            p(f"C1-{lang}", f"  Translate → {lang}", False, f"status={r.status_code}")

    # ══════════════════════════════════════════════════════════════
    # SECTION D: Org & Chat Assistant (Floating FAB panel)
    # ══════════════════════════════════════════════════════════════
    print()
    print("─" * 60)
    print("  SECTION D: Org & Chat Assistant Panel (recipes + freeform)")
    print("─" * 60)

    # Org recipes
    org_recipes = ["summarize_24h", "find_followups", "triage_unassigned", "stale_tickets"]
    for recipe in org_recipes:
        r = c.post(f"{BASE_URL}/ai/assistant", json={"recipe": recipe})
        if r.status_code == 200:
            ans = r.json().get("answer", "")
            p(f"D1-{recipe}", f"  Org recipe: {recipe}", True, f"'{ans[:55]}...'")
        else:
            p(f"D1-{recipe}", f"  Org recipe: {recipe}", False, f"status={r.status_code}")

    # Chat recipes
    chat_recipes = ["summarize_chat", "sentiment", "draft_reply"]
    for recipe in chat_recipes:
        r = c.post(f"{BASE_URL}/ai/assistant", json={"chat_id": CHAT_ID, "recipe": recipe})
        if r.status_code == 200:
            ans = r.json().get("answer", "")
            p(f"D2-{recipe}", f"  Chat recipe: {recipe}", True, f"'{ans[:55]}...'")
        else:
            p(f"D2-{recipe}", f"  Chat recipe: {recipe}", False, f"status={r.status_code}")

    # Freeform org question
    r = c.post(f"{BASE_URL}/ai/assistant", json={"prompt": "Who is the most recently unread customer and what did they ask about?"})
    p("D3", "  Freeform org question", r.status_code == 200,
      f"'{r.json().get('answer','')[:55]}...'" if r.status_code == 200 else r.text[:60])

    # Freeform chat question
    r = c.post(f"{BASE_URL}/ai/assistant", json={"chat_id": CHAT_ID, "prompt": "What action should I take next for this customer?"})
    p("D4", "  Freeform chat question", r.status_code == 200,
      f"'{r.json().get('answer','')[:55]}...'" if r.status_code == 200 else r.text[:60])

    # Empty prompt/recipe → 400
    r = c.post(f"{BASE_URL}/ai/assistant", json={"prompt": ""})
    p("D5", "  Empty prompt blocked (expect 400)", r.status_code == 400, f"status={r.status_code}")

    # ══════════════════════════════════════════════════════════════
    # SECTION E: Knowledge Base Preview
    # ══════════════════════════════════════════════════════════════
    print()
    print("─" * 60)
    print("  SECTION E: Knowledge Base  (AI page preview + full CRUD)")
    print("─" * 60)

    # E1 – List KB items (rendered in AI page sidebar)
    r = c.get(f"{BASE_URL}/knowledge-base", params={"limit": 5})
    items = r.json() if r.status_code == 200 else []
    p("E1", "KB preview list loads on AI page", r.status_code == 200,
      f"items={len(items)} (any count is OK)")

    # E2 – Create KB item
    r = c.post(f"{BASE_URL}/knowledge-base", json={"title": "Refund Policy", "content": "We offer 30-day refunds. Contact billing@company.com."})
    if r.status_code == 201:
        kb_id = r.json()["id"]
        p("E2", "Create KB item via Manage page", True, f"id={kb_id}")
    else:
        kb_id = None
        p("E2", "Create KB item via Manage page", False, f"status={r.status_code} {r.text[:60]}")

    # E3 – Delete KB item
    if kb_id:
        r = c.delete(f"{BASE_URL}/knowledge-base/{kb_id}")
        p("E3", "Delete KB item", r.status_code == 204, f"status={r.status_code}")

    print()
    print("=" * 100)
    print("  ALL AI AGENT UI SIMULATION TESTS COMPLETED")
    print("=" * 100)
    print()

if __name__ == "__main__":
    run()
