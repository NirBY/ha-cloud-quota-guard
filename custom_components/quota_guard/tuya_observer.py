"""Passive, instance-scoped Tuya HTTP request counts; never account-wide usage."""

from datetime import UTC, datetime
from threading import Lock


class RequestCounter:
    """Count attempts without inspecting or retaining URLs, headers or bodies."""

    def __init__(self, session, previous=None):
        self.session = session
        self.original = session.request
        self.lock = Lock()
        now = datetime.now(UTC)
        self.started_at = now.isoformat()
        self.period = now.date().isoformat() + " UTC"
        previous = previous or {}
        self.used = (
            int(previous.get("used", 0)) if previous.get("period") == self.period else 0
        )
        self.errors = 0
        self.rate_limited = 0
        self.wrapper = self.request
        session.request = self.wrapper

    def rollover(self, now):
        period = now.date().isoformat() + " UTC"
        if period != self.period:
            self.period, self.used, self.errors, self.rate_limited = period, 0, 0, 0

    def request(self, *args, **kwargs):
        with self.lock:
            self.rollover(datetime.now(UTC))
            self.used += 1
        try:
            response = self.original(*args, **kwargs)
        except Exception:
            with self.lock:
                self.errors += 1
            raise
        with self.lock:
            status = getattr(response, "status_code", 0)
            self.errors += int(status >= 400)
            self.rate_limited += int(status == 429)
        return response

    def snapshot(self):
        now = datetime.now(UTC)
        with self.lock:
            self.rollover(now)
            return {
                "sample": {
                    "used": self.used,
                    "period": self.period,
                    "timestamp": now.isoformat(),
                },
                "observation": {
                    "started_at": self.started_at,
                    "errors": self.errors,
                    "rate_limited": self.rate_limited,
                    "scope": "local_http_attempts",
                },
            }

    def close(self):
        if self.session.request is self.wrapper:
            self.session.request = self.original


class TuyaObserver:
    """Attach only to loaded official Tuya SDK sessions and restore on unload."""

    def __init__(self):
        self.counters = {}

    def collect(self, entries, metrics):
        active = set()
        rows = []
        for entry in entries:
            if entry.domain != "tuya" or entry.state.value != "loaded":
                continue
            runtime = getattr(entry, "runtime_data", None)
            api = getattr(getattr(runtime, "manager", None), "customer_api", None)
            session = getattr(api, "session", None)
            if session is None or not callable(getattr(session, "request", None)):
                continue
            id = "tuya_observed_" + entry.entry_id.lower()
            active.add(id)
            counter = self.counters.get(id)
            if counter is None or counter.session is not session:
                if counter:
                    counter.close()
                counter = self.counters[id] = RequestCounter(
                    session, metrics.get(id, {}).get("sample")
                )
            rows.append(
                {
                    "config": {
                        "id": id,
                        "name": "Tuya — observed requests today",
                        "builtin_name": True,
                        "entry_ids": [entry.entry_id],
                        "kind": "rate_limit",
                        "source": "Local Tuya Sharing SDK HTTP observer",
                        "unit": "requests",
                        "max_age": 180,
                        "enforce": False,
                    },
                    "provider_note": "Counts only HTTP attempts observed in this Home Assistant instance, including failed attempts and token refreshes. Excludes MQTT messages, startup before attachment, monitoring gaps and other apps. Not provider billing or total account usage. Daily reset: UTC.",
                    **counter.snapshot(),
                }
            )
        for id in set(self.counters) - active:
            self.counters.pop(id).close()
        return rows

    def close(self):
        for counter in self.counters.values():
            counter.close()
        self.counters.clear()
