# Telemetry contract — onboarding milestone checkpoints

Binding wherever a skill in this plugin reports a milestone. Read alongside
[safety-contract.md](safety-contract.md), which governs everything else.

## What this is for

The funnel is `setup → credentials → verify → discover-data → instrument → conversions`.
Today we have no idea where real users fall out of it. Checkpoints answer that: one event
per milestone, carrying the outcome and — when something went wrong — a class naming what.

Validated separately against production before being brought here: the transport reaches
GCP, the payload shape is confirmed, and it degrades safely where egress is denied.

## Why the milestones are NOT the ones from the install prototype

The prototype used `preflight` / `dependency_added` / `build_passed`. Those don't port:

- **`build_passed` has no home in `setup`.** Setup writes files and hands off; building and
  running is `verify`'s job. Keeping it here would mean reporting a milestone this skill
  cannot observe.
- **`dependency_added` is too narrow.** Setup applies one reviewed change set — manifest,
  init, wrapper, platform config, verification file — as a single approved unit. Splitting
  that into "dependency" understates what happened.
- **The most valuable milestone in this skill has no prototype equivalent.** `setup`
  Step 3 states plainly: *"The single worst onboarding failure is installing the SDK before
  push credentials exist."* That is a testable claim about where onboarding breaks, and
  nothing in the prototype measured it.

So the vocabulary is rebuilt around this funnel. What ports unchanged is the machinery:
`scripts/checkpoint.sh`, `scripts/otlp_encode.py`, the payload schema, the OTLP→GCP field
mapping, and the degradation behaviour.

## The unit of analysis is a funnel run, not an install

`run_id` is generated once and **persists across every skill in the funnel**, in
`.onesignal/run_id`. That is the point: it lets you follow one developer from `setup`
through `conversions` and see exactly which step they stopped at. A per-skill id would
throw that away.

Clear it only when starting a genuinely new onboarding attempt.

## Milestones

Each is `skill.milestone`. Status is `ok`, `ok_after_fix`, or `fail`.

### setup

| Milestone | Fires when | Why it matters |
|---|---|---|
| `setup.preflight` | after Step 0–1: tree checked, prior install detected, platform identified | platform distribution; how often we meet an existing install |
| `setup.app_id` | App ID obtained (Step 2) | how often users arrive without an app |
| `setup.credentials_gate` | Step 3 resolves | **the headline metric.** `ok` = configured, `ok_after_fix` = uploaded during the run, `fail` = missing, class `deferred` when the user chose to skip |
| `setup.sdk_pinned` | exact version resolved from releases.json (Step 4) | catches releases.json being unreachable |
| `setup.install_applied` | change set approved and written (Step 5) | how often users reject the diff |
| `setup.verification_added` | verification file written (Step 6) | — |
| `setup.complete` | handing off (Step 7) | setup's own completion rate |

### Other skills

Owners add their own; keep the `skill.milestone` shape and reuse
`credentials.*`, `verify.subscribed`, `verify.delivered`, `instrument.*`,
`conversions.*`. `verify.delivered` is the true activation event and the funnel's terminal
success.

## Failure classes

Reuse an existing class where one fits; otherwise add it here rather than inventing one at
the call site. Current set:

`dirty_tree`, `prior_install`, `platform_ambiguous`, `no_app_id`, `invalid_app_id`,
`credentials_missing`, `uploaded_during_run`, `deferred`, `releases_unreachable`,
`diff_rejected`, `network_blocked`, `kotlin_stdlib_floor`, `minsdk_floor`, `agp_floor`,
`dependency_conflict`, `buildconfig_disabled`, `manifest_merger`, `unknown`.

`buildconfig_disabled`: AGP 8+ stopped generating `BuildConfig` by default, so the
verification file's `BuildConfig.DEBUG` guard needs `buildFeatures { buildConfig = true }`
added to the app module — this will hit most modern Android projects (android.md documents
the alternative that avoids it).

`no_app_id` and `invalid_app_id` are different findings: the first means the user has no
OneSignal app yet, the second means they supplied an ID that does not parse as a UUID
(a truncated paste, most likely). Conflating them hides which fix the onboarding flow
needs — app creation versus input validation.

## `ok_after_fix` — use it

If a milestone succeeded only because you changed something the user did not ask for —
raised a `minSdk`, upgraded a Kotlin or AGP version, resolved a dependency conflict —
report `ok_after_fix <class>`, never plain `ok`.

A bare `ok` makes that install indistinguishable from a clean one and erases the exact
friction this telemetry exists to find. This is not hypothetical: the prototype's most
valuable single finding (an undocumented Kotlin floor in the Android SDK) was initially
reported as `ok` and nearly lost.

## What is sent

Per event: milestone, status, failure class, `run_id`, platform, skill name, plugin
version, agent runtime, OS, timestamp, and the **OneSignal App ID** — plus three fixed
constants: the source tag (`onesignal-agent-plugin`), the payload schema version, and the
service name (`OneSignalAgentSkill`).

Never sent: source code, file contents, file paths, project or package names, repo
metadata, and — per the safety contract's "Never" rules and its "The setup key" section —
**the setup key or any other credential**. The App ID is public (safety contract, "Never"
section) and is the only identifier included.

## App ID ordering, and buffering

The ingestion endpoint requires the App ID as a query parameter, but
`setup.preflight` fires before Step 2 has one. So:

- Milestones before the App ID is known are **written locally and held**.
- Once Step 2 resolves it, run `bash scripts/checkpoint.sh flush` — each pending event is
  sent as its own request, carrying the now-known App ID and every field as recorded:
  milestone, status, failure class, skill, timestamp, platform, source, run_id, runtime
  and os. Nothing is re-derived at flush time.
- Everything after that sends as it happens. A failed send is held for a later flush or
  dropped, according to the retry matrix below.
- Run `flush` one final time at `setup.complete`, so events re-buffered mid-run get a
  second attempt before the session ends.

A flush sends each row as its own request and keeps **only the rows that failed**. An event
confirmed as accepted is never sent again, so a single bad row no longer forces the whole
buffer to be re-delivered. A partial flush reports `<n> of <total> sent — <k> kept for a
later flush`, and the buffer file is rewritten with those `k` rows.

If egress is blocked, events stay in `pending.jsonl` and a run that later gains network
access loses nothing. That holds both before the App ID exists and after it.

### Retry matrix

The rule is whether an identical retry could plausibly succeed. Anything transient is held;
anything that rejects **this** payload is dropped, because a permanent failure re-queued
forever would also block every row behind it.

| Outcome | `transport.log` | Held for a retry? |
| --- | --- | --- |
| `202` (or `200`) with an empty body | `sent` | Delivered, nothing to hold |
| `2xx` with an HTML body (captive portal) | `http_2xx_html` | Yes — never reached the service |
| `2xx` otherwise unexpected | `http_2xx_unexpected` | Yes — delivery unconfirmed |
| `curl` never got a reply | `dns_unresolved`, `connection_refused`, `timeout`, `tls_error`, `connection_killed`, `curl_rc_<n>` | Yes |
| `curl` succeeded but sent no status line | `no_response` | Yes |
| `429` — rate limited | `http_retryable` | Yes |
| `5xx` — server or gateway error | `http_retryable` | Yes |
| Any other `4xx`, including `400` and `415` | `http_rejected` | **No** — same payload fails again |
| `1xx` or `3xx` | `http_other` | **No** — the endpoint is misconfigured, not busy |
| Encoder error before any request | `encode_failed` | **No** — deterministic |

`429` is the one 4xx that is held: it is an instruction to slow down, not a verdict on the
payload. `408` and `425` are treated as permanent, because the ingestion service does not
emit them — a gateway that does would cost one dropped checkpoint, not a retry loop.

**Delivery is at-least-once, so de-duplication stays mandatory.** Per-row bookkeeping
removes the *bulk* duplicate source, but not every one: a request whose response is lost
after the server accepted it is indistinguishable from a request that never arrived, so it
is held and re-sent. Never drop an event to avoid a duplicate.

**Never substitute a placeholder or demo App ID to make an early send work.** Setup Step 2
already forbids hardcoded fallback App IDs, and attributing a real user's onboarding to a
OneSignal test app would corrupt the data it is meant to produce.

**Known measurement limit that follows from this:** a run that ends before an App ID exists
never reaches GCP — its events stay buffered forever, because the endpoint's only gate is
the App ID. The funnel's earliest failures (`dirty_tree`, `platform_ambiguous`, `no_app_id`,
`invalid_app_id`) are therefore visible only in the local `.onesignal/` record unless the
user returns and the buffer flushes. Production dashboards undercount pre-App-ID dropouts;
treat their absence as a floor, not a measurement.

## Refusal and failure behaviour

- `checkpoint.sh` always exits 0. Telemetry never fails a user's onboarding.
- If the user declines network access, re-run the same checkpoint with
  `ONESIGNAL_SKILL_TELEMETRY=0` and continue. No network call is attempted, the local
  record is kept, and `transport.log` records `telemetry_disabled` so the refusal is
  auditable.
- Do not ask twice. Do not reach the network by another route. A refusal is a valid answer.
- Per safety contract §13, a `fail` checkpoint is sent **at** the failing step, and then
  the skill stops. Never retry with mutations to make a milestone reportable.

## Local state

Everything lands in `.onesignal/` at the repo root: `run_id`, `checkpoints.jsonl` (every
payload, sent or not), `transport.log` (what happened to each attempt), `pending.jsonl`.

The two logs answer different questions, and the distinction is what makes them assertable:
**`checkpoints.jsonl` holds exactly one row per milestone reported**, and `transport.log`
holds one row per delivery attempt. A buffered event that is later flushed therefore
appears once in `checkpoints.jsonl` and twice in `transport.log` (`buffered`, then `sent`).

The wire timestamp is the **event time**: `otlp_encode.py` stamps the record with the
payload's own `ts`, and a flush re-send carries the buffered event's original `ts` through.
So a milestone that waited in the buffer lands in GCP at the moment it happened, and
ordering GCP results by timestamp reflects the user's actual experience — a recovered
failure sorts *before* the recovery, even though it was sent after it. The send moment
travels separately in `observed_time_unix_nano`.

Because skills declare a file allow-list before writing (safety contract §4, §10),
**`.onesignal/` must appear in that declared list and be added to `.gitignore`.** It is
run state, not project content, and must never be committed.

## Querying it

```
resource.type="k8s_container"
resource.labels.namespace_name="log-ingestion-service-production"
labels.scope_name="onesignal-agent-skill"
```

Then slice by `labels."agent.platform"`, `labels."agent.skill"`,
`jsonPayload."agent.milestone"`, `jsonPayload."agent.status"`. Group by
`jsonPayload."agent.run_id"` for funnel analysis — use `=` and not `=~`, since a regex
silently merges runs.

**De-duplication is mandatory, not optional.** Delivery is at-least-once: a response lost
after the server accepted the request cannot be told apart from one that never arrived, so
the event is held and re-sent — duplicate delivery is a designed trade-off (never drop an
event to avoid a duplicate). Every count, funnel step, and
completion rate MUST first de-duplicate on `run_id` + milestone, keeping one row per pair
(earliest timestamp). A query that skips this step overcounts whatever it measures.
