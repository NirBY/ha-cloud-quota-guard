# Changelog

## 0.8.1 — 2026-11-14

- Show saved GitHub/Google connections as service devices with a connection-status
  entity, including a pending state before the first poll.
- Add a named usage entity for each active source, including manual sensor sources.
  Keep stale, missing and delayed measurements unavailable instead of reporting zero.
- Discover sources dynamically and remove only Quota Guard's obsolete entities
  when a provider is removed; retain archived quota history.
- Open the README setup guide from Home Assistant's help button. Explain credential
  creation, exact form fields, Google read permissions and post-save verification.
- Add English/Hebrew field help and direct provider-specific README links.

## 0.8.0 — 2026-11-14

- Show actionable diagnostics for missing sensors/attributes, invalid usage or
  limits, missing or timezone-less timestamps, stale/future measurements, wrong
  reporting months, invalid cutoff targets and missing explicit cutoff consent.
- Show retired GitHub/Google/Tuya sources with reconnection instructions and
  retained history, instead of silently removing them from the dashboard.
- Add an administrator-only acknowledgment for retired sources; it cannot hide
  problems in active sources or delete history.
- Separate provider authentication, permission and rate-limit errors from general
  data unavailability. Surface integration setup failures independently of quota coverage.
- Add English/Hebrew explanations and direct settings links. Block cutoff while
  an active metric has a configuration error.
- Use a stable, versioned bootstrap URL so a cached frontend URL continues to
  resolve after an upgrade/restart. Keep fingerprinted URLs for live card upgrades.

## 0.7.0 — 2026-11-14

- Resolve the HACS review: no access to other integrations' GitHub tokens or Google
  service accounts. Optional connections now use Quota Guard's own credential forms.
- Remove the Tuya SDK HTTP observer and all runtime client patching.
- Default every source and the configure action to monitor-only. Only an explicit
  administrator mapping with cutoff enabled can disable an integration. Provider
  polls preserve that choice and cannot infer targets or enable enforcement.
- Migrate legacy automatic metrics to retained history and reset old cutoff flags
  before the first evaluation. Keep sensor sources, history and events; never
  automatically re-enable integrations that were previously disabled.
- Add English/Hebrew provider connection, removal and cutoff mapping screens.

## 0.6.0

- Save configuration and evaluate submitted/sensor measurements without waiting for slow cloud polls; skip overlapping polls.
- Add native Configure options and a dashboard form for adding and editing sensor sources, with English and Hebrew labels.
- Support external services without cutoff targets, reference usage-page links, sensor attributes, separate period/timestamp sensors, and explicitly configured monthly periods.
- Preserve unknown/stale status when measurements are missing and invalidate prior-month samples at a configured monthly reset.
- Recognize IMOU OP1013 setup failures without a repair, keep one notice per account/month, and clear inactive historical incidents when the integration recovers.
- Reject unsafe usage URLs and prevent built-in sources from being overwritten through manual configuration.


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
