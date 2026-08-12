#!/usr/bin/env python3
"""Read-only discovery scan for the discover-data skill.

Runs the grep-signatures cookbook deterministically so the exclusion set,
secret-file skip list, and data-value skip list are ALWAYS applied — instead
of relying on the model to remember guardrails 3–4 each time. It reports
candidate *locations* (file:line + which rank/signal matched); it never reads
data values and never opens secret or data-dump files. The agent then reads the
reported locations for names/schema only.

Usage:
    discover_scan.py [root]     # defaults to CWD

Output: JSON { "root", "hits_by_rank": {rank: [{file,line,signal,preview}]},
               "skipped_secret_files": N, "summary": {...} }

`preview` is the matched line trimmed to the signal token — it is a schema/name
hint, not a data value. Files on the skip lists are never opened.
"""
import json
import os
import re
import sys

EXCLUDE_DIRS = {
    "node_modules", "dist", "build", ".next", ".nuxt", "vendor", "Pods",
    ".git", ".gradle", "DerivedData", "coverage", "__pycache__", ".venv",
    "venv", "out", ".dart_tool", "tests", "test", "__tests__", "__mocks__",
    "stories", ".storybook",
}
# Never open (secret-file skip list — grep-signatures guardrail 4).
SECRET_FILES = re.compile(
    r"(^\.env(\..+)?$|(?<!example)\.pem$|\.key$|\.p8$|\.p12$|\.jks$|\.keystore$|"
    r"credentials\.json$|service-account.*\.json$|google-services\.json$|"
    r"GoogleService-Info\.plist$|^\.npmrc$|^\.netrc$)", re.I)
SECRET_DIR = re.compile(r"(^|/)secrets?(/|$)")
# Never open (data-value skip list — schema-names-only).
DATA_FILES = re.compile(
    r"(\.csv$|\.tsv$|\.parquet$|\.sqlite\d*$|seeds?\b.*\.(rb|ts|js|sql)$|"
    r"seed\.(rb|ts|js|sql)$|\.snap$|dump.*\.sql$)", re.I)

CODE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".py", ".rb", ".kt", ".swift",
             ".java", ".go", ".dart", ".prisma", ".graphql", ".vue", ".svelte"}
SCHEMA_FILES = {"schema.prisma", "schema.rb"}

# rank -> (signal label, regex). Mirrors references/grep-signatures.md.
RANKS = {
    "1-tracking-plan": [
        ("typed-event-union", re.compile(r"type\s+\w*Event\w*\s*=|enum\s+\w*Event|avo\.|trackingPlan")),
    ],
    "2-event-constants": [
        ("event-constants", re.compile(r"EVENTS?\s*[:=]\s*[{(]|[A-Z0-9_]{3,}\s*[:=]\s*['\"][A-Za-z ]+['\"]")),
    ],
    "3-analytics-callsites": [
        ("analytics-call", re.compile(
            r"\b(analytics|amplitude|mixpanel|posthog)\b\s*\.\s*"
            r"(identify|track|capture|logEvent|setUserId|setUserProperty|group|alias)\(")),
        ("segment-call", re.compile(r"analytics\.(identify|track|page|screen|group|alias)\(")),
    ],
    "4-orm-schema": [
        ("prisma-model", re.compile(r"^\s*model\s+\w+|^\s*\w+\s+(String|Int|Boolean|DateTime|Decimal|Float|Json)")),
        ("rails-schema", re.compile(r"create_table|t\.(string|integer|boolean|datetime|decimal|jsonb?)\s+\"")),
        ("orm-decorator", re.compile(r"@Column|@Entity|sequelize\.define\(|models\.\w+Field\(|Column\(String")),
    ],
    "5-auth-provider": [
        ("auth-id", re.compile(
            r"@clerk|@auth0|next-auth|firebase/auth|supabase\.auth|devise|"
            r"sessionClaims|app_metadata|publicMetadata|user\.(sub|uid|id)\b")),
    ],
    "6-feature-flags": [
        ("feature-flag", re.compile(r"launchdarkly|ldClient|statsig|checkGate|unleash|isEnabled\(|useFlags\(|flagsmith|growthbook")),
    ],
}


def preview(line, m):
    """Return a small window around the match — a schema/name hint, not the whole
    line. Some signals put the name BEFORE the match end (`user.sub`) and some just
    AFTER (`t.string "col"` — the column follows the matched prefix), so we keep a
    little on both sides. It stays a hint, not a data dump: the window is bounded,
    so bulk values on a wide log line are not echoed."""
    start, end = m.span()
    lo = max(0, start - 16)
    hi = min(len(line), end + 16)
    snippet = line[lo:hi].strip()
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(line) else "")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        sys.stderr.write(f"ERROR: not a directory: {root}\n")
        sys.exit(2)

    hits = {r: [] for r in RANKS}
    skipped_secret = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in EXCLUDE_DIRS]
        rel_dir = os.path.relpath(dp, root)
        if SECRET_DIR.search("/" + rel_dir.replace(os.sep, "/")):
            continue
        for f in fn:
            if SECRET_FILES.search(f) or DATA_FILES.search(f):
                if SECRET_FILES.search(f):
                    skipped_secret += 1
                continue
            ext = os.path.splitext(f)[1]
            if ext not in CODE_EXTS and f not in SCHEMA_FILES:
                continue
            fp = os.path.join(dp, f)
            relf = os.path.relpath(fp, root)
            try:
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if len(line) > 400:
                            continue  # skip minified/data blobs
                        for rank, sigs in RANKS.items():
                            for label, rx in sigs:
                                m = rx.search(line)
                                if m:
                                    hits[rank].append({
                                        "file": relf, "line": i,
                                        "signal": label, "preview": preview(line, m),
                                    })
                                    break
            except Exception:
                continue

    # Cap output so a huge repo doesn't flood context.
    for r in hits:
        if len(hits[r]) > 40:
            hits[r] = hits[r][:40] + [{"note": f"...{len(hits[r]) - 40} more; scan a subdir with a path arg"}]

    total = sum(len(v) for v in hits.values())
    out = {
        "root": root,
        "hits_by_rank": {k: v for k, v in hits.items() if v},
        "skipped_secret_files": skipped_secret,
        "summary": {
            "total_candidate_sites": total,
            "guidance": ("Read the reported file:line locations for NAMES/SCHEMA only "
                         "(trait keys, event-name literals, column names). Never read data "
                         "values. Rank 1-3 are richest; run the semantic allow/deny pass on "
                         "rank 4 columns before proposing (classification-reference.md)."),
        },
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
