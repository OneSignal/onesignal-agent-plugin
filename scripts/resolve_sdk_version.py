#!/usr/bin/env python3
"""Resolve the exact, pinned OneSignal SDK version for a platform.

This exists to remove a specific failure: models were emitting Gradle/CocoaPods
version *ranges* (e.g. `[5.6.1, 5.9.99]`), which cause real mobile build
failures. There is no code path here that can produce a range — the only
output is an exact pin read from the official release feed.

Source of truth: https://onesignal.github.io/sdk-releases/releases.json
Never guess a version. If the feed is unreachable, this exits non-zero and the
caller must ask the user to confirm — it must not invent a number.

Usage:
    resolve_sdk_version.py <platform> [--track stable|current] [--format FMT]

Platforms:
    android ios web react-native expo flutter cordova capacitor unity

Formats:
    json (default)  machine + human summary: version, coordinate, dep line
    raw             just the exact version string (e.g. 5.9.1)
    line            just the ready-to-paste dependency line for the platform

Exit codes:
    0 resolved   2 unknown platform / no version on any track   3 feed unreachable
"""
import argparse
import json
import sys
import urllib.request

FEED_URL = "https://onesignal.github.io/sdk-releases/releases.json"

# platform key -> (feed "name", how to render the dependency line)
# The line renderers take the bare version and return the exact, pinned snippet.
PLATFORMS = {
    "android": {
        "feed": "Android",
        "coordinate": "com.onesignal:OneSignal",
        "lines": {
            "gradle-kts": 'implementation("com.onesignal:OneSignal:{v}") // onesignal:managed v1',
            "gradle-groovy": "implementation 'com.onesignal:OneSignal:{v}' // onesignal:managed v1",
        },
        "default_line": "gradle-kts",
    },
    "ios": {
        "feed": "iOS",
        "coordinate": "OneSignal-iOS-SDK / OneSignalXCFramework",
        "lines": {
            "spm": 'https://github.com/OneSignal/OneSignal-iOS-SDK — "Exact Version" {v} (product: OneSignalFramework)',
            "podfile": "pod 'OneSignal/OneSignal', '{v}' # onesignal:managed v1",
        },
        "default_line": "spm",
    },
    "react-native": {
        "feed": "ReactNative",
        "coordinate": "react-native-onesignal",
        "lines": {"npm": '"react-native-onesignal": "{v}"'},
        "default_line": "npm",
    },
    "expo": {
        "feed": "ReactNative",  # the runtime dependency; plugin resolved separately below
        "coordinate": "react-native-onesignal + onesignal-expo-plugin",
        "lines": {"npm": '"react-native-onesignal": "{v}"'},
        "default_line": "npm",
        "companion": {"feed": "Expo", "coordinate": "onesignal-expo-plugin"},
    },
    "flutter": {
        "feed": "Flutter",
        "coordinate": "onesignal_flutter",
        "lines": {"pubspec": "onesignal_flutter: {v}"},
        "default_line": "pubspec",
    },
    "cordova": {
        "feed": "Cordova",
        "coordinate": "onesignal-cordova-plugin",
        "lines": {"npm": '"onesignal-cordova-plugin": "{v}"'},
        "default_line": "npm",
    },
    "capacitor": {
        "feed": "Capacitor",
        "coordinate": "@onesignal/capacitor-plugin",
        "lines": {"npm": '"@onesignal/capacitor-plugin": "{v}"'},
        "default_line": "npm",
    },
    "unity": {
        "feed": "Unity",
        "coordinate": "com.onesignal.unity.push",
        "lines": {"unity": "OneSignal Unity SDK {v} (install via .unitypackage / UPM — GUI step)"},
        "default_line": "unity",
    },
    # Web is special: it ships from a fixed CDN major (v16), not a pinned
    # semver. The build number in the feed is metadata; the correct "pin" is
    # the CDN v16 script, which is stable and self-updating within v16.
    "web": {
        "feed": "Web",
        "coordinate": "cdn.onesignal.com/sdks/web/v16",
        "lines": {
            "cdn": '<script src="https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js" defer></script>',
        },
        "default_line": "cdn",
        "web_fixed_major": "v16",
    },
}


def fetch_feed():
    try:
        with urllib.request.urlopen(FEED_URL, timeout=15) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001 - any failure means "ask the user"
        sys.stderr.write(
            f"ERROR: could not fetch {FEED_URL}: {e}\n"
            "Do NOT guess a version. Ask the user to confirm the SDK version from\n"
            "the OneSignal dashboard or release notes, then pin it exactly.\n"
        )
        sys.exit(3)


def find_entry(feed, feed_name):
    for e in feed:
        if e.get("name") == feed_name:
            return e
    return None


def resolve_version(feed, feed_name, track):
    """Return (version, track_used). Falls back stable->current with a note."""
    entry = find_entry(feed, feed_name)
    if entry is None:
        return None, None
    channels = entry.get("channels") or {}
    order = [track] + [t for t in ("current", "stable") if t != track]
    for t in order:
        ch = channels.get(t)
        if ch and ch.get("version"):
            v = ch["version"].lstrip("v")  # feed prefixes some API SDKs with 'v'
            return v, t
    return None, None


def main():
    ap = argparse.ArgumentParser(description="Resolve exact OneSignal SDK version (never a range).")
    ap.add_argument("platform", choices=sorted(PLATFORMS.keys()))
    ap.add_argument("--track", default="stable", choices=["stable", "current"])
    ap.add_argument("--format", default="json", choices=["json", "raw", "line"])
    ap.add_argument("--line-format", default=None,
                    help="override the dependency-line renderer (e.g. gradle-groovy)")
    args = ap.parse_args()

    spec = PLATFORMS[args.platform]
    feed = fetch_feed()

    version, track_used = resolve_version(feed, spec["feed"], args.track)
    if version is None:
        sys.stderr.write(
            f"ERROR: no version found for '{args.platform}' on any track. "
            "The feed shape may have changed — ask the user to confirm the version.\n"
        )
        sys.exit(2)
    # Emit the track substitution on stderr too, so it is visible in raw/line
    # formats (which print only the version/dep line to stdout) — not just in JSON.
    if track_used != args.track:
        sys.stderr.write(
            f"NOTE: requested track '{args.track}' had no release for '{args.platform}'; "
            f"used '{track_used}' ({version}).\n"
        )

    line_key = args.line_format or spec["default_line"]
    if line_key not in spec["lines"]:
        line_key = spec["default_line"]
    dep_line = spec["lines"][line_key].format(v=version)

    companion = None
    if "companion" in spec:
        cv, ct = resolve_version(feed, spec["companion"]["feed"], args.track)
        if cv:
            companion = {
                "coordinate": spec["companion"]["coordinate"],
                "version": cv,
                "track_used": ct,
                "line": f'"onesignal-expo-plugin": "{cv}"',
            }
        else:
            # Don't fall silent: an unresolved companion is exactly the case where
            # the agent goes back to memory and fabricates a plugin version.
            sys.stderr.write(
                f"WARNING: could not resolve companion package "
                f"'{spec['companion']['coordinate']}' from the feed. Do NOT guess its "
                "version — confirm it with the user before pinning.\n"
            )

    if args.format == "raw":
        print(version)
        return
    if args.format == "line":
        print(dep_line)
        if companion:
            print(companion["line"])
        return

    out = {
        "platform": args.platform,
        "coordinate": spec["coordinate"],
        "version": version,
        "track_requested": args.track,
        "track_used": track_used,
        "line_format": line_key,
        "dependency_line": dep_line,
        "source": FEED_URL,
        "is_range": False,
    }
    if spec.get("web_fixed_major"):
        out["note"] = (
            f"Web pins to the fixed CDN major {spec['web_fixed_major']}; "
            f"feed build number is {version} (metadata only)."
        )
    if track_used != args.track:
        fallback = f"Requested track '{args.track}' had no release; used '{track_used}'."
        out["note"] = (out.get("note", "") + " " + fallback).strip()
    if companion:
        out["companion"] = companion
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
