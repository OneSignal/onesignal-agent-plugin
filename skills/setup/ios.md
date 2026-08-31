# iOS native integration (OneSignal iOS SDK 5.x)

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file is the iOS install detail. Mirrors `sdk-ai-prompts/docs/ios/integrate.md`. Do not contradict [../../references/platform-matrix.md](../../references/platform-matrix.md).

## What the agent does vs. the human (matrix)

- **Agent (text-editable):** add the SPM package or Podfile line; `OneSignal.initialize(appId, withLaunchOptions:)` in `AppDelegate` / SwiftUI `init()`; `Info.plist` `UIBackgroundModes = remote-notification`; the two `project.pbxproj` build settings; entitlements text; wrapper + debug-only verification helper.
- **Human (Xcode GUI + Apple portal — you CANNOT reliably do these):**
  - Apple Developer portal (paid account): enable the **Push Notifications** capability on the App ID, generate an **APNs `.p8`** key + capture Key ID + Team ID → handed to the **credentials** skill.
  - Xcode GUI: signing, the Push Notifications + Background Modes capability toggles, and — if needed — **Notification Service Extension target creation** (File ▸ New ▸ Target). NSE is NOT reliably text-editable; guide the human.
  - Test on a **physical device** (signing + device trust are Xcode-GUI-bound). A simulator on an Apple-silicon Mac is NOT a human step — the verify skill boots, installs, and launches it headlessly (Xcode 14+ simulators there receive real sandbox APNs pushes; Intel-Mac simulators do not receive remote push). Only the notification permission tap stays with the human.

## NSE is OPTIONAL for a minimal install

A Notification Service Extension is only needed for rich media, confirmed delivery, or badges. OneSignal's own AI prompt skips it for minimal install — so do NOT create an NSE unless the user asks for those features. Keep the integration minimal (safety contract §8).

## Dependency

Detect the existing manager: `Podfile`/`Podfile.lock` → CocoaPods; `Package.swift`/`Package.resolved` or an SPM project → SPM. Match it; don't introduce a second package manager. Resolve the exact version with the script — do not read the feed by hand and do not use a range (on SPM that means an **exact-version** rule, never `upToNextMajorVersion` / `from:`):

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/resolve_sdk_version.py ios --format json   # for the version
${CLAUDE_PLUGIN_ROOT}/scripts/resolve_sdk_version.py ios --format line --line-format podfile   # Podfile line
```

**Swift Package Manager** (smaller XCFramework download — matrix): add package `https://github.com/OneSignal/OneSignal-XCFramework` with an **Exact Version** rule set to the resolver's `version`, and add the **`OneSignalFramework`** library product to the app target (add `OneSignalInAppMessages` / `OneSignalLocation` only if those features are wanted). SPM add is partly GUI — if you cannot edit the pbxproj package references safely, give the human the exact File ▸ Add Packages steps.

**CLI builds + SPM keychain wall:** `xcodebuild`-driven SPM resolution can pop a macOS **login-keychain password prompt** (and re-prompt on Deny), which stalls headless/agent runs. Pass `-scmProvider system` to `xcodebuild` so package fetching uses system git credentials instead of Xcode's keychain-backed SCM.

**CocoaPods** (`Podfile`) — paste the resolver's `--line-format podfile` output verbatim (it is an exact pin), then `pod install`. Shape:
```ruby
pod 'OneSignal/OneSignal', '5.5.1' # onesignal:managed v1
```

## Initialize at launch

`AppDelegate` (UIKit):
```swift
import OneSignalFramework
// in application(_:didFinishLaunchingWithOptions:)
OneSignal.Debug.setLogLevel(.LL_VERBOSE) // remove for production
OneSignal.initialize("YOUR_ONESIGNAL_APP_ID", withLaunchOptions: launchOptions) // onesignal:managed v1
```
SwiftUI `@main` app with no AppDelegate: call the same two lines from the `App`'s `init()` (pass `withLaunchOptions: nil`). Detect which lifecycle the project uses and match it.

## Background Modes — three coordinated edits (verified in upstream ios/integrate.md)

1. Ensure an `Info.plist` exists with:
   ```xml
   <key>UIBackgroundModes</key>
   <array><string>remote-notification</string></array>
   ```
2. Add to the target's Debug AND Release `XCBuildConfiguration` in `project.pbxproj`:
   ```
   INFOPLIST_FILE = "YourApp/Info.plist";
   INFOPLIST_KEY_UIBackgroundModes = "remote-notification";
   ```
3. For Xcode 16+ file-system-synchronized projects (`PBXFileSystemSynchronizedRootGroup`), add a `PBXFileSystemSynchronizedBuildFileExceptionSet` excluding `Info.plist` from the resource copy phase to avoid "Multiple commands produce Info.plist". Skip step 3 for classic `PBXGroup`/`PBXFileReference` projects. The exact pbxproj snippets are in the upstream ios/integrate.md — reproduce them; pbxproj edits are fragile, so preview them precisely in the diff (safety contract §5) and if the format doesn't match, hand the capability toggle to the human via Xcode's Signing & Capabilities tab instead.

Add `aps-environment` to the `.entitlements` file (`development`, or `production` for release). Deployment target iOS 12.0+; do not change it if already set.

## Centralized wrapper (Swift)

Use the template [assets/ios/OneSignalManager.swift.tmpl](assets/ios/OneSignalManager.swift.tmpl) as-is (no substitution needed). Signatures are verified against api-reference "SDK data surface"; `login()` before tags/email/sms. No direct OneSignal calls outside this wrapper except the verification observer.

## Debug-only verification helper (SwiftUI + UIKit)

Use the verified template [assets/ios/OneSignalSetupVerification.swift.tmpl](assets/ios/OneSignalSetupVerification.swift.tmpl) — write it as-is (no substitution needed). Do NOT hand-write this file. It works for both UIKit and SwiftUI apps; call `OneSignalSetupVerification.install()` once from your launch context right after `OneSignal.initialize(...)`. Every API in it is validated against the iOS SDK source and the file is **compile-verified** against the real iOS SDK + a faithful OneSignal stub by `scripts/compile_check_ios.sh` (which also proves the check rejects the fabricated call shapes below). **Use the real observer API** — do NOT wire verification to a `NotificationCenter` event (an eval fabrication: agents listened for a OneSignal registration notification the SDK never posts; it compiles and is functionally dead). The correct surface:
- conform to `OSPushSubscriptionObserver` and implement `func onPushSubscriptionDidChange(state: OSPushSubscriptionChangedState)`; read `state.current.id` (type `String?`).
- register with `OneSignal.User.pushSubscription.addObserver(self)`; also read `OneSignal.User.pushSubscription.id` immediately (race guard).
- `requestPermission` on iOS DOES take a completion block: `OneSignal.Notifications.requestPermission({ accepted in ... }, fallbackToSettings: false)` (unlike Android's suspend form).

The Step-8 structural self-check (`verify_integration.py --platform ios`) enforces `#if DEBUG`, the real push observer (not NotificationCenter), and init in a launch context. Non-negotiable properties (SKILL.md Step 6):
- Guard on `#if DEBUG` so it never ships.
- `OneSignal.Notifications.requestPermission(..., fallbackToSettings: false)` at install — the ONLY permission prompt. `fallbackToSettings` stays `false`: the call runs at launch with no user gesture, and `true` would send a previously-denied user to the Settings app on every debug start.
- Register the observer AND call `evaluate(OneSignal.User.pushSubscription.id)` immediately (race guard).
- `isRegistered` = non-empty AND not `hasPrefix("local-")`.
- `hasLogged` guard; print the subscription ID exactly once, then remove the observer.
- No dialog and no network call — the verify skill confirms the subscription server-side and sends the test push from chat.
- Top-of-file comment naming the file + call site, and saying the file is debug-only and safe to keep.

## Handoffs

- Hand off to **credentials** — iOS push needs the APNs `.p8` (+ Key ID/Team ID) on the OneSignal app, and the human must toggle capabilities + create the NSE in Xcode if rich features are wanted.
- Then **verify** — on an Apple-silicon Mac the verify skill runs the simulator itself; a physical device needs the human to launch from Xcode.
