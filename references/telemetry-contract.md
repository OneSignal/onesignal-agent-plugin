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

From schema 3 a second value, `seq`, gives the position of each milestone inside the run.
`run_id` says which run, and `seq` says where in that run. Together they identify one
milestone report.

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
version, agent runtime, OS, timestamp, and the **OneSignal App ID** — plus the source tag
(`onesignal-agent-plugin`) and the payload schema version. Schema 2 adds the service name
(`OneSignalAgentSkill`). Schema 3 drops the service name and adds `seq`.

Never sent: source code, file contents, file paths, project or package names, repo
metadata, and — per the safety contract's "Never" rules and its "The setup key" section —
**the setup key or any other credential**. The App ID is public (safety contract, "Never"
section) and is the only identifier included.

## Wire format

Two transports exist. The plugin sends schema 2 today. Schema 3 replaces it when the REST
endpoint is live. Read both: production holds schema 2 rows for as long as the old
transport runs, so a query that spans the change must accept both shapes.

### Schema 2 — OTLP protobuf to `/sdk/log` (current)

`scripts/otlp_encode.py` builds the record, so the plugin controls every field. Resource
attributes become GCP labels. The scope name is `onesignal-agent-skill`. The status sets
the severity: `ok` gives INFO, `ok_after_fix` gives WARNING, `fail` gives ERROR. The
record time is the event time.

### Schema 3 — REST GET to `/agent-progress` (target, not live yet)

The server builds the record from query parameters. The plugin sends the request with
`curl -G --data-urlencode`, so curl encodes every value. The request needs no header.

The server changes the request in 4 ways:

1. It removes `app_id` from the parameters and writes it as the resource attribute
   `ossdk.app_id`. The value stays a GCP label.
2. It adds the prefix `os_agent.` to every other key, then writes the keys as log record
   attributes. They land in `jsonPayload`, not in `labels`.
3. It reads `timestamp` and stamps the record with that value. The service adds this
   parameter for us. Without it the record carries the time of receipt, which is wrong
   for every event that waited in the buffer.
4. It writes severity INFO for every record, and it writes no log body.

Point 4 is the reason `status` below repeats what the OTLP record carried in its own
severity field.

#### Keys

| Key | Always sent | Example | Notes |
|---|---|---|---|
| `app_id` | yes | `6a1b2c3d-…` | The server consumes it. It is the only gate on the endpoint. |
| `schema` | yes | `3` | Payload schema version. Schema 2 is the OTLP payload. |
| `source` | yes | `onesignal-agent-plugin` | The discriminator. It replaces `scope_name` and `service.name`. |
| `run_id` | yes | `73832ff7632b0476` | Stable for one funnel run. |
| `seq` | yes | `1` | Position in the run. See below. |
| `skill` | yes | `setup` | |
| `milestone` | yes | `credentials_gate` | The bare milestone, without the skill. |
| `status` | yes | `fail` | `ok`, `ok_after_fix` or `fail`. |
| `failure_class` | no | `credentials_missing` | The key is absent when there is no class. |
| `timestamp` | yes | `1786000000` | The event time, in epoch seconds. The server writes it as the record time. |
| `platform` | yes | `android` | |
| `runtime` | yes | `claude-code` | |
| `os` | yes | `darwin` | |
| `skill_version` | yes | `0.3.0` | |
| `message` | yes | `onesignal onboarding [android/setup]: …` | The GCP message column is empty, because the server writes no body. |

Do not send `severity`. The server always writes INFO, so the value only repeats `status`.

Do not send `sdk_base`. `platform` holds the same value.

An absent `failure_class` is deliberate. An empty value creates an attribute that holds an
empty string, which every count of failure classes must then exclude.

#### `seq` — the position of a report inside a run

`seq` counts the milestone reports of one run. The `timestamp` parameter does not remove
the need for it. There are 3 reasons:

1. **A milestone repeats inside one run, by design.** The `setup` skill reports
   `setup.preflight fail platform_ambiguous`, asks the user, then reports
   `setup.preflight ok`. The pair is the recovery story. A key of `run_id` + milestone
   holds 1 row for the 2 reports and deletes the recovery. A key of `run_id` + `seq` keeps
   both, because a re-send of one report carries the same `seq`.
2. **The timestamp holds whole seconds, so 2 reports can tie.** `checkpoint.sh` writes
   whole seconds and the consumer reads whole seconds. Eval runs and CI runs report
   several milestones inside one second.
3. **The clock belongs to the user.** A machine with a wrong clock gives a wrong order and
   a wrong absolute time. The counter never reads the clock.

- The plugin keeps the counter in `.onesignal/seq`, next to `run_id`.
- The first checkpoint of a run sends `seq=1`.
- The counter increments one time for each milestone recorded, not for each send attempt.
  A flush re-send carries the original number, exactly as it carries the original
  `timestamp`.
- `seq=0` means the plugin could not read or write the counter.

**Order a run by `seq`.**

#### Field mapping

| Schema 2 | Schema 3 |
|---|---|
| `labels."ossdk.app_id"` | unchanged |
| `labels.scope_name` | `jsonPayload."os_agent.source"` |
| `labels.scope_version` | `jsonPayload."os_agent.skill_version"` |
| `labels."service.name"` | removed |
| `labels."agent.source"` | `jsonPayload."os_agent.source"` |
| `labels."agent.platform"` | `jsonPayload."os_agent.platform"` |
| `labels."agent.skill"` | `jsonPayload."os_agent.skill"` |
| `labels."agent.runtime"` | `jsonPayload."os_agent.runtime"` |
| `labels."agent.skill.version"` | `jsonPayload."os_agent.skill_version"` |
| `labels."os.name"` | `jsonPayload."os_agent.os"` |
| `labels."ossdk.sdk_base"` | `jsonPayload."os_agent.platform"` |
| `jsonPayload."agent.milestone"` | `jsonPayload."os_agent.milestone"` |
| `jsonPayload."agent.status"` | `jsonPayload."os_agent.status"` |
| `jsonPayload."agent.failure_class"` | `jsonPayload."os_agent.failure_class"` |
| `jsonPayload."agent.run_id"` | `jsonPayload."os_agent.run_id"` |
| `jsonPayload."agent.schema"` | `jsonPayload."os_agent.schema"` |
| severity INFO / WARNING / ERROR | `jsonPayload."os_agent.status"` |
| the record timestamp | unchanged, set from the `timestamp` parameter |
| the log message | `jsonPayload."os_agent.message"` |

Only `ossdk.app_id` survives as a label. Every other filter moves into `jsonPayload`, which
costs more to query. Keep `labels."ossdk.app_id"` in a query when the app is known.

## App ID ordering, and buffering

The ingestion endpoint requires the App ID as a query parameter, but
`setup.preflight` fires before Step 2 has one. So:

- Milestones before the App ID is known are **written locally and held**.
- Once Step 2 resolves it, run `bash scripts/checkpoint.sh flush` — each pending event is
  sent as its own request, carrying the now-known App ID and every field as recorded:
  milestone, status, failure class, skill, timestamp, platform, source, run_id, runtime,
  os, and `seq` from schema 3. Nothing is re-derived at flush time.
- Everything after that sends as it happens. A **transport failure** on one of those sends
  (blocked network, timeout, killed connection, no HTTP response) puts the event back into
  `pending.jsonl` for a later flush. Deterministic failures do not re-buffer: an encoder
  error or an HTTP 4xx would fail identically on every retry, forever.
- Run `flush` one final time at `setup.complete`, so events re-buffered mid-run get a
  second attempt before the session ends.

The buffer is cleared **only when every pending event was accepted**. If egress is blocked
the events stay in `pending.jsonl` for a later flush, so a run that later gains network
access loses nothing — this holds both before the App ID exists and after it. A partial
flush reports `<n> of <total> sent` and keeps the whole buffer; the accepted events will
be re-sent on the next attempt, so treat duplicate delivery as possible and de-duplicate
on `run_id` + milestone when analysing.

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

Under schema 2 the wire timestamp is the **event time**: `otlp_encode.py` stamps the record
with the payload's own `ts`, and a flush re-send carries the buffered event's original `ts`
through. So a milestone that waited in the buffer lands in GCP at the moment it happened,
and ordering GCP results by timestamp reflects the user's actual experience — a recovered
failure sorts *before* the recovery, even though it was sent after it. The send moment
travels separately in `observed_time_unix_nano`.

Schema 3 keeps that property through the `timestamp` parameter, which sets the record time
on the server. Order a run by `seq` even so, because the timestamp holds whole seconds and
2 reports in the same second have no order of their own.

Because skills declare a file allow-list before writing (safety contract §4, §10),
**`.onesignal/` must appear in that declared list and be added to `.gitignore`.** It is
run state, not project content, and must never be committed.

## Querying it

Both queries start from the same 2 lines:

```
resource.type="k8s_container"
resource.labels.namespace_name="log-ingestion-service-production"
```

### Schema 2

```
labels.scope_name="onesignal-agent-skill"
```

Then slice by `labels."agent.platform"`, `labels."agent.skill"`,
`jsonPayload."agent.milestone"`, `jsonPayload."agent.status"`. Group by
`jsonPayload."agent.run_id"` for funnel analysis — use `=` and not `=~`, since a regex
silently merges runs.

### Schema 3

```
jsonPayload."os_agent.source"="onesignal-agent-plugin"
```

Then slice by `jsonPayload."os_agent.platform"`, `jsonPayload."os_agent.skill"`,
`jsonPayload."os_agent.milestone"` and `jsonPayload."os_agent.status"`. Group by
`jsonPayload."os_agent.run_id"`, and order each run by `jsonPayload."os_agent.seq"`.
Add `labels."ossdk.app_id"` when the app is known: it is the one field that stays an
indexed label.

Severity no longer separates outcomes, because the server writes INFO for every record.
Read `jsonPayload."os_agent.status"` instead. Any saved view or alert that reads severity
needs the same change.

### De-duplication

**De-duplication is mandatory, not optional.** A partial flush keeps the whole buffer, so
an accepted event re-sends on the next attempt — duplicate delivery is a designed
trade-off (never drop an event to avoid a duplicate). Every count, funnel step, and
completion rate MUST first de-duplicate, keeping one row per key.

**Schema 3:** de-duplicate on `run_id` + `seq`. Both values survive a re-send unchanged, so
duplicates collapse and separate reports stay separate.

**Schema 2** has no `seq`, so it needs a compound key: `run_id` + milestone + status +
failure class, keeping the earliest timestamp. **Do not use `run_id` + milestone alone.**
A milestone repeats inside one run by design: `setup.preflight` reports
`fail platform_ambiguous`, the user answers, and the skill reports `ok`. The shorter key
keeps only the failure and deletes the recovery, which is the exact friction the funnel
exists to measure.

Where `seq` is `0`, the counter was unavailable. Treat those rows as schema 2 and use the
compound key.

A query that skips this step overcounts whatever it measures.
