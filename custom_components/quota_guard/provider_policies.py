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
            "headline": "1 הודעה לשנייה בצ׳אט",
            "summary": "מגבלות קצב, טקסט וקבצים מתועדות. אין חיבור למונה שליחות בפועל.",
            "scope": "Bot API רגיל בענן"
            if not custom
            else "שרת Bot API מותאם — יש לבדוק את מגבלות השרת",
            "notes": [
                "המלצת קצב לצ׳אט בודד: להימנע מיותר מהודעה אחת בשנייה; חריגה עשויה להחזיר 429.",
                "20 הודעות בדקה הן מגבלת קבוצה; אין להחיל מספר זה אוטומטית על כל ערוץ.",
                "קצב שידור חינמי כולל: בערך 30 הודעות בשנייה. שידורים בתשלום הם מסלול נפרד ואינם מופעלים כאן.",
                "מגבלות גודל תלויות בסוג הקובץ ובשיטת השליחה. שרת Bot API מקומי עשוי להשתמש במגבלות אחרות.",
                "אלה מגבלות לכל חלון זמן או בקשה, ולא מכסה יומית מצטברת. אין כיבוי אוטומטי המבוסס עליהן כאן.",
            ],
            "limits": [
                {
                    "name": "קצב לצ׳אט בודד",
                    "value": "1",
                    "unit": "הודעה לשנייה",
                    "scope": "הנחיית קצב לצ׳אט",
                    "source": TELEGRAM_FAQ,
                },
                {
                    "name": "שליחה לקבוצה",
                    "value": "20",
                    "unit": "הודעות בדקה",
                    "scope": "לכל קבוצה",
                    "source": TELEGRAM_FAQ,
                },
                {
                    "name": "שידור חינמי כולל",
                    "value": "≈30",
                    "unit": "הודעות בשנייה",
                    "scope": "כל הצ׳אטים בבוט יחד",
                    "source": TELEGRAM_FAQ,
                },
                {
                    "name": "אורך הודעת טקסט",
                    "value": "4,096",
                    "unit": "תווים",
                    "scope": "לאחר פענוח entities",
                    "source": "https://core.telegram.org/bots/api#sendmessage",
                },
                {
                    "name": "העלאת תמונה",
                    "value": "10",
                    "unit": "MB",
                    "scope": "multipart/form-data · תמונה חדשה",
                    "source": TELEGRAM_API,
                },
                {
                    "name": "העלאת קובץ אחר",
                    "value": "50",
                    "unit": "MB",
                    "scope": "multipart/form-data · קובץ חדש",
                    "source": TELEGRAM_API,
                },
                {
                    "name": "שליחה באמצעות URL",
                    "value": "5 / 20",
                    "unit": "MB",
                    "scope": "תמונה / קובץ אחר",
                    "source": TELEGRAM_API,
                },
                {
                    "name": "הורדת קובץ לבוט",
                    "value": "20",
                    "unit": "MB",
                    "scope": "getFile בשרת הרגיל",
                    "source": "https://core.telegram.org/bots/api#getfile",
                },
            ],
            "source": TELEGRAM_FAQ,
        }
    if domain == "tuya":
        sharing = "user_code" in data or "terminal_id" in data
        return {
            "title": "Tuya",
            "headline": "מגבלות לפי API",
            "summary": "Sharing SDK: מגבלת החשבון הפעילה אינה מאומתת."
            if sharing
            else "Cloud OpenAPI: כל endpoint מוגבל בנפרד לכל פרויקט.",
            "scope": "Tuya Sharing SDK — מגבלות Cloud OpenAPI הן מידע ייחוס בלבד"
            if sharing
            else "Cloud OpenAPI — לכל endpoint ולכל פרויקט",
            "notes": [
                "אין מספר QPS יחיד לכל Tuya ואין מכסה יומית שניתן להסיק מתקרת בקשות בשנייה.",
                "המספרים להלן הם מגבלות Cloud OpenAPI מהתיעוד המקושר, ולא מדידת שימוש בחשבון.",
                "האינטגרציה הרשמית המותקנת משתמשת ב־Sharing SDK; אין להחיל עליה את מספרי Cloud OpenAPI כהגנת חשבון."
                if sharing
                else "יש להתאים את המגבלה ל־endpoint שבאמת נקרא ולפרויקט הרלוונטי.",
            ],
            "limits": [
                {
                    "name": "שאילתת מאפייני מכשיר",
                    "value": "50",
                    "unit": "בקשות בשנייה",
                    "scope": "GET /v2.0/cloud/thing/{device_id}/shadow/properties",
                    "source": TUYA_DOC,
                },
                {
                    "name": "שאילתת מצב מכשיר",
                    "value": "5",
                    "unit": "בקשות בשנייה",
                    "scope": "GET /v2.0/cloud/thing/{device_id}/state",
                    "source": TUYA_DOC,
                },
                {
                    "name": "הפעלת פעולה",
                    "value": "4",
                    "unit": "בקשות בשנייה",
                    "scope": "POST /v2.0/cloud/thing/{device_id}/shadow/actions",
                    "source": TUYA_DOC,
                },
            ],
            "source": TUYA_DOC,
        }
    if domain == "xiaomi_miot":
        mode = options.get("conn_mode", data.get("conn_mode"))
        labels = {
            "auto": "אוטומטי — LAN כאשר נתמך, וענן לפי הצורך",
            "local": "חיבור מקומי",
            "cloud": "חיבור ענן",
        }
        return {
            "title": "Xiaomi MIoT",
            "headline": {
                "auto": "Auto · מקומי וענן",
                "local": "Local · חיבור מקומי",
                "cloud": "Cloud · חיבור ענן",
            }.get(mode, "הגדרות MIoT"),
            "summary": "לא נמצאה תקרת בקשות מספרית מאומתת. תדירות התשאול אינה מכסה.",
            "scope": labels.get(mode, "מצב החיבור לא נקבע ברמת הרשומה"),
            "connection_mode": mode,
            "notes": [
                "Auto אינו Cloud בלבד: האינטגרציה מעדיפה LAN במכשירים נתמכים ומשתמשת בענן לפי הצורך.",
                "Local אינו מבטיח שכל תקשורת החשבון מקומית; חלק מהתכונות עשויות להשתמש בענן.",
                "לא קובעים תקרה יומית או אחוז ניצול לפי מספר המכשירים או לפי polling של 30/60 שניות.",
                "לא אותר במקורות שאומתו מספר רשמי אחיד למגבלת החשבון. שגיאת 429 כשלעצמה אינה מגלה את התקרה.",
            ],
            "limits": [],
            "source": XIAOMI_DOC,
        }
    return None
