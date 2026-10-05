#!/usr/bin/env python3
"""Build and check the self-contained skill bundle for the OpenAI plugin directory.

The directory accepts a zip whose top level is one skill root or a directory of
skill roots. Each skill root must be self-contained. In this repository the 3
skills share `scripts/`, `references/`, and `endpoint.conf` at the plugin root,
so this script builds a copy in which every skill folder carries its own copy
of the shared files, then rewrites the reference links to point inside the
skill folder.

Usage:
    package_directory_bundle.py                 # write onesignal-skills-<version>.zip in the cwd
    package_directory_bundle.py --zip <path>    # write the zip to <path>
    package_directory_bundle.py --out <dir>     # leave the unzipped bundle at <dir>
    package_directory_bundle.py --check         # build in a temp dir, run the gate, write nothing
    package_directory_bundle.py --ref <git-ref> # read the tree from a git ref instead of the working tree
    package_directory_bundle.py --self-test     # run the gate against known-good and known-bad trees

Exit: 0 when the gate passes, 1 when it fails, 2 on a usage error.

Path convention that the gate enforces in the source tree:
  * a skill refers to a shared script only as `<plugin>/scripts/<name>`;
  * a skill refers to a reference only as `[...](../../references/<name>.md)`;
  * every `SKILL.md` carries the walk-up definition of `<plugin>`.
Any other spelling fails `--check`.

Python 3.7+, standard library only.
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

SKILLS = ("setup", "credentials", "verify")

RUNTIME_SCRIPTS = (
    "android_kotlin_check.py",
    "checkpoint.sh",
    "detect_platform.py",
    "onesignal_api.py",
    "resolve_sdk_version.py",
    "scan_secrets.py",
    "verify_integration.py",
)

# Which runtime scripts each skill calls. compile_check_ios.sh and this
# packager are development tooling and are never copied into the bundle.
SCRIPTS_FOR_SKILL = {
    "setup": RUNTIME_SCRIPTS,
    "credentials": ("checkpoint.sh", "onesignal_api.py"),
    "verify": ("checkpoint.sh", "onesignal_api.py"),
}

SHARED_ROOT_FILES = ("endpoint.conf",)

VERSION_FILES = {
    "claude": ".claude-plugin/plugin.json",
    "codex": ".codex-plugin/plugin.json",
    "cursor": ".cursor-plugin/plugin.json",
    "checkpoint": "scripts/checkpoint.sh",
}

# Both fragments must appear in every SKILL.md. Together they identify the
# walk-up rule that makes `<plugin>` resolve in the repository and in the bundle.
WALK_UP_MARKERS = ("walk up", "contains a `scripts/` folder")

PLUGIN_SCRIPT_RE = re.compile(r"<plugin>/scripts/([A-Za-z0-9_]+\.(?:py|sh))")
BARE_SCRIPT_RE = re.compile(r"(?<!<plugin>/)\bscripts/[A-Za-z0-9_]")
SOURCE_REFERENCE_RE = re.compile(r"\.\./\.\./references/([A-Za-z0-9_\-]+\.md)")
BARE_REFERENCE_RE = re.compile(r"(?<!\.\./\.\./)\breferences/[A-Za-z0-9_]")
BUNDLE_REFERENCE_RE = re.compile(r"\breferences/([A-Za-z0-9_\-]+\.md)")
# The target of an inline link, with or without a quoted title after it.
MARKDOWN_LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HOST_PATH_RE = re.compile(r"CLAUDE_PLUGIN_ROOT|PLUGIN_ROOT\}|/Users/|/home/")
# A `../` path segment. An ellipsis such as `.../identity` is prose, not a path.
PARENT_SEGMENT_RE = re.compile(r"(?<![.\w])\.\./")

COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


class GateError(Exception):
    """Raised with the list of findings when the gate fails."""

    def __init__(self, findings):
        super().__init__("\n".join(findings))
        self.findings = findings


# ---------------------------------------------------------------------------
# Source tree
# ---------------------------------------------------------------------------

def repository_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def export_ref(ref, dest):
    """Extract `git archive <ref>` into dest."""
    proc = subprocess.run(
        ["git", "-C", repository_root(), "archive", "--format=tar", ref],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise GateError(["git archive %s failed: %s" % (ref, proc.stderr.decode(errors="replace").strip())])
    with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as tar:
        try:
            tar.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 has no filter argument
            tar.extractall(dest)


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def markdown_files(directory):
    for root, dirs, files in os.walk(directory):
        dirs[:] = sorted(d for d in dirs if d not in ("__pycache__",))
        for name in sorted(files):
            if name.endswith(".md"):
                yield os.path.join(root, name)


def relpath(path, start):
    return os.path.relpath(path, start).replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def read_versions(source):
    """Return the 4 version values; a missing or malformed file reads as None."""
    versions = {}
    for key in ("claude", "codex", "cursor"):
        path = os.path.join(source, VERSION_FILES[key])
        try:
            with open(path, encoding="utf-8") as handle:
                versions[key] = json.load(handle).get("version")
        except (OSError, ValueError, AttributeError):
            versions[key] = None
    try:
        checkpoint = read_text(os.path.join(source, VERSION_FILES["checkpoint"]))
    except OSError:
        checkpoint = ""
    match = re.search(r'^PLUGIN_VERSION="([^"]+)"', checkpoint, re.M)
    versions["checkpoint"] = match.group(1) if match else None
    return versions


def check_versions(source, findings):
    versions = read_versions(source)
    values = set(versions.values())
    if None in values or len(values) != 1:
        findings.append(
            "version mismatch: %s"
            % ", ".join("%s=%s" % (VERSION_FILES[k], v) for k, v in sorted(versions.items()))
        )
        return None
    return versions["claude"]


# ---------------------------------------------------------------------------
# Source gate: the path convention
# ---------------------------------------------------------------------------

def check_source(source):
    findings = []
    version = check_versions(source, findings)
    skills_dir = os.path.join(source, "skills")
    scripts_dir = os.path.join(source, "scripts")
    references_dir = os.path.join(source, "references")

    if os.path.isdir(skills_dir):
        for name in sorted(os.listdir(skills_dir)):
            if os.path.isdir(os.path.join(skills_dir, name)) and name not in SKILLS:
                findings.append("skills/%s is not in the SKILLS table and would not ship in the bundle" % name)

    for skill in SKILLS:
        skill_dir = os.path.join(skills_dir, skill)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            findings.append("skills/%s/SKILL.md is missing" % skill)
            continue
        allowed_scripts = set(SCRIPTS_FOR_SKILL[skill])

        for path in markdown_files(skill_dir):
            rel = relpath(path, source)
            text = read_text(path)
            for lineno, line in enumerate(text.splitlines(), 1):
                if BARE_SCRIPT_RE.search(line):
                    findings.append("%s:%d: script path without the <plugin>/ prefix" % (rel, lineno))
                if BARE_REFERENCE_RE.search(line):
                    findings.append("%s:%d: reference path without the ../../ prefix" % (rel, lineno))
                if PARENT_SEGMENT_RE.search(line.replace("../../references/", "")):
                    findings.append("%s:%d: relative path leaves the skill folder (only ../../references/ is allowed)" % (rel, lineno))
                if HOST_PATH_RE.search(line):
                    findings.append("%s:%d: host-specific path or environment variable" % (rel, lineno))
                for name in PLUGIN_SCRIPT_RE.findall(line):
                    if name not in allowed_scripts:
                        findings.append("%s:%d: %s is not in the script table for the %s skill" % (rel, lineno, name, skill))
                    elif not os.path.isfile(os.path.join(scripts_dir, name)):
                        findings.append("%s:%d: scripts/%s does not exist" % (rel, lineno, name))
                for name in SOURCE_REFERENCE_RE.findall(line):
                    if not os.path.isfile(os.path.join(references_dir, name)):
                        findings.append("%s:%d: references/%s does not exist" % (rel, lineno, name))

        skill_text = read_text(skill_md)
        for marker in WALK_UP_MARKERS:
            if marker not in skill_text:
                findings.append("skills/%s/SKILL.md: the <plugin> walk-up paragraph is missing (%r not found)" % (skill, marker))

    for name in SHARED_ROOT_FILES:
        if not os.path.isfile(os.path.join(source, name)):
            findings.append("%s is missing at the plugin root" % name)
    for name in RUNTIME_SCRIPTS:
        if not os.path.isfile(os.path.join(scripts_dir, name)):
            findings.append("scripts/%s is missing" % name)

    if findings:
        raise GateError(findings)
    return version


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_bundle(source, bundle_dir):
    """Copy the 3 skills into bundle_dir, vendor the shared files, rewrite links."""
    os.makedirs(bundle_dir, exist_ok=True)
    for skill in SKILLS:
        dest = os.path.join(bundle_dir, skill)
        shutil.copytree(os.path.join(source, "skills", skill), dest, ignore=COPY_IGNORE)
        shutil.copytree(os.path.join(source, "references"), os.path.join(dest, "references"), ignore=COPY_IGNORE)
        for name in SHARED_ROOT_FILES:
            shutil.copy2(os.path.join(source, name), os.path.join(dest, name))
        scripts_dest = os.path.join(dest, "scripts")
        os.makedirs(scripts_dest)
        for name in SCRIPTS_FOR_SKILL[skill]:
            shutil.copy2(os.path.join(source, "scripts", name), os.path.join(scripts_dest, name))
        for path in markdown_files(dest):
            if os.path.commonpath([path, os.path.join(dest, "references")]) == os.path.join(dest, "references"):
                continue
            text = read_text(path)
            rewritten = text.replace("../../references/", "references/")
            if rewritten != text:
                write_text(path, rewritten)


# ---------------------------------------------------------------------------
# Bundle gate: every path resolves inside its skill folder
# ---------------------------------------------------------------------------

def is_external_link(target):
    return target.startswith(("http://", "https://", "mailto:", "#"))


def check_bundle(bundle_dir):
    findings = []
    for skill in SKILLS:
        skill_dir = os.path.join(bundle_dir, skill)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            findings.append("%s/SKILL.md is missing from the bundle" % skill)
            continue
        for name in SHARED_ROOT_FILES:
            if not os.path.isfile(os.path.join(skill_dir, name)):
                findings.append("%s/%s is missing from the bundle" % (skill, name))
        if not os.path.isfile(os.path.join(skill_dir, "scripts", "checkpoint.sh")):
            findings.append("%s/scripts/checkpoint.sh is missing from the bundle" % skill)

        skill_text = read_text(skill_md)
        for marker in WALK_UP_MARKERS:
            if marker not in skill_text:
                findings.append("%s/SKILL.md: the <plugin> walk-up paragraph is missing (%r not found)" % (skill, marker))

        for path in markdown_files(skill_dir):
            rel = relpath(path, bundle_dir)
            in_references = relpath(path, skill_dir).startswith("references/")
            text = read_text(path)
            for lineno, line in enumerate(text.splitlines(), 1):
                if PARENT_SEGMENT_RE.search(line):
                    findings.append("%s:%d: a relative path still leaves the skill folder" % (rel, lineno))
                if HOST_PATH_RE.search(line):
                    findings.append("%s:%d: host-specific path or environment variable" % (rel, lineno))
                for name in PLUGIN_SCRIPT_RE.findall(line):
                    if not os.path.isfile(os.path.join(skill_dir, "scripts", name)):
                        findings.append("%s:%d: <plugin>/scripts/%s does not resolve inside %s/" % (rel, lineno, name, skill))
                if not in_references:
                    for name in BUNDLE_REFERENCE_RE.findall(line):
                        if not os.path.isfile(os.path.join(skill_dir, "references", name)):
                            findings.append("%s:%d: references/%s does not resolve inside %s/" % (rel, lineno, name, skill))
                for target in MARKDOWN_LINK_RE.findall(line):
                    if is_external_link(target):
                        continue
                    target_path = target.split("#", 1)[0]
                    if not target_path:
                        continue
                    resolved = os.path.normpath(os.path.join(os.path.dirname(path), target_path))
                    inside = os.path.commonpath([resolved, skill_dir]) == skill_dir
                    if not inside or not os.path.exists(resolved):
                        findings.append("%s:%d: link target %s does not resolve inside %s/" % (rel, lineno, target, skill))
    if findings:
        raise GateError(findings)


# ---------------------------------------------------------------------------
# Zip
# ---------------------------------------------------------------------------

def write_zip(bundle_dir, zip_path):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(bundle_dir):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(root, name)
                archive.write(full, relpath(full, bundle_dir))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def copy_source_subset(source, dest):
    """Copy only the parts of the tree the packager reads."""
    os.makedirs(dest)
    for name in ("skills", "references", "scripts", ".claude-plugin", ".codex-plugin", ".cursor-plugin"):
        shutil.copytree(os.path.join(source, name), os.path.join(dest, name), ignore=COPY_IGNORE)
    for name in SHARED_ROOT_FILES:
        shutil.copy2(os.path.join(source, name), os.path.join(dest, name))


def run_gate(source, workdir):
    check_source(source)
    bundle_dir = os.path.join(workdir, "bundle")
    build_bundle(source, bundle_dir)
    check_bundle(bundle_dir)
    return bundle_dir


def expect_failure(label, source, workdir, needle):
    try:
        run_gate(source, workdir)
    except GateError as error:
        if any(needle in finding for finding in error.findings):
            print("self-test: %s -> rejected as expected" % label)
            return True
        print("self-test: %s -> rejected, but not for the expected reason:\n  %s" % (label, "\n  ".join(error.findings)))
        return False
    print("self-test: %s -> ACCEPTED, but the gate should have failed" % label)
    return False


def self_test(source):
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        try:
            run_gate(source, os.path.join(tmp, "good"))
            print("self-test: current tree -> passes")
        except GateError as error:
            print("self-test: current tree -> FAILS:\n  %s" % "\n  ".join(error.findings))
            ok = False

        bad = os.path.join(tmp, "bad-script-path")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "verify", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\nRun `bash scripts/checkpoint.sh flush` before you finish.\n")
        ok = expect_failure("script path without <plugin>/", bad, os.path.join(tmp, "bad-script-path-work"),
                            "script path without the <plugin>/ prefix") and ok

        bad = os.path.join(tmp, "bad-reference-link")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "setup", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\nSee [the matrix](../references/platform-matrix.md).\n")
        ok = expect_failure("reference link with a single ../", bad, os.path.join(tmp, "bad-reference-link-work"),
                            "reference path without the ../../ prefix") and ok

        bad = os.path.join(tmp, "bad-missing-reference")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "credentials", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\nSee [the notes](../../references/does-not-exist.md).\n")
        ok = expect_failure("link to a missing reference", bad, os.path.join(tmp, "bad-missing-reference-work"),
                            "references/does-not-exist.md does not exist") and ok

        bad = os.path.join(tmp, "bad-unknown-skill")
        copy_source_subset(source, bad)
        os.makedirs(os.path.join(bad, "skills", "extra"))
        write_text(os.path.join(bad, "skills", "extra", "SKILL.md"), "---\nname: extra\n---\n")
        ok = expect_failure("skill directory missing from the SKILLS table", bad, os.path.join(tmp, "bad-unknown-skill-work"),
                            "skills/extra is not in the SKILLS table") and ok

        bad = os.path.join(tmp, "bad-titled-link")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "verify", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write('\nSee [the notes](does-not-exist.md "Notes").\n')
        ok = expect_failure("titled link to a missing file", bad, os.path.join(tmp, "bad-titled-link-work"),
                            "link target does-not-exist.md does not resolve inside verify/") and ok

        bad = os.path.join(tmp, "bad-version-mismatch")
        copy_source_subset(source, bad)
        path = os.path.join(bad, VERSION_FILES["checkpoint"])
        write_text(path, re.sub(r'^PLUGIN_VERSION="[^"]*"', 'PLUGIN_VERSION="0.0.0"', read_text(path), count=1, flags=re.M))
        ok = expect_failure("version mismatch across the 4 files", bad, os.path.join(tmp, "bad-version-mismatch-work"),
                            "version mismatch") and ok

        bad = os.path.join(tmp, "bad-walk-up-marker")
        copy_source_subset(source, bad)
        path = os.path.join(bad, "skills", "setup", "SKILL.md")
        write_text(path, read_text(path).replace("walk up", "go up"))
        ok = expect_failure("SKILL.md without the walk-up paragraph", bad, os.path.join(tmp, "bad-walk-up-marker-work"),
                            "the <plugin> walk-up paragraph is missing") and ok

        bad = os.path.join(tmp, "bad-host-path")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "setup", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\nRun `bash ${CLAUDE_PLUGIN_ROOT}/scripts/checkpoint.sh flush`.\n")
        ok = expect_failure("host environment variable in skill text", bad, os.path.join(tmp, "bad-host-path-work"),
                            "host-specific path or environment variable") and ok
    return ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="build in a temp dir, run the gate, write nothing")
    parser.add_argument("--out", metavar="DIR", help="leave the unzipped bundle at DIR (DIR must not exist or must be empty)")
    parser.add_argument("--zip", metavar="PATH", help="write the zip to PATH instead of ./onesignal-skills-<version>.zip")
    parser.add_argument("--ref", metavar="GIT_REF", help="read the tree from `git archive GIT_REF` instead of the working tree")
    parser.add_argument("--self-test", action="store_true", help="run the gate against known-good and known-bad trees")
    args = parser.parse_args(argv)

    if args.check and (args.out or args.zip):
        parser.error("--check writes nothing; do not combine it with --out or --zip")
    if args.out and os.path.isdir(args.out) and os.listdir(args.out):
        parser.error("--out %s exists and is not empty" % args.out)

    with tempfile.TemporaryDirectory() as tmp:
        if args.ref:
            source = os.path.join(tmp, "source")
            os.makedirs(source)
            try:
                export_ref(args.ref, source)
            except GateError as error:
                print(error, file=sys.stderr)
                return 1
        else:
            source = repository_root()

        if args.self_test:
            return 0 if self_test(source) else 1

        try:
            version = check_source(source)
            bundle_dir = os.path.join(tmp, "bundle")
            build_bundle(source, bundle_dir)
            check_bundle(bundle_dir)
        except GateError as error:
            print("bundle check FAILED:", file=sys.stderr)
            for finding in error.findings:
                print("  " + finding, file=sys.stderr)
            return 1

        if args.check:
            print("bundle check passed (version %s; nothing written)" % version)
            return 0

        if args.out:
            out = os.path.abspath(args.out)
            if os.path.isdir(out):
                os.rmdir(out)
            shutil.copytree(bundle_dir, out)
            print("bundle written to %s" % out)

        if args.zip or not args.out:
            zip_path = os.path.abspath(args.zip or "onesignal-skills-%s.zip" % version)
            write_zip(bundle_dir, zip_path)
            print("zip written to %s" % zip_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
