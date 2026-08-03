_MEDIA_TYPES: set[str] = {
    "image", "photo", "video", "audio", "ptt", "voice",
    "document", "pdf", "sticker", "gif", "location", "contact", "vcard",
}

_MEDIA_LABELS: dict[str, str] = {
    "image": "📷 Photo", "photo": "📷 Photo",
    "video": "🎬 Video",
    "audio": "🎤 Voice message", "voice": "🎤 Voice message", "ptt": "🎤 Voice message",
    "document": "📄 Document", "pdf": "📄 Document",
    "sticker": "🖼 Sticker", "gif": "🎞 GIF",
    "location": "📍 Location",
    "contact": "👤 Contact", "vcard": "👤 Contact",
}

_SYSTEM_LABELS: dict[str, str] = {
    "revoke": "🗑 Message deleted",
    "call_log": "📞 Call",
    "e2e_notification": "🔒 Encrypted notification",
    "notification_template": "📋 Notification",
    "protocol": "🔄 System message",
    "order": "🛒 Order",
    "product": "📦 Product",
    "list": "📋 List message",
    "list_response": "📋 List response",
    "buttons_response": "📋 Button response",
    "template_button_reply": "📋 Template reply",
    "interactive": "📋 Interactive message",
    "poll_creation": "📊 Poll",
    "poll_update": "📊 Poll response",
}
