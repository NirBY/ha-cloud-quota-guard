# Usage, automatic discovery and cutoff

## Automatic request counters

**Configure → Automatic HTTP request counters** is on by default. Quota Guard
checks for supported integration-owned clients at startup and every 10 seconds.
New sources and entities appear automatically in the dashboard on its next refresh.
The same path handles a first installation, new integrations and reloaded clients.

| Integration | Automatic source |
| --- | --- |
| Mashov | Its dedicated aiohttp client (verified with 1.2.1) |
| Life360 | Dedicated account clients, aggregated by integration entry (0.11.0) |
| Xiaomi Cloud Map Extractor | Dedicated Xiaomi cloud client, including map downloads (v3.0.0-alpha-24) |
| Meitav Tariff Sync | Its owned-session interface (1.0.2) |
| Other integrations | An integration-owned session exposed through the interface below; otherwise connect an existing usage sensor |

These are **observed HTTP requests sent by this Home Assistant instance**. Redirects
and HTTP retries count separately, including requests that receive an error.
DNS/connect failures before headers are sent do not count. Cached updates, entity
refreshes and dashboard refreshes do not count. LAN/UDP vacuum commands, MQTT
messages, other apps, phones and provider-side background work are outside this scope.
See the [aiohttp tracing lifecycle](https://docs.aiohttp.org/en/stable/tracing_reference.html).

Counters start when a client is discovered. Startup, client-replacement and offline
gaps can be missing, so the UI always calls this a **partial day**, never a complete
account total. Daily buckets reset at **00:00 UTC**. Counts are saved with Quota
Guard's normal storage and flushed on shutdown; abrupt crashes can lose changes
since the previous save. Historical points remain available. Removed integrations
are archived. Unavailable clients pause counting; they do not become zero usage.

No provider allowance, price, remaining balance or charge is inferred. Meitav's
ordinary website fetch occurs about every six hours; its internal reconciliation
updates are not website requests. Counts do not establish a provider quota.

### For integration authors

Expose `entry.runtime_data.quota_guard_sessions` as a tuple/list of **only the
aiohttp sessions owned by that entry**. Return replacement sessions when clients
are recreated. Do not expose Home Assistant's shared session, sessions belonging
to another integration, credentials or account records. Quota Guard attaches a
frozen headers-sent observer to each unique session, detaches on replacement/unload,
and rejects clients advertised by multiple entries. It never replaces global HTTP
methods or reads request contents. Native counters can instead use the existing
sensor/report source form with explicit timestamps and reporting periods.

This does not automatically support every integration or every HTTP library.
Integration updates can change client structure; the settings page shows when a
supported client cannot be found. No usage is fabricated in that case.

## Warning and critical alerts

Open **Configure → Warning and critical alerts**, choose a source, then choose:

- **Usage reaches an amount**: for example warning at 100 requests and critical at 200.
- **Remaining balance falls to an amount**: for example warning at $10 and critical at $5.
- **Percentage of the known allowance**: for example 80% and 95%.

The values use the source's unit. Warning must precede critical. Both alerts are
recorded on a jump across both values and deduplicated within the reporting period.
Stale/invalid samples cannot trigger cutoff. A critical alert does not enable cutoff.
New counters do not invent warning amounts; choose values that suit your workload.

Home Assistant persistent notifications are always used. **Configure → Alert delivery**
adds existing `notify` services or notify entities for email/SMTP, Telegram,
GreenAPI/WhatsApp, mobile apps, or another HA notifier. Install/configure that
notification integration first. Enter required recipients only for a service;
an entity already identifies its destination. Select warning, critical and/or
cutoff messages, then explicitly enable delivery. Delivery is off by default;
saving does not send a test message. Failed delivery leaves the local alert visible.

## AI services, personal subscriptions, Tuya and IMOU

**Configure → AI services → provider** groups OpenAI, Google AI/Gemini, Claude,
Cursor, DeepSeek, Grok/xAI, Meta AI, OpenRouter and Mistral. Choose API/team reporting,
an existing sensor, a manual reading, or updating an existing manual reading.
Personal subscriptions use a sensor/manual reading when there is no supported
account reporting API; an API key does not expose ChatGPT, Gemini or Claude app quotas.

| Provider | API form credentials and measured scope |
| --- | --- |
| OpenAI | Organization **Admin API key**; organization costs in USD this UTC month. [API reference](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs) |
| Google AI / Gemini | Complete service-account JSON, with Cloud Monitoring read permissions on that project; reported **output tokens**, not input tokens or cost. [Metric catalog](https://docs.cloud.google.com/monitoring/api/metrics_gcp#gcp-generativelanguage) |
| Claude | Organization key authorized for usage/cost analytics; organization USD costs from the provider's report snapshot, possibly delayed up to 24 hours. [Analytics API](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api) |
| Cursor | Team Admin API key; **on-demand** team spend in the current subscription billing cycle, excluding included usage. [Admin API](https://prod.cursor.com/docs/account/teams/admin-api) |
| DeepSeek | API key and currency; **available balance**, not spent usage. Use lower-balance alerts. [Balance API](https://api-docs.deepseek.com/api/get-user-balance/) |
| Grok / xAI | Management API key and team ID; reported team USD usage. [Billing API](https://docs.x.ai/developers/rest-api-reference/management/billing) |
| OpenRouter | The API key being measured; its monthly USD spend, excluding separate BYOK usage. [Key usage](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key) |
| Mistral | Enterprise Admin API key; aggregate **API calls**, not cost or personal subscription usage. [Usage API](https://docs.mistral.ai/admin/admin-api/usage-metrics) |
| Meta AI | Existing sensor/manual reading; no supported personal-usage reporting API in this plugin |

API reports poll hourly and may be delayed further by the provider. Empty reports
are not fabricated as zero; expired monthly reports cannot trigger next-month actions.

**Configure → Tuya usage** and **IMOU usage** offer a sensor or manual console reading.
No verified numeric API-request-allowance endpoint is used for either provider.
Enter the actual measured value, reporting period and timezone-aware measurement
timestamp. Optionally enter an expiry. Update the reading after the provider changes it.
The existing IMOU OP1013 signal remains separate: quota exhaustion is real, exact
usage is unknown, and the next monthly reset is an estimate until confirmed.

## Family screen time

**Configure → Family screen time → Microsoft Family Safety / Google Family Link**
selects a daily numeric screen-time sensor from that specific integration. Values
are minutes, with your warning/critical amounts and an optional local daily target.
The source date must match today in Home Assistant's timezone, and readings expire
at local midnight. Microsoft's poll timestamp is used when provided; otherwise
freshness means the last HA state report, not a verified provider API refresh.
This does not change the child's restrictions or turn off their devices. Use the
family app's parental controls for those actions.

## Provider cutoff

Local HA cutoff and provider-side cutoff are separate options. Use **Configure →
Provider-side cutoff**, select a source, enter exact target identifiers, and review
the provider-validated targets. Everything defaults to **off**. If already critical,
enabling can act on the next evaluation. OpenAI/Mistral revocation is permanent.
Only configured targets are affected; no automatic re-enable occurs on a reset.

| Provider | Implemented optional action | Preferred first safeguard |
| --- | --- | --- |
| Oracle | SOFTSTOP selected compute instances | Account allowances, budgets and independent storage monitoring |
| OpenAI | Revoke selected project API keys | Native organization/project spending limits; [documentation](https://developers.openai.com/api/docs/guides/spend-limits) |
| Claude | Set selected keys inactive | Workspace limits; [Admin API](https://platform.claude.com/docs/en/manage-claude/admin-api) |
| OpenRouter | Disable selected keys with a separate Management key | Native per-key spending limit; [controls guide](https://openrouter.ai/blog/tutorials/team-spend-controls-setup/) |
| xAI | Disable selected keys in the configured team | Native team spending controls; [Management API](https://docs.x.ai/developers/rest-api-reference/management/auth) |
| Mistral | Delete selected keys using Enterprise Admin access | Organization spending limit; [key management](https://docs.mistral.ai/api/endpoint/beta/admin/api-keys) |
| Cursor | Set selected members' spend limits to $0, verify effective limit | Native team/member caps. Included usage can continue; higher group limits may prevent a zero cap. [Admin API](https://prod.cursor.com/docs/account/teams/admin-api) |
| Google APIs / Gemini | Disable only the measured API in its project; never disable dependencies | Native quota/cost limits; [Service Usage API](https://docs.cloud.google.com/service-usage/docs/reference/rest/v1/services/disable) |
| GitHub, DeepSeek, Tuya, IMOU, Meta AI, sensor/manual sources | Stop explicitly selected HA integrations; no undocumented provider key-control endpoint | Provider portal controls and reducing the actual workload |

Provider reporting and actions may be delayed. These actions cannot guarantee a
hard spending cap. Google controls require `serviceusage.services.disable`; read-only
monitoring permissions are insufficient. Control errors are displayed as failed or
unconfirmed, never as protection. Retries are limited to three, at least 15 minutes
apart. Reconfigure after correcting an error. Already completed actions are not repeated.
