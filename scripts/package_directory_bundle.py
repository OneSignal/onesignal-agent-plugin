#!/usr/bin/env python3
"""Build and check the self-contained skill bundle for the OpenAI plugin directory.

The directory's Skills-only upload accepts a zip with a plugin root at the top
level: `.codex-plugin/plugin.json`, the skills under `skills/`, and the brand
assets under `assets/`. The manifest `name` becomes the skill namespace in
Codex (`onesignal:setup`). Each skill folder must be self-contained. In this
repository the 3 skills share `scripts/`, `references/`, and `endpoint.conf`
at the plugin root, so this script builds a copy in which every skill folder
carries its own copy of the shared files, then rewrites the reference links to
point inside the skill folder. The bundled manifest is the repository manifest
without `mcpServers`: a Skills-only upload rejects MCP configuration, and the
listing's MCP server is attached in the portal.

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
  * a skill runs a Python helper through an interpreter, as
    `python3 <plugin>/scripts/<name>.py` (`python` and `py -3` are the
    preflight fallbacks), never by the path alone; the path may be quoted,
    as in `python3 "<plugin>/scripts/<name>.py"`;
  * a skill refers to a reference only as `[...](../../references/<name>.md)`;
  * every `SKILL.md` carries the walk-up definition of `<plugin>`.
Any other spelling fails `--check`.

`--self-test` also strips the exec bit from every Python helper in a built
bundle and runs each one through the interpreter, so an install that drops
file modes cannot break the helper calls.

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

CODEX_MANIFEST = ".codex-plugin/plugin.json"
ASSETS_DIR = "assets"
BUNDLE_SKILLS_PATH = "./skills/"
# The directory keys the plugin on this field. Lowercase letters, digits, and
# single hyphens are what the submission rules accept.
PLUGIN_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BRAND_ASSET_FIELDS = ("logo", "composerIcon")

VERSION_FILES = {
    "claude": ".claude-plugin/plugin.json",
    "codex": ".codex-plugin/plugin.json",
    "checkpoint": "scripts/checkpoint.sh",
}

# Both fragments must appear in every SKILL.md. Together they identify the
# walk-up rule that makes `<plugin>` resolve in the repository and in the bundle.
WALK_UP_MARKERS = ("walk up", "contains a `scripts/` folder")

PLUGIN_SCRIPT_RE = re.compile(r"<plugin>/scripts/([A-Za-z0-9_]+\.(?:py|sh))")
# The interpreters the setup preflight can select, each followed by the space
# that separates it from the script path.
INTERPRETER_PREFIXES = ("python3 ", "python ", "py -3 ")
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
    """Return the 3 version values; a missing or malformed file reads as None."""
    versions = {}
    for key in ("claude", "codex"):
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
# Manifest
# ---------------------------------------------------------------------------

def read_manifest(source):
    """Return the Codex manifest as a dict, or None when it is missing or malformed."""
    try:
        with open(os.path.join(source, CODEX_MANIFEST), encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def bundle_manifest(manifest):
    """The manifest that ships in the bundle: the repository manifest without MCP configuration."""
    bundled = dict(manifest)
    bundled.pop("mcpServers", None)
    bundled["skills"] = BUNDLE_SKILLS_PATH
    return bundled


def check_manifest(source, findings):
    """Check the fields the directory reads from the manifest and the files they point to."""
    manifest = read_manifest(source)
    if manifest is None:
        findings.append("%s is missing or is not a JSON object" % CODEX_MANIFEST)
        return
    name = manifest.get("name")
    if not isinstance(name, str) or not PLUGIN_NAME_RE.match(name):
        findings.append("%s: name must be lowercase letters, digits, and single hyphens (got %r)" % (CODEX_MANIFEST, name))
    for key in ("version", "description"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            findings.append("%s: %s is missing" % (CODEX_MANIFEST, key))
    author = manifest.get("author")
    if not isinstance(author, dict) or not author.get("name"):
        findings.append("%s: author.name is missing" % CODEX_MANIFEST)
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        findings.append("%s: interface is missing" % CODEX_MANIFEST)
        return
    for field in BRAND_ASSET_FIELDS:
        path = interface.get(field)
        if not isinstance(path, str) or not path.startswith("./%s/" % ASSETS_DIR):
            findings.append("%s: interface.%s must point into ./%s/ (got %r)" % (CODEX_MANIFEST, field, ASSETS_DIR, path))
        elif not os.path.isfile(os.path.join(source, path[2:])):
            findings.append("%s: interface.%s points to %s, which does not exist" % (CODEX_MANIFEST, field, path))


# ---------------------------------------------------------------------------
# Source gate: the path convention
# ---------------------------------------------------------------------------

def check_source(source):
    findings = []
    version = check_versions(source, findings)
    check_manifest(source, findings)
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
                for match in PLUGIN_SCRIPT_RE.finditer(line):
                    name = match.group(1)
                    if name not in allowed_scripts:
                        findings.append("%s:%d: %s is not in the script table for the %s skill" % (rel, lineno, name, skill))
                    elif not os.path.isfile(os.path.join(scripts_dir, name)):
                        findings.append("%s:%d: scripts/%s does not exist" % (rel, lineno, name))
                    before_path = line[:match.start()].rstrip("\"'")
                    if name.endswith(".py") and not before_path.endswith(INTERPRETER_PREFIXES):
                        findings.append("%s:%d: %s runs without an interpreter prefix (write `python3 <plugin>/scripts/%s`)" % (rel, lineno, name, name))
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
    """Write the manifest and assets, copy the 3 skills under skills/, vendor the shared files, rewrite links."""
    os.makedirs(os.path.join(bundle_dir, os.path.dirname(CODEX_MANIFEST)), exist_ok=True)
    manifest = bundle_manifest(read_manifest(source))
    write_text(os.path.join(bundle_dir, CODEX_MANIFEST), json.dumps(manifest, indent=2) + "\n")
    shutil.copytree(os.path.join(source, ASSETS_DIR), os.path.join(bundle_dir, ASSETS_DIR), ignore=COPY_IGNORE)
    skills_dir = os.path.join(bundle_dir, "skills")
    os.makedirs(skills_dir)
    for skill in SKILLS:
        dest = os.path.join(skills_dir, skill)
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


def check_bundle_manifest(bundle_dir, findings):
    manifest = read_manifest(bundle_dir)
    if manifest is None:
        findings.append("%s is missing from the bundle" % CODEX_MANIFEST)
        return
    if manifest.get("skills") != BUNDLE_SKILLS_PATH:
        findings.append("%s: skills must be %r in the bundle (got %r)" % (CODEX_MANIFEST, BUNDLE_SKILLS_PATH, manifest.get("skills")))
    if "mcpServers" in manifest:
        findings.append("%s: mcpServers must not ship in a Skills-only bundle" % CODEX_MANIFEST)
    if os.path.exists(os.path.join(bundle_dir, ".mcp.json")):
        findings.append(".mcp.json must not ship in a Skills-only bundle")
    interface = manifest.get("interface") or {}
    for field in BRAND_ASSET_FIELDS:
        path = interface.get(field)
        if not isinstance(path, str) or not os.path.isfile(os.path.join(bundle_dir, path)):
            findings.append("%s: interface.%s (%r) does not resolve inside the bundle" % (CODEX_MANIFEST, field, path))


def check_bundle(bundle_dir):
    findings = []
    check_bundle_manifest(bundle_dir, findings)
    for skill in SKILLS:
        skill_dir = os.path.join(bundle_dir, "skills", skill)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            findings.append("skills/%s/SKILL.md is missing from the bundle" % skill)
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
    for name in ("skills", "references", "scripts", ASSETS_DIR, ".claude-plugin", ".codex-plugin"):
        shutil.copytree(os.path.join(source, name), os.path.join(dest, name), ignore=COPY_IGNORE)
    for name in SHARED_ROOT_FILES:
        shutil.copy2(os.path.join(source, name), os.path.join(dest, name))


def run_gate(source, workdir):
    check_source(source)
    bundle_dir = os.path.join(workdir, "bundle")
    build_bundle(source, bundle_dir)
    check_bundle(bundle_dir)
    return bundle_dir


def helpers_run_without_exec_bit(bundle_dir, workdir):
    """Copy the bundle, strip the exec bit from its Python helpers, and run each one through the interpreter.

    Returns the list of helpers that failed. An install path that drops file
    modes must not break the skills, because every call site names the
    interpreter; this check is what proves that in CI.
    """
    copy = os.path.join(workdir, "bundle-no-exec-bit")
    shutil.copytree(bundle_dir, copy)
    failures = []
    with tempfile.TemporaryDirectory() as empty_project:
        for skill in SKILLS:
            scripts_dir = os.path.join(copy, "skills", skill, "scripts")
            for name in sorted(os.listdir(scripts_dir)):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(scripts_dir, name)
                os.chmod(path, 0o644)
                args = [empty_project] if name == "detect_platform.py" else ["--help"]
                proc = subprocess.run([sys.executable, path] + args, capture_output=True)
                if proc.returncode != 0:
                    failures.append("skills/%s/scripts/%s exited %d: %s" % (
                        skill, name, proc.returncode, proc.stderr.decode(errors="replace").strip()))
    return failures


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
            good_bundle = run_gate(source, os.path.join(tmp, "good"))
            print("self-test: current tree -> passes")
        except GateError as error:
            print("self-test: current tree -> FAILS:\n  %s" % "\n  ".join(error.findings))
            good_bundle = None
            ok = False

        if good_bundle is not None:
            failures = helpers_run_without_exec_bit(good_bundle, os.path.join(tmp, "good"))
            if failures:
                print("self-test: Python helpers without the exec bit -> FAIL:\n  %s" % "\n  ".join(failures))
                ok = False
            else:
                print("self-test: Python helpers without the exec bit -> run through the interpreter")

        bad = os.path.join(tmp, "bad-bare-python-call")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "setup", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\nRun `<plugin>/scripts/detect_platform.py` first.\n")
        ok = expect_failure("Python helper called without an interpreter", bad, os.path.join(tmp, "bad-bare-python-call-work"),
                            "runs without an interpreter prefix") and ok

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
        ok = expect_failure("version mismatch across the 3 files", bad, os.path.join(tmp, "bad-version-mismatch-work"),
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

        bad = os.path.join(tmp, "bad-plugin-name")
        copy_source_subset(source, bad)
        path = os.path.join(bad, CODEX_MANIFEST)
        manifest = json.loads(read_text(path))
        manifest["name"] = "OneSignal"
        write_text(path, json.dumps(manifest, indent=2) + "\n")
        ok = expect_failure("display name in the manifest name field", bad, os.path.join(tmp, "bad-plugin-name-work"),
                            "name must be lowercase letters, digits, and single hyphens") and ok

        bad = os.path.join(tmp, "bad-brand-asset")
        copy_source_subset(source, bad)
        os.remove(os.path.join(bad, ASSETS_DIR, "logomark.png"))
        ok = expect_failure("manifest logo that points to a missing file", bad, os.path.join(tmp, "bad-brand-asset-work"),
                            "interface.logo points to ./assets/logomark.png, which does not exist") and ok
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
