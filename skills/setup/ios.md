# iOS native integration (OneSignal iOS SDK 5.x)

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file is the iOS install detail. `<plugin>` in the commands below is the directory SKILL.md defines: walk up from this file's directory to the first directory that contains a `scripts/` folder. Mirrors `sdk-ai-prompts/docs/ios/integrate.md`. Do not contradict [../../references/platform-matrix.md](../../references/platform-matrix.md).

## What the agent does vs. the human (matrix)

- **Agent (text-editable):** add the SPM package products or Podfile lines; `OneSignal.initialize(appId, withLaunchOptions:)` in `AppDelegate` / SwiftUI `init()`; `Info.plist` `UIBackgroundModes = remote-notification`; the two `project.pbxproj` build settings; entitlements text (`aps-environment` + the App Group); the Notification Service Extension folder (3 template files) and its pbxproj target objects ([ios-nse.md](ios-nse.md)); wrapper + debug-only verification helper.
- **Human (Xcode GUI + Apple portal — you CANNOT reliably do these):**
  - Apple Developer portal (paid account): enable the **Push Notifications** capability on the App ID, generate an **APNs `.p8`** key + capture Key ID + Team ID → handed to the **credentials** skill.
  - Xcode GUI: signing and the Push Notifications + Background Modes capability toggles. **Notification Service Extension target creation** (File ▸ New ▸ Target) only as the fallback when the pbxproj format does not match the shapes in ios-nse.md.
  - Test on a **physical device** (signing + device trust are Xcode-GUI-bound). A simulator on an Apple-silicon Mac is NOT a human step — the verify skill boots, installs, and launches it unattended (Xcode 14+ simulators there receive real sandbox APNs pushes; Intel-Mac simulators do not receive remote push). Only the notification permission tap stays with the human.

## NSE and App Group are part of the default install

Images, action buttons, badge counts, and confirmed delivery all run inside the **Notification Service Extension**, and the extension reads the SDK's state through an **App Group** shared with the main app. A plain push still arrives without them, so a missing extension fails silently. Create both on every native iOS install (safety contract §8 names them as part of the minimal integration); the recipe is [ios-nse.md](ios-nse.md). Add nothing else by default: no in-app message code, no notification-click handler, and location only when the user opts in ("Ask about `OneSignalLocation`" below).

## Which SDK products

The documentation names 4 SPM products and the pod subspecs that match them. The default set, and the reason for each:

| Product | Target | Default |
|---|---|---|
| `OneSignalFramework` | app | yes — the SDK |
| `OneSignalInAppMessages` | app | yes — the documentation recommends it; the module needs no code and no permission, and in-app messages sent from the dashboard render only when it is linked |
| `OneSignalExtension` | `OneSignalNotificationServiceExtension` | yes — the NSE links it ([ios-nse.md](ios-nse.md)) |
| `OneSignalLocation` | app | **ask** — the documentation marks it optional. It shares the device's location with OneSignal and needs a location permission, so add it only when the user opts in (below) |

### Ask about `OneSignalLocation` (once, before the Step-5 diff)

Skip the question in 2 cases:

- The user's request already says to add location (treat it as yes) or not to add it (treat it as no).
- A prior install already links `OneSignalLocation`. Keep it and do not ask.

Otherwise ask one structured question (safety contract §14) before you compute the Step-5 diff, so the one approval covers the final change set. If another question is open at that point, put both in the same tool call. Say in one sentence what the module does: OneSignal stores the device's latitude and longitude for location-based segments, after the user grants a location permission. Ask "Do you also want location tracking (the optional `OneSignalLocation` module)?" with these 2 options, in this order and with no "(Recommended)" label:

1. "No, push only"
2. "Yes, add location"

**On yes**, add these 3 changes to the Step-5 change set:

1. The module. SPM: the `OneSignalLocation` product on the app target, from the same package and exact version. CocoaPods: the resolver's `--line-format podfile-location` line in the app target.
2. The usage description. Add `NSLocationWhenInUseUsageDescription` to the app's `Info.plist`; on a project that sets Info.plist keys as build settings, add `INFOPLIST_KEY_NSLocationWhenInUseUsageDescription` to Debug and Release instead. Without it, iOS never shows the location prompt and the SDK logs an error. Write one plain sentence about why the app uses location, and tell the user in Step 8 to replace it with their own words, because App Review reads it. Do not add the "Always" keys or the `location` background mode unless the user asks for background location.
3. Location sharing. Right after `OneSignal.initialize(...)`, add `OneSignal.Location.isShared = true // onesignal:managed v1`. The SDK defaults sharing to off, so without this line the module collects nothing.

Do not call `OneSignal.Location.requestPermission()` at launch: the push request in the verification helper stays the only prompt the setup adds. In the Step-8 summary, name the 2 documented ways to ask for the location permission: a location prompt in an in-app message (no code; `OneSignalInAppMessages` is already linked), or `OneSignal.Location.requestPermission()` at a moment the app chooses.

**On no**, add one sentence to the Step-8 summary: location is an optional module, and a new setup run can add it.

## Dependency

Detect the existing manager: `Podfile`/`Podfile.lock` → CocoaPods; `Package.swift`/`Package.resolved` or an SPM project → SPM. Match it; don't introduce a second package manager. Resolve the exact version with the script — do not read the feed by hand and do not use a range (on SPM that means an **exact-version** rule, never `upToNextMajorVersion` / `from:`):

```bash
python3 <plugin>/scripts/resolve_sdk_version.py ios --format json   # for the version
python3 <plugin>/scripts/resolve_sdk_version.py ios --format line --line-format podfile       # app target Podfile lines
python3 <plugin>/scripts/resolve_sdk_version.py ios --format line --line-format podfile-nse   # NSE target Podfile line
python3 <plugin>/scripts/resolve_sdk_version.py ios --format line --line-format podfile-location   # app target, only on a location opt-in
```

The feed entry is the `OneSignal-iOS-SDK` repository; the SPM package is `OneSignal-XCFramework`, which publishes the same version tags, so the resolver's `version` is the pin for both (the script says so in its `note`).

**Swift Package Manager:** add the package `https://github.com/OneSignal/OneSignal-XCFramework` — **not** `OneSignal-iOS-SDK`, which is the source repository and makes Xcode build the whole SDK — with an **Exact Version** rule set to the resolver's `version`. Map the products per "Which SDK products" above: `OneSignalFramework` and `OneSignalInAppMessages` on the app target (plus `OneSignalLocation` on a location opt-in), `OneSignalExtension` on the `OneSignalNotificationServiceExtension` target. The most common install mistake is a product on the wrong target: `No such module 'OneSignalFramework'` means the app target lacks it; `No such module 'OneSignalExtension'` means the NSE target lacks it. SPM add is a pbxproj edit (`XCRemoteSwiftPackageReference` + one `XCSwiftPackageProductDependency` per product) — if you cannot edit the package references safely, give the human the exact File ▸ Add Package Dependencies steps with the URL, the version, and the product-to-target table.

**CLI builds + SPM keychain wall:** `xcodebuild`-driven SPM resolution can pop a macOS **login-keychain password prompt** (and re-prompt on Deny), which stalls headless/agent runs. Pass `-scmProvider system` to `xcodebuild` so package fetching uses system git credentials instead of Xcode's keychain-backed SCM.

**CocoaPods** (`Podfile`) — paste the resolver's `--line-format podfile` output verbatim into the app target and the `--line-format podfile-nse` output into the NSE target (both are exact pins; on a location opt-in, also the `--line-format podfile-location` line into the app target), then `pod repo update && pod install`, and build the `.xcworkspace`. Shape:
```ruby
target 'YourApp' do
  pod 'OneSignal/OneSignal', 'X.Y.Z' # onesignal:managed v1
  pod 'OneSignal/OneSignalInAppMessages', 'X.Y.Z' # onesignal:managed v1
end

target 'OneSignalNotificationServiceExtension' do
  pod 'OneSignal/OneSignal', 'X.Y.Z' # onesignal:managed v1
end
```
The `OneSignal` pod vendors the same XCFrameworks as the SPM package, so CocoaPods needs no repository change. CocoaPods 1.16.2+ is required. Do not change `objectVersion` by default. Only when `pod install` fails with `Unable to find compatibility version string for object version '70'` (the `xcodeproj` gem is too old for an Xcode 16 project), the documented workaround is `objectVersion = 55;` in the pbxproj. That line downgrades the project file format, so it is a new edit: first suggest a CocoaPods update (`gem update cocoapods` or `bundle update cocoapods`). If the user cannot update, show the one-line change as its own diff, name the revert (set `objectVersion` back to `70`), and ask for approval before you write it and run `pod install` again.

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
3. For Xcode 16+ file-system-synchronized projects (`PBXFileSystemSynchronizedRootGroup`), add a `PBXFileSystemSynchronizedBuildFileExceptionSet` excluding `Info.plist` from the resource copy phase to avoid "Multiple commands produce Info.plist". Skip step 3 for classic `PBXGroup`/`PBXFileReference` projects. The `PBXFileSystemSynchronizedBuildFileExceptionSet` shape is the one in [ios-nse.md](ios-nse.md) step 3, with the app target and `Info.plist` in place of the extension's values; pbxproj edits are fragile, so preview them precisely in the diff (safety contract §5) and if the format doesn't match, hand the capability toggle to the human via Xcode's Signing & Capabilities tab instead.

Add `aps-environment` to the `.entitlements` file (`development`, or `production` for release), and the App Group `group.__MAIN_APP_BUNDLE_ID__.onesignal` next to it ([ios-nse.md](ios-nse.md) → "App Group rules"). If the target has no `CODE_SIGN_ENTITLEMENTS` setting yet, add it to the Debug and Release configurations, and on an Xcode 16+ synchronized project list the entitlements file in the same `membershipExceptions` as `Info.plist`. Deployment target iOS 12.0+; do not change it if already set.

## Notification Service Extension + App Group

Follow [ios-nse.md](ios-nse.md): write the 3 template files into `OneSignalNotificationServiceExtension/`, add the target objects to `project.pbxproj` (or hand the target creation to the human when the format does not match), link `OneSignalExtension` (SPM) or the NSE pod block (CocoaPods), and put the same App Group in both entitlements files. The NSE folder, its 3 files, and the pbxproj objects belong in the Step-5 allow-list and diff.

## Centralized wrapper (Swift)

Use the template [assets/ios/OneSignalManager.swift.tmpl](assets/ios/OneSignalManager.swift.tmpl) as-is (no substitution needed). Signatures are verified against api-reference "SDK data surface"; `login()` before tags/email/sms. No direct OneSignal calls outside this wrapper except the verification observer.

## Debug-only verification helper (SwiftUI + UIKit)

Use the verified template [assets/ios/OneSignalSetupVerification.swift.tmpl](assets/ios/OneSignalSetupVerification.swift.tmpl) — write it as-is (no substitution needed). Do NOT hand-write this file. It works for both UIKit and SwiftUI apps; call `OneSignalSetupVerification.install()` once from your launch context right after `OneSignal.initialize(...)`. Every API in it is validated against the iOS SDK source and the file is **compile-verified** against the real iOS SDK + a faithful OneSignal stub by the plugin repository's `compile_check_ios.sh` check (which also proves the check rejects the fabricated call shapes below). **Use the real observer API** — do NOT wire verification to a `NotificationCenter` event (an eval fabrication: agents listened for a OneSignal registration notification the SDK never posts; it compiles and is functionally dead). The correct surface:
- conform to `OSPushSubscriptionObserver` and implement `func onPushSubscriptionDidChange(state: OSPushSubscriptionChangedState)`; read `state.current.id` (type `String?`).
- register with `OneSignal.User.pushSubscription.addObserver(self)`; also read `OneSignal.User.pushSubscription.id` immediately (race guard).
- `requestPermission` on iOS DOES take a completion block: `OneSignal.Notifications.requestPermission({ accepted in ... }, fallbackToSettings: false)` (unlike Android's suspend form).

The Step-8 structural self-check (`verify_integration.py --platform ios`) fails the run on a missing `#if DEBUG` guard, a NotificationCenter observer in place of the real push observer, or 2 different App Groups in the app and extension entitlements. It reports, as warnings only, init outside a launch context, a missing NSE target or `OneSignalExtension` link, a missing App Group, a missing `OneSignalInAppMessages` link, and, when `OneSignalLocation` is linked, a missing location usage description. Fix every warning the iOS install should have prevented. Properties the helper must have (SKILL.md Step 6):
- Guard on `#if DEBUG` so it never ships.
- `OneSignal.Notifications.requestPermission(..., fallbackToSettings: false)` at install — the ONLY permission prompt. `fallbackToSettings` stays `false`: the call runs at launch with no user gesture, and `true` would send a previously-denied user to the Settings app on every debug start.
- Register the observer AND call `evaluate(OneSignal.User.pushSubscription.id)` immediately (race guard).
- `isRegistered` = non-empty AND not `hasPrefix("local-")`.
- `hasLogged` guard; print the subscription ID exactly once, then remove the observer.
- No dialog and no network call — the verify skill confirms the subscription server-side and sends the test push from chat.
- Top-of-file comment naming the file + call site, and saying the file is debug-only and safe to keep.

## Checkpoint — `setup.platform_config` (capabilities + permission request + extension)

Report the iOS push prerequisites right after `setup.verification_added`, once the
Step-5 change set and the verification helper both exist (telemetry contract rules
apply, consent included). Run exactly one of:

- All 6 prerequisites are in place — `UIBackgroundModes` `remote-notification`, the
  `aps-environment` entitlement, the pbxproj build settings, the `requestPermission`
  call in the helper, the `OneSignalNotificationServiceExtension` target with
  `OneSignalExtension` linked, and the same App Group in both entitlements files. Do not
  decide the last 2 from your own reading: run the Step-8 self-check
  (`verify_integration.py --platform ios`) first, and both `ios_nse_present` and
  `ios_app_group_matches` must pass:
  `bash <plugin>/scripts/checkpoint.sh setup.platform_config ok`
- The pbxproj format did not match and you handed the capability toggles or the NSE
  target creation to the human in Xcode, or one of those 2 rows still fails after you
  repair it:
  `bash <plugin>/scripts/checkpoint.sh setup.platform_config fail capability_manual`
  — report it at the hand-off and continue; the fail row records the drop out of agent
  automation, not a skill stop.

## Handoffs

- Hand off to **credentials** — iOS push needs the APNs `.p8` (+ Key ID/Team ID) on the OneSignal app, and the human must toggle capabilities in Xcode (and create the NSE target when you handed that step over).
- Then **verify** — on an Apple-silicon Mac the verify skill runs the simulator itself; a physical device needs the human to launch from Xcode.
