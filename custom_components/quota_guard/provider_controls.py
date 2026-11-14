"""Explicitly selected provider controls; no implicit or automatic re-enabling.

All URLs are fixed provider endpoints. Only credentials stored in Quota Guard
are used. Validation is read-only; mutations run only after saved consent and a
fresh critical measurement. Exceptions are never rendered with response bodies.
"""

import re
from urllib.parse import quote

CONTROLS = {
    "openai": (
        "Permanently revoke selected project API keys",
        "One project_id/key_id per line. Revocation cannot be undone.",
    ),
    "claude": (
        "Deactivate selected API keys",
        "One API key ID per line. Requires an organization Admin key.",
    ),
    "openrouter": (
        "Disable selected API keys",
        "One API key hash per line. Supply a Management key. Select the same key measured by this source.",
    ),
    "grok": (
        "Disable selected team API keys",
        "One API key ID per line, belonging to the team configured for this connection.",
    ),
    "mistral": (
        "Permanently revoke selected API keys",
        "One API key UUID per line. Enterprise Admin access required. Revocation cannot be undone.",
    ),
    "cursor": (
        "Set selected members' spend limits to zero",
        "One team member email per line. Enterprise Admin access required. Included usage may continue; higher group limits can prevent the effective cap.",
    ),
    "google": (
        "Disable this Google project API",
        "Select exactly the service named by this metric. All applications using this project API are affected; dependent services are not disabled.",
    ),
    "google_ai": (
        "Disable this Gemini project API",
        "Enter generativelanguage.googleapis.com. All applications using this project API are affected.",
    ),
}
DONE = {"disabled", "revoked", "zero_spend_limit"}


def targets(value):
    ids = list(dict.fromkeys(x.strip() for x in re.split(r"[,\n]", value) if x.strip()))
    if not 1 <= len(ids) <= 5 or any(
        not re.fullmatch(r"[A-Za-z0-9_@.+/-]{1,200}", x) or ".." in x for x in ids
    ):
        raise ValueError("Enter one to five exact target identifiers")
    return ids


def call(session, method, url, **kwargs):
    response = session.request(method, url, timeout=25, allow_redirects=False, **kwargs)
    response.raise_for_status()
    if not 200 <= response.status_code < 300:
        raise ValueError("Provider did not accept request")
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Unexpected control response")
    return body


def session_for(provider):
    import requests

    kind = provider["type"]
    if kind in ("google", "google_ai"):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        credential = service_account.Credentials.from_service_account_info(
            provider["service_account_info"],
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        session = AuthorizedSession(credential)
    else:
        session = requests.Session()
        key = (
            provider.get("control_api_key")
            if kind == "openrouter"
            else provider["api_key"]
        )
        if not key:
            raise ValueError("A provider management key is required")
        if kind == "cursor":
            session.auth = (key, "")
        elif kind in ("claude", "mistral"):
            session.headers["x-api-key"] = key
            if kind == "claude":
                session.headers["anthropic-version"] = "2026-10-21"
        else:
            session.headers["Authorization"] = "Bearer " + key
    session.trust_env = False
    return session


def key_url(provider, target):
    kind = provider["type"]
    safe = quote(target, safe="")
    if kind == "openai":
        parts = target.split("/")
        if len(parts) != 2 or not all(
            re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts
        ):
            raise ValueError("Use project_id/key_id")
        return f"https://api.openai.com/v1/organization/projects/{parts[0]}/api_keys/{parts[1]}"
    if kind == "claude":
        return "https://api.anthropic.com/v1/organizations/api_keys/" + safe
    if kind == "openrouter":
        return "https://openrouter.ai/api/v1/keys/" + safe
    if kind == "grok":
        return "https://management-api.x.ai/v1/auth/api-keys/" + safe
    if kind == "mistral":
        return "https://api.mistral.ai/v1/admin/api-keys/" + safe
    project = provider["service_account_info"]["project_id"]
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
        raise ValueError("Invalid project identifier")
    return f"https://serviceusage.googleapis.com/v1/projects/{project}/services/{safe}"


def list_mistral(session):
    keys = []
    for offset in range(0, 10000, 100):
        body = call(
            session,
            "GET",
            "https://api.mistral.ai/v1/admin/api-keys",
            params={"limit": 100, "offset": offset},
        )
        batch = body["keys"]
        keys.extend(
            {
                "id": k["key_id"],
                "name": k.get("name", k["key_id"]),
                "can_delete": k.get("can_delete", False),
            }
            for k in batch
        )
        if len(batch) < 100:
            return keys
    raise ValueError("Incomplete key listing")


def cursor_members(session):
    from .ai_provider import request

    members = {}
    for page in range(1, 101):
        body = request(
            session,
            "POST",
            "https://api.cursor.com/teams/spend",
            json={"page": page, "pageSize": 100},
        )
        for member in body["teamMemberSpend"]:
            email = member["email"]
            if email in members:
                raise ValueError("Repeated member")
            members[email] = member.get("effectivePerUserLimitDollars")
        if page >= body["totalPages"]:
            return members
    raise ValueError("Incomplete member listing")


def grok_key(session, provider, target):
    team = provider["team_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", team):
        raise ValueError("Invalid team")
    params, seen = {"pageSize": 100}, set()
    for _ in range(100):
        body = call(
            session,
            "GET",
            f"https://management-api.x.ai/v1/auth/teams/{team}/api-keys",
            params=params,
        )
        match = next((k for k in body["apiKeys"] if k["apiKeyId"] == target), None)
        if match:
            return match
        token = body.get("paginationToken")
        if not token or token in seen:
            break
        seen.add(token)
        params["paginationToken"] = token
    raise ValueError("Selected key not found in this team")


def inspect_target(session, provider, target, service=None):
    kind = provider["type"]
    if kind == "cursor":
        members = cursor_members(session)
        if target not in members:
            raise ValueError("Member is not in this team")
        return {"id": target, "name": target, "stopped": members[target] == 0}
    if kind == "mistral":
        key = next((k for k in list_mistral(session) if k["id"] == target), None)
        if not key or not key["can_delete"]:
            raise ValueError("Selected key cannot be revoked by this account")
        return {"id": target, "name": key["name"], "stopped": False}
    if kind in ("google", "google_ai"):
        if target != service:
            raise ValueError("Select exactly the API measured by this source")
        body = call(session, "GET", key_url(provider, target))
        if body.get("config", {}).get("name") != target:
            raise ValueError("Service identity mismatch")
        return {"id": target, "name": target, "stopped": body["state"] == "DISABLED"}
    body = (
        grok_key(session, provider, target)
        if kind == "grok"
        else call(session, "GET", key_url(provider, target))
    )
    if kind == "openrouter":
        body = body["data"]
        identity, stopped = body.get("hash"), body.get("disabled") is True
    elif kind == "grok":
        if body.get("teamId") != provider["team_id"]:
            raise ValueError("Key belongs to a different team")
        identity, stopped = body.get("apiKeyId"), body.get("disabled") is True
    else:
        identity, stopped = (
            body.get("id"),
            body.get("status") in ("inactive", "archived", "expired"),
        )
    if identity != target.split("/")[-1]:
        raise ValueError("Key identity mismatch")
    return {"id": target, "name": body.get("name") or target, "stopped": stopped}


def validate(provider, ids, service=None):
    if provider["type"] not in CONTROLS:
        raise ValueError("No verified provider control API")
    if targets("\n".join(ids)) != ids:
        raise ValueError("Invalid or repeated targets")
    with session_for(provider) as session:
        return [inspect_target(session, provider, target, service) for target in ids]


def stop(provider, ids, previous, service=None):
    """Validate the whole target set before any mutation; never retry completed keys."""
    result = {k: v for k, v in previous.items() if k in ids and v in DONE}
    pending = [target for target in ids if target not in result]
    if not pending:
        return result
    kind = provider["type"]
    with session_for(provider) as session:
        checked = [
            inspect_target(session, provider, target, service) for target in pending
        ]
        for item in checked:
            target = item["id"]
            if item["stopped"]:
                result[target] = "zero_spend_limit" if kind == "cursor" else "disabled"
                continue
            try:
                url = (
                    key_url(provider, target)
                    if kind != "cursor"
                    else "https://api.cursor.com/teams/user-spend-limit"
                )
                if kind in ("openai", "mistral"):
                    body = call(session, "DELETE", url)
                    if kind == "openai":
                        if (
                            body.get("deleted") is not True
                            or body.get("id") != target.split("/")[-1]
                        ):
                            raise ValueError("Revocation not confirmed")
                    elif any(k["id"] == target for k in list_mistral(session)):
                        raise ValueError("Revocation not confirmed")
                    result[target] = "revoked"
                    continue
                if kind == "claude":
                    call(session, "POST", url, json={"status": "inactive"})
                elif kind == "openrouter":
                    call(session, "PATCH", url, json={"disabled": True})
                elif kind == "grok":
                    call(
                        session,
                        "PUT",
                        url,
                        json={"apiKey": {"disabled": True}, "fieldMask": "disabled"},
                    )
                elif kind == "cursor":
                    call(
                        session,
                        "POST",
                        url,
                        json={"userEmail": target, "spendLimitDollars": 0},
                    )
                else:
                    body = call(
                        session,
                        "POST",
                        url + ":disable",
                        json={
                            "disableDependentServices": False,
                            "checkIfServiceHasUsage": "SKIP",
                        },
                    )
                    if body.get("error"):
                        raise ValueError("Service disable failed")
                if not inspect_target(session, provider, target, service)["stopped"]:
                    raise ValueError("Provider has not confirmed the effective cutoff")
                result[target] = "zero_spend_limit" if kind == "cursor" else "disabled"
            except Exception:
                result[target] = "failed_or_unconfirmed"
    return result
