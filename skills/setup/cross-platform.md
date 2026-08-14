# Cross-platform integration: bare React Native, Flutter, Cordova, Capacitor/Ionic, Unity

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file covers the wrapper frameworks. All of these wrap the native iOS + Android SDKs, so **the iOS-native human column applies in full** (Apple portal `.p8` + Xcode capabilities/NSE for rich features) and Android needs the FCM v1 service-account JSON — see [../../references/platform-matrix.md](../../references/platform-matrix.md), and cross-reference [ios.md](ios.md) / [android.md](android.md) for the native details. Do not contradict the matrix.

Common to all: pin exact versions from https://onesignal.github.io/sdk-releases/releases.json (`channels.stable.version` per SDK entry — never guess, never the human-readable page, never a range/caret); detect the package manager from the lockfile; mark generated blocks `onesignal:managed v1`; init once at app entry; route all calls through one wrapper; drop ONE deletable verification file; then hand off to **credentials** then **verify**. The verified verification-file shape (debug-only guard, observer + immediate ID check, `local-` exclusion, once-guard, "Got it" dialog, unauthenticated self-send with 401 fallback) is identical to the mobile flows in `sdk-ai-prompts/docs/*/integrate.md` — reuse it per framework.

---

## Bare React Native (`react-native-onesignal`)

Detected by `react-native` in `package.json` WITHOUT `expo`. Ask JS vs TS (upstream flow). Full detail: `sdk-ai-prompts/docs/react-native/integrate.md`.

- Install: `npm install react-native-onesignal` (or yarn/pnpm/bun per lockfile), then `cd ios && pod install`.
- Init in `App.tsx`/`App.js`/`index.js` root, before rendering:
  ```ts
  OneSignal.Debug.setLogLevel(LogLevel.Verbose); // remove for production
  OneSignal.initialize('YOUR_ONESIGNAL_APP_ID'); // onesignal:managed v1
  ```
- **iOS side is manual native work** (Xcode): Push + Background Modes capabilities, and NSE + App Group only if rich media/confirmed delivery are wanted (App Group is required if an NSE is added). Guide the human — these are GUI/pbxproj steps (see ios.md).
- **Android side is handled by the plugin** — no manual Gradle/manifest edits documented (matrix). Do NOT add `google-services.json` (the FCM v1 credential is server-side; see android.md).
- Environment prerequisites (upstream, critical): Android needs **JDK 17** (JDK 25+ breaks CMake); RN 0.71+ (0.76+ recommended); New Architecture needs RN 0.79+. State constraints; don't silently upgrade the project.

## Flutter (`onesignal_flutter`)

Detected by `pubspec.yaml`. Full detail: `sdk-ai-prompts/docs/flutter/integrate.md`. Pin the exact Stable from releases.json (Flutter entry) — no caret.

- Add to `pubspec.yaml` dependencies (example — `5.3.5` was Stable at authoring; read the current value from releases.json):
  ```yaml
  dependencies:
    onesignal_flutter: 5.3.5   # onesignal:managed v1 — exact Stable from releases.json
  ```
  then `flutter pub get`; `cd ios && pod install`.
- Init in `main()` BEFORE `runApp()`:
  ```dart
  OneSignal.Debug.setLogLevel(OSLogLevel.verbose); // remove for production
  OneSignal.initialize('YOUR_ONESIGNAL_APP_ID'); // onesignal:managed v1
  ```
- Wrapper: a single `OneSignalService` class (async methods). Signatures per api-reference "SDK data surface"; tag values are strings; `login()` before tags/email/sms.
- Verification file (deletable): guard the whole thing on `kDebugMode` (from `package:flutter/foundation.dart`); register `OneSignal.User.pushSubscription.addObserver((state) {...})` reading `state.current.id`, and read `OneSignal.User.pushSubscription.id` immediately (race guard); `await OneSignal.Notifications.requestPermission(true)` returns `Future<bool>` (like web/RN — NOT iOS's completion block or Android's suspend form). A notification-received listener is not proof of registration — key off the push subscription. The Step-8 self-check (`verify_integration.py --platform flutter`) enforces init in `main()`, the `kDebugMode` guard, and the real push-subscription observer.
- iOS side: same native Xcode human steps as ios.md (Flutter's `ios/` subproject). Android side: the plugin handles Gradle/manifest; do NOT add `google-services.json`. Flutter 3.29+ recommended (matrix).

## Cordova (`onesignal-cordova-plugin`)

Detected by `config.xml` + `cordova` in `package.json`.

- Install: `cordova plugin add onesignal-cordova-plugin` (exact version from releases.json, Cordova entry).
- Init in the `deviceready` handler:
  ```js
  window.plugins.OneSignal.Debug.setLogLevel(6); // verbose; lower for production
  window.plugins.OneSignal.initialize('YOUR_ONESIGNAL_APP_ID'); // onesignal:managed v1
  ```
  Verify the exact JS namespace (`window.plugins.OneSignal` vs an imported module) against the current Cordova SDK reference before finalizing — API surface is authoritative.
- `OneSignal.Notifications.requestPermission(fallbackToSettings?)` returns `Promise<boolean>`; read the subscription id with `OneSignal.User.pushSubscription.getIdAsync()` (the `.id` getter is deprecated) and observe with `pushSubscription.addEventListener('change', (e) => e.current.id)`. The Step-8 self-check (`verify_integration.py --platform cordova`) enforces the package present, init present (near `deviceready`), and that the verification file reads the push subscription (not a notification-received listener).
- Native iOS/Android obligations are the same as the RN wrapper: iOS GUI capabilities (+ NSE for rich features), no `google-services.json` for Android.

## Capacitor / Ionic (`@onesignal/capacitor-plugin`)

Detected by `capacitor.config.{ts,js,json}` or `@capacitor/core` in `package.json`.

- Install: `npm install @onesignal/capacitor-plugin` (per lockfile) then `npx cap sync`.
- **Set `ios.handleApplicationNotifications: false` in `capacitor.config`** (matrix) so OneSignal and Capacitor don't both claim notification callbacks:
  ```json
  { "ios": { "handleApplicationNotifications": false } }
  ```
- Init in the app bootstrap (e.g. `main.ts`/`app.component.ts` for Ionic-Angular, root for React/Vue):
  ```ts
  import OneSignal from '@onesignal/capacitor-plugin';
  OneSignal.initialize('YOUR_ONESIGNAL_APP_ID'); // onesignal:managed v1
  ```
- `OneSignal.initialize(appId)` returns `Promise<void>`; `requestPermission(fallbackToSettings?)` returns `Promise<boolean>`; read the id with `pushSubscription.getIdAsync()` and observe with `pushSubscription.addEventListener('change', ...)`. The Step-8 self-check (`verify_integration.py --platform capacitor`) enforces the package present, init present, `ios.handleApplicationNotifications=false`, and that the verification file reads the push subscription.
- Native obligations same as above (iOS GUI; Android handled by plugin, no `google-services.json`). Run `npx cap sync` after native config changes.

## Unity (`OneSignal.Initialize`)

Detected by a Unity project (`ProjectSettings/`, `Assets/`, `.csproj`). **Low agent-automatability (matrix):** install is GUI-bound (Package Manager / Asset Store) and Player Settings gradle-template toggles are manual. Do NOT attempt to script the package install.

- Guide the human to install the OneSignal Unity SDK via Package Manager, then the code you can write is:
  ```csharp
  OneSignal.Initialize("YOUR_ONESIGNAL_APP_ID"); // onesignal:managed v1 — in a MonoBehaviour Awake/Start
  ```
- Human: Package Manager/Asset Store install, Player Settings (Android gradle template, min API 33+, Unity 2022.3+), plus the usual Apple portal `.p8` and Firebase service-account JSON.
- The debug verification dialog is impractical in Unity's native-UI model — instead have the human confirm a subscription in the dashboard and use a dashboard test send. Be explicit that you cannot fully automate Unity.

---

## Handoffs (all frameworks)

1. **credentials** — iOS `.p8` and Android FCM v1 service-account JSON must be on the OneSignal app or push won't deliver; iOS capabilities/NSE are human Xcode steps.
2. **verify** — activation ladder on a real device (Android device with Google Play Services; iOS physical device or Apple-silicon-Mac simulator).
