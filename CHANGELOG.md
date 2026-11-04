# Changelog

## 0.5.2

- Show IMOU monthly call exhaustion in Needs attention, with one closing notice, the expected reset date and a visible pause on further alerts.


## 0.5.1

- Fix upgrades keeping an already registered older dashboard implementation.
- Use content-addressed card URLs and reconcile frontend registration on reload.
- Detect newer backend assets from open cards and show card/monitor versions.
- Build and verify complete release archives; test component replacement preserves quota history and dashboard configuration.


## 0.5.0

- Display measured usage and raw-unit history even when the limit is unknown.
- Passively count official Tuya Sharing SDK HTTP attempts in this HA instance.
- Link mapped cutoff integrations and directly related automations.
- Accept generic usage-only reports and sensors; never derive percentages or cutoffs without a verified limit.

- Clarify that unavailable quota data does not indicate a disconnected integration.
- Explain Google Translate TTS separately from metered Google Cloud TTS.


## 0.4.0

- English-default dashboard, provider descriptions and notifications.
- Hebrew translations, RTL layout, localized dates and numbers.
- Automatic Home Assistant language selection and explicit card language override.
- Reusable dashboard YAML template and stable alert deduplication across language changes.


## 0.3.0 — 2026-10-31

- Distinguish a successful empty Google usage query from a monitoring outage.
  Show the numeric count of reported requests (zero) with its reporting period
  and check time, without treating it as a verified zero-use quota sample.
- Stop false missing-data notifications for fresh, successful empty reports.
  Never enforce cutoff or add a zero history point from an empty response.
- Persist the missing-data notification latch. Unchanged alerts are not recreated
  every minute or after restart, preventing repeated forwarded Telegram messages.
- Add source-linked reference cards for Telegram Bot, Tuya and Xiaomi MIoT.
  Keep per-endpoint, per-chat, per-group and connection-mode scope explicit.
- Identify Tuya Sharing SDK separately from Developer Cloud OpenAPI quotas;
  distinguish Xiaomi Auto, Local and Cloud connection modes.
- Correct Telegram photo/file limits and document standard-server exceptions.
- Reject missing or invalid numeric provider points rather than converting them
  into zero; scope Google quota queries to the consumer quota resource.

## 0.2.0 — 2026-10-31

First public HACS release of Cloud Quota Guard.

- Bundled dashboard with clickable service tiles, usage history, search and filters.
- Separate unknown limits, missing measurements, stale readings and fresh data.
- GitHub REST core and supported Google project quota adapters.
- Administrator-only sensor configuration and measurement reporting actions.
- Persistent warnings at 90% and mapped integration cutoff at 95%.
- Source timestamp validation, reset-aware history, and recovery notification cleanup.
- Local Home Assistant brand icons, English/Hebrew setup and action descriptions.

The integration domain remains `quota_guard` for compatibility with existing installations.
