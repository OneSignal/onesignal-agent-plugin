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

## What's in flight (planned or spiked — NOT built yet)

Be precise about these when you share — they're direction, not features:

- **"We prove push arrives" as a *measured* metric.** The `verify` skill already
  walks the full closed loop (build → subscription → identity → real send →
  server-confirmed delivery) as a *guided* flow needing a device + a human tap.
  Turning that into a headless, eval-measured **confirmed-delivery rate** is
  **spiked, not built** — the spike doc lives in the internal eval repo.
  This is the intended differentiator.
- **MCP credential provisioning — remaining phases.** Phase 1 shipped (see
  "What's real today"). **Not** built yet: create-or-update/replace semantics and
  the OAuth-only lockdown (later server-side phases), after which app-key support
  is retired. Until then provisioning stays write-once, and app-key remains the
  default auth mode.
- **Flutter template + `flutter analyze` eval gate** — blocked on installing the
  Flutter SDK.
- **Cursor / Codex adapters and non-optional hooks** (verify + secret-scan) —
  identified, not started.
- **`npx` CLI** — blocked on the model-ownership decision (see Open decisions below).

## Where it's going (roadmap, in leverage order)

1. **Close the delivery loop** — make confirmed-delivery a real eval metric (the
   spike above is step one). Highest leverage; it's the thing competitors don't do.
2. **Finish the OAuth credential path** — Phase 1 (the MCP tool + OAuth
   acceptance) shipped; what remains is replace/create-or-update semantics and
   the OAuth-only lockdown (later server-side phases), then retiring app-key
   support.
3. **Regenerate reference docs from source** — kill doc rot while keeping the
   exact-string fidelity embedded docs give us.

## How it's proven

The eval harness is the reason to trust any of the above. It runs the
real agent against fixture apps and grades with a mix of build/compile gates,
deterministic structural checks, and LLM judges — with the compile/type gates
kept as the load-bearing signal because judges are noisy at low trial counts. The
guiding principle throughout: a check that can't fail on wrong input is worse than
no check, so every gate ships with a negative control.

## Open decisions (need a human, not code)

Model ownership for a standalone CLI; whether to invest in deterministic iOS
*native* (pbxproj) work; adopting confirmed-delivery as the north-star metric; and
a couple of eval-hygiene calls.

## Repos

- **Plugin:** `onesignal-agent-plugin` (this repo) — skills, scripts, templates.
- **Eval:** an internal repo — harness, fixtures, graders, version arms.
- Planning docs for the in-flight cross-repo work are internal.
