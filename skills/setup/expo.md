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

- **Agent:** install packages; add the config plugin to `app.json` (**plugin FIRST in the plugins array**, set `mode`, `smallIcons`); init in `App.tsx`/`_layout.tsx`; wrapper + verification file.
- **Human:** procure Apple **`.p8`** + Firebase **service-account JSON**; ensure EAS credentials match; run the dev build. Push credentials → **credentials** skill.

## Install

```bash
npx expo install react-native-onesignal onesignal-expo-plugin
```
Use `npx expo install` (it aligns versions to the Expo SDK). If pinning explicitly, read exact versions from https://onesignal.github.io/sdk-releases/releases.json (React Native entry for `react-native-onesignal`; note the Expo plugin has **no stable channel** in releases.json — prefer the `expo install` alignment for it). No ranges/carets.

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

## Deletable verification file

Use the verified JS/TS observer from `sdk-ai-prompts/docs/react-native-expo/integrate.md`. Non-negotiable properties (SKILL.md Step 6):
- `isRegistered` = truthy AND not `startsWith('local-')`.
- Register `OneSignal.User.pushSubscription.addEventListener('change', ...)` AND immediately resolve `OneSignal.User.pushSubscription.getIdAsync()` (race guard).
- `dialogShown` once-guard; `Alert.alert` "Your OneSignal SDK integration is complete!" with a single **"Got it"** button.
- On tap → `OneSignal.Notifications.requestPermission(true)` (the ONLY permission prompt) → prompt/body → unauthenticated `POST https://api.onesignal.com/notifications` with `include_subscription_ids` (no Authorization header; on 401 fall back to a dashboard/REST-key send).
- Guard so it runs only in dev (`__DEV__`) and top-of-file comment naming the file + call site to delete.
Import the installer once from the root component and note the exact call site for the cleanup summary.

## Handoffs

- **credentials** for the Apple `.p8` and Firebase service-account JSON.
- **verify** on a real dev build — physical device, or an Apple-silicon-Mac simulator (receives real sandbox APNs pushes).

## Troubleshooting (from upstream)

Push silent in Expo Go → make a dev build. Missing `bundleIdentifier`/`package` errors → set them in `app.json`. Pod errors → `npx expo prebuild --clean`. Android Gradle → ensure JDK 17.
