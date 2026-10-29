#!/usr/bin/env python3
"""Build and check the self-contained skill bundle for the OpenAI plugin directory.

The directory accepts a zip with a plugin root at the top level:
`.codex-plugin/plugin.json`, `.mcp.json`, the skills under `skills/`, and the
brand assets under `assets/`. The manifest `name` becomes the skill namespace
in Codex. Each skill folder must be self-contained. In this repository the 3
skills share `scripts/`, `references/`, and `endpoint.conf` at the plugin
root, so this script builds a copy in which every skill folder carries its
own copy of the shared files, then rewrites the reference links to point
inside the skill folder. The bundled manifest is the repository manifest with
one change: `name` is the identifier the directory assigned to the published
listing (an update must match it). The MCP declaration ships unchanged: the
published listing has an MCP connection, and the portal rejects an update
that omits it.

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
  * every `SKILL.md` carries the walk-up definition of `<plugin>`;
  * no skill text names a host path or a host variable such as
    `${CLAUDE_PLUGIN_ROOT}`, except the `allowed-tools` frontmatter field,
    where Claude Code substitutes that variable and Codex ignores the field.
Any other spelling fails `--check`.

`--self-test` also runs every Python helper in a built bundle through the
interpreter (`--help` must exit 0), so each helper starts from the bundle
layout. The interpreter-prefix rule above is what keeps a call independent
of the file's exec bit; the self-test does not exercise that bit.

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
MCP_CONFIG = ".mcp.json"
ASSETS_DIR = "assets"
BUNDLE_SKILLS_PATH = "./skills/"
BUNDLE_MCP_PATH = "./.mcp.json"
# The directory keys the plugin on this field. Lowercase letters, digits, and
# single hyphens are what the submission rules accept.
PLUGIN_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# The identifier of the published listing. The portal rejects an update whose
# `name` differs from it (`plugin_name_mismatch`).
DIRECTORY_PLUGIN_NAME = "app-6a3d93b924488191bdb7eb40fc4219e6"
BRAND_ASSET_FIELDS = ("logo", "composerIcon")
# Dark-theme variant of the logo. The directory falls back to the light file when it
# is absent. The manifest has no dark variant of composerIcon; that icon ships its
# own background so it reads on both themes.
OPTIONAL_BRAND_ASSET_FIELDS = ("logoDark",)
# The portal accepts this field only for a plugin with a custom UI
# (`screenshot_configuration_excluded`); this plugin has none.
EXCLUDED_INTERFACE_FIELDS = ("screenshots",)
# Limits the portal applies at submission, which are tighter than the ones at upload.
SUBMISSION_TEXT_LIMITS = {"displayName": 30, "shortDescription": 30}
# A plugin with an MCP connection must list all 4 URLs before submission.
LISTING_URL_FIELDS = ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL")

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
# The one frontmatter field in which a host variable belongs: Claude Code
# substitutes ${CLAUDE_PLUGIN_ROOT} in its Bash rules, and Codex ignores the
# field, so the literal never reaches a command there.
ALLOWED_TOOLS_FIELD = "allowed-tools:"
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


def allowed_tools_lines(text):
    """Return the 1-based line numbers of the `allowed-tools` frontmatter field.

    The set covers the key line and the indented continuation lines of a YAML
    list under it. The host-path check and the bare `scripts/` check skip these
    lines and no others: a Bash rule names the script as
    `${CLAUDE_PLUGIN_ROOT}/scripts/<name>`, which is neither spelling the body
    may use.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return set()
    numbers = set()
    in_field = False
    for lineno, line in enumerate(lines[1:], 2):
        if line.strip() == "---":
            break
        if in_field and line[:1] in (" ", "\t"):
            numbers.add(lineno)
            continue
        in_field = line.startswith(ALLOWED_TOOLS_FIELD)
        if in_field:
            numbers.add(lineno)
    return numbers


def fenced_code_lines(text):
    """Return the 1-based line numbers inside fenced code blocks.

    Only the parent-segment check skips these lines: a `../` inside a fence is
    content the agent writes into the user's project (a pbxproj `rpath`, for
    example), not a link the plugin resolves. Every other check still reads
    fenced lines, because the `<plugin>/scripts/` calls live in them. A fence
    that never closes exempts nothing, so a stray fence cannot hide the rest of
    the file from the check.
    """
    numbers = set()
    block = None
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            if block is None:
                block = []
            else:
                numbers.update(block)
                block = None
            continue
        if block is not None:
            block.append(lineno)
    return numbers


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
    """The manifest that ships in the bundle: the repository manifest under the directory's name."""
    bundled = dict(manifest)
    bundled["name"] = DIRECTORY_PLUGIN_NAME
    bundled["skills"] = BUNDLE_SKILLS_PATH
    bundled["mcpServers"] = BUNDLE_MCP_PATH
    return bundled


def brand_asset_fields(interface):
    """The required asset fields, plus every optional one the manifest sets."""
    return BRAND_ASSET_FIELDS + tuple(f for f in OPTIONAL_BRAND_ASSET_FIELDS if f in interface)


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
    for field, limit in SUBMISSION_TEXT_LIMITS.items():
        value = interface.get(field)
        if not isinstance(value, str) or not value:
            findings.append("%s: interface.%s is missing" % (CODEX_MANIFEST, field))
        elif len(value) > limit:
            findings.append("%s: interface.%s must be %d characters or fewer (got %d)" % (CODEX_MANIFEST, field, limit, len(value)))
    for field in LISTING_URL_FIELDS:
        value = interface.get(field)
        if not isinstance(value, str) or not value.startswith("https://"):
            findings.append("%s: interface.%s must be an HTTPS URL (got %r)" % (CODEX_MANIFEST, field, value))
    for field in brand_asset_fields(interface):
        path = interface.get(field)
        if not isinstance(path, str) or not path.startswith("./%s/" % ASSETS_DIR):
            findings.append("%s: interface.%s must point into ./%s/ (got %r)" % (CODEX_MANIFEST, field, ASSETS_DIR, path))
        elif not os.path.isfile(os.path.join(source, path[2:])):
            findings.append("%s: interface.%s points to %s, which does not exist" % (CODEX_MANIFEST, field, path))
    for field in EXCLUDED_INTERFACE_FIELDS:
        if field in interface:
            findings.append("%s: interface.%s is accepted only for a plugin with a custom UI; remove it" % (CODEX_MANIFEST, field))


def check_mcp_config(source, findings):
    """Check that the MCP declaration the bundle ships is a JSON object with `mcpServers`."""
    try:
        with open(os.path.join(source, MCP_CONFIG), encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError):
        findings.append("%s is missing or is not valid JSON" % MCP_CONFIG)
        return
    if not isinstance(config, dict) or not isinstance(config.get("mcpServers"), dict) or not config["mcpServers"]:
        findings.append("%s: mcpServers must be a non-empty object" % MCP_CONFIG)


# ---------------------------------------------------------------------------
# Source gate: the path convention
# ---------------------------------------------------------------------------

def check_source(source):
    findings = []
    version = check_versions(source, findings)
    check_manifest(source, findings)
    check_mcp_config(source, findings)
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
            allowed_tools = allowed_tools_lines(text) if path == skill_md else set()
            fenced = fenced_code_lines(text)
            for lineno, line in enumerate(text.splitlines(), 1):
                if BARE_SCRIPT_RE.search(line) and lineno not in allowed_tools:
                    findings.append("%s:%d: script path without the <plugin>/ prefix" % (rel, lineno))
                if BARE_REFERENCE_RE.search(line):
                    findings.append("%s:%d: reference path without the ../../ prefix" % (rel, lineno))
                if lineno not in fenced and PARENT_SEGMENT_RE.search(line.replace("../../references/", "")):
                    findings.append("%s:%d: relative path leaves the skill folder (only ../../references/ is allowed)" % (rel, lineno))
                if HOST_PATH_RE.search(line) and lineno not in allowed_tools:
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

    # References are shared by the 3 skills, so the per-skill script table does
    # not apply; the interpreter rule and the file-exists rule do.
    if os.path.isdir(references_dir):
        for path in markdown_files(references_dir):
            rel = relpath(path, source)
            for lineno, line in enumerate(read_text(path).splitlines(), 1):
                for match in PLUGIN_SCRIPT_RE.finditer(line):
                    name = match.group(1)
                    if not os.path.isfile(os.path.join(scripts_dir, name)):
                        findings.append("%s:%d: scripts/%s does not exist" % (rel, lineno, name))
                    before_path = line[:match.start()].rstrip("\"'")
                    if name.endswith(".py") and not before_path.endswith(INTERPRETER_PREFIXES):
                        findings.append("%s:%d: %s runs without an interpreter prefix (write `python3 <plugin>/scripts/%s`)" % (rel, lineno, name, name))

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
    shutil.copy2(os.path.join(source, MCP_CONFIG), os.path.join(bundle_dir, MCP_CONFIG))
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
    if manifest.get("name") != DIRECTORY_PLUGIN_NAME:
        findings.append("%s: name must be %r in the bundle (got %r)" % (CODEX_MANIFEST, DIRECTORY_PLUGIN_NAME, manifest.get("name")))
    if manifest.get("skills") != BUNDLE_SKILLS_PATH:
        findings.append("%s: skills must be %r in the bundle (got %r)" % (CODEX_MANIFEST, BUNDLE_SKILLS_PATH, manifest.get("skills")))
    if manifest.get("mcpServers") != BUNDLE_MCP_PATH:
        findings.append("%s: mcpServers must be %r in the bundle (got %r)" % (CODEX_MANIFEST, BUNDLE_MCP_PATH, manifest.get("mcpServers")))
    if not os.path.isfile(os.path.join(bundle_dir, MCP_CONFIG)):
        findings.append("%s is missing from the bundle" % MCP_CONFIG)
    interface = manifest.get("interface") or {}
    for field in brand_asset_fields(interface):
        path = interface.get(field)
        if not isinstance(path, str) or not os.path.isfile(os.path.join(bundle_dir, path)):
            findings.append("%s: interface.%s (%r) does not resolve inside the bundle" % (CODEX_MANIFEST, field, path))
    for field in EXCLUDED_INTERFACE_FIELDS:
        if field in interface:
            findings.append("%s: interface.%s must not ship in the bundle" % (CODEX_MANIFEST, field))


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
            allowed_tools = allowed_tools_lines(text) if path == skill_md else set()
            fenced = fenced_code_lines(text)
            for lineno, line in enumerate(text.splitlines(), 1):
                if lineno not in fenced and PARENT_SEGMENT_RE.search(line):
                    findings.append("%s:%d: a relative path still leaves the skill folder" % (rel, lineno))
                if HOST_PATH_RE.search(line) and lineno not in allowed_tools:
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
    for name in SHARED_ROOT_FILES + (MCP_CONFIG,):
        shutil.copy2(os.path.join(source, name), os.path.join(dest, name))


def run_gate(source, workdir):
    check_source(source)
    bundle_dir = os.path.join(workdir, "bundle")
    build_bundle(source, bundle_dir)
    check_bundle(bundle_dir)
    return bundle_dir


def bundled_helpers_start(bundle_dir, workdir):
    """Run each bundled Python helper through the interpreter and return the ones that fail.

    This proves that every helper starts from the bundle layout (its
    ``--help`` exits 0). It does not test the exec bit: ``[sys.executable,
    path]`` never reads the file mode. The guard against a call that depends
    on the exec bit is the interpreter-prefix rule in ``check_source``.
    """
    copy = os.path.join(workdir, "bundle-helpers-start")
    shutil.copytree(bundle_dir, copy)
    failures = []
    with tempfile.TemporaryDirectory() as empty_project:
        for skill in SKILLS:
            scripts_dir = os.path.join(copy, "skills", skill, "scripts")
            for name in sorted(os.listdir(scripts_dir)):
                if not name.endswith(".py"):
                    continue
                path = os.path.join(scripts_dir, name)
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
            failures = bundled_helpers_start(good_bundle, os.path.join(tmp, "good"))
            if failures:
                print("self-test: bundled Python helpers start under the interpreter -> FAIL:\n  %s" % "\n  ".join(failures))
                ok = False
            else:
                print("self-test: bundled Python helpers start under the interpreter -> each --help exits 0")

        bad = os.path.join(tmp, "bad-bare-python-call")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "setup", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\nRun `<plugin>/scripts/detect_platform.py` first.\n")
        ok = expect_failure("Python helper called without an interpreter", bad, os.path.join(tmp, "bad-bare-python-call-work"),
                            "runs without an interpreter prefix") and ok

        bad = os.path.join(tmp, "bad-bare-python-call-in-reference")
        copy_source_subset(source, bad)
        reference = os.path.join(bad, "references", "api-reference.md")
        reference_text = read_text(reference)
        bad_line = len(reference_text.splitlines()) + (2 if reference_text.endswith("\n") else 1)
        with open(reference, "a", encoding="utf-8") as handle:
            handle.write("\nRun `<plugin>/scripts/onesignal_api.py app <app_id>` first.\n")
        ok = expect_failure("Python helper called without an interpreter in references/", bad,
                            os.path.join(tmp, "bad-bare-python-call-in-reference-work"),
                            "references/api-reference.md:%d: onesignal_api.py runs without an interpreter prefix" % bad_line) and ok

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

        bad = os.path.join(tmp, "bad-parent-path-in-prose")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "setup", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\nCopy the file from ../shared/template.swift.\n")
        ok = expect_failure("a ../ path in prose outside a code fence", bad, os.path.join(tmp, "bad-parent-path-in-prose-work"),
                            "relative path leaves the skill folder") and ok

        bad = os.path.join(tmp, "bad-parent-path-after-unclosed-fence")
        copy_source_subset(source, bad)
        with open(os.path.join(bad, "skills", "setup", "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\n```text\nCopy the file from ../shared/template.swift.\n")
        ok = expect_failure("a ../ path after a code fence that never closes", bad,
                            os.path.join(tmp, "bad-parent-path-after-unclosed-fence-work"),
                            "relative path leaves the skill folder") and ok

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

        bad = os.path.join(tmp, "bad-host-path-in-frontmatter")
        copy_source_subset(source, bad)
        path = os.path.join(bad, "skills", "setup", "SKILL.md")
        skill_lines = read_text(path).splitlines()
        hint_line = next(i for i, line in enumerate(skill_lines, 1) if line.startswith("argument-hint: "))
        skill_lines[hint_line - 1] = skill_lines[hint_line - 1].replace("argument-hint: ", "argument-hint: ${CLAUDE_PLUGIN_ROOT} ", 1)
        write_text(path, "\n".join(skill_lines) + "\n")
        ok = expect_failure("host environment variable in a frontmatter field other than allowed-tools", bad,
                            os.path.join(tmp, "bad-host-path-in-frontmatter-work"),
                            "skills/setup/SKILL.md:%d: host-specific path or environment variable" % hint_line) and ok

        bad = os.path.join(tmp, "bad-plugin-name")
        copy_source_subset(source, bad)
        path = os.path.join(bad, CODEX_MANIFEST)
        manifest = json.loads(read_text(path))
        manifest["name"] = "OneSignal"
        write_text(path, json.dumps(manifest, indent=2) + "\n")
        ok = expect_failure("display name in the manifest name field", bad, os.path.join(tmp, "bad-plugin-name-work"),
                            "name must be lowercase letters, digits, and single hyphens") and ok

        bad = os.path.join(tmp, "bad-long-subtitle")
        copy_source_subset(source, bad)
        path = os.path.join(bad, CODEX_MANIFEST)
        manifest = json.loads(read_text(path))
        manifest["interface"]["shortDescription"] = "Set up and send push, email, and SMS"
        write_text(path, json.dumps(manifest, indent=2) + "\n")
        ok = expect_failure("subtitle over the submission limit", bad, os.path.join(tmp, "bad-long-subtitle-work"),
                            "interface.shortDescription must be 30 characters or fewer") and ok

        bad = os.path.join(tmp, "bad-missing-support-url")
        copy_source_subset(source, bad)
        path = os.path.join(bad, CODEX_MANIFEST)
        manifest = json.loads(read_text(path))
        del manifest["interface"]["supportURL"]
        write_text(path, json.dumps(manifest, indent=2) + "\n")
        ok = expect_failure("manifest without a support URL", bad, os.path.join(tmp, "bad-missing-support-url-work"),
                            "interface.supportURL must be an HTTPS URL") and ok

        bad = os.path.join(tmp, "bad-missing-mcp-config")
        copy_source_subset(source, bad)
        os.remove(os.path.join(bad, MCP_CONFIG))
        ok = expect_failure("tree without the MCP declaration", bad, os.path.join(tmp, "bad-missing-mcp-config-work"),
                            "%s is missing or is not valid JSON" % MCP_CONFIG) and ok

        bad = os.path.join(tmp, "bad-brand-asset")
        copy_source_subset(source, bad)
        os.remove(os.path.join(bad, ASSETS_DIR, "logomark.png"))
        ok = expect_failure("manifest logo that points to a missing file", bad, os.path.join(tmp, "bad-brand-asset-work"),
                            "interface.logo points to ./assets/logomark.png, which does not exist") and ok

        bad = os.path.join(tmp, "bad-dark-brand-asset")
        copy_source_subset(source, bad)
        path = os.path.join(bad, CODEX_MANIFEST)
        manifest = json.loads(read_text(path))
        manifest["interface"]["logoDark"] = "./assets/does-not-exist.svg"
        write_text(path, json.dumps(manifest, indent=2) + "\n")
        ok = expect_failure("manifest logoDark that points to a missing file", bad, os.path.join(tmp, "bad-dark-brand-asset-work"),
                            "interface.logoDark points to ./assets/does-not-exist.svg, which does not exist") and ok

        bad = os.path.join(tmp, "bad-screenshots")
        copy_source_subset(source, bad)
        path = os.path.join(bad, CODEX_MANIFEST)
        manifest = json.loads(read_text(path))
        manifest["interface"]["screenshots"] = ["./assets/logomark.png"]
        write_text(path, json.dumps(manifest, indent=2) + "\n")
        ok = expect_failure("interface.screenshots in the manifest", bad, os.path.join(tmp, "bad-screenshots-work"),
                            "interface.screenshots is accepted only for a plugin with a custom UI") and ok
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
