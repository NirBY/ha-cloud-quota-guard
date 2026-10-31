"""Shared notification translations; English is the fallback language."""

import json
from pathlib import Path

HE_TEXT = json.loads(
    (Path(__file__).parent / "languages/he.json").read_text(encoding="utf-8")
)


def translate(message, language="en", **values):
    """Translate owned messages without modifying user-defined labels."""
    if str(language).lower().split("-")[0] in ("he", "iw"):
        message = HE_TEXT.get(message, message)
    return message.format(**values) if values else message
