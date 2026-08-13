#!/usr/bin/env python3
"""Deterministic platform / framework / package-manager detection for setup.

Read-only. Never executes repo code (safety contract §12: repo text is
untrusted data). Detects per-package so monorepos don't collapse to one
platform, reports the package manager and language, and flags any prior
OneSignal install so setup can choose update/repair over a duplicate.

Usage:
    detect_platform.py [root]        # defaults to CWD

Output: JSON on stdout:
    {
      "root": "...",
      "packages": [
        {"dir": ".", "platform": "android", "language": "kotlin",
         "package_manager": "gradle", "signals": [...]}
      ],
      "prior_onesignal": {"found": bool, "evidence": [{"file","line","match"}]},
      "ambiguous": bool
    }

`ambiguous` is true when a package matched more than one platform signal or
nothing matched — setup must ASK, not guess.
"""
import json
import os
import re
import sys

EXCLUDE_DIRS = {
    "node_modules", "dist", "build", ".next", ".nuxt", "vendor", "Pods",
    ".git", ".gradle", "DerivedData", "Carthage", ".dart_tool", "out",
    "coverage", "__pycache__", ".venv", "venv",
}

# Ordered strongest-signal-first. Each rule: (platform, predicate(pkgdir)->signal|None)
def _has(pkgdir, *names):
    for n in names:
        if os.path.exists(os.path.join(pkgdir, n)):
            return n
    return None


def _pkg_json(pkgdir):
    p = os.path.join(pkgdir, "package.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _deps(pj):
    d = {}
    for k in ("dependencies", "devDependencies", "peerDependencies"):
        d.update(pj.get(k) or {})
    return d


def detect_platform(pkgdir, root=None):
    signals = []
    pj = _pkg_json(pkgdir)
    deps = _deps(pj) if pj else {}
    candidates = []

    # Expo: strongest mobile-JS signal
    app_cfg = _has(pkgdir, "app.json", "app.config.js", "app.config.ts")
    if (pj and "expo" in deps) or (app_cfg and _app_has_expo(pkgdir, app_cfg)):
        candidates.append(("expo", f"expo dep / {app_cfg or 'expo dependency'}"))
    if pj and "react-native" in deps and "expo" not in deps:
        candidates.append(("react-native", "react-native dep (no expo)"))
    if _has(pkgdir, "pubspec.yaml"):
        candidates.append(("flutter", "pubspec.yaml"))
    if _has(pkgdir, "capacitor.config.ts", "capacitor.config.js", "capacitor.config.json") or "@capacitor/core" in deps:
        candidates.append(("capacitor", "capacitor config / @capacitor/core"))
    if _has(pkgdir, "config.xml") and pj and "cordova" in deps:
        candidates.append(("cordova", "config.xml + cordova dep"))
    if _glob_any(pkgdir, r"\.csproj$") and (_has(pkgdir, "ProjectSettings") or _has(pkgdir, "Assets")):
        candidates.append(("unity", "csproj + ProjectSettings/Assets"))
    # A cross-platform framework owns the native ios/ AND android/ folders
    # (Flutter's Runner, RN/Expo/Capacitor/Cordova) — the package to integrate is
    # the JS/Dart one, not "ios"/"android". Both native rules share the same guard,
    # or such a project reports a spurious native co-candidate (ambiguous → the
    # skill needlessly asks which package to integrate).
    _hybrid_js = pj and any(d in deps for d in ("react-native", "expo", "@capacitor/core", "cordova"))
    _has_capacitor = _has(pkgdir, "capacitor.config.ts", "capacitor.config.js", "capacitor.config.json")
    _cross_platform = _has(pkgdir, "pubspec.yaml") or _hybrid_js or _has_capacitor
    if ((_has(pkgdir, "Podfile") or _glob_any(pkgdir, r"\.xcodeproj$", r"\.xcworkspace$") or _appdelegate(pkgdir))
            and not _cross_platform):
        candidates.append(("ios", "Podfile / xcodeproj / AppDelegate"))
    gradle_file = (_has(pkgdir, "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
                   or _find_file(pkgdir, "build.gradle") or _find_file(pkgdir, "build.gradle.kts"))
    if (gradle_file
            and _find_file(pkgdir, "AndroidManifest.xml")
            and not _cross_platform):
        candidates.append(("android", "build.gradle + AndroidManifest.xml"))
    if pj and any(w in deps for w in ("next", "react-dom", "vue", "@angular/core", "svelte", "vite")):
        candidates.append(("web", "web framework dep"))
    elif _has(pkgdir, "index.html") and not candidates:
        candidates.append(("web", "index.html, no native project"))

    platform = candidates[0][0] if candidates else None
    signals = [c[1] for c in candidates]
    ambiguous = len(candidates) != 1
    rel = os.path.relpath(pkgdir, root) if root else os.path.relpath(pkgdir)
    return {
        "dir": rel or ".",
        "platform": platform,
        "candidates": [c[0] for c in candidates],
        "language": _language(pkgdir, platform),
        "package_manager": _pkg_manager(pkgdir, platform),
        "signals": signals,
    }, ambiguous


def _app_has_expo(pkgdir, app_cfg):
    try:
        with open(os.path.join(pkgdir, app_cfg), encoding="utf-8") as f:
            return "expo" in f.read()
    except Exception:
        return False


def _appdelegate(pkgdir):
    return bool(_find_file(pkgdir, "AppDelegate.swift") or _find_file(pkgdir, "AppDelegate.m"))


def _find_swift(root, max_depth=4):
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        if dirpath[len(root):].count(os.sep) > max_depth:
            dirnames[:] = []
            continue
        if any(f.endswith(".swift") for f in filenames):
            return True
    return False


def _glob_any(pkgdir, *patterns):
    try:
        for name in os.listdir(pkgdir):
            for p in patterns:
                if re.search(p, name):
                    return name
    except Exception:
        pass
    return None


def _find_file(root, target, max_depth=4):
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        depth = dirpath[len(root):].count(os.sep)
        if depth > max_depth:
            dirnames[:] = []
            continue
        if target in filenames:
            return os.path.join(dirpath, target)
    return None


def _language(pkgdir, platform):
    if platform in ("web", "react-native", "expo", "capacitor", "cordova"):
        pj = _pkg_json(pkgdir)
        if pj and ("typescript" in _deps(pj) or _has(pkgdir, "tsconfig.json")):
            return "typescript"
        return "javascript"
    if platform == "android":
        return "kotlin" if _find_file(pkgdir, "MainActivity.kt") or _glob_any(pkgdir, r"\.kts$") else "java"
    if platform == "ios":
        if _find_file(pkgdir, "AppDelegate.swift") or _glob_any(pkgdir, r"\.swift$") or _find_swift(pkgdir):
            return "swift"
        return "objc"
    if platform == "flutter":
        return "dart"
    return None


def _pkg_manager(pkgdir, platform):
    lock = _has(pkgdir, "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock",
                "Podfile.lock", "Package.resolved", "pubspec.lock")
    mapping = {
        "package-lock.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm",
        "bun.lock": "bun", "Podfile.lock": "cocoapods", "Package.resolved": "spm",
        "pubspec.lock": "pub",
    }
    if platform == "android":
        return "gradle"
    return mapping.get(lock)


def find_packages(root):
    """Return package dirs. Monorepo-aware via workspaces / marker files."""
    root = os.path.abspath(root)
    pkgs = set()
    pj = _pkg_json(root)
    monorepo = False
    if pj and pj.get("workspaces"):
        monorepo = True
    for marker in ("pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json"):
        if os.path.exists(os.path.join(root, marker)):
            monorepo = True
    # Always consider root itself.
    pkgs.add(root)
    if monorepo:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            if dirpath == root:
                continue
            if "package.json" in filenames or "pubspec.yaml" in filenames or "build.gradle" in filenames:
                pkgs.add(dirpath)
    return sorted(pkgs), monorepo


PRIOR_PATTERNS = [
    (r"com\.onesignal:OneSignal", "gradle dependency"),
    (r"react-native-onesignal", "RN package"),
    (r"onesignal_flutter", "flutter package"),
    (r"onesignal-cordova-plugin", "cordova plugin"),
    (r"@onesignal/capacitor-plugin", "capacitor plugin"),
    (r"OneSignalXCFramework|OneSignal-iOS-SDK", "iOS SDK"),
    (r"OneSignal\.(init|initialize|initWithContext)", "init call"),
    (r"onesignal:managed", "managed marker"),
    (r"OneSignalSDKWorker", "web service worker"),
]
SCAN_EXTS = {".json", ".gradle", ".kts", ".swift", ".m", ".kt", ".java", ".dart",
             ".ts", ".tsx", ".js", ".jsx", ".yaml", ".yml", ".html", ".xml", ".rb"}


def detect_prior(root):
    root = os.path.abspath(root)
    evidence = []
    compiled = [(re.compile(p), label) for p, label in PRIOR_PATTERNS]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            ext = os.path.splitext(fn)[1]
            if ext not in SCAN_EXTS and fn != "Podfile":
                continue
            fp = os.path.join(dirpath, fn)
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        for rx, label in compiled:
                            if rx.search(line):
                                evidence.append({
                                    "file": os.path.relpath(fp, root),
                                    "line": i,
                                    "match": label,
                                })
                                break
            except Exception:
                continue
            if len(evidence) >= 50:
                break
    return {"found": bool(evidence), "evidence": evidence}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    if not os.path.isdir(root):
        sys.stderr.write(f"ERROR: not a directory: {root}\n")
        sys.exit(2)
    pkg_dirs, monorepo = find_packages(root)
    packages = []
    any_ambiguous = False
    for d in pkg_dirs:
        info, ambiguous = detect_platform(d, root=os.path.abspath(root))
        if info["platform"] is None and d != os.path.abspath(root):
            continue  # skip noise sub-packages with no platform signal
        if info["platform"] is None and len(pkg_dirs) > 1:
            continue
        packages.append(info)
        any_ambiguous = any_ambiguous or ambiguous
    packages = [p for p in packages if p["platform"] or len(packages) == 1]
    out = {
        "root": os.path.abspath(root),
        "monorepo": monorepo,
        "packages": packages,
        "prior_onesignal": detect_prior(root),
        "ambiguous": any_ambiguous or len(packages) > 1 or (packages and packages[0]["platform"] is None),
        "guidance": ("Multiple packages or ambiguous signals — ASK the user which "
                     "package(s) to integrate; do not guess."
                     if (any_ambiguous or len(packages) > 1) else
                     "Single unambiguous platform detected."),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
