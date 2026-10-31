"""Google consumer quota limits + Cloud Monitoring DELTA observations.

Reads existing Google Cloud credentials in memory. No invented free-tier limits.
Minute metrics are explicitly the highest observed minute of the provider day,
not live rate: Google publishes observations with delay.
"""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

SERVICES = {
    "google_assistant_sdk": (
        "embeddedassistant.googleapis.com",
        "embedded-assistant-prod/converse_requests",
        "Google Assistant — בקשות היום",
        "day",
    ),
    "google_cloud": (
        "texttospeech.googleapis.com",
        "texttospeech.googleapis.com/requests",
        "Google TTS — שיא בקשות לדקה היום",
        "minute_peak",
    ),
    "google": (
        "calendar-json.googleapis.com",
        "calendar-json.googleapis.com/default",
        "Google Calendar — שיא בקשות לדקה היום",
        "minute_peak",
    ),
}


def aggregate(series, metric, mode):
    """Sum distinct method series by interval, not max of a single method."""
    buckets = {}
    for ts in series:
        if ts.get("metric", {}).get("labels", {}).get("quota_metric") != metric:
            continue
        if ts.get("metricKind") != "DELTA":
            raise ValueError("Expected DELTA counter")
        for point in ts.get("points", []):
            end = point["interval"]["endTime"]
            value = float(
                point["value"].get("int64Value", point["value"].get("doubleValue", 0))
            )
            buckets[end] = buckets.get(end, 0) + value
    if not buckets:
        return None, None
    return (sum(buckets.values()) if mode == "day" else max(buckets.values())), max(
        buckets
    )


class GoogleProvider:
    def __init__(self):
        self.sessions = {}
        self.limits = {}

    def fetch(self, accounts, entries):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        now = datetime.now(UTC)
        midnight = now.astimezone(ZoneInfo("America/Los_Angeles")).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        results = []
        for account in accounts:
            info = account["info"]
            info = json.loads(info) if isinstance(info, str) else info
            project = info["project_id"]
            if account["id"] not in self.sessions:
                cred = service_account.Credentials.from_service_account_info(
                    info,
                    scopes=[
                        "https://www.googleapis.com/auth/cloud-platform.read-only",
                        "https://www.googleapis.com/auth/monitoring.read",
                    ],
                )
                self.sessions[account["id"]] = AuthorizedSession(cred)
            session = self.sessions[account["id"]]
            for domain, (service, metric, name, mode) in SERVICES.items():
                target = [
                    e["entry_id"]
                    for e in entries
                    if e["domain"] == domain
                    and (
                        domain == "google_cloud"
                        and e["entry_id"] == account["id"]
                        or domain != "google_cloud"
                        and e.get("project") == project
                    )
                ]
                if not target:
                    continue
                id = "google_" + account["id"].lower() + "_" + domain
                cfg = {
                    "id": id,
                    "name": name,
                    "entry_ids": target,
                    "kind": "rate_limit",
                    "source": "Google Service Usage + Cloud Monitoring",
                    "unit": "בקשות ביום"
                    if mode == "day"
                    else "בקשות בדקה (שיא יומי מדווח)",
                    "max_age": 600,
                    "enforce": True,
                }
                row = {
                    "config": cfg,
                    "provider_note": "מדידה מהספק בהשהיה; אינה תקרת חיוב או מונה תווים חינמיים",
                    "project": project,
                }
                try:
                    key = (project, service)
                    if (
                        key not in self.limits
                        or (now - self.limits[key][0]).total_seconds() > 3600
                    ):
                        response = session.get(
                            f"https://serviceusage.googleapis.com/v1beta1/projects/{project}/services/{service}/consumerQuotaMetrics",
                            params={"view": "FULL", "pageSize": 100},
                            timeout=15,
                        )
                        response.raise_for_status()
                        self.limits[key] = (now, response.json())
                    quota = self.limits[key][1]
                    unit = "1/d/{project}" if mode == "day" else "1/min/{project}"
                    limits = [
                        float(b["effectiveLimit"])
                        for m in quota.get("metrics", [])
                        if m["metric"] == metric
                        for quota_limit in m["consumerQuotaLimits"]
                        if quota_limit["unit"] == unit
                        for b in quota_limit["quotaBuckets"]
                        if not b.get("dimensions")
                    ]
                    if len(limits) != 1 or limits[0] <= 0:
                        raise ValueError("Ambiguous/unsupported quota")
                    series = []
                    page = None
                    while True:
                        params = {
                            "filter": f'metric.type="serviceruntime.googleapis.com/quota/rate/net_usage" AND resource.labels.service="{service}"',
                            "interval.startTime": midnight.astimezone(UTC).isoformat(),
                            "interval.endTime": now.isoformat(),
                            "pageSize": 10000,
                            "view": "FULL",
                        }
                        if page:
                            params["pageToken"] = page
                        response = session.get(
                            f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries",
                            params=params,
                            timeout=15,
                        )
                        response.raise_for_status()
                        body = response.json()
                        series.extend(body.get("timeSeries", []))
                        page = body.get("nextPageToken")
                        if not page:
                            break
                    used, last = aggregate(series, metric, mode)
                    row["verified_limit"] = limits[0]
                    row["last_usage_point"] = last
                    row["provider_error"] = (
                        None
                        if used is not None
                        else "Google has not reported any usage samples for this day; not assumed zero"
                    )
                    if used is not None:
                        row["sample"] = {
                            "used": used,
                            "limit": limits[0],
                            "period": midnight.date().isoformat()
                            + " America/Los_Angeles",
                            "timestamp": now.isoformat(),
                        }
                except Exception as exc:
                    row["provider_error"] = (
                        f"Google quota read failed ({type(exc).__name__})"
                    )
                results.append(row)
        return results
