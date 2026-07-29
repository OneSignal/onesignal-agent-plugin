#!/usr/bin/env python3
"""Deterministic structural verification of a written OneSignal integration.

The back slice of the "deterministic sandwich": after the agent writes code, run
checks a compiler cannot make. A build is a weak signal in both directions —
fabricated integrations compile (a dead NotificationCenter observer type-checks),
and correct ones fail (iOS pbxproj handed to the human; the Kotlin-floor blocker).
This asserts the constraint-following facts the plugin actually gets wrong.

Checks are grep/structure-level and platform-aware. Each returns pass/fail with a
severity. `error` failures set a nonzero exit; `warn` are advisory.

Usage:
    verify_integration.py <project_dir> --platform android|ios|web|expo|react-native|flutter|cordova|capacitor [--app-id UUID]

Exit: 0 all error-level checks pass; 1 an error-level check failed; 2 usage.
"""
import argparse
import json
import os
import re
import sys

EXCLUDE = {"node_modules", "build", ".gradle", "Pods", ".git", "dist", "DerivedData",
           "__pycache__", ".dart_tool", "out"}
CODE_EXTS = {".kt", ".java", ".swift", ".m", ".ts", ".tsx", ".js", ".jsx", ".dart",
             ".gradle", ".kts", ".html", ".xml", ".rb", ".ruby", ".plist", ".json"}
# .json is needed for package.json / app.json (Expo/RN config, npm version ranges).

# A OneSignal dependency line carrying a range/dynamic version instead of a pin.
RANGE_PATTERNS = [
    re.compile(r"OneSignal[\"']?\s*[,:]\s*[\"']?\[[^\]]*,"),          # gradle [x, y]
    re.compile(r"onesignal[^\n]*:\s*[\d.]+\s*\+"),                     # gradle x.y.+
    re.compile(r"upToNextMajorVersion|upToNextMinorVersion"),          # SPM range
    re.compile(r"\.package\([^)]*from\s*:"),                           # SPM from:
    re.compile(r"[\"'][^\"'\n]*onesignal[^\"'\n]*[\"']\s*:\s*[\"'][~^]"),  # npm ^/~ in package.json
    re.compile(r"onesignal_flutter:\s*[\"']?[\^~]"),                   # pubspec ^/~
    re.compile(r"pod\s+['\"]OneSignal[^'\"]*['\"]\s*,\s*['\"]\s*[~>]"),   # cocoapods ~>
]


def walk_files(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE]
        for f in fn:
            if os.path.splitext(f)[1] in CODE_EXTS or f in ("Podfile", "Package.swift"):
                yield os.path.join(dp, f)


def read(fp):
    try:
        return open(fp, encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""


def grep(root, pattern, files=None):
    """Return list of (relpath, lineno) where pattern matches."""
    rx = pattern if hasattr(pattern, "search") else re.compile(pattern)
    hits = []
    for fp in (files if files is not None else walk_files(root)):
        for i, line in enumerate(read(fp).splitlines(), 1):
            if rx.search(line):
                hits.append((os.path.relpath(fp, root), i))
    return hits


class Checks:
    def __init__(self, root, platform, app_id):
        self.root, self.platform, self.app_id = root, platform, app_id
        self.results = []

    def add(self, name, passed, severity, detail=""):
        self.results.append({"check": name, "passed": passed, "severity": severity, "detail": detail})

    # ---- universal ----
    def no_version_range(self):
        hits = []
        for rx in RANGE_PATTERNS:
            hits += grep(self.root, rx)
        self.add("no_version_range", not hits, "error",
                 "" if not hits else f"range/dynamic version at {hits[:5]} — must be an exact pin")

    def managed_marker_present(self):
        hits = grep(self.root, r"onesignal:managed")
        self.add("managed_marker_present", bool(hits), "warn",
                 "no onesignal:managed marker found — skill may not have engaged, or markers were dropped")

    def no_placeholder_app_id(self):
        hits = grep(self.root, r"YOUR_ONESIGNAL_APP_ID|<APP_ID>")
        self.add("no_placeholder_app_id", not hits, "error",
                 "" if not hits else f"unreplaced placeholder App ID at {hits[:5]}")

    def app_id_present(self):
        if not self.app_id:
            self.add("app_id_present", True, "warn", "no --app-id given; skipped exact-ID check")
            return
        hits = grep(self.root, re.compile(re.escape(self.app_id)))
        self.add("app_id_present", bool(hits), "error",
                 "" if hits else f"expected App ID {self.app_id} not found in any init call")

    def no_deprecated_addoutcome(self):
        hits = grep(self.root, r"\.addOutcome")
        self.add("no_deprecated_addoutcome", not hits, "error",
                 "" if not hits else f"deprecated addOutcome API at {hits[:5]} — use custom events")

    def no_committed_secrets(self):
        # light check; scan_secrets.py is the fuller tool (Step 8)
        rx = re.compile(r"os_v2_(?:app|org)_[a-z0-9]{20,}|-----BEGIN (?:EC )?PRIVATE KEY-----", re.I)
        hits = grep(self.root, rx)
        self.add("no_committed_secrets", not hits, "error",
                 "" if not hits else f"secret-shaped string at {hits[:3]} — see scan_secrets.py")

    # ---- android ----
    def android_init_in_application(self):
        init_hits = grep(self.root, r"OneSignal\.initWithContext")
        if not init_hits:
            self.add("android_init_in_application", False, "error", "no OneSignal.initWithContext found")
            return
        # the file holding init (or a sibling it calls) should be an Application subclass
        app_class = grep(self.root, r":\s*Application\(\)|extends\s+Application")
        self.add("android_init_in_application", bool(app_class), "error",
                 "" if app_class else "init present but no Application subclass found (init likely in Activity — breaks cold start)")

    def android_manifest_registers_app(self):
        manifests = [fp for fp in walk_files(self.root) if os.path.basename(fp) == "AndroidManifest.xml"]
        hit = any(re.search(r"<application[^>]*android:name=", read(fp)) for fp in manifests)
        self.add("android_manifest_registers_app", hit, "error",
                 "" if hit else "no <application android:name=...> — Application subclass not registered")

    def android_no_stray_google_services(self):
        gs = [fp for fp in walk_files(self.root) if os.path.basename(fp) == "google-services.json"]
        firebase = grep(self.root, r"com\.google\.firebase")
        # only a problem if google-services.json exists AND the app doesn't otherwise use Firebase
        bad = bool(gs) and not firebase
        self.add("android_no_stray_google_services", not bad, "warn",
                 "" if not bad else "google-services.json present but no Firebase client usage — OneSignal does NOT require it")

    def android_requestpermission_not_callback(self):
        # OneSignal.Notifications.requestPermission is `suspend fun(Boolean): Boolean`
        # with NO callback overload. A trailing-lambda call is a fabrication that
        # does not compile — must be called from a coroutine. (A build would catch
        # this, but the Kotlin-stdlib floor error can mask it; check it directly.)
        rx = re.compile(r"requestPermission\s*\([^)]*\)\s*\{")
        hits = []
        for fp in walk_files(self.root):
            for i, line in enumerate(read(fp).splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue  # a cautionary comment quoting the bad form is not code
                if rx.search(line):
                    hits.append((os.path.relpath(fp, self.root), i))
        self.add("android_requestpermission_not_callback", not hits, "error",
                 "" if not hits else f"requestPermission called with a callback lambda at {hits[:3]} "
                 "— it is a suspend fun; call it from a coroutine (fabricated overload won't compile)")

    def android_buildconfig_feature_enabled(self):
        # BuildConfig.DEBUG only resolves if the app module enables the buildConfig
        # feature. AGP 8+ defaults it OFF, so a correct guard can still fail to
        # compile with a cryptic "unresolved reference: BuildConfig" — a bad first
        # experience for the customer. Only relevant if BuildConfig.DEBUG is used.
        if not grep(self.root, re.compile(r"BuildConfig\.DEBUG")):
            self.add("android_buildconfig_feature_enabled", True, "warn",
                     "BuildConfig.DEBUG not used; buildConfig feature not required")
            return
        enabled = bool(grep(self.root, re.compile(r"buildConfig\s*=\s*true")))
        for dp, dn, fn in os.walk(self.root):
            dn[:] = [d for d in dn if d not in EXCLUDE]
            if "gradle.properties" in fn and re.search(
                r"android\.defaults\.buildfeatures\.buildconfig\s*=\s*true",
                read(os.path.join(dp, "gradle.properties"))):
                enabled = True
        self.add("android_buildconfig_feature_enabled", enabled, "error",
                 "" if enabled else "verification file uses BuildConfig.DEBUG but the app module does not enable "
                 "the buildConfig feature (AGP 8+ defaults it off) — add `android { buildFeatures { buildConfig = "
                 "true } }` or BuildConfig won't resolve and the build fails")

    def android_verification_debug_guarded(self):
        vfiles = [fp for fp in walk_files(self.root) if "verification" in os.path.basename(fp).lower()]
        if not vfiles:
            self.add("android_verification_debug_guarded", False, "warn", "no verification file found (deletable proof step)")
            return
        guarded = any(re.search(r"BuildConfig\.DEBUG", read(fp)) for fp in vfiles)
        self.add("android_verification_debug_guarded", guarded, "error",
                 "" if guarded else "verification file not guarded by BuildConfig.DEBUG — would ship to production")

    # ---- ios ----
    def ios_init_in_launch(self):
        init_hits = grep(self.root, re.compile(r"OneSignal\.initialize\s*\("))
        if not init_hits:
            self.add("ios_init_in_launch", False, "error", "no OneSignal.initialize(...) found")
            return
        # the init must run at app launch, not from a SwiftUI View body
        launch = False
        for fp in walk_files(self.root):
            if fp.endswith(".swift"):
                txt = read(fp)
                if "OneSignal.initialize" in txt and re.search(r"didFinishLaunchingWithOptions|@main|:\s*App\b", txt):
                    launch = True
        self.add("ios_init_in_launch", launch, "warn",
                 "" if launch else "OneSignal.initialize is not in an AppDelegate/@main App launch context "
                 "(if it's in a View, cold-start push/deep links break)")

    def ios_verification_debug_guarded(self):
        vfiles = [fp for fp in walk_files(self.root) if "verification" in os.path.basename(fp).lower() and fp.endswith(".swift")]
        if not vfiles:
            self.add("ios_verification_debug_guarded", False, "warn", "no Swift verification file found (deletable proof step)")
            return
        guarded = any("#if DEBUG" in read(fp) for fp in vfiles)
        self.add("ios_verification_debug_guarded", guarded, "error",
                 "" if guarded else "verification file not guarded by #if DEBUG — would ship to release")

    def ios_verification_uses_push_observer(self):
        # FINDINGS: agents wired verification to a NotificationCenter event OneSignal
        # never posts (compiles, functionally dead). The real mechanism is
        # OSPushSubscriptionObserver.onPushSubscriptionDidChange. Flag the dead form.
        vfiles = [fp for fp in walk_files(self.root) if "verification" in os.path.basename(fp).lower() and fp.endswith(".swift")]
        if not vfiles:
            self.add("ios_verification_uses_push_observer", True, "warn", "no Swift verification file to check")
            return
        blob = "\n".join(read(fp) for fp in vfiles)
        uses_real = "OSPushSubscriptionObserver" in blob or "onPushSubscriptionDidChange" in blob
        uses_notifcenter = re.search(r"NotificationCenter|NSNotificationCenter", blob) is not None
        ok = uses_real and not uses_notifcenter
        detail = ""
        if not ok:
            detail = ("verification does not use OSPushSubscriptionObserver.onPushSubscriptionDidChange"
                      + (" and relies on NotificationCenter (a OneSignal registration event it never posts — dead)" if uses_notifcenter else ""))
        self.add("ios_verification_uses_push_observer", ok, "error", detail)

    # ---- expo / react-native (shared package: react-native-onesignal) ----
    def rn_package_present(self):
        pkgs = [fp for fp in walk_files(self.root) if os.path.basename(fp) == "package.json"]
        present = any("react-native-onesignal" in read(fp) for fp in pkgs)
        self.add("rn_package_present", present, "error",
                 "" if present else "react-native-onesignal not found in any package.json")

    def rn_init_present(self):
        hits = grep(self.root, re.compile(r"OneSignal\.initialize\s*\("))
        self.add("rn_init_present", bool(hits), "error",
                 "" if hits else "no OneSignal.initialize(appId) call found")

    def rn_verification_dev_guarded(self):
        vfiles = [fp for fp in walk_files(self.root)
                  if "verif" in os.path.basename(fp).lower() and os.path.splitext(fp)[1] in (".ts", ".tsx", ".js", ".jsx")]
        if not vfiles:
            self.add("rn_verification_dev_guarded", False, "warn", "no JS/TS verification file found (deletable proof step)")
            return
        guarded = any("__DEV__" in read(fp) for fp in vfiles)
        self.add("rn_verification_dev_guarded", guarded, "error",
                 "" if guarded else "verification file not guarded by __DEV__ — would ship to production")

    def _expo_app_config(self):
        return [fp for fp in walk_files(self.root)
                if os.path.basename(fp) in ("app.json", "app.config.js", "app.config.ts")]

    def expo_plugin_registered(self):
        cfgs = self._expo_app_config()
        present = any("onesignal-expo-plugin" in read(fp) for fp in cfgs)
        self.add("expo_plugin_registered", present, "error",
                 "" if present else "onesignal-expo-plugin not registered in app.json/app.config plugins")

    def expo_plugin_first(self):
        # The config plugin must be first in the plugins array (upstream + matrix).
        # Only checkable deterministically for JSON app.json; skip for app.config.*.
        appjson = [fp for fp in self._expo_app_config() if os.path.basename(fp) == "app.json"]
        if not appjson:
            self.add("expo_plugin_first", True, "warn", "app.config.* (not JSON) — plugin-order not statically checkable")
            return
        import json as _json
        for fp in appjson:
            try:
                plugins = (_json.loads(read(fp)).get("expo") or {}).get("plugins") or []
            except Exception:
                continue
            names = [(p[0] if isinstance(p, list) and p else p) for p in plugins]
            if "onesignal-expo-plugin" in names:
                first = names[0] == "onesignal-expo-plugin"
                self.add("expo_plugin_first", first, "warn",
                         "" if first else "onesignal-expo-plugin is not the FIRST entry in expo.plugins")
                return
        self.add("expo_plugin_first", False, "warn", "onesignal-expo-plugin not found in app.json expo.plugins")

    # ---- flutter (onesignal_flutter) ----
    def _flutter_verification_files(self):
        return [fp for fp in walk_files(self.root)
                if "verif" in os.path.basename(fp).lower() and fp.endswith(".dart")]

    def flutter_init_present(self):
        init_hits = grep(self.root, re.compile(r"OneSignal\.initialize\s*\("))
        if not init_hits:
            self.add("flutter_init_present", False, "error", "no OneSignal.initialize(appId) call found")
            return
        # init belongs in main() before runApp(), not in a widget build()
        in_main = False
        for fp in walk_files(self.root):
            if fp.endswith(".dart"):
                txt = read(fp)
                if "OneSignal.initialize" in txt and re.search(r"void\s+main\s*\(|runApp\s*\(", txt):
                    in_main = True
        self.add("flutter_init_present", in_main, "warn",
                 "" if in_main else "OneSignal.initialize is not in main()/near runApp() "
                 "(init once at app entry before runApp)")

    def flutter_verification_debug_guarded(self):
        vfiles = self._flutter_verification_files()
        if not vfiles:
            self.add("flutter_verification_debug_guarded", False, "warn", "no Dart verification file found (deletable proof step)")
            return
        guarded = any("kDebugMode" in read(fp) for fp in vfiles)
        self.add("flutter_verification_debug_guarded", guarded, "error",
                 "" if guarded else "verification file not guarded by kDebugMode — would ship to release")

    def flutter_verification_uses_push_observer(self):
        # Mirror the iOS/RN intent: the verification must key off the real push
        # subscription surface (pushSubscription.addObserver / .id, validated
        # against onesignal_flutter source), not a notification-received listener
        # (addForegroundWillDisplayListener fires on delivery, not registration).
        vfiles = self._flutter_verification_files()
        if not vfiles:
            self.add("flutter_verification_uses_push_observer", True, "warn", "no Dart verification file to check")
            return
        blob = "\n".join(read(fp) for fp in vfiles)
        uses_real = "pushSubscription.addObserver" in blob or "pushSubscription.id" in blob
        self.add("flutter_verification_uses_push_observer", uses_real, "error",
                 "" if uses_real else "verification does not read OneSignal.User.pushSubscription "
                 "(.addObserver/.id) — a notification-received listener is not proof of registration")

    # ---- web ----
    def web_worker_is_importscripts(self):
        workers = [fp for fp in walk_files(self.root) if os.path.basename(fp) == "OneSignalSDKWorker.js"]
        if not workers:
            self.add("web_worker_is_importscripts", False, "error", "OneSignalSDKWorker.js not found")
            return
        for fp in workers:
            txt = read(fp)
            if "importScripts" in txt and len(txt) < 2000:
                self.add("web_worker_is_importscripts", True, "error"); return
            if "<html" in txt.lower() or len(txt) > 50000:
                self.add("web_worker_is_importscripts", False, "error",
                         f"{os.path.relpath(fp, self.root)} looks like a downloaded HTML/blob, not the importScripts one-liner")
                return
        self.add("web_worker_is_importscripts", False, "warn", "worker present but not the expected importScripts shape")

    def web_init_present(self):
        hits = grep(self.root, re.compile(r"OneSignalDeferred|OneSignal\.init\b|new OneSignal|react-onesignal|onesignal-vue|onesignal-ngx"))
        self.add("web_init_present", bool(hits), "error",
                 "" if hits else "no OneSignal init found (OneSignalDeferred/OneSignal.init or a framework wrapper)")

    def web_page_sdk_v16(self):
        # the page SDK must load from the fixed CDN v16 major; a pinned patch or a
        # non-CDN path is a drift/staleness hazard.
        loaded = grep(self.root, re.compile(r"cdn\.onesignal\.com/sdks/web/v16/OneSignalSDK\.page\.js"))
        wrapper = grep(self.root, re.compile(r"react-onesignal|onesignal-vue|onesignal-ngx"))
        ok = bool(loaded) or bool(wrapper)  # a framework wrapper loads the SDK itself
        self.add("web_page_sdk_v16", ok, "warn",
                 "" if ok else "no v16 CDN page script (or framework wrapper) found")

    def run(self):
        universal = [self.no_version_range, self.managed_marker_present, self.no_placeholder_app_id,
                     self.app_id_present, self.no_deprecated_addoutcome, self.no_committed_secrets]
        by_platform = {
            "android": [self.android_init_in_application, self.android_manifest_registers_app,
                        self.android_no_stray_google_services, self.android_verification_debug_guarded,
                        self.android_requestpermission_not_callback, self.android_buildconfig_feature_enabled],
            "ios": [self.ios_init_in_launch, self.ios_verification_debug_guarded,
                    self.ios_verification_uses_push_observer],
            "expo": [self.expo_plugin_registered, self.expo_plugin_first,
                     self.rn_package_present, self.rn_init_present, self.rn_verification_dev_guarded],
            "react-native": [self.rn_package_present, self.rn_init_present, self.rn_verification_dev_guarded],
            "web": [self.web_worker_is_importscripts, self.web_init_present, self.web_page_sdk_v16],
            "flutter": [self.flutter_init_present, self.flutter_verification_debug_guarded,
                        self.flutter_verification_uses_push_observer],
        }
        for c in universal + by_platform.get(self.platform, []):
            c()
        return self.results


def main():
    ap = argparse.ArgumentParser(description="Deterministic structural verification of a OneSignal integration.")
    ap.add_argument("project_dir")
    ap.add_argument("--platform", required=True,
                    choices=["android", "ios", "web", "expo", "react-native", "flutter", "cordova", "capacitor"])
    ap.add_argument("--app-id")
    args = ap.parse_args()
    if not os.path.isdir(args.project_dir):
        sys.stderr.write(f"ERROR: not a directory: {args.project_dir}\n"); sys.exit(2)

    results = Checks(os.path.abspath(args.project_dir), args.platform, args.app_id).run()
    errors = [r for r in results if not r["passed"] and r["severity"] == "error"]
    warns = [r for r in results if not r["passed"] and r["severity"] == "warn"]
    out = {
        "platform": args.platform,
        "checks": results,
        "summary": {"passed": sum(1 for r in results if r["passed"]),
                    "total": len(results), "errors": len(errors), "warnings": len(warns)},
        "verdict": "pass" if not errors else "fail",
    }
    print(json.dumps(out, indent=2))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
