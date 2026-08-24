# Telemetry contract — onboarding milestone checkpoints

Binding wherever a skill in this plugin reports a milestone. Read alongside
[safety-contract.md](safety-contract.md), which governs everything else.

This file carries what a skill needs to report a milestone: the vocabulary, the rules, and
the command. The wire format, the transport behaviour and the analysis rules belong to
`scripts/checkpoint.sh` and to an internal design document, not here.

## What this is for

The funnel is `setup → credentials → verify → discover-data → instrument → conversions`.
Today we have no idea where real users fall out of it. Checkpoints answer that: one event
per milestone, carrying the outcome and — when something went wrong — a class naming what.

Validated separately against production before being brought here: the transport reaches the
ingestion service, the payload shape is confirmed, and it degrades safely where egress is
denied.

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
`scripts/checkpoint.sh`, the payload schema, and the degradation behaviour.

## The unit of analysis is a funnel run, not an install

`run_id` is generated once and **persists across every skill in the funnel**, in
`.onesignal/run_id`. That is the point: it lets you follow one developer from `setup`
through `conversions` and see exactly which step they stopped at. A per-skill id would
throw that away.

`checkpoint.sh` owns the id. A skill never reads it, writes it, or decides when a run ends.
A new run starts in 2 cases: this checkpoint is `setup.preflight` and the run already
reported a successful preflight, or the run was idle for more than 8 hours.
`ONESIGNAL_SKILL_RUN_ID` overrides the id and is never persisted, which is how a CI smoke
test keeps its own.

One run can hold more than one row for the same milestone, by design: a `fail` and the `ok`
that follows it are the recovery story, and both belong to the run.

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

An absent failure class is deliberate. Send no class where there is none: an empty value
creates a category that every count of failure classes must then exclude.

### `failure_detail` — only when the class is `unknown`

When no existing class fits, report `unknown` and pass a short slug as the 4th
argument. Recurring slugs become real classes in a later plugin release.

```bash
bash scripts/checkpoint.sh setup.install_applied ok_after_fix unknown foo_bar_missing
```

Rules:

- Set the field only when the caller passes class `unknown`. Any other class drops it.
- Use the naming pattern of the known classes: a noun and a state.
- Never include a path, a project name, a version number, an ID, or code.

`checkpoint.sh` rewrites the slug to lowercase. Other characters become `_`.
The script cuts the result to 30 characters. The script drops the value if
the caller did not pass class `unknown`. It also drops a raw argument that
holds `/`, `\`, `.`, `@`, or `:`. It drops 4 digits in a row. It drops a
result that does not match `^[a-z][a-z0-9_]*$`. An empty result omits the
key. `message` never includes this field. The script is a structural
backstop. A name with no punctuation is an agent-rule case.

## `ok_after_fix` — use it

If a milestone succeeded only because you changed something the user did not ask for —
raised a `minSdk`, upgraded a Kotlin or AGP version, resolved a dependency conflict —
report `ok_after_fix <class>`, never plain `ok`.

A bare `ok` makes that install indistinguishable from a clean one and erases the exact
friction this telemetry exists to find. This is not hypothetical: the prototype's most
valuable single finding (an undocumented Kotlin floor in the Android SDK) was initially
reported as `ok` and nearly lost.

## What is sent

Per event: milestone, status, failure class, `run_id`, the position of the report in the
run, platform, skill name, plugin version, agent runtime, OS, timestamp, and the
**OneSignal App ID**. When the class is `unknown`, a sanitized `failure_detail`
slug may also go out. One more field, `message`, carries a readable line that
`checkpoint.sh` builds from the other fields on this list — never from
`failure_detail`. Safety contract §16 lists the fixed constants that also go out.

Never sent: source code, file contents, file paths, project or package names, repo
metadata, and — per the safety contract's "Never" rules and its "The setup key" section —
**the setup key or any other credential**. The script cannot tell a bare name from a
valid slug; agent rules still forbid project and package names. The App ID is public
(safety contract, "Never" section) and is the only identifier included.

## The `platform` vocabulary

`platform` is a filter key for every funnel query, so it holds a closed set of 9 tokens:

`android`, `ios`, `web`, `react-native`, `expo`, `flutter`, `capacitor`, `cordova`, `unity`.

`scripts/detect_platform.py` emits exactly these values, and `setup/SKILL.md` Step 1 writes
the script's value into `.onesignal/platform`. A 10th value, `unknown`, reaches the wire
only when `checkpoint.sh` finds no readable file. Never add a spelling outside this set: a
second spelling of one platform splits that row of the funnel and every count built on it.
`capacitor` covers Ionic, because an Ionic app is a Capacitor app or a Cordova app.

## Reporting a checkpoint

```bash
bash scripts/checkpoint.sh <skill.milestone> <ok|ok_after_fix|fail> [class] [detail]
bash scripts/checkpoint.sh flush
```

The script builds and sends the event. A skill never builds a request, and never needs to
know the endpoint, the keys, or the encoding.

### App ID ordering, and buffering

The ingestion endpoint requires the App ID, but `setup.preflight` fires before Step 2 has
one. So:

- Milestones before the App ID is known are **written locally and held**.
- Once Step 2 resolves it, run `bash scripts/checkpoint.sh flush` — each pending event is
  sent carrying the now-known App ID and every field as recorded. Nothing is re-derived at
  flush time, so a milestone that waited in the buffer still reports the moment it happened.
- Everything after that sends as it happens. A failed send is held for a later flush, unless
  the failure is one that an identical retry cannot fix.
- Run `flush` one final time at `setup.complete`, so events re-buffered mid-run get a
  second attempt before the session ends.

**Never substitute a placeholder or demo App ID to make an early send work.** Setup Step 2
already forbids hardcoded fallback App IDs, and attributing a real user's onboarding to a
OneSignal test app would corrupt the data it is meant to produce.

If egress is blocked, events stay buffered and a run that later gains network access loses
nothing. That holds both before the App ID exists and after it.

## Refusal and failure behaviour

- `checkpoint.sh` always exits 0. Telemetry never fails a user's onboarding.
- If the user declines network access, re-run the same checkpoint with
  `ONESIGNAL_SKILL_TELEMETRY=0` and continue. No network call is attempted, the local
  record is kept, and the refusal is recorded so it stays auditable.
- Do not ask twice. Do not reach the network by another route. A refusal is a valid answer.
- Per safety contract §13, a `fail` checkpoint is sent **at** the failing step, and then
  the skill stops. Never retry with mutations to make a milestone reportable.

## Local state

`checkpoint.sh` keeps its run state in `.onesignal/` at the repo root: the run id, the
position counter, the buffer of held events, and a record of every checkpoint and delivery
attempt.

Because skills declare a file allow-list before writing (safety contract §4, §10),
**`.onesignal/` must appear in that declared list and be added to `.gitignore`.** It is
run state, not project content, and must never be committed.
