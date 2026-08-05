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

`dirty_tree`, `prior_install`, `platform_ambiguous`, `no_app_id`, `credentials_missing`,
`deferred`, `releases_unreachable`, `diff_rejected`, `network_blocked`,
`kotlin_stdlib_floor`, `manifest_merger`, `unknown`.

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
version, agent runtime, OS, timestamp, and the **OneSignal App ID**.

Never sent: source code, file contents, file paths, project or package names, repo
metadata, and — per safety contract §31 — **the setup key or any other credential**. The
App ID is public (safety contract §23) and is the only identifier included.

## App ID ordering, and buffering

The ingestion endpoint requires the App ID as a query parameter, but
`setup.preflight` fires before Step 2 has one. So:

- Milestones before the App ID is known are **written locally and held**.
- Once Step 2 resolves it, run `bash scripts/checkpoint.sh flush` — each pending event is
  sent as its own request, carrying the now-known App ID and its original milestone,
  status, failure class and skill.
- Everything after that sends as it happens.

The buffer is cleared **only when every pending event was accepted**. If egress is blocked
the events stay in `pending.jsonl` for a later flush, so a sandboxed run that later gains
network access loses nothing. A partial flush reports `<n> of <total> sent` and keeps the
whole buffer; the accepted events will be re-sent on the next attempt, so treat duplicate
delivery as possible and de-duplicate on `run_id` + milestone when analysing.

**Never substitute a placeholder or demo App ID to make an early send work.** Setup Step 2
already forbids hardcoded fallback App IDs, and attributing a real user's onboarding to a
OneSignal test app would corrupt the data it is meant to produce.

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

One caveat when correlating with GCP: the wire timestamp is stamped at send time by
`otlp_encode.py`, so a flushed milestone is recorded in GCP at the moment of the flush, not
when it actually occurred. The local `ts` in `checkpoints.jsonl` is the accurate one.

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
