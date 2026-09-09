# OneSignal agent plugin — team status & orientation

A snapshot for teammates picking this up or evaluating it. For customer-facing
install/usage, see [README.md](README.md). This file is the team-facing
orientation; the deep engineering rationale lives in the git history and in
internal planning docs.

**One line:** an agent-driven OneSignal SDK onboarding plugin (Claude Code skills +
deterministic Python scripts) that installs the SDK with pinned versions, writes
compile-verified integration code, and self-checks its own work — proven by an
eval harness, not vibes.

## Try it in 30 seconds

```bash
claude --plugin-dir /path/to/onesignal-agent-plugin
```
Then just say *"set up OneSignal in this app"* (or `/onesignal:setup`). No install,
nothing persistent. Full install options (marketplace, MCP, Cursor) are in the README.

---

## What's real today (shippable, committed, eval-backed)

- **Three skills**, a funnel: `setup → credentials → verify`. A confirmed
  delivery in `verify` (ACTIVATED) is the terminal success. The `discover-data`,
  `instrument`, `conversions`, and `status` skills are out of the v1 scope; they
  live on the `v2` branch until a later release.
- **Cross-platform setup.** Compile-verified integration templates for **Android,
  iOS, Web**; validated docs for **Expo**; structural checks for **Flutter,
  Cordova, Capacitor**. Every SDK API in a template was validated against SDK
  source, not memory.
- **A deterministic core** (stdlib-only Python, `scripts/`): exact version
  resolution (never a range), platform detection, secret scanning, and a
  structural self-check (`verify_integration.py`) that runs as the setup skill's
  close-out and as the eval's yardstick.
- **Verification with teeth.** The structural checker has per-platform checks that
  catch the specific ways agents fabricate integrations (wrong `requestPermission`
  call shape, dead `NotificationCenter` wiring, a nonexistent-but-exact version
  pin, missing debug guards). The iOS templates are compile-checked against the
  real iOS SDK; the eval adds offline `swift-typecheck` (iOS) and `ts-typecheck`
  (Capacitor) gates that reject fabricated call shapes.
- **Credentials work today** via the current app-key API path (Apple `.p8`,
  Firebase FCM v1 JSON uploaded for you; console steps guided).
- **MCP credential provisioning (Phase 1, shipped).** The OneSignal MCP exposes
  `provision_app_credentials` for APNs, FCM, and web, and the credentials
  endpoint accepts OAuth bearer tokens alongside app/org keys (additive,
  flag-gated per app). The
  credentials skill prefers the tool when the MCP is connected, gated on an
  App-ID precondition, with a direct-`POST` fallback.
- **An eval harness** (internal repo) with a 3-arm version comparison and
  per-check structural rows. Most recent signal: the plugin's version-pin
  discipline took Android + Expo gating from 0–67% up to 67–100%, and a
  companion-package fabrication bug we caught took Expo from 67% → 100%.

## What's not built

The roadmap and the open decisions live in the internal project tracker, not in this
repository. Two limits are worth stating here because the skills work around them:

- **Confirmed delivery is a guided flow, not a measured metric.** The `verify` skill
  walks the full closed loop (build → subscription → identity → real send →
  server-confirmed delivery), but it needs a device and a human tap.
- **MCP credential provisioning is write-once.** Create-or-update semantics are not
  built. App-key remains the default auth mode.

## How it's proven

The eval harness is the reason to trust any of the above. It runs the
real agent against fixture apps and grades with a mix of build/compile gates,
deterministic structural checks, and LLM judges — with the compile/type gates
kept as the load-bearing signal because judges are noisy at low trial counts. The
guiding principle throughout: a check that can't fail on wrong input is worse than
no check, so every gate ships with a negative control.

## Repos

- **Plugin:** `onesignal-agent-plugin` (this repo) — skills, scripts, templates.
- **Eval:** an internal repo — harness, fixtures, graders, version arms.
- Planning docs for the in-flight cross-repo work are internal.
