#!/usr/bin/env python3
"""Scan files (or the working-tree diff) for secret-shaped strings.

setup Step 8 requires scanning the agent's own diff before finishing: no REST
API key, org key, .p8, or service-account JSON may land in a committed/client
file. This makes that check deterministic instead of an eyeball pass.

Usage:
    scan_secrets.py [path ...]      # scan given files/dirs (default: git diff)
    scan_secrets.py --staged        # scan staged changes only
    git diff | scan_secrets.py -    # scan a diff on stdin

Exit: 0 clean, 1 secret(s) found, 2 usage error.
Findings print as file:line (path:line parsed from the diff's +++/@@ headers when
scanning a diff) with the matched rule and column. The matched value is never
printed — only where it is and which rule fired — so this script never re-emits it.
"""
import argparse
import os
import re
import subprocess
import sys

# (rule, compiled regex). Ordered most-specific first.
RULES = [
    ("apns_p8_private_key", re.compile(r"-----BEGIN (?:EC )?PRIVATE KEY-----")),
    ("service_account_private_key", re.compile(r'"private_key"\s*:\s*"-----BEGIN')),
    ("service_account_json", re.compile(r'"type"\s*:\s*"service_account"')),
    ("onesignal_v2_key", re.compile(r"\bos_v2_(?:app|org)_[a-z0-9]{20,}", re.I)),
    ("authorization_key_header", re.compile(r"Authorization\s*:\s*(?:Basic|Key)\s+[A-Za-z0-9_\-]{16,}")),
    ("rest_key_assignment", re.compile(
        r"(?i)(rest[_-]?api[_-]?key|onesignal[_-]?api[_-]?key|org[_-]?key|api[_-]?key|secret|token)"
        r"\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]")),
]

# Never flag obvious placeholders / public identifiers.
ALLOW = re.compile(
    r"(YOUR_ONESIGNAL_APP_ID|<APP_ID>|<app-scoped|example|placeholder|xxxx|"
    r"changeme|your[_-]?key|dummy|sample|test[_-]?key)", re.I)

# The App ID is public; a bare UUID is not a secret.
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

EXCLUDE_DIRS = {".git", "node_modules", "dist", "build", "Pods", "vendor",
                ".next", ".gradle", "__pycache__", ".venv", "venv"}
# Files that legitimately hold secrets and are gitignored — not "committed".
SKIP_FILES = {".env", ".env.local"}


def scan_line(where, line):
    """Scan one line; report location + rule only, never the value."""
    findings = []
    for rule, rx in RULES:
        m = rx.search(line)
        if not m:
            continue
        # Scope the allow-list to the matched span, not the whole line: a real key
        # on a line that merely contains the word "example" must still be flagged.
        if ALLOW.search(m.group(0)):
            continue
        if rule == "rest_key_assignment":
            val = re.search(r"['\"]([A-Za-z0-9_\-]{24,})['\"]", line)
            if val and (UUID.match(val.group(1)) or ALLOW.search(val.group(1))):
                continue  # public App ID or an explicit placeholder value
        findings.append({"where": where, "rule": rule, "col": m.start() + 1})
        break
    return findings


def scan_text(label, text):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        findings += scan_line(f"{label}:{i}", line)
    return findings


def scan_diff(diff_text):
    """Scan a unified diff, tracking file + new-file line from +++/@@ headers so a
    finding prints as <path>:<line>, not `diff:<n>`."""
    findings = []
    path, lineno = None, 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            p = raw[4:].strip().split("\t", 1)[0]
            if p.startswith("b/"):
                p = p[2:]
            path = None if p == "/dev/null" else p
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("---"):
            continue
        elif raw.startswith("+"):
            findings += scan_line(f"{path or '?'}:{lineno}", raw[1:])
            lineno += 1
        elif raw.startswith(" "):
            lineno += 1
        # '-' (removed) lines don't advance the new-file counter.
    return findings


def iter_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for dp, dn, fn in os.walk(p):
                dn[:] = [d for d in dn if d not in EXCLUDE_DIRS]
                for f in fn:
                    yield os.path.join(dp, f)


def git_diff(staged):
    args = ["git", "diff", "--unified=0"] + (["--staged"] if staged else [])
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def main():
    ap = argparse.ArgumentParser(description="Scan for secret-shaped strings (redacted output).")
    ap.add_argument("paths", nargs="*", help="files/dirs; default = git diff of the working tree")
    ap.add_argument("--staged", action="store_true", help="scan staged changes")
    args = ap.parse_args()

    findings = []
    if args.paths == ["-"]:
        findings += scan_diff(sys.stdin.read())
    elif args.paths:
        for fp in iter_files(args.paths):
            if os.path.basename(fp) in SKIP_FILES:
                continue
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    findings += scan_text(os.path.relpath(fp), f.read())
            except Exception:
                continue
    else:
        diff = git_diff(args.staged)
        if diff is None:
            sys.stderr.write("No paths given and no git diff available. Pass files or pipe a diff.\n")
            sys.exit(2)
        findings += scan_diff(diff)

    if findings:
        print("SECRET-SHAPED STRINGS FOUND — do not commit:")
        for f in findings:
            print(f"  {f['where']}  [{f['rule']}]  col {f['col']}")
        print("\nMove secrets to env vars (safety contract §\"Never\"). App IDs are public and OK.")
        sys.exit(1)
    print("clean: no secret-shaped strings found")


if __name__ == "__main__":
    main()
