# OneSignal agent-plugin — hardening & strategy handoff

Self-contained plan for continuing this work in a fresh session. Written because
context saturated after a long hardening session + a competitive analysis. Read
§0 first (there's a run in flight), then §1 for state, then pick from §3
(finish the platforms) and §4 (strategic moves). §8 preserves validated SDK API
surfaces so you never re-derive them.

Untracked by default — commit it into the plugin repo if you want it in history.

---

## §0. Read first — run in flight

A 3-arm × 3-case version comparison is running in the eval repo
(`/Users/kalleypowell/src/OS/general-eval`), PID `52417` (orchestrator
`run-comparison.sh`). It compares plugin **v0.1.0 / v0.2.0 / v0.3.0** on
`android-sdk-plugin`, `web-sdk-plugin`, `expo-sdk-plugin`, 3 trials each.

```sh
ps -p 52417 -o pid,etime,command                       # alive?
grep '^>>>' /Users/kalleypowell/src/OS/general-eval/comparison-run.log   # progress
cat /Users/kalleypowell/src/OS/general-eval/comparison-summary.txt        # final table (per-check pass rates, timing, cost)
```

**Interpretation:** `android-bare` is now modern (Kotlin 2.2.20), so v0.1.0's
version *range* builds green there — the Kotlin-floor failure is gone. So the
build and `no_version_range` will NOT separate the arms; the differentiator is
the **structural per-check rows** (verification guard, `requestPermission` not
faked, worker-is-importscripts, expo plugin-first, etc.) and the judge means.
Let the actual numbers steer which platform to deepen first.

---

## §1. Where things stand

Two repos:
- **Plugin** `/Users/kalleypowell/src/onesignal-agent-plugin` — git-versioned this session.
- **Eval** `/Users/kalleypowell/src/OS/general-eval` — pre-existing git; harness + fixtures + version arms.

### Per-platform hardening status

| Platform | Version pin | Templates | Structural checks | Build-proven |
|---|---|---|---|---|
| Android | ✅ resolver | ✅ wrapper/Application/verification (compiled green on Kotlin 2.2 fixture) | ✅ 6 checks | ✅ |
| Web | ✅ resolver | ✅ worker + init | ✅ 3 checks | grader is file-contains + judge |
| iOS | ✅ resolver | ✅ wrapper + verification (compile-verified: `scripts/compile_check_ios.sh`, real iOS SDK + OneSignal stub) | ✅ 3 checks (catch the fabrications) | Swift typecheck ✓; pbxproj/GUI still human |
| Expo / React Native | ✅ resolver | docs validated; no template | ✅ checks (+ companion-pin fix) | eval case |
| Flutter / Cordova / Capacitor / Unity | ✅ resolver | ✗ | ✗ | ✗ |

### Commits this session

Plugin: `fa5bccb` init v0.3.0 · `96b5a57` comment-aware requestPermission fix +
template compiles · `c8cb298` web hardening · `ea5dfc4` iOS detection hardening ·
`c574ec0` expo/RN + latent-bug fixes.

Eval: `8ac8f10` harness + modern/legacy fixtures · `8c21607` yardstick web ·
`06b1173` yardstick iOS + case · `5e5662a` yardstick expo + case.

### The machinery (so you know what exists)

Deterministic sandwich = deterministic inputs (front) + LLM (fill) + deterministic
self-check (back). Plugin `scripts/`:
- `resolve_sdk_version.py <platform>` — exact pin per platform; **cannot emit a range**.
- `detect_platform.py` — platform/language/pkg-mgr + prior-install, monorepo-aware, read-only.
- `onesignal_api.py` — API probes (web-probe cache-bust, android-params poll, notification-stats, subscribers, app).
- `discover_scan.py` — grep cookbook with guardrails.
- `scan_secrets.py` — secret-diff scan (Step 8).
- `android_kotlin_check.py` — derive the real Kotlin floor from the resolved dep graph.
- `verify_integration.py` — the back-slice structural self-check; also the eval's `structural` grader (constant yardstick copy at `general-eval/tools/verify_integration.py`).

`verify_integration.py` checks today:
- universal: `no_version_range`, `managed_marker_present`, `no_placeholder_app_id`, `app_id_present`, `no_deprecated_addoutcome`, `no_committed_secrets`
- android: `init_in_application`, `manifest_registers_app`, `no_stray_google_services`, `verification_debug_guarded`, `requestpermission_not_callback`, `buildconfig_feature_enabled`
- ios: `init_in_launch`, `verification_debug_guarded`, `verification_uses_push_observer`
- web: `worker_is_importscripts`, `init_present`, `page_sdk_v16`
- expo/react-native: `expo_plugin_registered`, `expo_plugin_first`, `rn_package_present`, `rn_init_present`, `rn_verification_dev_guarded`

Setup skill runs `verify_integration.py` as its Step-8 close; all four plugin
eval cases carry the `structural` grader (advisory).

---

## §2. The strategic frame (from the competitive analysis)

The analysis (PostHog/Sentry/Customer.io the real reference points) said, bluntly:
our deterministic-sandwich architecture is **table-stakes, not a differentiator**,
and we do one thing PostHog explicitly regrets (embedding docs). The two
under-served things — and where OneSignal can actually win — are **closed-loop
delivery verification** and **deterministic native install**. We are already
near both. §4 turns that into moves. Treat competitor *metrics* as directional
(vendor-self-reported); the *architectural patterns* are the durable signal.

---

## §3. Finish the platforms (tactical continuation)

Priority within this section follows the run's findings (§0). Each platform uses
the same recipe: **curl the SDK source to validate every API (never trust
memory/agent output), add `verify_integration.py` checks, then a template only
once it's compile-verifiable.**

1. **iOS Swift verification template.** ✅ DONE. Shipped
   `assets/ios/OneSignalSetupVerification.swift.tmpl` (+ `OneSignalManager.swift.tmpl`),
   wired into `ios.md`. Compile path: `scripts/compile_check_ios.sh` typechecks
   against the real iphonesimulator SDK (UIKit) + a faithful OneSignal stub built
   from §8.1; negative controls prove it rejects the web/RN `requestPermission(true)`
   and Android suspend/`await` fabrications. The remaining iOS gap is native
   pbxproj/entitlements/capabilities (still human) — that is §4 Move 2, not this.
2. **Flutter / Cordova / Capacitor** checks + templates. Validate each SDK's
   observer/permission API from source first (§8.4 has the pointers), add checks
   (init present, exact pin, verification guard), then templates. Packages:
   `onesignal_flutter`, `onesignal-cordova-plugin`, `@onesignal/capacitor-plugin`.
   - **Flutter**: ✅ checks added (`flutter_init_present` [in `main()`],
     `flutter_verification_debug_guarded` [`kDebugMode`],
     `flutter_verification_uses_push_observer` [`pushSubscription.addObserver`/`.id`]),
     registered in `by_platform`, positive/negative tested, yardstick synced,
     `cross-platform.md` updated. API validated against source (§8.6). **Template
     DEFERRED**: no Dart/Flutter toolchain on this machine, and a Flutter
     verification file needs the framework (dialog UI + `kDebugMode`) to analyze,
     not just standalone Dart — same "template only when compile-verifiable" gate
     that held iOS. Installing Flutter (~1GB+) is a user decision (§6).
   - **Cordova / Capacitor**: not started. Same recipe.
3. **Extend `verify_integration.py`** platform maps for the above (add entries to
   the `by_platform` dict in `Checks.run`).
4. **Unity** — GUI-bound, low agent-automatability; checks only, low priority.

### §3.5 Compile-gating in the eval (assessed this session)

Goal: every shipped template is gated by a *real* build/analyze in an eval case,
not just my out-of-band `compile_check_ios.sh`. Findings after reading the eval
harness:

- **Harness cost = ~zero.** `src/graders/command.ts` already runs arbitrary
  `sh -c <cmd>` in the workspace with a `timeoutMs`. Android's `gradle-build` and
  expo's `expo-prebuild` graders are just `command` entries in `case.yaml`. A new
  toolchain-gated grader is a one-line `command:` block — no TS changes. Fixtures
  live in `general-eval/fixtures/{android-bare,expo-bare,ios-bare,web-bare,...}`;
  a case is `cases/<id>/{case.yaml,prompt.md}` with `fixture:` + `graders:`.
- **Flutter (low–medium, blocked on toolchain install).** Add `fixtures/flutter-bare`
  (`flutter create` skeleton), `cases/flutter-sdk-plugin/`, and a grader
  `command: flutter pub get && flutter analyze`. `flutter analyze` resolves the
  REAL `onesignal_flutter` and fails on a wrong call shape / arity — a genuine
  compile-equivalent, *better* than the iOS stub (real package, not a stub). Only
  blocker: install Flutter SDK (~1GB) on the eval host. `flutter build apk` is the
  heavier variant (reuses the android arm's JDK/Android SDK).
- **iOS (medium, NO new toolchain — Xcode already present).** Don't stand up full
  `xcodebuild` linking the XCFramework (needs a real pbxproj, SPM/pod resolution,
  the keychain wall in `ios.md`, a simulator, network — heavy + flaky headless).
  Instead generalize `scripts/compile_check_ios.sh` into a `command` grader that
  typechecks the *agent's workspace* `*.swift` against the §8.1 OneSignal stub +
  real UIKit (`xcrun --sdk iphonesimulator swiftc -typecheck`). No network, no
  project, no simulator. This catches an agent that fabricates the call shape in
  its OUTPUT at eval time — the Android-class bug — which the current ios case
  (file-contains + judge + advisory structural) does not. Highest value/effort
  ratio here.
- **Recommended order:** (1) iOS stub-typecheck command grader — no install, high
  value; (2) Flutter `flutter analyze` grader once someone OKs the SDK install;
  (3) skip full native `xcodebuild` unless Move 2 (deterministic iOS native) is
  greenlit. Cordova/Capacitor are JS — their analog is `npx cap sync` / a tsc/lint
  command grader, cheap, toolchain already here.

---

## §4. Strategic moves (from the analysis) — prioritized by leverage

### Move 1 — Headline verification (closed delivery loop). HIGHEST LEVERAGE.
The field's biggest gap; we're ~70% there. We have the `verify` skill,
`onesignal_api.py` probes, a self-sending verification file, and the activation
ladder (SDK → subscription → external_id → **delivered**).
- Emit a machine-readable **ProvisioningResult** (extend `verify_integration.py`'s
  `verdict` JSON + fold in `onesignal_api` delivery state) so agents branch on it.
- Build toward the north-star metric competitors can't measure: **confirmed-
  test-event / delivered-push rate**. In the eval this needs a device/sandbox or
  a mocked delivery path — hard headless; scope a real APNs/FCM sandbox harness
  or a recorded-fixture delivery check.
- Reframe the plugin narrative around "we prove push actually arrives," not "we
  pin versions."
- Effort: medium.

### Move 2 — Deterministic iOS native (the moat). BIGGEST DIFFERENTIATION.
Native (pbxproj/Gradle/entitlements/APNs) is least commoditized; we currently
punt pbxproj to the human in `ios.md`.
- Adopt the **`xcode` npm package** (Sentry's choice) for deterministic pbxproj
  mutation (UIBackgroundModes, capabilities, INFOPLIST). This is node, so it
  belongs in the envoy CLI (Move 4), not the python scripts — do 2 and 4 together.
- Also use `xml-js` for `Info.plist`/`AndroidManifest.xml`, `@expo/config-plugins`
  + CNG for RN/Expo (sanctioned), scoped Gradle regex (we already do this in
  `android_kotlin_check`).
- Compile-verify the iOS template (§3.1).
- Effort: high (needs iOS build tooling). Gate on: is native >~30% of installs?

### Move 3 — Regenerate reference docs from source (kill rot, keep fidelity).
We embed `android.md`/`ios.md`/`api-reference.md`/`platform-matrix.md` — PostHog's
regretted mistake — but FINDINGS proved embedded beats fetch-summarized for exact
strings. Synthesis: **generate the embedded facts from source at build time.**
- Write `scripts/regen_reference.py`: curl the SDK sources + `releases.json`,
  regenerate API-signature / version-example / dialog-string sections between
  markers. Run pre-release / in CI. Keeps fidelity AND freshness.
- We already live-fetch versions and validated APIs against source this session —
  formalize that into the regen step.
- Effort: medium.

### Move 4 — `npx` envoy CLI + MCP setup tool + model-ownership decision.
We're a Claude Code plugin (agent-invoked only); a terminal-first dev gets
nothing. Scripts are harness-agnostic python3.
- Wrap the deterministic logic as `npx @onesignal/wizard` (node shelling python,
  or port the hot scripts to node) AND expose the same as an MCP `setup` tool —
  the dual surface PostHog/Sentry (CLI) and Customer.io (MCP) each do only half.
- Adopt PostHog's health-check pre-gate (abort before the LLM if a dep is down)
  and a secret scanner on the model boundary (we have `scan_secrets.py`).
- **DECIDE model ownership** (unmade): (a) local coding agent = model — cheapest,
  agent-only, our current implicit choice; (b) hosted gateway (PostHog ~$6.67/
  user) — quality/version control, you eat inference; (c) MCP server-side — also
  solves the python3-on-customer-machine portability problem. This is the same
  fork as the "customer-facing version" discussion, now with price data.
- Effort: high.

---

## §5. Hard-won rules — keep these

- **Validate every SDK API against source before templating.** This session
  caught a fabricated Android `requestPermission(cb){}` (a suspend fn with no
  callback overload — didn't compile) that the structural grader had waved
  through. Curl the SDK repo; don't trust agent output or memory.
- **Template only when compile-verifiable.** Android was proven by building on the
  Kotlin 2.2 fixture. iOS is now proven by `scripts/compile_check_ios.sh`
  (real iphonesimulator SDK + a faithful OneSignal stub from §8.1, with negative
  controls that must reject fabricated call shapes).
- **Pin the companion package too, from the resolver.** The comparison run caught
  an expo trial fabricating `onesignal-expo-plugin@3.0.0` (does not exist →
  `expo prebuild` fails with npm ETARGET). The agent pinned the main SDK from the
  resolver but free-handed the companion. The resolver already emits
  `companion.line`; `expo.md` now forces the agent to copy it verbatim. Same class
  as the Android fabrication: a nonexistent *exact* pin passes `no_version_range`
  — the range check can't catch "valid pin, wrong number."
- **Structural grader stays advisory in cases** so gating pass-rate is comparable
  across arms; per-check data lands in `subChecks`.
- **Fixtures:** `android-bare` = modern happy path (Kotlin 2.2.20, buildConfig on);
  `android-legacy-kotlin` = 1.9.24 floor case (verify the plugin *surfaces* the
  Kotlin bump, never silently bumps). Fixtures double as PostHog-style reference
  apps.
- `verify_integration.py` scans `.json` (needed for package.json/app.json + npm
  ranges). The npm-range regex was malformed and silently never fired until fixed
  this session — re-test any regex check against a real positive AND negative.
- **macOS is bash 3.2** — no associative arrays / `declare -A`; `run-comparison.sh`
  derives arm dirs from names instead.
- Verify a backgrounded run actually launched (check the process), don't trust a
  foreground "launched PID" echo — a `bash -n` parse failure earlier let the echo
  print while nothing ran.

---

## §6. Open decisions for the user

- **`no_version_range` false-positive on prebuilt Expo `ios/`+`android/`.** After
  the companion-pin fix, the expo re-run went gating 67%→100% (all 3 trials pin
  `onesignal-expo-plugin@2.7.0`, no ETARGET; verified 2026-07-29, result
  `general-eval/results/2026-07-29T20-46-31-346Z.json`, label `v0.3.0-fix`). But
  one trial's *advisory* structural still failed `no_version_range` — the hit is
  `ios/Podfile` `pod 'OneSignalXCFramework', '>= 5.0', '< 6.0'`, which the
  `onesignal-expo-plugin` **generates** during `expo prebuild` (CNG), not agent-
  authored. `verify_integration.py`'s `EXCLUDE` skips `Pods/`/`build/`/`DerivedData/`
  but not the prebuild-generated native roots. Same verifier is the setup skill's
  Step-8 self-check on real projects, so a user who prebuilds gets a spurious
  *error*. Fix = skip generated `ios/`+`android/` for Expo/CNG projects — but the
  scoping is a judgment call (Expo CNG regenerates them; a *bare* RN project's
  native dirs are hand-authored and should still be scanned). Touches the shared
  yardstick → needs the §7 sync dance. Deferred for a decision.
- **Plugin copies in the eval** (`general-eval/plugins/versions/*` + bundled
  `plugins/onesignal-agent-plugin`) duplicate the versioned plugin repo. Gitignore
  as disposable arms, or keep committed as pinned versions? (Unresolved.)
- **Model ownership** (Move 4).
- **Invest in iOS native?** (Move 2) — gate on native install share.
- **North-star metric**: adopt confirmed-delivery rate (Move 1)?

---

## §7. Command reference

Version comparison (spends real subscription + minutes; each arm builds):
```sh
cd /Users/kalleypowell/src/OS/general-eval
npm run eval <case> -- --plugin-dir "$(pwd)/plugins/versions/onesignal-<ver>" --label v<ver> --trials 3
npm run report results/<a>.json results/<b>.json results/<c>.json
# or the whole matrix, backgrounded:
nohup bash run-comparison.sh > comparison-nohup.log 2>&1 &
```
- Structural per-check rows are the real signal; gating pass-rate is noisy.
- Hold the judge model constant across arms.
- `npm run typecheck` is the gate after any TS change (no test suite).
- Env for Android builds: `JAVA_HOME` = Android Studio JBR (Java 21);
  `run-comparison.sh` exports it so the agent inherits it.

Verifier (also the eval yardstick):
```sh
scripts/verify_integration.py <project_dir> --platform <p> [--app-id UUID]
```
When you change it, sync the copy the eval grades with:
`cp scripts/verify_integration.py /Users/kalleypowell/src/OS/general-eval/tools/verify_integration.py`
and into any arm you want to re-measure (`.../plugins/versions/onesignal-0.3.0/scripts/`).

---

## §8. Validated SDK API surfaces (don't re-curl these)

All confirmed against the SDK source this session.

### §8.1 iOS (OneSignal-iOS-SDK, ObjC headers + OneSignalUser Swift)
- `OneSignal.initialize(_ appId: String, withLaunchOptions:)`; `OneSignal.login(_:)`
- `OneSignal.User.pushSubscription: OSPushSubscription`
- `protocol OSPushSubscription { var id: String?; var token: String?; var optedIn: Bool; func optIn(); func optOut(); func addObserver(_ observer: OSPushSubscriptionObserver); func removeObserver(...) }`
- `protocol OSPushSubscriptionObserver { func onPushSubscriptionDidChange(state: OSPushSubscriptionChangedState) }`
- `class OSPushSubscriptionChangedState { let current: OSPushSubscriptionState; let previous: OSPushSubscriptionState }`
- `class OSPushSubscriptionState { let id: String?; let token: String?; let optedIn: Bool }`
- `OneSignal.Notifications.requestPermission({ accepted in ... }, fallbackToSettings: true)` — **completion block** (ObjC `requestPermission:(OSUserResponseBlock)block fallbackToSettings:(BOOL)`). NOT suspend, NOT Promise.
- Verification: `#if DEBUG` guard; present `UIAlertController` from the key window's rootViewController; title "Your OneSignal SDK integration is complete!"; single button "Got it"; registered ⇔ id non-nil, non-empty, not `hasPrefix("local-")`.

### §8.2 React Native / Expo (react-native-onesignal, src/index.ts)
- `OneSignal.initialize(appId)`
- `OneSignal.Notifications.requestPermission(fallbackToSettings: boolean): Promise<boolean>` — `requestPermission(true)` is correct.
- `OneSignal.User.pushSubscription.getIdAsync(): Promise<string | null>` (`.id` getter is deprecated → use getIdAsync)
- `OneSignal.User.pushSubscription.addEventListener('change', (change) => …)` (`change.current.id`)
- Verification guard: `__DEV__`.

### §8.3 Web (OneSignal-Website-SDK, src/onesignal)
- `OneSignal.Notifications.requestPermission(): Promise<boolean>` (no args)
- `OneSignal.User.PushSubscription.id: string | null | undefined`
- `OneSignal.User.PushSubscription.addEventListener('change', (change: SubscriptionChangeEvent) => void)`
- Worker (copy verbatim, do NOT download): `importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js");`
- Init: `OneSignalDeferred.push(async (OneSignal) => await OneSignal.init({ appId }))` + `<script src=".../v16/OneSignalSDK.page.js" defer>`.

### §8.4 Android (OneSignal-Android-SDK) — already templated
- `OneSignal.initWithContext(context, appId)`; `login/logout`; `User.addEmail/addSms/addTag`
- `IPushSubscriptionObserver.onPushSubscriptionChange(state: PushSubscriptionChangedState)`; `state.current.id`
- `OneSignal.User.pushSubscription.addObserver(...)`, `.id`
- `OneSignal.Notifications.requestPermission(true)` is **suspend** — call from a coroutine (`CoroutineScope(Dispatchers.Main).launch { ... }`). This is the opposite of iOS (block) and web (Promise) — do not conflate.
- Kotlin floor: the OTel submodule drags `kotlin-stdlib` 2.2.x transitively → host needs Kotlin ≥ ~2.1 (compiler reads one minor ahead). `BuildConfig.DEBUG` needs `android { buildFeatures { buildConfig = true } }` (AGP 8+ default off). Both enforced by `verify_integration.py` + `android_kotlin_check.py`.

### §8.5 Feed
- `https://onesignal.github.io/sdk-releases/releases.json` — version source of
  truth. Web ships a build number behind fixed CDN major `v16`; Expo plugin's
  `stable` is null (use `current` / `expo install` alignment).

### §8.6 Flutter (OneSignal-Flutter-SDK, lib/src) — checks added, template deferred
Validated against source this session (`lib/onesignal_flutter.dart`, `src/pushsubscription.dart`, `src/notifications.dart`, `src/subscription.dart`, `src/debug.dart`):
- `OneSignal.initialize(String appId): Future<void>` — call in `main()` before `runApp()`.
- `OneSignal.Notifications.requestPermission(bool fallbackToSettings): Future<bool>` — so `await requestPermission(true)`. **Like web/RN (Future), NOT iOS's block, NOT Android's suspend.**
- `OneSignal.User.pushSubscription.id: String?` (getter); `.token: String?`; `.optedIn: bool`.
- Observer is a **function typedef**, not a class: `typedef void OnPushSubscriptionChangeObserver(OSPushSubscriptionChangedState state)`; register with `OneSignal.User.pushSubscription.addObserver((state) { state.current.id })`.
- `OSPushSubscriptionChangedState { current, previous: OSPushSubscriptionState }`; `OSPushSubscriptionState { id: String?, token: String?, optedIn: bool }`.
- `OneSignal.Debug.setLogLevel(OSLogLevel.verbose)`; `enum OSLogLevel { none, fatal, error, warn, info, debug, verbose }`.
- Verification guard: `kDebugMode` (from `package:flutter/foundation.dart`).
