# Expo integration (react-native-onesignal + onesignal-expo-plugin)

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file is the Expo install detail. Mirrors `sdk-ai-prompts/docs/react-native-expo/integrate.md`. Do not contradict [../../references/platform-matrix.md](../../references/platform-matrix.md). Expo is the **best mobile automatability** path — the config plugin generates the NSE, entitlements, App Group, and UIBackgroundModes at prebuild, so there is **zero manual Xcode work**.

## Ask up front: JS or TS

Mirror the upstream flow — ask whether the user wants JavaScript or TypeScript integration code, and use that throughout.

## Push does NOT work in Expo Go (matrix + upstream)

State this clearly. The user must make a **development build**:
```bash
npx expo prebuild
npx expo run:ios      # or
npx expo run:android
```
or use EAS Build. Do not tell them push will work in Expo Go.

## What the agent does vs. the human (matrix)

- **Agent:** install packages; add the config plugin to `app.json` (**plugin FIRST in the plugins array**, set `mode`, `smallIcons`); init in `App.tsx`/`_layout.tsx`; wrapper + verification helper.
- **Human:** procure Apple **`.p8`** + Firebase **service-account JSON**; ensure EAS credentials match; run the dev build. Push credentials → **credentials** skill.

## Install

```bash
npx expo install react-native-onesignal onesignal-expo-plugin
```
Use `npx expo install` (it aligns versions to the Expo SDK). If pinning explicitly, do **not** read versions by hand and do **not** guess the plugin version — run the resolver (SKILL.md Step 2) and paste both exact pins verbatim:
```
scripts/resolve_sdk_version.py expo --format json
```
It emits `dependency_line` for `react-native-onesignal` **and** `companion.line` for `onesignal-expo-plugin` (resolved from the `Expo` entry in releases.json; the plugin has no `stable` channel, so the resolver falls back to `current`). Copy both lines as-is into `package.json`. Never invent a companion version — a nonexistent exact pin (e.g. `onesignal-expo-plugin@3.0.0`) passes the range check but fails `expo prebuild` with npm `ETARGET`. No ranges/carets.

## app.json / app.config.js

Requires `expo.ios.bundleIdentifier` and `expo.android.package` set (prebuild fails without them — ask the user if missing; do not invent a bundle id). Add the plugin as the FIRST entry of `plugins`:
```json
{
  "expo": {
    "ios": { "bundleIdentifier": "com.yourcompany.yourapp" },
    "android": { "package": "com.yourcompany.yourapp" },
    "plugins": [
      ["onesignal-expo-plugin", { "mode": "development" }]
    ]
  }
}
```
Plugin options (matrix + upstream): `mode` (`development`/`production`), `devTeam` (iOS Apple Team ID, needed for physical-device builds), `iPhoneDeploymentTarget`, `smallIcons`/`largeIcons` (Android). Requires Expo SDK 53+ / RN 0.79+ for New Architecture (matrix); if the project is older, note the constraint rather than force-upgrading (safety contract §7 — no unrelated bumps).

## Initialize in the root component

Match the routing style: classic `App.tsx`/`App.js`, or `app/_layout.tsx` for expo-router. Init once at mount:
```ts
import { OneSignal, LogLevel } from 'react-native-onesignal';
// in a top-level effect / module init
OneSignal.Debug.setLogLevel(LogLevel.Verbose); // remove for production
OneSignal.initialize('YOUR_ONESIGNAL_APP_ID'); // onesignal:managed v1
```

## Centralized wrapper

Route all OneSignal calls through one module (signatures per api-reference "SDK data surface"; tag values are strings; `login()` before tags/email/sms):
```ts
import { OneSignal } from 'react-native-onesignal';
export const OneSignalWrapper = { // onesignal:managed v1
  login: (id: string) => OneSignal.login(id),
  logout: () => OneSignal.logout(),
  addEmail: (email: string) => OneSignal.User.addEmail(email),
  addSms: (e164: string) => OneSignal.User.addSms(e164),
  addTag: (k: string, v: string) => OneSignal.User.addTag(k, String(v)),
};
```
The upstream file also offers Context-Provider and custom-hook patterns — use one only if it matches the app's existing state architecture.

## Debug-only verification helper

The APIs below are validated against the `react-native-onesignal` source: `OneSignal.User.pushSubscription.getIdAsync(): Promise<string|null>`, `.addEventListener('change', ...)`, and `OneSignal.Notifications.requestPermission(fallbackToSettings): Promise<boolean>` (a single boolean argument is the correct call shape — there is no callback overload). The Step-8 structural self-check (`verify_integration.py --platform expo`) enforces the config plugin is registered (and first), the packages and init are present, and the verification helper is `__DEV__`-guarded. Non-negotiable properties (SKILL.md Step 6):
- Guard so it runs only in dev (`__DEV__`).
- `OneSignal.Notifications.requestPermission(false)` at install — the ONLY permission prompt. `fallbackToSettings` stays `false`: the call runs at launch with no user gesture, and `true` would send a previously-denied user to the OS Settings screen on every dev start.
- Register `OneSignal.User.pushSubscription.addEventListener('change', ...)` AND immediately resolve `OneSignal.User.pushSubscription.getIdAsync()` (race guard).
- `isRegistered` = truthy AND not `startsWith('local-')`.
- Log the subscription ID exactly once (a logged-once guard) — no `Alert`, no in-app prompt, no network call. The verify skill confirms the subscription server-side and sends the test push from chat.
- Top-of-file comment naming the file + call site, and saying the file is dev-only and safe to keep.
Import the installer once from the root component and note the exact call site in the summary.

## Handoffs

- **credentials** for the Apple `.p8` and Firebase service-account JSON.
- **verify** on a real dev build — physical device, or an Apple-silicon-Mac simulator (receives real sandbox APNs pushes).

## Troubleshooting (from upstream)

Push silent in Expo Go → make a dev build. Missing `bundleIdentifier`/`package` errors → set them in `app.json`. Pod errors → `npx expo prebuild --clean`. Android Gradle → ensure JDK 17.
