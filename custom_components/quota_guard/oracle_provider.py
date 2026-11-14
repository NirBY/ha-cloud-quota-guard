"""OCI usage and explicitly selected compute power-off; own API keys only."""

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta


def credentials(value):
    """Validate only supplied credentials; never read OCI profiles or key files."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    clean = {
        key: str(value[key]).strip()
        for key in ("tenancy", "user", "fingerprint", "region", "private_key")
    }
    for field in ("tenancy", "user"):
        if not re.fullmatch(
            r"ocid1\." + field + r"\.oc1\.\.[a-zA-Z0-9]+", clean[field]
        ):
            raise ValueError("invalid_oracle_credentials")
    if not re.fullmatch(r"[a-z]{2,3}-[a-z]+-\d+", clean["region"]):
        raise ValueError("invalid_oracle_credentials")
    # A password input strips literal newlines when pasting PEM. Reconstruct
    # the PEM envelope, accepting both multiline and escaped-newline content.
    pem = clean.pop("private_key").replace("\\n", "\n")
    match = re.fullmatch(
        r"\s*-----BEGIN (PRIVATE KEY|RSA PRIVATE KEY|ENCRYPTED PRIVATE KEY)-----\s*([A-Za-z0-9+/=\s]+)\s*-----END \1-----\s*(?:OCI_API_KEY\s*)?",
        pem,
    )
    if not match:
        raise ValueError("invalid_oracle_credentials")
    body = "".join(match[2].split())
    pem = (
        "-----BEGIN "
        + match[1]
        + "-----\n"
        + "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
        + "\n-----END "
        + match[1]
        + "-----\n"
    )
    phrase = value.get("pass_phrase") or None
    key = serialization.load_pem_private_key(
        pem.encode(), password=phrase.encode() if phrase else None
    )
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("invalid_oracle_credentials")
    public = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    fingerprint = hashlib.md5(public, usedforsecurity=False).hexdigest()
    if clean["fingerprint"].lower().replace(":", "") != fingerprint:
        raise ValueError("invalid_oracle_credentials")
    clean["fingerprint"] = ":".join(fingerprint[i : i + 2] for i in range(0, 32, 2))
    clean["key_content"] = pem
    if phrase:
        clean["pass_phrase"] = phrase
    return clean


def month_bounds(now):
    start = now.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def aggregate(rows, now):
    """Separate SKUs/units; reject forecasts, non-finite values and other months."""
    start, end = month_bounds(now)
    meters = {}
    for row in rows:
        if row.is_forecast:
            raise ValueError("Forecast is not observed usage")
        if row.time_usage_started != start or row.time_usage_ended != end:
            raise ValueError("Unexpected usage period")
        fields = {
            key: getattr(row, key)
            for key in ("sku_part_number", "sku_name", "service", "unit")
        }
        if any(not isinstance(v, str) or not v.strip() for v in fields.values()):
            raise ValueError("Missing SKU/unit")
        quantity = row.computed_quantity
        if (
            isinstance(quantity, bool)
            or quantity is None
            or not math.isfinite(float(quantity))
            or float(quantity) < 0
        ):
            raise ValueError("Invalid usage")
        # SKU and unit are the stable meter identity; product labels may change.
        id = hashlib.sha256(
            json.dumps(
                [fields["service"], fields["sku_part_number"], fields["unit"]]
            ).encode()
        ).hexdigest()[:12]
        meter = meters.setdefault(id, {"id": id, **fields, "used": 0.0})
        meter["used"] += float(quantity)
        if not math.isfinite(meter["used"]):
            raise ValueError("Invalid aggregate")
    return meters


def _client(kind, config):
    import oci

    # Explicit endpoints and signer input prevent alternate auth hosts, local
    # profiles, instance principals or environment credentials being inherited.
    region = config["region"]
    if (
        region not in oci.regions.REGIONS
        or oci.regions.REGION_REALMS.get(region) != "oc1"
    ):
        raise ValueError("Unsupported commercial OCI region")
    options = dict(timeout=(5, 20), retry_strategy=oci.retry.NoneRetryStrategy())
    if kind == "usage":
        return oci.usage_api.UsageapiClient(
            config,
            service_endpoint=f"https://usageapi.{region}.oci.oraclecloud.com",
            **options,
        )
    return oci.core.ComputeClient(
        config, service_endpoint=f"https://iaas.{region}.oraclecloud.com", **options
    )


def discover(config, now=None):
    """Read reported month-to-date usage, including all pages, with read access."""
    import oci

    now = now or datetime.now(UTC)
    start, end = month_bounds(now)
    client = _client("usage", config)
    try:
        query = oci.usage_api.models.RequestSummarizedUsagesDetails(
            tenant_id=config["tenancy"],
            time_usage_started=start,
            time_usage_ended=end,
            granularity="MONTHLY",
            query_type="USAGE",
            is_aggregate_by_time=False,
            group_by=["service", "skuPartNumber", "skuName", "unit"],
        )
        rows, page, seen = [], None, set()
        while True:
            response = client.request_summarized_usages(
                query, **({"page": page} if page else {})
            )
            rows.extend(response.data.items)
            page = response.headers.get("opc-next-page")
            if not page:
                return aggregate(rows, now)
            if page in seen or len(seen) >= 100:
                raise ValueError("Invalid pagination")
            seen.add(page)
    finally:
        client.base_client.session.close()


def validate_targets(config, ids):
    """Resolve only explicit instance OCIDs in this connection's region."""
    if not ids or len(ids) > 20 or len(set(ids)) != len(ids):
        raise ValueError("Select 1–20 unique instances")
    if any(
        not re.fullmatch(r"ocid1\.instance\.oc1\.[a-zA-Z0-9-]+\.[a-zA-Z0-9]+", id)
        for id in ids
    ):
        raise ValueError("Invalid instance OCID")
    client = _client("compute", config)
    try:
        targets = []
        for id in ids:
            item = client.get_instance(id).data
            if item.id != id or item.lifecycle_state not in (
                "RUNNING",
                "STOPPED",
                "STOPPING",
            ):
                raise ValueError("Instance cannot be selected")
            targets.append(
                {"id": id, "name": item.display_name, "region": config["region"]}
            )
        return targets
    finally:
        client.base_client.session.close()


def stop_instances(config, ids, previous=None):
    """Only SOFTSTOP explicit running targets; never start or terminate anything."""
    client = _client("compute", config)
    result = {}
    try:
        for id in ids:
            try:
                response = client.get_instance(id)
                state = response.data.lifecycle_state
                if response.data.id != id:
                    raise ValueError("Unexpected instance")
                if (
                    state == "RUNNING"
                    and (previous or {}).get(id) == "power_off_requested"
                ):
                    result[id] = "power_off_requested"
                elif state == "RUNNING":
                    # ETag protects against a target changing between read/action.
                    etag = response.headers.get("etag")
                    if not etag:
                        raise ValueError("Missing concurrency guard")
                    client.instance_action(id, "SOFTSTOP", if_match=etag)
                    result[id] = "power_off_requested"
                elif state in ("STOPPED", "STOPPING"):
                    result[id] = state.lower()
                else:
                    result[id] = "state_not_eligible"
            except Exception:
                result[id] = "power_off_failed"
        return result
    finally:
        client.base_client.session.close()


class OracleProvider:
    def __init__(self):
        self.checked = {}

    def fetch(self, account, now=None):
        now = now or datetime.now(UTC)
        id = account["id"]
        last = self.checked.get(id)
        if (
            last
            and last[0] == now.strftime("%Y-%m")
            and (now - last[1]).total_seconds() < 900
        ):
            return None
        self.checked[id] = (now.strftime("%Y-%m"), now)
        try:
            meters = discover(account["credentials"], now)
            error, status = None, None
        except Exception as exc:
            meters = {}
            error = "Oracle usage query failed; check this connection and IAM access"
            status = getattr(exc, "status", None)
        results = []
        for selected in account["meters"]:
            meter = meters.get(selected["id"])
            row = {
                "config": {
                    "id": "qg_oracle_" + id + "_" + selected["id"],
                    "name": "Oracle Free Tier — " + selected["sku_name"],
                    "provider_id": id,
                    "provider_kind": "oracle",
                    "entry_ids": [],
                    "kind": "free_allowance",
                    "unit": selected["unit"],
                    "source": "Oracle Cloud Usage API — reported monthly usage",
                    "max_age": 1800,
                    "enforce": False,
                },
                "provider_kind": "oracle",
                "provider_note": "Oracle usage reports are delayed. Configure your verified allowance for this SKU and unit. Power-off does not stop storage or other resource charges.",
                "provider_error": error,
                "provider_http_status": status,
                "provider_error_code": {
                    401: "authentication_failed",
                    403: "permission_denied",
                    429: "rate_limited",
                }.get(status, "provider_unavailable")
                if error
                else None,
                "reporting": None,
            }
            if not error and meter is not None:
                row["sample"] = {
                    "used": meter["used"],
                    "period": now.strftime("%Y-%m"),
                    "timestamp": now.isoformat(),
                }
            elif not error:
                row["reporting"] = {
                    "state": "no_activity_reported",
                    "checked_at": now.isoformat(),
                    "period": now.strftime("%Y-%m"),
                    "basis": "No reported usage for the selected SKU in this month; not assumed zero",
                }
            results.append(row)
        return results
