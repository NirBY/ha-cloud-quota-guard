"""Observe supported, integration-owned HTTP sessions without reading credentials.

Only count the aiohttp headers-sent signal: redirects and transport retries are
separate requests, while a cached result or a failed DNS lookup is not a request
sent to the provider. No URLs, headers, payloads or responses are retained.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

from aiohttp import ClientSession, TraceConfig

SUPPORTED = {
    "mashov": "Mashov",
    "life360": "Life360",
    "xiaomi_cloud_map_extractor": "Xiaomi Cloud Map Extractor",
    "meitav_tariff_sync": "Meitav Tariff Sync",
}


def sessions_for(hass, entry):
    """Explicit paths only; never walk arbitrary objects or config-entry data."""
    runtime = getattr(entry, "runtime_data", None)
    # Opt-in interoperability: integration authors expose ONLY owned sessions.
    advertised = getattr(runtime, "quota_guard_sessions", None)
    if isinstance(advertised, (tuple, list)):
        return advertised
    if entry.domain == "mashov":
        client = hass.data.get("mashov", {}).get(entry.entry_id, {}).get("client")
        return (getattr(client, "_session", None),)
    if entry.domain == "life360":
        coordinator = getattr(runtime, "coordinator", None)
        accounts = getattr(coordinator, "_acct_data", {})
        return tuple(getattr(account, "session", None) for account in accounts.values())
    if entry.domain == "xiaomi_cloud_map_extractor":
        coordinator = getattr(runtime, "coordinator", None)
        connector = getattr(coordinator, "connector", None)
        cloud = getattr(connector, "_cloud_connector", None)
        data = getattr(cloud, "_session_data", None)
        return (getattr(data, "session", None),)
    return ()


def metric_id(entry_id):
    return "qg_requests_" + sha256(entry_id.encode()).hexdigest()[:24]


class RequestCounters:
    """Event-loop-only counters, persisted with the normal Quota Guard store."""

    def __init__(self, hass, data, now=None):
        self.hass = hass
        self.data = data
        self.now = now or (lambda: datetime.now(UTC))
        self.attached = {}
        self.states = {}
        self.closed = False

    def _bucket(self, entry_id):
        now = self.now()
        period = now.strftime("%Y-%m-%d")
        counters = self.data.setdefault("request_counters", {})
        record = counters.setdefault(entry_id, {})
        if record.get("period") != period:
            record.clear()
            record.update(period=period, used=0, first_observed=now.isoformat())
        return record

    def _attach(self, session, entry_id):
        trace = TraceConfig()
        active = {"enabled": True}

        async def sent(_session, _context, _parameters):
            # Never inspect parameters (they can contain private URLs/headers).
            if active["enabled"] and not self.closed:
                record = self._bucket(entry_id)
                record["used"] += 1
                record["last_request_at"] = self.now().isoformat()

        trace.on_request_headers_sent.append(sent)
        trace.freeze()
        # aiohttp exposes this list via ClientSession.trace_configs. Appending a
        # frozen observer affects future requests only; request methods, sessions,
        # existing observers and authentication remain untouched.
        session.trace_configs.append(trace)
        self.attached[session] = (entry_id, trace, active)
        record = self._bucket(entry_id)
        record["last_attached_at"] = self.now().isoformat()

    def _detach(self, session):
        _entry_id, trace, active = self.attached.pop(session)
        active["enabled"] = False  # also disables callbacks of in-flight requests
        if trace in session.trace_configs:
            session.trace_configs.remove(trace)

    def refresh(self, entries):
        """First install and later additions share the same idempotent discovery."""
        if self.closed:
            return
        enabled = self.data.get("automatic_request_counters", True)
        desired, states = {}, {}
        for entry in entries:
            if entry.domain == "quota_guard":
                continue
            advertised = isinstance(
                getattr(
                    getattr(entry, "runtime_data", None), "quota_guard_sessions", None
                ),
                (tuple, list),
            )
            if entry.domain not in SUPPORTED and not advertised:
                continue
            state = "waiting_for_client"
            if not enabled:
                state = "off"
            elif entry.disabled_by or entry.state.value != "loaded":
                state = "integration_not_loaded"
            else:
                try:
                    sessions = sessions_for(self.hass, entry)
                    sessions = {
                        session
                        for session in sessions
                        if isinstance(session, ClientSession) and not session.closed
                    }
                except (AttributeError, TypeError):
                    sessions = set()
                if sessions:
                    state = "observing"
                    for session in sessions:
                        # Never attribute one shared session to two integrations.
                        if session in desired and desired[session] != entry.entry_id:
                            if desired[session] is not None:
                                states[desired[session]]["state"] = (
                                    "shared_client_unsupported"
                                )
                            desired[session] = None
                            state = "shared_client_unsupported"
                        elif session not in desired:
                            desired[session] = entry.entry_id
            states[entry.entry_id] = {
                "domain": entry.domain,
                "name": SUPPORTED.get(entry.domain, entry.domain),
                "state": state,
            }
        for session, (entry_id, _trace, _active) in list(self.attached.items()):
            if (
                desired.get(session) != entry_id
                or states[entry_id]["state"] != "observing"
            ):
                self._detach(session)
        for session, entry_id in desired.items():
            if (
                entry_id
                and states[entry_id]["state"] == "observing"
                and session not in self.attached
            ):
                self._attach(session, entry_id)
        self.states = states

    def rows(self):
        now = self.now()
        expiry = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        records = self.data.get("request_counters", {})
        for entry_id, state in self.states.items():
            if (
                state["state"] not in ("observing", "waiting_for_client")
                and entry_id not in records
            ):
                continue  # An unsupported client is never fabricated as zero usage.
            row = {
                "config": {
                    "id": metric_id(entry_id),
                    "name": state["name"] + " — HTTP requests today",
                    "entry_ids": [entry_id],
                    "source": "Observed Home Assistant HTTP requests",
                    "kind": "budget",
                    "unit": "requests",
                    "max_age": 180,
                    "enforce": False,
                    "automatic_counter": True,
                    "period_timezone": "UTC",
                },
                "counter": {
                    **state,
                    "scope": "This Home Assistant instance only",
                    "partial_period": True,
                },
            }
            if state["state"] == "observing":
                record = self._bucket(entry_id)
                row["sample"] = {
                    "used": record["used"],
                    "period": record["period"],
                    "timestamp": now.isoformat(),
                    "expires_at": expiry.isoformat(),
                }
                row["counter"].update(record)
            yield row

    def close(self):
        self.closed = True
        for session in list(self.attached):
            self._detach(session)
