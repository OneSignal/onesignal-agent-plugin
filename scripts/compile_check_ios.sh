#!/usr/bin/env bash
# Compile-verify the shipped iOS Swift templates against the real iOS SDK (UIKit)
# plus a faithful stub of the OneSignal SDK surface (validated against
# OneSignal-iOS-SDK source). This is the "template only when
# compile-verifiable" gate for iOS — the analog of building the Android
# templates on the Kotlin fixture.
#
# It also runs negative controls: the plausible wrong call shapes an agent
# fabricates (web/RN `requestPermission(true)`, Android suspend/await form) MUST
# fail to typecheck, proving the stub has teeth and is not a rubber stamp.
#
# Requires macOS with Xcode (xcrun + iphonesimulator SDK). Skips with code 0 and
# a notice elsewhere.
set -euo pipefail

ASSETS="$(cd "$(dirname "$0")/../skills/setup/assets/ios" && pwd)"
TARGET="arm64-apple-ios15.0-simulator"

if ! command -v xcrun >/dev/null 2>&1 || ! xcrun --sdk iphonesimulator --show-sdk-path >/dev/null 2>&1; then
    echo "SKIP: no Xcode iphonesimulator SDK on this host — cannot compile-check iOS."
    exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/out"

# --- Faithful OneSignal SDK stub (validated against OneSignal-iOS-SDK source) ---
cat > "$WORK/OneSignalFramework.swift" <<'SW'
import Foundation
public enum OSLogLevel { case LL_NONE, LL_FATAL, LL_ERROR, LL_WARN, LL_INFO, LL_DEBUG, LL_VERBOSE }
public class OSPushSubscriptionState {
    public let id: String?; public let token: String?; public let optedIn: Bool
    public init(id: String?, token: String?, optedIn: Bool) { self.id = id; self.token = token; self.optedIn = optedIn }
}
public class OSPushSubscriptionChangedState {
    public let current: OSPushSubscriptionState; public let previous: OSPushSubscriptionState
    public init(current: OSPushSubscriptionState, previous: OSPushSubscriptionState) { self.current = current; self.previous = previous }
}
public protocol OSPushSubscriptionObserver: AnyObject {
    func onPushSubscriptionDidChange(state: OSPushSubscriptionChangedState)
}
public protocol OSPushSubscription {
    var id: String? { get }; var token: String? { get }; var optedIn: Bool { get }
    func optIn(); func optOut()
    func addObserver(_ observer: OSPushSubscriptionObserver)
    func removeObserver(_ observer: OSPushSubscriptionObserver)
}
public class OSUser {
    public var pushSubscription: OSPushSubscription { fatalError("stub") }
    public func addEmail(_ email: String) {}
    public func addSms(_ smsNumber: String) {}
    public func addTag(key: String, value: String) {}
}
public class OSNotifications {
    // ObjC: requestPermission:(OSUserResponseBlock)block fallbackToSettings:(BOOL)
    public func requestPermission(_ block: ((Bool) -> Void)?, fallbackToSettings: Bool) {}
}
public class OSDebug { public func setLogLevel(_ logLevel: OSLogLevel) {} }
public enum OneSignal {
    public static var User: OSUser { OSUser() }
    public static var Notifications: OSNotifications { OSNotifications() }
    public static var Debug: OSDebug { OSDebug() }
    public static func initialize(_ appId: String, withLaunchOptions: [AnyHashable: Any]?) {}
    public static func login(_ externalId: String) {}
    public static func logout() {}
}
SW

# --- Faithful OneSignalExtension stub (validated against OneSignalExtension.h) ---
# The header has no nullability region, so Swift imports the ObjC class methods
# with optional parameters and an optional return; the return is discardable.
cat > "$WORK/OneSignalExtension.swift" <<'SW'
import Foundation
import UserNotifications
public class OneSignalExtension: NSObject {
    @discardableResult
    public class func didReceiveNotificationExtensionRequest(
        _ request: UNNotificationRequest, with replacementContent: UNMutableNotificationContent?,
        withContentHandler contentHandler: ((UNNotificationContent) -> Void)?
    ) -> UNMutableNotificationContent? { nil }
    @discardableResult
    public class func serviceExtensionTimeWillExpireRequest(
        _ request: UNNotificationRequest, with replacementContent: UNMutableNotificationContent?
    ) -> UNMutableNotificationContent? { nil }
}
SW

sc() { xcrun --sdk iphonesimulator swiftc -target "$TARGET" "$@"; }

echo "building OneSignal stub modules..."
sc -emit-module -module-name OneSignalFramework \
   -emit-module-path "$WORK/out/OneSignalFramework.swiftmodule" \
   -parse-as-library "$WORK/OneSignalFramework.swift"
sc -emit-module -module-name OneSignalExtension \
   -emit-module-path "$WORK/out/OneSignalExtension.swiftmodule" \
   -parse-as-library "$WORK/OneSignalExtension.swift"

fail=0

# --- positive: shipped templates MUST compile ---
sed 's/__APP_ID__/11111111-2222-3333-4444-555555555555/' \
    "$ASSETS/OneSignalSetupVerification.swift.tmpl" > "$WORK/verify.swift"
if sc -typecheck -D DEBUG -I "$WORK/out" "$WORK/verify.swift"; then
    echo "✓ OneSignalSetupVerification.swift.tmpl compiles"
else
    echo "✗ OneSignalSetupVerification.swift.tmpl FAILED to compile"; fail=1
fi
cp "$ASSETS/OneSignalManager.swift.tmpl" "$WORK/manager.swift"
if sc -typecheck -I "$WORK/out" "$WORK/manager.swift"; then
    echo "✓ OneSignalManager.swift.tmpl compiles"
else
    echo "✗ OneSignalManager.swift.tmpl FAILED to compile"; fail=1
fi
cp "$ASSETS/NotificationService.swift.tmpl" "$WORK/nse.swift"
if sc -typecheck -I "$WORK/out" "$WORK/nse.swift"; then
    echo "✓ NotificationService.swift.tmpl compiles"
else
    echo "✗ NotificationService.swift.tmpl FAILED to compile"; fail=1
fi
for plist in OneSignalNotificationServiceExtension-Info.plist.tmpl OneSignalNotificationServiceExtension.entitlements.tmpl; do
    if sed 's/__MAIN_APP_BUNDLE_ID__/com.example.app/' "$ASSETS/$plist" | plutil -lint - >/dev/null; then
        echo "✓ $plist is a valid plist"
    else
        echo "✗ $plist is NOT a valid plist"; fail=1
    fi
done

# --- negative controls: fabricated call shapes MUST NOT compile ---
cat > "$WORK/bad_web.swift" <<'SW'
import OneSignalFramework
func x() { OneSignal.Notifications.requestPermission(true) }
SW
cat > "$WORK/bad_suspend.swift" <<'SW'
import OneSignalFramework
func x() async { _ = await OneSignal.Notifications.requestPermission(true) }
SW
cat > "$WORK/bad_nse_label.swift" <<'SW'
import UserNotifications
import OneSignalExtension
func x(_ r: UNNotificationRequest, _ c: UNMutableNotificationContent, _ h: @escaping (UNNotificationContent) -> Void) {
    OneSignalExtension.didReceiveNotificationExtensionRequest(r, withMutableNotificationContent: c, withContentHandler: h)
}
SW
for bad in bad_web bad_suspend bad_nse_label; do
    if sc -typecheck -I "$WORK/out" "$WORK/$bad.swift" 2>/dev/null; then
        echo "✗ negative control $bad UNEXPECTEDLY compiled — stub is a rubber stamp"; fail=1
    else
        echo "✓ negative control $bad correctly rejected"
    fi
done

[ "$fail" -eq 0 ] && echo "iOS templates compile-verified." || echo "iOS compile-check FAILED."
exit "$fail"
