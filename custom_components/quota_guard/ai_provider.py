"""Read-only AI account reporting using credentials supplied to Quota Guard."""

import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

AI_SERVICES = {
    "openai": ("OpenAI", "https://platform.openai.com/usage"),
    "google_ai": ("Google AI (Gemini)", "https://aistudio.google.com/usage"),
    "claude": ("Claude (Anthropic)", "https://platform.claude.com/usage"),
    "cursor": ("Cursor", "https://cursor.com/dashboard"),
    "deepseek": ("DeepSeek", "https://platform.deepseek.com/usage"),
    "grok": ("Grok (xAI)", "https://console.x.ai"),
    "meta_ai": ("Meta AI", "https://www.meta.ai"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/activity"),
    "mistral": ("Mistral", "https://admin.mistral.ai/usage"),
}
AUTOMATIC = set(AI_SERVICES) - {"meta_ai"}


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing numeric measurement")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or not math.isfinite(float(result)):
        raise ValueError("Invalid numeric measurement")
    return result


def request(session, method, url, **kwargs):
    # Only fixed provider endpoints call this helper. Never follow redirects with keys.
    response = session.request(method, url, timeout=25, allow_redirects=False, **kwargs)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError("Unexpected provider status")
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Unexpected provider response")
    return body


def pages(session, url, headers, params):
    seen = set()
    for _ in range(100):
        body = request(session, "GET", url, headers=headers, params=params)
        if (
            not isinstance(body.get("data"), list)
            or type(body.get("has_more")) is not bool
        ):
            raise ValueError("Invalid report page")
        yield body
        if not body["has_more"]:
            return
        token = body.get("next_page")
        if not isinstance(token, str) or not token or token in seen:
            raise ValueError("Incomplete report")
        seen.add(token)
        params = {**params, "page": token}
    raise ValueError("Report page limit reached")


def month_end(now):
    return (
        (now.replace(day=1) + timedelta(days=32))
        .replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )


def account_report(provider, session, now):
    kind = provider["type"]
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period = start.strftime("%Y-%m UTC")
    total, count = Decimal(0), 0
    timestamp = now.isoformat()
    key = provider["api_key"]
    if kind in ("openai", "claude"):
        if kind == "openai":
            url = "https://api.openai.com/v1/organization/costs"
            headers = {"Authorization": "Bearer " + key}
            params = {
                "start_time": int(start.timestamp()),
                "end_time": int(now.timestamp()),
                "limit": 31,
            }
        else:
            url = "https://api.anthropic.com/v1/organizations/analytics/cost"
            headers = {
                "x-api-key": key,
                "anthropic-version": "2026-10-21",
                "anthropic-beta": "analytics-usage-cost-2026-08-24",
            }
            params = {
                "starting_at": start.isoformat(),
                "ending_at": now.isoformat(),
                "limit": 1000,
            }
        refreshed, first_page = None, True
        for body in pages(session, url, headers, params):
            if kind == "claude":
                current = body.get("data_refreshed_at")
                if not first_page and current != refreshed:
                    raise ValueError("Report snapshot changed")
                refreshed = current
                first_page = False
                if current is not None:
                    stamp = datetime.fromisoformat(current.replace("Z", "+00:00"))
                    if stamp.tzinfo is None or stamp > now:
                        raise ValueError("Invalid report timestamp")
                    timestamp = stamp.isoformat()
                elif body["data"]:
                    raise ValueError("Missing report timestamp")
            for row in body["data"]:
                if kind == "openai":
                    if not start.timestamp() <= row["start_time"] <= now.timestamp():
                        raise ValueError("Report outside requested month")
                    values = [item["amount"] for item in row["results"]]
                else:
                    values = [row["cost"]]
                for amount in values:
                    if str(amount["currency"]).upper() != "USD":
                        raise ValueError("Unsupported report currency")
                    total += number(
                        amount["value"] if kind == "openai" else amount["amount"]
                    ) / (1 if kind == "openai" else 100)
                    count += 1
    elif kind == "cursor":
        cycle, total_pages, members = None, None, set()
        for page in range(1, 101):
            body = request(
                session,
                "POST",
                "https://api.cursor.com/teams/spend",
                auth=(key, ""),
                json={"page": page, "pageSize": 100},
            )
            rows = body["teamMemberSpend"]
            current_cycle = body["subscriptionCycleStart"]
            if not isinstance(rows, list) or (
                cycle is not None and cycle != current_cycle
            ):
                raise ValueError("Billing cycle changed")
            cycle = current_cycle
            if not 0 < float(cycle) <= now.timestamp() * 1000:
                raise ValueError("Invalid billing cycle")
            if total_pages is not None and total_pages != body["totalPages"]:
                raise ValueError("Report changed during pagination")
            total_pages = body["totalPages"]
            if type(total_pages) is not int or not 1 <= total_pages <= 100:
                raise ValueError("Invalid page count")
            for row in rows:
                if row["userId"] in members:
                    raise ValueError("Duplicate member in report")
                members.add(row["userId"])
                total += number(row["spendCents"]) / 100
                count += 1
            if page == total_pages:
                if len(members) != body["totalMembers"]:
                    raise ValueError("Incomplete team report")
                break
        period = (
            "Cursor billing cycle "
            + datetime.fromtimestamp(float(cycle) / 1000, UTC).isoformat()
        )
    elif kind == "grok":
        team = provider["team_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", team):
            raise ValueError("Invalid team ID")
        body = request(
            session,
            "POST",
            f"https://management-api.x.ai/v1/billing/teams/{team}/usage",
            headers={"Authorization": "Bearer " + key},
            json={
                "analyticsRequest": {
                    "timeRange": {
                        "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
                        "endTime": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "timezone": "Etc/GMT",
                    },
                    "timeUnit": "TIME_UNIT_DAY",
                    "values": [{"name": "usd", "aggregation": "AGGREGATION_SUM"}],
                    "groupBy": [],
                    "filters": [],
                }
            },
        )
        if body.get("limitReached") is not False or not isinstance(
            body.get("timeSeries"), list
        ):
            raise ValueError("Incomplete usage report")
        for series in body["timeSeries"]:
            for point in series["dataPoints"]:
                stamp = datetime.fromisoformat(
                    point["timestamp"].replace("Z", "+00:00")
                )
                if not start <= stamp <= now or len(point["values"]) != 1:
                    raise ValueError("Invalid usage bucket")
                total += number(point["values"][0])
                count += 1
    elif kind == "openrouter":
        body = request(
            session,
            "GET",
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": "Bearer " + key},
        )
        total = number(body["data"]["usage_monthly"])
        count = 1
    elif kind == "mistral":
        body = request(
            session,
            "POST",
            "https://api.mistral.ai/v1/admin/usage/query",
            headers={"x-api-key": key},
            json={
                "start": start.isoformat(),
                "end": now.isoformat(),
                "include_aggregates": True,
                "include_nb_api_calls": True,
                "limit": 1,
            },
        )
        # Aggregates cover the full query, independently of grouped-row pagination.
        query = body["metadata"]["query_params"]
        if any(
            datetime.fromisoformat(query[k].replace("Z", "+00:00")) != value
            for k, value in (("start", start), ("end", now))
        ):
            raise ValueError("Unexpected reporting period")
        calls = body["aggregates"]["total_api_calls"]
        if type(calls) is not int:
            raise ValueError("Invalid call count")
        total, count = number(calls), 1
    else:
        raise ValueError("Unsupported cost provider")
    return (
        {
            "used": float(total),
            "period": period,
            "timestamp": timestamp,
            **({"expires_at": month_end(now)} if kind != "cursor" else {}),
        }
        if count
        else None
    )


def gemini_report(provider, now):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    info = provider["service_account_info"]
    cred = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/monitoring.read"]
    )
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    metric = (
        "generativelanguage.googleapis.com/generate_content_usage_output_token_count"
    )
    params = {
        "filter": f'metric.type="{metric}"',
        "interval.startTime": start.isoformat(),
        "interval.endTime": now.isoformat(),
        "view": "FULL",
        "pageSize": 1000,
    }
    total, count, seen = Decimal(0), 0, set()
    with AuthorizedSession(cred) as session:
        for _ in range(100):
            body = request(
                session,
                "GET",
                f"https://monitoring.googleapis.com/v3/projects/{info['project_id']}/timeSeries",
                params=params,
            )
            for series in body.get("timeSeries", []):
                if (
                    series.get("metricKind") != "DELTA"
                    or series.get("metric", {}).get("type") != metric
                ):
                    raise ValueError("Unexpected Gemini metric")
                for point in series["points"]:
                    stamp = datetime.fromisoformat(
                        point["interval"]["endTime"].replace("Z", "+00:00")
                    )
                    if not start < stamp <= now:
                        raise ValueError("Usage outside requested month")
                    total += number(point["value"]["int64Value"])
                    count += 1
            token = body.get("nextPageToken")
            if not token:
                return (
                    {
                        "used": float(total),
                        "period": start.strftime("%Y-%m UTC"),
                        "timestamp": now.isoformat(),
                        "expires_at": month_end(now),
                    }
                    if count
                    else None
                )
            if token in seen:
                raise ValueError("Repeated page")
            seen.add(token)
            params["pageToken"] = token
    raise ValueError("Incomplete Gemini report")


class AIProvider:
    def __init__(self):
        self.checked = {}

    def fetch(self, provider, *, force=False):
        import requests

        now = datetime.now(UTC)
        id, kind = provider["id"], provider["type"]
        if (
            not force
            and id in self.checked
            and (now - self.checked[id]).total_seconds() < 3600
        ):
            return []
        self.checked[id] = now
        balance = kind == "deepseek"
        name, portal = AI_SERVICES[kind]
        suffix = (
            "credit balance"
            if balance
            else "output tokens this month"
            if kind == "google_ai"
            else "on-demand spend this billing cycle"
            if kind == "cursor"
            else "reported key spend this month"
            if kind == "openrouter"
            else "reported API calls this month"
            if kind == "mistral"
            else "reported cost this month"
        )
        config = {
            "id": f"qg_ai_{id}",
            "name": f"{name} — {provider['name']} — {suffix}",
            "provider_id": id,
            "entry_ids": [],
            "kind": "free_allowance"
            if kind in ("google_ai", "mistral") or balance
            else "budget",
            "unit": provider.get("currency", "USD")
            if balance
            else "tokens"
            if kind == "google_ai"
            else "requests"
            if kind == "mistral"
            else "USD",
            "source": f"{name} reporting API",
            "usage_url": portal,
            "max_age": 86400 if kind == "claude" else 7200,
            "enforce": False,
            "warning_percent": provider.get("warning_percent", 90),
            "cutoff_percent": provider.get("cutoff_percent", 95),
        }
        if not balance and provider.get("limit") is not None:
            config["limit"] = provider["limit"]
        if balance:
            config["measurement"] = "balance"
        row = {
            "config": config,
            "provider_kind": kind,
            "provider_error": None,
            "provider_error_code": None,
            "reporting": None,
            "provider_http_status": None,
            "provider_note": "Read-only reported data; delayed and not a provider-enforced spending cap.",
        }
        if kind == "google_ai":
            row["provider_note"] = (
                "Reported Gemini output tokens across this project, UTC calendar month. Does not measure input tokens, cost, or model-specific rate limits."
            )
        if balance:
            row["provider_note"] = (
                "Available API credit balance, not usage or a monthly allowance. Configure low-balance warning and critical values; local cutoff is optional and off by default."
            )
        if kind == "openrouter":
            row["provider_note"] = (
                "Provider-reported monthly USD spend for this API key only, excluding separate BYOK usage. Not whole-account spend. Optional budget is local to Quota Guard."
            )
        if kind == "mistral":
            row["provider_note"] = (
                "Reported distinct API calls across the organization, UTC calendar month. Enterprise Admin API access required. This is a request count, not cost or personal subscription usage."
            )
        try:
            if kind == "google_ai":
                sample = gemini_report(provider, now)
            else:
                with requests.Session() as session:
                    session.trust_env = False
                    if balance:
                        body = request(
                            session,
                            "GET",
                            "https://api.deepseek.com/user/balance",
                            headers={"Authorization": "Bearer " + provider["api_key"]},
                        )
                        values = [
                            b
                            for b in body["balance_infos"]
                            if b["currency"] == config["unit"]
                        ]
                        if len(values) != 1:
                            raise ValueError("Selected currency not returned")
                        sample = {
                            "used": float(number(values[0]["total_balance"])),
                            "period": "Current credit balance",
                            "timestamp": now.isoformat(),
                        }
                    else:
                        sample = account_report(provider, session, now)
            if sample is not None:
                sample["limit"] = config.get("limit")
                row["sample"] = sample
            else:
                row["provider_note"] += (
                    " The query succeeded but no measurement is published yet; usage is unknown."
                )
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            row["provider_http_status"] = status
            row["provider_error_code"] = {
                401: "authentication_failed",
                403: "permission_denied",
                429: "rate_limited",
            }.get(status, "provider_unavailable")
            row["provider_error"] = f"{name} report unavailable ({type(exc).__name__})"
        return [row]
