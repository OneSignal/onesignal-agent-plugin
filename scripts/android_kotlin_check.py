#!/usr/bin/env python3
"""Decide, deterministically, whether an Android host's Kotlin toolchain can
build the OneSignal SDK — and if not, exactly what floor it needs.

Why this exists: OneSignal's Android SDK (its `otel` / OpenTelemetry submodule)
transitively drags in a newer `kotlin-stdlib` than the SDK's own POM declares
(5.9.1's POM says 1.9.25, but the resolved graph pins 2.2.20 via OpenTelemetry).
Gradle's highest-wins resolution then requires a Kotlin *compiler* that can read
that stdlib's metadata. An eval trial bumped the host to Kotlin 2.0.0 — still
below the resolved 2.2.20 — so the build failed AND the customer's toolchain was
mutated for nothing. This script reads the *actual resolved* stdlib and the
host's declared Kotlin version and returns a clear verdict, so the agent never
guesses the floor or bumps to an insufficient version.

The floor is not fetchable from Maven metadata (it's buried in OpenTelemetry's
transitive graph), so the resolved value comes from Gradle itself:

    ./gradlew -q :app:dependencies --configuration debugRuntimeClasspath \
        | android_kotlin_check.py <project_dir> --deps -

Usage:
    android_kotlin_check.py <project_dir> --deps <file|->
    android_kotlin_check.py <project_dir>          # host version only (no verdict)

Exit: 0 build-compatible OR host-only mode; 1 bump/blocker required; 2 usage.
"""
import argparse
import json
import os
import re
import sys

KOTLIN_PLUGIN_PATTERNS = [
    re.compile(r'org\.jetbrains\.kotlin\.android["\')]?\s*\)?\s*version\s*["\']([0-9]+\.[0-9]+\.[0-9]+)'),
    re.compile(r'kotlin\(["\']android["\']\)\s*version\s*["\']([0-9]+\.[0-9]+\.[0-9]+)'),
    re.compile(r'org\.jetbrains\.kotlin:kotlin-gradle-plugin:([0-9]+\.[0-9]+\.[0-9]+)'),
]
# version-catalog fallback: kotlin = "1.9.24"
CATALOG_PATTERN = re.compile(r'^\s*kotlin\s*=\s*["\']([0-9]+\.[0-9]+\.[0-9]+)["\']', re.M)
STDLIB_PATTERN = re.compile(r'org\.jetbrains\.kotlin:kotlin-stdlib(?:-common)?:([0-9]+\.[0-9]+\.[0-9]+)(?:\s*->\s*([0-9]+\.[0-9]+\.[0-9]+))?')


def _mm(v):
    """(major, minor) tuple for comparison."""
    p = v.split(".")
    return (int(p[0]), int(p[1]))


def read_host_kotlin(project_dir):
    candidates = []
    for name in ("build.gradle.kts", "build.gradle"):
        for base in (project_dir, os.path.join(project_dir, "app")):
            fp = os.path.join(base, name)
            if os.path.isfile(fp):
                candidates.append(fp)
    for fp in candidates:
        try:
            text = open(fp, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for rx in KOTLIN_PLUGIN_PATTERNS:
            m = rx.search(text)
            if m:
                return m.group(1), os.path.relpath(fp, project_dir)
    # version catalog fallback
    toml = os.path.join(project_dir, "gradle", "libs.versions.toml")
    if os.path.isfile(toml):
        m = CATALOG_PATTERN.search(open(toml, encoding="utf-8", errors="ignore").read())
        if m:
            return m.group(1), "gradle/libs.versions.toml"
    return None, None


def read_resolved_stdlib(deps_text):
    """Return the highest resolved kotlin-stdlib version in a deps tree."""
    best = None
    for m in STDLIB_PATTERN.finditer(deps_text):
        resolved = m.group(2) or m.group(1)  # after '->' if present
        if best is None or _mm(resolved) > _mm(best) or (
            _mm(resolved) == _mm(best) and resolved > best
        ):
            best = resolved
    return best


def main():
    ap = argparse.ArgumentParser(description="OneSignal Android Kotlin-floor check (deterministic).")
    ap.add_argument("project_dir")
    ap.add_argument("--deps", help="path to `gradlew :app:dependencies` output, or - for stdin")
    args = ap.parse_args()

    if not os.path.isdir(args.project_dir):
        sys.stderr.write(f"ERROR: not a directory: {args.project_dir}\n")
        sys.exit(2)

    host_kotlin, host_src = read_host_kotlin(args.project_dir)
    out = {"host_kotlin": host_kotlin, "host_kotlin_source": host_src}

    if not args.deps:
        out["verdict"] = "host_only"
        out["guidance"] = (
            "Host Kotlin version read. To decide if a bump is needed, re-run with "
            "the resolved dependency tree: "
            "`./gradlew -q :app:dependencies --configuration debugRuntimeClasspath | "
            "android_kotlin_check.py <project_dir> --deps -`"
        )
        print(json.dumps(out, indent=2))
        return

    deps_text = sys.stdin.read() if args.deps == "-" else open(args.deps, encoding="utf-8", errors="ignore").read()
    resolved = read_resolved_stdlib(deps_text)
    out["resolved_kotlin_stdlib"] = resolved

    if resolved is None:
        out["verdict"] = "unknown"
        out["guidance"] = "No kotlin-stdlib found in the deps output — check the configuration name."
        print(json.dumps(out, indent=2)); sys.exit(2)
    if host_kotlin is None:
        r_maj, r_min = _mm(resolved)
        out["verdict"] = "unknown_host"
        out["required_floor"] = f">= {r_maj}.{max(0, r_min - 1)}"
        out["guidance"] = ("Could not read the host Kotlin version. The resolved stdlib "
                           f"{resolved} requires a Kotlin compiler {out['required_floor']}. "
                           "Confirm the host's Kotlin plugin version manually.")
        print(json.dumps(out, indent=2)); sys.exit(1)

    # A Kotlin compiler reads stdlib metadata up to one minor ahead of itself
    # (the build error states "compiler 2.0.0 can read versions up to 2.1.0").
    # Same major: compatible if host_minor + 1 >= resolved_minor. Higher major
    # host is compatible; lower major host is not.
    (h_maj, h_min), (r_maj, r_min) = _mm(host_kotlin), _mm(resolved)
    compatible = h_maj > r_maj or (h_maj == r_maj and h_min + 1 >= r_min)
    out["compatible"] = compatible
    if compatible:
        out["verdict"] = "compatible"
        out["guidance"] = f"Host Kotlin {host_kotlin} can build against resolved stdlib {resolved}. No toolchain change needed."
        print(json.dumps(out, indent=2))
        return

    # Minimum compatible host: same major, minor one below the resolved stdlib
    # (a compiler reads one minor ahead), floored at .0.
    floor = f"{r_maj}.{max(0, r_min - 1)}"
    out["verdict"] = "bump_or_blocker"
    out["required_floor"] = f">= {floor}"
    out["guidance"] = (
        f"Host Kotlin {host_kotlin} is BELOW the resolved kotlin-stdlib {resolved} that the "
        f"OneSignal SDK pulls in transitively (via its OpenTelemetry module). The {host_kotlin} "
        f"compiler cannot read {resolved} metadata, so the build will fail. This is a real "
        f"decision to SURFACE to the user, not a silent change:\n"
        f"  (A) Bump the host Kotlin Gradle plugin to at least {floor}.x — a real change to their "
        f"build config; get explicit approval. Do NOT bump to an intermediate version below {floor} "
        f"(e.g. 2.0.0 when {floor} is required) — it still fails and mutates their build for nothing.\n"
        f"  (B) If they can't move off Kotlin {host_kotlin}, this is a hard compatibility blocker. "
        f"resolve_sdk_version.py only serves the current stable/current pins, so it will NOT hand you an "
        f"older line — do NOT guess one. Ask the user to name a specific older OneSignal SDK version (from "
        f"the SDK release notes), confirm it resolves a kotlin-stdlib their compiler can read, and pin "
        f"exactly that."
    )
    print(json.dumps(out, indent=2))
    sys.exit(1)


if __name__ == "__main__":
    main()
