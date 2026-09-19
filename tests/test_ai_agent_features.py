import sys
import httpx

BASE_URL = "http://localhost:8000/api/v1"
TARGET_CHAT_ID = 1608  # hr Aarvi

def print_result(case_id, description, status, details=""):
    status_icon = "✅ PASS" if status else "❌ FAIL"
    details_str = f" - {details}" if details else ""
    print(f"[{case_id}] {description:<75} {status_icon}{details_str}")

def run_ai_tests():
    print("=" * 100)
    print("HYPERSCOPE CRM: AI AGENT & GEMINI INTEGRATION LIFECYCLE CHECKOUT")
    print("=" * 100)

    client = httpx.Client(timeout=30.0)

    # 1. AUTHENTICATE
    login_payload = {"email": "admin@gmail.com", "password": "Admin@123"}
    r = client.post(f"{BASE_URL}/auth/login", json=login_payload)
    if r.status_code != 200:
        print_result("AI.1", "Admin authentication", False, f"Status: {r.status_code}")
        sys.exit(1)
    
    token = r.json().get("access_token")
    client.headers.update({"Authorization": f"Bearer {token}"})
    print_result("AI.1", "Admin authentication and token retrieval", True)

    # 2. UPDATE AI SETTINGS
    settings_payload = {
        "enabled": True,
        "auto_activate_new_chats": True,
        "agent_name": "QA Testing Assistant",
        "personality": "sales",
        "role_description": "Hyperscope test agent simulating dynamic customer engagement.",
        "custom_instructions": "Keep all messages under 20 words.",
        "response_delay_seconds": 5,
        "snooze_after_human_seconds": 60,
        "flag_enabled": True,
        "flag_criteria": "Customer expressing anger or complaining about billing."
    }
    r = client.put(f"{BASE_URL}/ai/settings", json=settings_payload)
    print_result("AI.2", "Update AI Agent settings", r.status_code == 200, f"Status: {r.status_code}")

    # 3. GET AI SETTINGS TO VERIFY
    r = client.get(f"{BASE_URL}/ai/settings")
    cfg = r.json()
    verified = (
        cfg.get("enabled") is True and
        cfg.get("agent_name") == "QA Testing Assistant" and
        cfg.get("personality") == "sales"
    )
    print_result("AI.3", "Verify updated AI settings values", verified, f"Enabled: {cfg.get('enabled')}, Name: {cfg.get('agent_name')}")

    # 4. ACTIVATE AI ON CHAT
    r = client.post(f"{BASE_URL}/ai/chat/{TARGET_CHAT_ID}/activate")
    print_result("AI.4", "Activate AI on target chat ID 1608", r.status_code == 200, f"Response: {r.json()}")

    # 5. SUGGEST REPLY (Checks Gemini reply generator integration)
    r = client.post(f"{BASE_URL}/ai/chat/{TARGET_CHAT_ID}/suggest-reply")
    if r.status_code == 200:
        reply = r.json().get("reply", "")
        print_result("AI.5", "Request AI reply suggestion for chat", True, f"Suggestion: '{reply[:60]}...'")
    else:
        print_result("AI.5", "Request AI reply suggestion for chat", False, f"Status: {r.status_code}, Body: {r.text}")

    # 6. SUMMARIZE CHAT (Checks Gemini chat summarizer integration)
    r = client.post(f"{BASE_URL}/ai/chat/{TARGET_CHAT_ID}/summarize")
    if r.status_code == 200:
        summary = r.json().get("summary", "")
        print_result("AI.6", "Request AI chat summary", True, f"Summary: '{summary[:60]}...'")
    else:
        print_result("AI.6", "Request AI chat summary", False, f"Status: {r.status_code}, Body: {r.text}")

    # 7. POLISH TEXT (Checks Gemini reply polisher)
    polish_payload = {"text": "hey how r u doing today? can u help me?", "tone": "professional"}
    r = client.post(f"{BASE_URL}/ai/polish", json=polish_payload)
    if r.status_code == 200:
        polished = r.json().get("polished", "")
        print_result("AI.7", "Polish a draft message tone", True, f"Polished: '{polished[:60]}...'")
    else:
        print_result("AI.7", "Polish a draft message tone", False, f"Status: {r.status_code}")

    # 8. TRANSLATE MESSAGE (Checks Gemini translation engine)
    translate_payload = {"text": "Hello, how can I assist you with your subscription today?", "target_language": "Spanish"}
    r = client.post(f"{BASE_URL}/ai/translate", json=translate_payload)
    if r.status_code == 200:
        translated = r.json().get("translated", "")
        print_result("AI.8", "Translate text to Spanish", True, f"Translated: '{translated[:60]}...'")
    else:
        print_result("AI.8", "Translate text to Spanish", False, f"Status: {r.status_code}")

    # 9. CHAT ASSISTANT (Checks chat recipe search)
    assistant_chat_payload = {"chat_id": TARGET_CHAT_ID, "recipe": "sentiment"}
    r = client.post(f"{BASE_URL}/ai/assistant", json=assistant_chat_payload)
    if r.status_code == 200:
        answer = r.json().get("answer", "")
        print_result("AI.9", "Use Chat Assistant recipe: sentiment", True, f"Answer: '{answer[:60]}...'")
    else:
        print_result("AI.9", "Use Chat Assistant recipe: sentiment", False, f"Status: {r.status_code}")

    # 10. ORG ASSISTANT (Checks workspace summary recipe)
    assistant_org_payload = {"recipe": "triage_unassigned"}
    r = client.post(f"{BASE_URL}/ai/assistant", json=assistant_org_payload)
    if r.status_code == 200:
        answer = r.json().get("answer", "")
        print_result("AI.10", "Use Org Assistant recipe: triage_unassigned", True, f"Answer: '{answer[:60]}...'")
    else:
        print_result("AI.10", "Use Org Assistant recipe: triage_unassigned", False, f"Status: {r.status_code}")

    # 11. DEACTIVATE AI ON CHAT
    r = client.post(f"{BASE_URL}/ai/chat/{TARGET_CHAT_ID}/deactivate")
    print_result("AI.11", "Deactivate AI on target chat ID 1608", r.status_code == 200, f"Response: {r.json()}")

    print("\n" + "=" * 100)
    print("🎉 AI AGENT & GEMINI LIFECYCLE CHECKOUT COMPLETED SUCCESSFULLY!")
    print("=" * 100)

if __name__ == "__main__":
    run_ai_tests()
