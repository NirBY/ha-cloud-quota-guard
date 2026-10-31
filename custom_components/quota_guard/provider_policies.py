"""Source-backed limits are reference information, never fabricated usage."""

TUYA_DOC = "https://developer.tuya.com/en/docs/iot/frequency-control?id=Kcojz2r2dg1f6"
TELEGRAM_FAQ = "https://core.telegram.org/bots/faq#how-do-i-avoid-hitting-limits"
TELEGRAM_API = "https://core.telegram.org/bots/api#sending-files"
XIAOMI_DOC = "https://github.com/al-one/hass-xiaomi-miot#config"


def policy_for(domain, data=None, options=None):
    """Return public policy plus safe connection settings; never copy credentials."""
    data, options = data or {}, options or {}
    if domain == "telegram_bot":
        endpoint = data.get("api_endpoint") or "https://api.telegram.org"
        custom = endpoint.rstrip("/") != "https://api.telegram.org"
        return {
            "title": "Telegram Bot",
            "headline": "1 message per second per chat",
            "summary": "Documented rate, text and file limits. No actual send counter is connected.",
            "scope": "Standard cloud Bot API"
            if not custom
            else "Custom Bot API server — check its server limits",
            "notes": [
                "Per-chat guidance: avoid more than one message per second; exceeding this may return 429.",
                "20 messages per minute is a group limit; do not automatically apply it to every channel.",
                "Free bulk broadcast rate: about 30 messages per second. Paid broadcasts are separate and are not enabled here.",
                "File limits depend on media type and upload method. A local Bot API server may have different limits.",
                "These limits apply per time window or request, not as a cumulative daily allowance. No automatic cutoff is based on them here.",
            ],
            "limits": [
                {
                    "name": "Per-chat rate",
                    "value": "1",
                    "unit": "message per second",
                    "scope": "Per-chat rate guidance",
                    "source": TELEGRAM_FAQ,
                },
                {
                    "name": "Send to group",
                    "value": "20",
                    "unit": "messages per minute",
                    "scope": "Per group",
                    "source": TELEGRAM_FAQ,
                },
                {
                    "name": "Free bulk broadcast",
                    "value": "≈30",
                    "unit": "messages per second",
                    "scope": "All chats for the bot combined",
                    "source": TELEGRAM_FAQ,
                },
                {
                    "name": "Text message length",
                    "value": "4,096",
                    "unit": "characters",
                    "scope": "After entity parsing",
                    "source": "https://core.telegram.org/bots/api#sendmessage",
                },
                {
                    "name": "Upload photo",
                    "value": "10",
                    "unit": "MB",
                    "scope": "multipart/form-data · new photo",
                    "source": TELEGRAM_API,
                },
                {
                    "name": "Upload other file",
                    "value": "50",
                    "unit": "MB",
                    "scope": "multipart/form-data · new file",
                    "source": TELEGRAM_API,
                },
                {
                    "name": "Send using URL",
                    "value": "5 / 20",
                    "unit": "MB",
                    "scope": "Photo / other file",
                    "source": TELEGRAM_API,
                },
                {
                    "name": "Download file to bot",
                    "value": "20",
                    "unit": "MB",
                    "scope": "getFile on the standard server",
                    "source": "https://core.telegram.org/bots/api#getfile",
                },
            ],
            "source": TELEGRAM_FAQ,
        }
    if domain == "tuya":
        sharing = "user_code" in data or "terminal_id" in data
        return {
            "title": "Tuya",
            "headline": "Limits vary by API",
            "summary": "Sharing SDK: the active account limit is not verified."
            if sharing
            else "Cloud OpenAPI: each endpoint has a separate per-project limit.",
            "scope": "Tuya Sharing SDK — Cloud OpenAPI limits are reference only"
            if sharing
            else "Cloud OpenAPI — per endpoint and project",
            "notes": [
                "There is no single Tuya-wide QPS limit. Requests per second do not establish a daily allowance.",
                "The limits below come from the linked Cloud OpenAPI documentation; they are not measured account usage.",
                "The installed official integration uses Sharing SDK; Cloud OpenAPI numbers must not be treated as account protection for it."
                if sharing
                else "Match the limit to the endpoint actually called and the relevant project.",
            ],
            "limits": [
                {
                    "name": "Query device properties",
                    "value": "50",
                    "unit": "requests per second",
                    "scope": "GET /v2.0/cloud/thing/{device_id}/shadow/properties",
                    "source": TUYA_DOC,
                },
                {
                    "name": "Query device state",
                    "value": "5",
                    "unit": "requests per second",
                    "scope": "GET /v2.0/cloud/thing/{device_id}/state",
                    "source": TUYA_DOC,
                },
                {
                    "name": "Invoke action",
                    "value": "4",
                    "unit": "requests per second",
                    "scope": "POST /v2.0/cloud/thing/{device_id}/shadow/actions",
                    "source": TUYA_DOC,
                },
            ],
            "source": TUYA_DOC,
        }
    if domain == "xiaomi_miot":
        mode = options.get("conn_mode", data.get("conn_mode"))
        labels = {
            "auto": "Automatic — LAN where supported, cloud as needed",
            "local": "Local connection",
            "cloud": "Cloud connection",
        }
        return {
            "title": "Xiaomi MIoT",
            "headline": {
                "auto": "Auto · local and cloud",
                "local": "Local · local connection",
                "cloud": "Cloud · cloud connection",
            }.get(mode, "MIoT settings"),
            "summary": "No verified numeric request limit found. Polling frequency is not a quota.",
            "scope": labels.get(mode, "Connection mode is not set at entry level"),
            "connection_mode": mode,
            "notes": [
                "Auto is not cloud-only: the integration prefers LAN on supported devices and uses cloud as needed.",
                "Local mode does not guarantee all account communication is local; some features may use cloud.",
                "Do not infer a daily limit or usage percentage from device count or a 30/60-second polling interval.",
                "No uniform official numeric account limit was found in the verified sources. A 429 response alone does not reveal the limit.",
            ],
            "limits": [],
            "source": XIAOMI_DOC,
        }
    return None
