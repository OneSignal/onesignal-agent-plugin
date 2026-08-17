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

### When a run ends

A run ends when the plugin starts a new one. `checkpoint.sh` starts a new run when either
rule below is true. In every other case it reuses the cached id.

**Rule 1 — a second entry into setup.** The milestone is `setup.preflight`, and the current
run already holds a `setup.preflight` row with status `ok` or `ok_after_fix`. One run passes
preflight at most one time, so a second pass is a new attempt.

**Rule 2 — 8 hours with no checkpoint.** `.onesignal/run_last_seen` holds the epoch time of
the last checkpoint. A gap of more than 8 hours starts a new run.

Rule 1 keeps every `fail` → `ok` recovery pair inside one run. Setup reports
`setup.preflight fail dirty_tree`, stops to ask the user, then reports `setup.preflight ok`
in the same session. The run holds no successful preflight when the failure is reported, so
the pair stays together. A reset at every `setup.preflight` would split the pair. The
milestone alone is therefore not the rule.

Rule 2 covers the case rule 1 cannot see. A user can stop at `fail dirty_tree` and never
reach a successful preflight. Rule 1 finds no successful preflight in that run, so a later
attempt would merge into the abandoned one. The 8 hour limit separates them. The value is a
judgment: a recovery inside one conversation takes minutes, and a user who returns the next
morning starts a new attempt.

Both rules fail safe. The plugin keeps the cached id if `run_last_seen` is absent, if the
value is unreadable, or if it cannot read the local record. A merged run under-counts one
dropout. A wrong reset invents a run that never happened, which is worse.

Two behaviours do not change. `ONESIGNAL_SKILL_RUN_ID` always wins and is never persisted,
so CI smoke tests keep their own id. Deletion of `.onesignal/` still starts a new run, which
is how each eval fixture gets one run.

A reset does not touch `pending.jsonl`. A held event carries the `run_id` it was recorded
with, so it still flushes into the run it belongs to.

One run can hold more than one `setup.preflight` row, by design. A de-duplication key of
`run_id` + milestone is therefore not enough. The "De-duplication" section defines the full
key for each schema. A reset also sets the `seq` counter back to 0.

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
`dependency_conflict`, `buildconfig_disabled`, `coroutines_missing`, `manifest_merger`,
`unknown`.

`buildconfig_disabled`: AGP 8+ stopped generating `BuildConfig` by default, so the
verification file's `BuildConfig.DEBUG` guard needs `buildFeatures { buildConfig = true }`
added to the app module — this will hit most modern Android projects (android.md documents
the alternative that avoids it).

`coroutines_missing`: the verification file calls the suspend `requestPermission` from a
coroutine, but the app has no `kotlinx-coroutines` on its compile classpath — the OneSignal
SDK ships it only as a runtime (`implementation`) dependency, not `api`, so a bare app fails
to compile until it declares coroutines (the `android_coroutines_on_classpath` check flags
this; android.md documents the exact dependency line). Sibling of `buildconfig_disabled`:
correct code that does not compile until the app module declares one more thing.

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

Do not read a schema 2 timestamp without checking the unit first. `otlp_encode.py` writes
**seconds** into `time_unix_nano`, because the consumer used to read seconds. The service
now divides the field by 1e9, so the same value reports a date in 1970. The file holds a
`TIME_FIELD_IS_SECONDS` flag for exactly this change, and the flag has to flip to `False`
when the new service reaches production.

### Schema 3 — REST to `/sdk/agent-progress` (merged, not yet confirmed in production)

The full path is `https://api.onesignal.com/sdk/agent-progress`. The service nests every
per-app route under `/sdk` (`nest_service("/sdk", …)` in `router.rs`), which is why the
current OTLP path is `/sdk/log`. The handler file names the route `/agent-progress`, so read
the nest before you copy a path out of that file.

The server builds the OTLP record from a flat key/value map. The endpoint accepts 2
methods, and the behaviour below is identical for both:

- **GET** carries every key in the query string, `app_id` included. The plugin sends this
  form with `curl -G --data-urlencode`, so curl encodes every value and the request needs
  no header.
- **POST** carries `app_id` in the query string and the rest as a JSON object in the body.
  A JSON value may be a number or a boolean, but the server flattens every value to a
  string, so the stored shape is the same.

**The plugin sends GET.** Nothing in this contract depends on the method, and the query
string has room to spare.

The longest possible request measures 494 bytes, with the longest value from every enum.
`message` is the only free-text field, and the plugin builds it from the other fields, so it
cannot grow without bound. jlbelmonte reports that Cloudflare accepts a URL up to 32 KB, so
the request uses about 1.5% of the budget. No setting in `OneSignal/infra` or
`OneSignal/infra-foundation` bounds a URL, a query string, or a request header for this
service, so the remaining hops (the GCP load balancer and Envoy) apply their own defaults.
Neither default is measured, and neither is plausibly below 494 bytes.

The one other size limit on the path is a Cloudflare rule that blocks a `POST /sdk/log` body
above 64 KB, and no GET request meets it.

**So size is not a reason to prefer POST.** The only reason to choose POST is the edge route
below. A new field costs about 25 bytes, so the budget is not a constraint on the schema
either.

#### The edge route does not exist yet

`api.onesignal.com` reaches this service through 1 Emissary mapping, in
`kubernetes/helmfiles/apps/emissary/production/mappings-production.yml`. The mapping matches
`prefix: "/sdk/log"` with `method: "POST"`, and it covers no other path. No mapping matches
`/sdk/agent-progress`, and the string `agent-progress` appears nowhere in `OneSignal/infra`.
The only other `/sdk` prefix in the file is `/sdks/`, plural, which belongs to a different
service. So a new mapping has to land in that repo, and it has to allow GET. The repo already
uses that form for other routes, so the shape is settled:

```yaml
- service: log-ingestion-http.log-ingestion-service-production
  hostname: api.onesignal.com
  auth: { bypass: true }
  routes:
    - name: agent-progress-production
      spec:
        prefix: "/sdk/agent-progress"
        method: "(GET|POST)"
        method_regex: true
```

`auth: { bypass: true }` matches the existing route. The endpoint carries no header, and
`app_id` is the whole gate. `method_regex` is needed because `method` holds 1 method
otherwise, and a copy of the current mapping would reject a GET at the edge. The staging file
needs the same route, for a test before the cutover.

The merged server code is necessary but not enough. Confirm the mapping before you read an
empty dashboard as an empty funnel.

The server changes the map in 4 ways:

1. It removes `app_id` and writes it as the resource attribute `ossdk.app_id`. The value
   stays a GCP label, and it is the only key that does.
2. It reserves `message` and writes it as the log record body, **not** as an attribute. So
   `message` reaches GCP as `jsonPayload.message`, which is the line the Logs Explorer
   shows for the row. Keep sending it: it is what makes a row readable without a query.
3. It reserves `timestamp` and writes it as the record time, then drops the key. So there
   is no `os_agent.timestamp`. The server reads epoch seconds, milliseconds, microseconds,
   nanoseconds, or an RFC 3339 string, and picks the unit from the magnitude. A missing or
   unreadable value falls back to the time of receipt.
4. It adds the prefix `os_agent.` to every remaining key and writes those as log record
   attributes. They land in `jsonPayload`, not in `labels`.

It also writes severity INFO for every record. That is the reason `status` below repeats
what the OTLP record carried in its own severity field.

The reserved keys and the prefix live in `src/log/agent_progress.rs` in
`log-ingestion-service`. Check that file before you assume any other key is special.

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
| `timestamp` | yes | `1786000000` | The event time. The server consumes the key, so it never becomes an attribute. Epoch seconds, or any unit down to nanoseconds, or RFC 3339. |
| `platform` | yes | `android` | A closed set. See below. |
| `runtime` | yes | `claude-code` | |
| `os` | yes | `darwin` | |
| `skill_version` | yes | `0.3.0` | |
| `message` | yes | `onesignal onboarding [android/setup]: …` | Becomes the log body, so GCP shows it as the row's own line. |

Because the server also reads RFC 3339, the plugin can send the ISO 8601 `ts` value that
`checkpoints.jsonl` already holds, with no conversion step. Either form is valid.

Do not send `severity`. The server always writes INFO, so the value only repeats `status`.

#### The `platform` vocabulary

`platform` is a filter key for every funnel query, so it holds a closed set of 9 tokens:

`android`, `ios`, `web`, `react-native`, `expo`, `flutter`, `capacitor`, `cordova`, `unity`.

`scripts/detect_platform.py` emits exactly these values, and `setup/SKILL.md` Step 1 writes
the script's value into `.onesignal/platform`. A 10th value, `unknown`, reaches the wire
only when `checkpoint.sh` finds no readable file. Never add a spelling outside this set: a
second spelling of one platform splits that row of the funnel and every count built on it.
`capacitor` covers Ionic, because an Ionic app is a Capacitor app or a Cordova app.

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
   whole seconds. Eval runs and CI runs report several milestones inside one second. The
   server accepts sub-second units, so the plugin could break most ties by sending
   milliseconds — but a tie stays possible, and reasons 1 and 3 hold either way.
3. **The clock belongs to the user.** A machine with a wrong clock gives a wrong order and
   a wrong absolute time. The counter never reads the clock.

- The plugin keeps the counter in `.onesignal/seq`, next to `run_id`.
- A new run sets the file back to 0, under either rule in "When a run ends".
- The first checkpoint of a run sends `seq=1`. The counter increments before the send, so
  no event ever carries 0 because of a reset.
- The counter increments one time for each milestone recorded, not for each send attempt.
  A flush re-send carries the original number, exactly as it carries the original
  `timestamp`.
- `seq=0` on the wire means one thing only: the plugin could not read or write the counter.

**Order a schema 3 run by `seq`.**

#### Field mapping

| Schema 2 | Schema 3 |
|---|---|
| `labels."ossdk.app_id"` | unchanged |
| `labels.scope_name` | removed — the value does not carry across, see below |
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
| the record timestamp | set from the `timestamp` parameter |
| `jsonPayload.message` | unchanged — the `message` parameter becomes the body |

Only `ossdk.app_id` survives as a label. Every other filter moves into `jsonPayload`, which
costs more to query. Keep `labels."ossdk.app_id"` in a query when the app is known.

**`scope_name` is not a rename of `os_agent.source`.** Both name the sender, but the 2 keys
hold different values. `scope_name` is always the literal `onesignal-agent-skill`, which
`otlp_encode.py` hardcodes. `os_agent.source` holds `onesignal-agent-plugin`, the same value
that schema 2 puts in `labels."agent.source"`. So a filter of
`labels.scope_name="onesignal-agent-skill"` becomes
`jsonPayload."os_agent.source"="onesignal-agent-plugin"`. A rewrite that keeps the old value
matches no rows.

## App ID ordering, and buffering

The ingestion endpoint requires the App ID as a query parameter, but
`setup.preflight` fires before Step 2 has one. So:

- Milestones before the App ID is known are **written locally and held**.
- Once Step 2 resolves it, run `bash scripts/checkpoint.sh flush` — each pending event is
  sent as its own request, carrying the now-known App ID and every field as recorded:
  milestone, status, failure class, skill, timestamp, platform, source, run_id, runtime,
  os, and `seq` from schema 3. Nothing is re-derived at flush time.
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

Everything lands in `.onesignal/` at the repo root: `run_id`, `run_last_seen` (the epoch
time of the last checkpoint, which rule 2 reads), `seq` (the counter schema 3 sends),
`checkpoints.jsonl` (every payload, sent or not), `transport.log` (what happened to each
attempt), `pending.jsonl`.

The two logs answer different questions, and the distinction is what makes them assertable:
**`checkpoints.jsonl` holds exactly one row per milestone reported**, and `transport.log`
holds one row per delivery attempt. A buffered event that is later flushed therefore
appears once in `checkpoints.jsonl` and twice in `transport.log` (`buffered`, then `sent`).

Both schemas put the **event time** on the wire, not the send time. Schema 2 stamps the
record in `otlp_encode.py` from the payload's own `ts`; schema 3 sends the same value as
the `timestamp` parameter and the server stamps the record. A flush re-send carries the
buffered event's original `ts` either way, so a milestone that waited in the buffer reports
the moment it happened. Under schema 2 the send moment travels separately in
`observed_time_unix_nano`.

**Do not read the event time from the log entry timestamp, under either schema.** The
consumer writes the event time as an ISO 8601 string under `jsonPayload.timestamp`. Nobody
has confirmed that GCP promotes the value to the log entry timestamp — the column the Logs
Explorer sorts by. Google's agent documents the promotion only for `timestamp` as an object
of `seconds` and `nanos`, for `timestampSeconds` with `timestampNanos`, or for a string
under the key `time`. One production sample agrees with the pessimistic reading: the entry
timestamp held the time of receipt while `jsonPayload.timestamp` kept the event time. That
sample is a schema 2 row, because schema 3 is not in production yet, so the finding covers
both schemas.

**Order a schema 3 run by `seq`.** The counter never reads a clock, so it holds the order
even when the times are wrong or equal.

**Order a schema 2 run by `jsonPayload.timestamp`.** Schema 2 sends no `seq`, so the counter
is not available for those rows. An ISO 8601 string in this format sorts correctly as text.
The value has a resolution of 1 second, so 2 milestones in the same second tie, and the tie
has no answer under schema 2.

Warning: do not trust a schema 2 event time between the deploy of the new service and the
flip of `TIME_FIELD_IS_SECONDS`. `otlp_encode.py` writes seconds into `time_unix_nano`, and
the new consumer divides by 1e9, so every schema 2 event time moves to 1970. The order
should survive, because the scale change keeps the sequence, but a gap of 60 seconds
becomes a gap of 60 nanoseconds.

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
silently merges runs. Order each run by `jsonPayload.timestamp`, because schema 2 sends no
`seq`.

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

**De-duplication is mandatory, not optional.** Delivery is at-least-once: a response lost
after the server accepted the request cannot be told apart from one that never arrived, so
the event is held and re-sent — duplicate delivery is a designed trade-off (never drop an
event to avoid a duplicate). Every count, funnel step, and completion rate MUST first
de-duplicate, keeping one row per key.

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
