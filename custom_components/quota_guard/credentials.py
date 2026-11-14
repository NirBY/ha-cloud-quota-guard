"""Validate credentials supplied directly to this integration's own flow."""

import json
import re


def google_credentials(value):
    """Validate key material locally; never accept a custom credential endpoint."""
    from google.oauth2 import service_account

    info = json.loads(value)
    if (
        not isinstance(info, dict)
        or info.get("type") != "service_account"
        or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", info.get("project_id", ""))
        or info.get("token_uri") != "https://oauth2.googleapis.com/token"
    ):
        raise ValueError("Invalid service account")
    # Do not retain arbitrary JSON or alternate universe/auth endpoints.
    clean = {
        key: info[key]
        for key in (
            "type",
            "project_id",
            "private_key_id",
            "private_key",
            "client_email",
            "client_id",
            "token_uri",
        )
    }
    service_account.Credentials.from_service_account_info(clean)
    return clean
