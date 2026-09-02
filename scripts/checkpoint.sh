#!/usr/bin/env bash
# checkpoint.sh — report one onboarding milestone for the OneSignal agent plugin.
#
# Usage:
#   bash scripts/checkpoint.sh <skill.milestone> <ok|ok_after_fix|fail> [class] [detail]
#   bash scripts/checkpoint.sh flush        # send events buffered before the App ID existed
#
# Milestone vocabulary, failure classes and the funnel model:
#   references/telemetry-contract.md
# Safety rules that bind this script:
#   references/safety-contract.md §15-20
#
# CONTRACT — do not break these three properties:
#   1. Always exits 0. Telemetry never fails the user's install.
#   2. Never sends source code, file contents, or file paths. The sanitizer
#      drops path-like punctuation so dotted package names do not reach the
#      wire. A project name with no punctuation is an agent-rule case.
#      NOTE: the App ID *is* sent — the ingestion endpoint requires it as a query
#      parameter. Earlier versions did not send it and both this comment and
#      SKILL.md described the payload as containing no App ID. That was true then
#      and is false now; any description given to a user must say so.
#   3. Sends only when consent resolves to exactly 1: ONESIGNAL_SKILL_TELEMETRY
#      set to 1, or .onesignal/telemetry recorded as 1. 0 is an opt-out. Any
#      other env value is ignored and the file decides — junk must not read as
#      a yes. No answer from either source means do not send. This script never
#      writes the consent file; the setup skill records the answer once, after
#      asking, so a stray env value on an invalid or dry-run call cannot pin it.
#
# Request (exactly this, nothing more): a GET carrying one query parameter per
# field. There is no body and no header beyond what curl sends by default.
#
#   schema=3
#   app_id=<uuid>          <- required; the endpoint answers 400 without it
#   source=onesignal-agent-plugin   <- discriminator, see below
#   run_id=<random hex, stable for one funnel run>
#   seq=<position of this report inside the run, counting from 1>
#   skill=setup
#   milestone=credentials_gate
#   status=ok | ok_after_fix | fail
#   failure_class=kotlin_stdlib_floor | ...   <- the key is absent when there is none
#   failure_detail=foo_bar_missing            <- only when class is unknown; else absent
#   platform=android | web | ...
#   runtime=claude-code | codex | ... | unknown
#   os=darwin | linux | ...
#   skill_version=0.5.1
#   timestamp=2026-07-27T12:00:00Z
#   message=<one readable line built from the fields above>
#
# The server reserves 2 of those names: `message` becomes the log body and
# `timestamp` becomes the record time. Every other key is stored as sent.
#
# `timestamp` is the EVENT time, so a buffered event flushed minutes later still
# reports the moment it happened rather than the moment it was sent.
#
# The same event is also written locally as one JSON line in
# .onesignal/checkpoints.jsonl. That line is the internal record, not the wire
# format: it names the event time `ts` and carries a null `failure_class` or
# `failure_detail` where the request omits the key.
#
# "source" exists so these events can be separated from real SDK traffic on the
# shared ingestion endpoint. Override it with $ONESIGNAL_SKILL_SOURCE if the
# ingestion service wants a different discriminator.
#
# Set ONESIGNAL_SKILL_DRY_RUN=1 to print the exact request without sending.

set -uo pipefail

PLUGIN_VERSION="0.5.3"
SKILL_VERSION="$PLUGIN_VERSION"
SCHEMA_VERSION=3
SOURCE_TAG="${ONESIGNAL_SKILL_SOURCE:-onesignal-agent-plugin}"
DEFAULT_ENDPOINT="https://example.invalid/skill-checkpoints"
TIMEOUT="${ONESIGNAL_SKILL_TIMEOUT:-5}"
UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'

RAW_MILESTONE="${1:-unknown}"
STATUS="${2:-unknown}"
FAILURE_CLASS="${3:-}"
RAW_FAILURE_CLASS="$FAILURE_CLASS"
RAW_FAILURE_DETAIL="${4:-}"

# Milestones are named "<skill>.<milestone>" so one funnel run can be followed across
# every skill. Derive the skill rather than taking it as another argument the agent
# could get wrong.
case "$RAW_MILESTONE" in
  *.*) SKILL_NAME="${RAW_MILESTONE%%.*}"; MILESTONE="${RAW_MILESTONE#*.}" ;;
  *)   SKILL_NAME="unknown";              MILESTONE="$RAW_MILESTONE" ;;
esac

# State lives at the REPO ROOT, per the contract. A cwd-relative path invoked
# from a monorepo package directory created a second .onesignal with a fresh
# run_id, splitting one install into two funnel runs. Outside a git work tree
# (or with git missing) there is no root to resolve, so cwd keeps the old
# behavior — Step 0 already warns the user when there is no VCS.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$REPO_ROOT" ] && [ -d "$REPO_ROOT" ]; then
  STATE_DIR="$REPO_ROOT/.onesignal"
else
  STATE_DIR=".onesignal"
fi
mkdir -p "$STATE_DIR" 2>/dev/null || true

# Consent resolves once, from two sources, into one fail-closed state. The env
# var is a per-invocation override (a CI run, or the fallback when the file
# cannot be written) and is never persisted, like ONESIGNAL_SKILL_RUN_ID. The
# file is the durable answer the setup skill records after asking. An env
# value other than exactly 0 or 1 — empty from a wrapper's unset var, "true",
# a typo — is ignored and the file decides: junk must not read as a yes, and
# it must not shadow a recorded opt-out.
TELEMETRY_FILE="$STATE_DIR/telemetry"

telemetry_file_value() {
  [ -f "$TELEMETRY_FILE" ] || return 1
  grep -vE '^[[:space:]]*(#|$)' "$TELEMETRY_FILE" 2>/dev/null | head -1 | tr -d '[:space:]'
}

# TELEMETRY_STATE: 0 (opt-out), 1 (consented), or "" (never answered).
# TELEMETRY_SOURCE names where the answer came from, so the local audit log
# can tell an env opt-out, a recorded opt-out, and a missing answer apart.
TELEMETRY_STATE=""
TELEMETRY_SOURCE="unset"
case "${ONESIGNAL_SKILL_TELEMETRY:-}" in
  0|1) TELEMETRY_STATE="$ONESIGNAL_SKILL_TELEMETRY"; TELEMETRY_SOURCE="env" ;;
esac
if [ -z "$TELEMETRY_STATE" ]; then
  _file_answer="$(telemetry_file_value || true)"
  case "$_file_answer" in
    0|1) TELEMETRY_STATE="$_file_answer"; TELEMETRY_SOURCE="file" ;;
  esac
fi

telemetry_disabled() {
  [ "$TELEMETRY_STATE" != "1" ]
}

# A "send" answer that lives only in the environment dies with the session: the
# next session resolves consent to "no answer" and every send silently stops,
# which turns a recorded "yes" into an opt-out nobody chose. This script must
# not write the consent file itself (see the contract above), so the one thing
# it can do is say so, on every send, until the file has the answer.
CONSENT_ENV_ONLY=0
if [ "$TELEMETRY_STATE" = "1" ] && [ "$TELEMETRY_SOURCE" = "env" ]; then
  case "$(telemetry_file_value || true)" in
    0|1) : ;;
    *)   CONSENT_ENV_ONLY=1 ;;
  esac
fi

warn_consent_env_only() {
  echo "checkpoint: WARNING — consent came from ONESIGNAL_SKILL_TELEMETRY only."
  echo "  $TELEMETRY_FILE has no recorded answer, and the env value dies with this"
  echo "  session: the next session will resolve consent to \"no answer\" and stop"
  echo "  sending. Record the user's answer now:"
  echo "    printf '1\n' > $TELEMETRY_FILE   # 0 for keep-local"
}

# Resolve our own directory using only bash builtins. Deliberately avoids
# `dirname`: if that binary is missing, an external-command failure here would
# cascade into misdiagnosing the endpoint as unconfigured.
_self="${BASH_SOURCE[0]:-$0}"
case "$_self" in
  */*) SCRIPT_DIR="${_self%/*}" ;;
  *)   SCRIPT_DIR="." ;;
esac
if [ -d "$SCRIPT_DIR" ]; then
  SCRIPT_DIR="$(cd "$SCRIPT_DIR" 2>/dev/null && pwd)" || SCRIPT_DIR="${_self%/*}"
fi

# ---------------------------------------------------------------------------
# flush — send events buffered before the App ID was known.
#
# Sends each pending payload with the now-known App ID substituted in, then clears the
# buffer. Only clears once every event has actually been accepted, so a blocked network
# leaves the events for a later attempt rather than silently dropping them.
#
# This block must stay OUTSIDE the SCRIPT_DIR case above. It was once nested inside the
# `*/*)` arm, which made flushing depend on whether the invocation path happened to
# contain a slash: `bash scripts/checkpoint.sh flush` flushed, while
# `cd scripts && bash checkpoint.sh flush` recorded a milestone literally named "flush"
# and appended it to the buffer it was meant to drain.
# ---------------------------------------------------------------------------
if [ "$RAW_MILESTONE" = "flush" ]; then
  if telemetry_disabled; then
    # An unanswered question and a recorded opt-out are different states, and
    # only one of them is settled. Say which, or the caller reads fail-closed
    # behaviour as a refusal nobody gave.
    if [ "$TELEMETRY_SOURCE" = "unset" ]; then
      echo "checkpoint: flush skipped (no consent answer recorded)"
      echo "  Fail-closed: nothing sends until an answer exists. If the user already"
      echo "  answered the consent question, record it and flush again:"
      echo "    printf '1\n' > $TELEMETRY_FILE   # 0 for keep-local"
      echo "  If they were never asked, ask once (setup SKILL.md, checkpoint consent)."
    else
      echo "checkpoint: flush skipped (reporting disabled)"
    fi
    exit 0
  fi
  if [ "$CONSENT_ENV_ONLY" -eq 1 ]; then
    warn_consent_env_only
  fi
  PENDING="$STATE_DIR/pending.jsonl"
  if [ ! -s "$PENDING" ]; then
    echo "checkpoint: nothing buffered"
    exit 0
  fi
  FLUSH_APP_ID="${ONESIGNAL_SKILL_APP_ID:-$(grep -vE '^[[:space:]]*(#|$)' "$STATE_DIR/app_id" 2>/dev/null | head -1 | tr -d '[:space:]')}"
  if [ -z "$FLUSH_APP_ID" ]; then
    echo "checkpoint: cannot flush — still no App ID. Write it to $STATE_DIR/app_id first."
    exit 0
  fi
  # A malformed App ID must not fan out: each child would treat it as absent and
  # re-append its event to the very buffer this loop is draining, duplicating
  # every row per flush attempt.
  if ! printf '%s' "$FLUSH_APP_ID" | grep -qE "$UUID_RE"; then
    echo "checkpoint: cannot flush — App ID '$FLUSH_APP_ID' is not a UUID. Fix $STATE_DIR/app_id first."
    exit 0
  fi

  # Pull one string field out of a buffered payload. A JSON `null` yields "", which is
  # what an absent failure_class should become.
  #
  # Pure bash, not a sed pattern: `[^"]*` stopped at the first quote, so any value
  # holding an escaped quote — json_escape emits \" and \\ — was truncated there, and
  # the re-sent event lost the rest of the value. A JSON string ends at the first quote
  # preceded by an EVEN number of backslashes, which is what the parity check below
  # measures. Bash rather than python3 keeps the flush path free of that dependency.
  buffered_field() {
    local line="$1" key="$2" rest chunk tail out=""
    case "$line" in
      *"\"$key\":\""*) rest="${line#*\"$key\":\"}" ;;
      *) return 0 ;;
    esac
    while :; do
      chunk="${rest%%\"*}"
      out="$out$chunk"
      rest="${rest#"$chunk"\"}"
      tail="${chunk##*[!\\]}"
      [ $(( ${#tail} % 2 )) -eq 0 ] && break
      out="$out\""
    done
    out="${out//\\\"/\"}"
    printf '%s' "${out//\\\\/\\}"
  }

  # seq is a JSON number, so it has no surrounding quotes for buffered_field to
  # find. An empty result — a row buffered before the counter existed — leaves
  # the child to report position 0, which is what an unknown position means.
  buffered_number() {
    local line="$1" key="$2" rest
    case "$line" in
      *"\"$key\":"*) rest="${line#*\"$key\":}" ;;
      *) return 0 ;;
    esac
    rest="${rest%%,*}"
    rest="${rest%%\}*}"
    case "$rest" in ''|*[!0-9]*) return 0 ;; esac
    printf '%s' "$rest"
  }

  COUNT=$(grep -c . "$PENDING" 2>/dev/null || echo 0)
  echo "checkpoint: flushing $COUNT buffered event(s) as app_id=$FLUSH_APP_ID"

  # Success cannot be read from the child's exit status: this script always exits 0 by
  # contract, so `|| FAILED=1` never fired and the buffer was cleared even when every
  # send was refused. The child reports its transport outcome out-of-band instead.
  RESULT_FILE="$STATE_DIR/.flush_result"

  # Per-row bookkeeping: collect the rows that failed and rewrite the buffer with only
  # those. One bad row used to keep the whole buffer, so every already-accepted row was
  # sent again on the next flush. When this file cannot be created, fall back to keeping
  # the whole buffer on any failure: a duplicate delivery is wasteful, a dropped event is
  # not recoverable.
  KEPT="$STATE_DIR/.pending.rewrite"
  if printf '' > "$KEPT" 2>/dev/null; then
    PER_ROW=1
  else
    PER_ROW=0
  fi

  keep_row() {
    [ "$PER_ROW" -eq 1 ] && printf '%s\n' "$1" >> "$KEPT" 2>/dev/null
    return 0
  }

  FAILED=0
  SENT=0
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    M=$(buffered_field "$line" milestone)
    S=$(buffered_field "$line" status)
    FC=$(buffered_field "$line" failure_class)
    SK=$(buffered_field "$line" skill)
    BTS=$(buffered_field "$line" ts)
    # Carry EVERY recorded field through, like ts. The child used to re-derive
    # platform, source, run_id, runtime and os from flush-time state, so a
    # buffered event could land platform=unknown (or under a fresh run_id) if
    # the state files had moved on. An empty extraction — a row from an older
    # buffer — leaves the variable empty, and the child's ${VAR:-fallback}
    # expansions re-derive exactly as before.
    BPLATFORM=$(buffered_field "$line" platform)
    BSOURCE=$(buffered_field "$line" source)
    BRUN_ID=$(buffered_field "$line" run_id)
    BRUNTIME=$(buffered_field "$line" runtime)
    BOS=$(buffered_field "$line" os)
    BSEQ=$(buffered_number "$line" seq)
    BFD=$(buffered_field "$line" failure_detail)
    # Re-qualify as "<skill>.<milestone>". Passing the bare milestone made the child
    # derive skill="unknown", erasing the skill of every buffered event.
    case "$SK" in
      ""|unknown) QUALIFIED="$M" ;;
      *)          QUALIFIED="$SK.$M" ;;
    esac
    # Fail closed. Seed the result file with a non-"sent" sentinel BEFORE the
    # child runs, and skip the send entirely if that write fails: when this
    # file cannot be written the child's outcome cannot be reported either, and
    # a stale "sent" left from a previous iteration would clear the buffer with
    # nothing delivered. The same sentinel covers a child that dies before
    # reporting — anything short of an explicit "sent" keeps the buffer.
    if ! printf 'unsent' > "$RESULT_FILE" 2>/dev/null; then
      FAILED=1
      keep_row "$line"
      continue
    fi
    # Re-send through this same script so every send path stays identical: one encoder,
    # one set of transport diagnostics, one place to get the request shape right.
    # The event is already in checkpoints.jsonl from when it was buffered, so the child
    # must not append it a second time.
    ONESIGNAL_SKILL_APP_ID="$FLUSH_APP_ID" \
    ONESIGNAL_SKILL_RESULT_FILE="$RESULT_FILE" \
    ONESIGNAL_SKILL_SKIP_LOCAL_RECORD=1 \
    ONESIGNAL_SKILL_TS="$BTS" \
    ONESIGNAL_SKILL_PLATFORM="$BPLATFORM" \
    ONESIGNAL_SKILL_SOURCE="$BSOURCE" \
    ONESIGNAL_SKILL_RUN_ID="$BRUN_ID" \
    ONESIGNAL_SKILL_RUNTIME="$BRUNTIME" \
    ONESIGNAL_SKILL_OS="$BOS" \
    ONESIGNAL_SKILL_SEQ="$BSEQ" \
      bash "$0" "$QUALIFIED" "$S" "$FC" "$BFD" 2>/dev/null
    if [ "$(cat "$RESULT_FILE" 2>/dev/null)" = "sent" ]; then
      SENT=$((SENT + 1))
    else
      FAILED=1
      keep_row "$line"
    fi
  done < "$PENDING"
  rm -f "$RESULT_FILE" 2>/dev/null || true

  if [ "$FAILED" -eq 0 ]; then
    : > "$PENDING"
    rm -f "$KEPT" 2>/dev/null || true
    echo "checkpoint: buffer cleared ($SENT sent)"
  elif [ "$PER_ROW" -eq 1 ] && mv "$KEPT" "$PENDING" 2>/dev/null; then
    HELD=$(grep -c . "$PENDING" 2>/dev/null || echo 0)
    echo "checkpoint: $SENT of $COUNT sent — $HELD kept for a later flush"
  else
    rm -f "$KEPT" 2>/dev/null || true
    echo "checkpoint: $SENT of $COUNT sent — buffer kept for a later flush"
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Validate at the door. Everything below serializes these values; garbage here
# used to reach the wire and corrupt the funnel it exists to measure.
#
# status: an unknown value ("failed", "OK") maps to SEVERITY_INFO in the
# encoder, shipping a failure as a success. Refuse and instruct instead —
# nothing is recorded, so the corrected re-run produces exactly one row.
#
# failure_class: classes come from telemetry-contract.md and are snake_case by
# construction. Free text here is an egress risk — a value like a file path
# would put project structure on the wire (contract §16). Reject the value,
# report "unknown", and say so loudly; never ship the raw text.
# ---------------------------------------------------------------------------
case "$STATUS" in
  ok|ok_after_fix|fail) : ;;
  *)
    echo "checkpoint: INVALID STATUS '$STATUS' — nothing recorded, nothing sent."
    echo "  Valid statuses: ok | ok_after_fix | fail. Re-run:"
    echo "    bash scripts/checkpoint.sh $RAW_MILESTONE <ok|ok_after_fix|fail> [failure_class] [failure_detail]"
    exit 0 ;;
esac

if [ -n "$FAILURE_CLASS" ] && ! printf '%s' "$FAILURE_CLASS" | grep -qE '^[a-z][a-z0-9_]{0,39}$'; then
  echo "checkpoint: failure_class '$FAILURE_CLASS' is not a valid class — reporting 'unknown' instead."
  echo "  Classes are lowercase snake_case from references/telemetry-contract.md."
  echo "  Free text (paths, error messages) must never reach the wire."
  FAILURE_CLASS="unknown"
fi

# failure_detail segments the unknown bucket. Rewrite of `/` `\` `.` `@` `:`
# would still name the file or package, so those characters in the raw
# argument drop the value. Detail is allowed only when the caller passed
# class `unknown`, not when the script rewrites an invalid class to `unknown`.
# After rewrite, the slug must match `^[a-z][a-z0-9_]*$`. LC_ALL=C keeps
# `[a-z]` as ASCII.
FAILURE_DETAIL=""
if [ -n "$RAW_FAILURE_DETAIL" ] && [ "$RAW_FAILURE_CLASS" = "unknown" ]; then
  case "$RAW_FAILURE_DETAIL" in
    */*|*\\*|*.*|*@*|*:*) ;;
    *)
      FAILURE_DETAIL="$(LC_ALL=C printf '%s' "$RAW_FAILURE_DETAIL" | LC_ALL=C tr '[:upper:]' '[:lower:]' | LC_ALL=C sed 's/[^a-z0-9_]/_/g')"
      FAILURE_DETAIL="${FAILURE_DETAIL:0:30}"
      case "$FAILURE_DETAIL" in
        *[0-9][0-9][0-9][0-9]*) FAILURE_DETAIL="" ;;
      esac
      if [ -n "$FAILURE_DETAIL" ] && ! printf '%s' "$FAILURE_DETAIL" | grep -qE '^[a-z][a-z0-9_]*$'; then
        FAILURE_DETAIL=""
      fi
      ;;
  esac
fi

# ---------------------------------------------------------------------------
# Endpoint resolution, in priority order.
#
# An environment variable alone is not workable: agents run bash in a shell
# whose environment the user cannot see or control, and IDE-hosted runtimes
# routinely do not inherit the user's exports. So a FILE is the primary
# mechanism, since it is inspectable and travels with the project.
#
#   1. $ONESIGNAL_SKILL_ENDPOINT        — CI, or when you control the shell
#   2. .onesignal/endpoint        — per-project, for testing (gitignored)
#   3. <skill>/endpoint.conf            — shipped/per-install default
#   4. built-in placeholder             — unresolvable; a safety net, not a
#      normal path. endpoint.conf ships with the production endpoint, so this
#      only fires when that file was deleted or emptied.
#
# Files may contain comments (#) and blank lines; the first non-comment line
# is used.
# ---------------------------------------------------------------------------
first_line() {
  [ -f "$1" ] || return 1
  grep -vE '^[[:space:]]*(#|$)' "$1" 2>/dev/null | head -1 | tr -d '[:space:]'
}

PROJECT_CONF="$STATE_DIR/endpoint"
SKILL_CONF="$SCRIPT_DIR/../endpoint.conf"

if [ -n "${ONESIGNAL_SKILL_ENDPOINT:-}" ]; then
  ENDPOINT="$ONESIGNAL_SKILL_ENDPOINT"
  ENDPOINT_SRC="env ONESIGNAL_SKILL_ENDPOINT"
elif ENDPOINT="$(first_line "$PROJECT_CONF")" && [ -n "$ENDPOINT" ]; then
  ENDPOINT_SRC="$PROJECT_CONF"
elif ENDPOINT="$(first_line "$SKILL_CONF")" && [ -n "$ENDPOINT" ]; then
  ENDPOINT_SRC="endpoint.conf"
else
  ENDPOINT="$DEFAULT_ENDPOINT"
  ENDPOINT_SRC="built-in default"
fi

# ---------------------------------------------------------------------------
# app_id — REQUIRED by the ingestion endpoint. It is passed as a
# query parameter and validated: must parse as a UUID (else 400) and the app
# must be Enabled (else 403), unless the UUID is in a server-side
# allow-list, which bypasses the status check.
#
# There is no other authentication on that endpoint. app_id is the whole gate.
#
# Resolution mirrors the endpoint: env -> project file -> skill conf.
# ---------------------------------------------------------------------------
PLATFORM="$(first_line "$STATE_DIR/platform" 2>/dev/null || true)"
PLATFORM="${ONESIGNAL_SKILL_PLATFORM:-${PLATFORM:-unknown}}"

APP_ID_PROJECT_CONF="$STATE_DIR/app_id"
APP_ID_SKILL_CONF="$SCRIPT_DIR/../app_id.conf"

# NO FALLBACK APP ID, BY CONTRACT.
# safety-contract.md §19 and setup Step 2 both forbid a placeholder or demo App ID.
# Attributing a real user's onboarding to a OneSignal test app would corrupt the very
# data this exists to produce. When the App ID is unknown the event is BUFFERED, not
# faked — see the pending/flush logic below.

if [ -n "${ONESIGNAL_SKILL_APP_ID:-}" ]; then
  APP_ID="$ONESIGNAL_SKILL_APP_ID"
  APP_ID_SRC="env ONESIGNAL_SKILL_APP_ID"
elif APP_ID="$(first_line "$APP_ID_PROJECT_CONF")" && [ -n "$APP_ID" ]; then
  APP_ID_SRC="$APP_ID_PROJECT_CONF"
elif APP_ID="$(first_line "$APP_ID_SKILL_CONF")" && [ -n "$APP_ID" ]; then
  APP_ID_SRC="app_id.conf"
else
  APP_ID=""
  APP_ID_SRC="none"
fi

# A malformed App ID must not reach the request URL: characters like &, # or a
# space would silently mutate the query string. After this check the value is a
# UUID, whose charset needs no URL encoding. Treat an invalid value as absent —
# the event buffers instead of riding a corrupt request.
if [ -n "$APP_ID" ] && ! printf '%s' "$APP_ID" | grep -qE "$UUID_RE"; then
  echo "checkpoint: App ID from $APP_ID_SRC is not a UUID — ignoring it; the event will buffer."
  echo "  Fix the value ($APP_ID_PROJECT_CONF or the env var), then run: bash scripts/checkpoint.sh flush"
  APP_ID=""
  APP_ID_SRC="invalid_ignored"
fi

# ---------------------------------------------------------------------------
# transport.log and note() must be defined BEFORE the run_id block below, which
# audits a run reset and a failed run_id cache write. note() used to live further
# down the file, so `note "run_id_write_failed"` called a function that did not
# exist yet and logged nothing at all.
# ---------------------------------------------------------------------------
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
TRANSPORT_LOG="$STATE_DIR/transport.log"

# Every transport outcome also goes to $ONESIGNAL_SKILL_RESULT_FILE when set, because a
# caller cannot learn it from the exit status — this script always exits 0. `flush` is
# the caller that needs it, to tell an accepted send from a refused one.
note() {
  printf '%s\t%s\t%s\t%s\n' "$TS" "$MILESTONE" "$1" "$2" >> "$TRANSPORT_LOG" 2>/dev/null || true
  if [ -n "${ONESIGNAL_SKILL_RESULT_FILE:-}" ]; then
    printf '%s' "$1" > "$ONESIGNAL_SKILL_RESULT_FILE" 2>/dev/null || true
  fi
}

# ---------------------------------------------------------------------------
# run_id — stable across the checkpoints of one funnel run, random per run.
#
# $ONESIGNAL_SKILL_RUN_ID overrides and is NOT persisted. Used for out-of-band
# checks (CI smoke tests, manual verification runs) so they never share a
# run_id with a real install and cannot corrupt completion-rate counting.
#
# Otherwise the id is cached in .onesignal/run_id so the milestones of one
# install share it. Two rules end a run:
#
#   1. A second entry into setup: this checkpoint is setup.preflight AND the
#      cached run already passed preflight. One run passes preflight at most
#      once, so a second pass is a new attempt. Keying on the milestone ALONE
#      would be wrong. Setup reports `preflight fail dirty_tree`, stops to ask
#      the user, then reports `preflight ok` in the same session, and that
#      fail -> ok pair is one run by design.
#   2. Idle for longer than RUN_IDLE_LIMIT. Covers the run abandoned BEFORE
#      preflight ever succeeded, which rule 1 cannot see.
#
# Both rules fail safe toward KEEPING the cached id. A merged run under-counts
# one dropout; a wrong reset invents a run that never happened.
# ---------------------------------------------------------------------------
new_id() {
  if [ -r /dev/urandom ]; then
    od -An -tx1 -N8 /dev/urandom 2>/dev/null | tr -d ' \n'
  else
    printf '%s-%s' "$(date +%s)" "$$"
  fi
}

RUN_ID_FILE="$STATE_DIR/run_id"
LAST_SEEN_FILE="$STATE_DIR/run_last_seen"
RUN_ENTRY_POINT="setup.preflight"
RUN_IDLE_LIMIT=28800   # 8 hours
NOW_EPOCH="$(date +%s 2>/dev/null || echo 0)"
case "$NOW_EPOCH" in ''|*[!0-9]*) NOW_EPOCH=0 ;; esac

# Rule 1. Only PRIOR rows can match: the current event is appended to
# checkpoints.jsonl further down, so it cannot see itself here.
run_passed_entry_point() {
  [ -f "$STATE_DIR/checkpoints.jsonl" ] || return 1
  grep -F "\"run_id\":\"$RUN_ID\"" "$STATE_DIR/checkpoints.jsonl" 2>/dev/null \
    | grep -E "\"milestone\":\"$MILESTONE\",\"status\":\"(ok|ok_after_fix)\"" \
    | grep -qF "\"skill\":\"$SKILL_NAME\""
}

# Rule 2. An absent or unparsable stamp means "cannot tell" — keep the run.
run_is_idle() {
  local last
  [ "$NOW_EPOCH" -gt 0 ] || return 1
  [ -f "$LAST_SEEN_FILE" ] || return 1
  last="$(cat "$LAST_SEEN_FILE" 2>/dev/null)"
  case "$last" in ''|*[!0-9]*) return 1 ;; esac
  [ $(( NOW_EPOCH - last )) -gt "$RUN_IDLE_LIMIT" ]
}

RUN_IS_NEW=0

if [ -n "${ONESIGNAL_SKILL_RUN_ID:-}" ]; then
  RUN_ID="$ONESIGNAL_SKILL_RUN_ID"
else
  [ -f "$RUN_ID_FILE" ] && RUN_ID="$(cat "$RUN_ID_FILE" 2>/dev/null)"
  if [ -n "${RUN_ID:-}" ]; then
    RESET_REASON=""
    if [ "$RAW_MILESTONE" = "$RUN_ENTRY_POINT" ] && run_passed_entry_point; then
      RESET_REASON="second $RUN_ENTRY_POINT in one run"
    elif run_is_idle; then
      RESET_REASON="idle for longer than $(( RUN_IDLE_LIMIT / 3600 ))h"
    fi
    if [ -n "$RESET_REASON" ]; then
      note "run_reset" "$RESET_REASON — previous run_id was $RUN_ID"
      RUN_ID=""
    fi
  fi
  if [ -z "${RUN_ID:-}" ]; then
    RUN_IS_NEW=1
    RUN_ID="$(new_id)"
    RUN_ID="${RUN_ID:-$(date +%s)-$$}"
    # Say so when the cache write fails: this event still sends with the fresh
    # id, but every later checkpoint mints another one and the funnel splits
    # into single-event runs that no completion-rate query can stitch together.
    if ! printf '%s' "$RUN_ID" > "$RUN_ID_FILE" 2>/dev/null; then
      note "run_id_write_failed" "cannot write $RUN_ID_FILE"
      echo "checkpoint: WARNING — could not cache run_id in $RUN_ID_FILE."
      echo "  Each milestone will mint its own run_id and this install will not"
      echo "  count as one funnel run. Check permissions on $STATE_DIR."
    fi
  fi
fi

# Mark activity for rule 2. The flush child re-sends an event that was recorded
# earlier, so it must not extend the session.
if [ "${ONESIGNAL_SKILL_SKIP_LOCAL_RECORD:-0}" != "1" ] && [ "$NOW_EPOCH" -gt 0 ]; then
  printf '%s' "$NOW_EPOCH" > "$LAST_SEEN_FILE" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# seq — where this report sits inside the run. run_id says which run, seq says
# the position in it, and the pair is what a query de-duplicates on. The event
# time cannot do that job: two checkpoints can share a whole second, and a
# buffered event reports a time from long before it arrives.
#
# The counter advances once per milestone RECORDED, never per send attempt. A
# flush re-send therefore carries the number the event was given when it was
# buffered, which is what makes the pair stable across a retry.
#
# seq=0 on the wire means exactly one thing: this position is unknown. A row
# from a buffer written before this counter existed reports 0, and so does a
# run whose counter file cannot be read or written.
# ---------------------------------------------------------------------------
SEQ_FILE="$STATE_DIR/seq"

if [ -n "${ONESIGNAL_SKILL_SEQ:-}" ]; then
  SEQ="$ONESIGNAL_SKILL_SEQ"
  case "$SEQ" in ''|*[!0-9]*) SEQ=0 ;; esac
elif [ "${ONESIGNAL_SKILL_SKIP_LOCAL_RECORD:-0}" = "1" ]; then
  SEQ=0
else
  SEQ=0
  SEQ_PREV=0
  SEQ_READABLE=1
  # A new run restarts at 1, so the previous value is deliberately not read.
  if [ "$RUN_IS_NEW" -eq 0 ] && [ -f "$SEQ_FILE" ]; then
    if SEQ_PREV="$(cat "$SEQ_FILE" 2>/dev/null)"; then
      case "$SEQ_PREV" in ''|*[!0-9]*) SEQ_PREV=0 ;; esac
    else
      note "seq_read_failed" "cannot read $SEQ_FILE"
      SEQ_READABLE=0
    fi
  fi
  if [ "$SEQ_READABLE" -eq 1 ]; then
    SEQ=$(( SEQ_PREV + 1 ))
    # A dry run shows the number this event would take without taking it. Storing
    # it here would leave a gap in the sequence of the run that follows.
    if [ "${ONESIGNAL_SKILL_DRY_RUN:-0}" = "1" ]; then
      :
    elif ! printf '%s' "$SEQ" > "$SEQ_FILE" 2>/dev/null; then
      # Reporting the number without storing it would give the next checkpoint
      # the same one, which reads as a duplicate of this event rather than a
      # missing position.
      note "seq_write_failed" "cannot write $SEQ_FILE"
      SEQ=0
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Runtime detection.
#
# HEURISTIC AND INCOMPLETE. These env vars are best guesses, not documented
# contracts, and some are certainly wrong. Confirm each one against a real run
# in that runtime by checking what `env | sort` actually shows, then correct
# this block. Until then expect "unknown" and treat the field as unreliable.
# ---------------------------------------------------------------------------
detect_runtime() {
  [ -n "${CLAUDECODE:-}${CLAUDE_CODE:-}" ]           && { echo "claude-code";   return; }
  [ -n "${CODEX_SANDBOX:-}${CODEX_HOME:-}" ]         && { echo "codex";         return; }
  [ -n "${CURSOR_TRACE_ID:-}${CURSOR_AGENT:-}" ]     && { echo "cursor";        return; }
  [ -n "${GITHUB_COPILOT_AGENT:-}" ]                 && { echo "copilot-agent"; return; }
  [ -n "${COPILOT_AGENT_ID:-}" ]                     && { echo "copilot-agent"; return; }
  [ -n "${GEMINI_CLI:-}${GEMINI_SANDBOX:-}" ]        && { echo "gemini-cli";    return; }
  [ -n "${AMP_THREAD_ID:-}" ]                        && { echo "amp";           return; }
  [ -n "${WINDSURF_SESSION_ID:-}" ]                  && { echo "windsurf";      return; }
  [ -n "${CLINE_SANDBOX:-}" ]                        && { echo "cline";         return; }
  [ -n "${DEVIN_SESSION_ID:-}" ]                     && { echo "devin";         return; }
  echo "unknown"
}

# The env overrides exist for the flush path: a re-send must carry the values
# recorded when the event happened, not re-detect them at flush time.
RUNTIME="${ONESIGNAL_SKILL_RUNTIME:-$(detect_runtime)}"
OS_NAME="${ONESIGNAL_SKILL_OS:-$(uname -s 2>/dev/null | tr '[:upper:]' '[:lower:]')}"
OS_NAME="${OS_NAME:-unknown}"

# The payload's ts is the EVENT time, which the encoder stamps onto the wire
# record. A flush re-send passes the buffered event's original ts in via
# ONESIGNAL_SKILL_TS so a milestone that waited in the buffer keeps the moment
# it happened, not the moment it was flushed. $TS (now) still stamps
# transport.log, which records attempts.
PAYLOAD_TS="${ONESIGNAL_SKILL_TS:-$TS}"

# JSON string escaping for every interpolated field. A double quote in any
# field used to produce invalid JSON, a nonzero encoder exit, and a permanently
# broken row in checkpoints.jsonl/pending.jsonl. JSON strings need exactly
# three things handled: backslash, double quote, and control characters —
# the first two are escaped, control characters are dropped (no field
# legitimately contains them). Pure shell, so the local record stays valid
# even on machines without python3.
json_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr -d '\000-\037'
}

E_SOURCE=$(json_escape "$SOURCE_TAG")
E_RUN_ID=$(json_escape "$RUN_ID")
E_MILESTONE=$(json_escape "$MILESTONE")
E_STATUS=$(json_escape "$STATUS")
E_RUNTIME=$(json_escape "$RUNTIME")
E_OS=$(json_escape "$OS_NAME")
E_TS=$(json_escape "$PAYLOAD_TS")
E_APP_ID=$(json_escape "$APP_ID")
E_PLATFORM=$(json_escape "$PLATFORM")
E_SKILL=$(json_escape "$SKILL_NAME")

if [ -n "$FAILURE_CLASS" ]; then
  FC_JSON="\"$(json_escape "$FAILURE_CLASS")\""
else
  FC_JSON="null"
fi

if [ -n "$FAILURE_DETAIL" ]; then
  FD_JSON="\"$(json_escape "$FAILURE_DETAIL")\""
else
  FD_JSON="null"
fi

PAYLOAD=$(cat <<JSON
{"schema":$SCHEMA_VERSION,"source":"$E_SOURCE","run_id":"$E_RUN_ID","seq":$SEQ,"skill_version":"$SKILL_VERSION","milestone":"$E_MILESTONE","status":"$E_STATUS","failure_class":$FC_JSON,"failure_detail":$FD_JSON,"runtime":"$E_RUNTIME","os":"$E_OS","ts":"$E_TS","app_id":"$E_APP_ID","platform":"$E_PLATFORM","skill":"$E_SKILL"}
JSON
)

# The one free-text field, and it is not free text: it is built from values this
# script already validated, so it cannot carry anything the query parameters do
# not carry. The server turns it into the log body, which is the column a person
# reads first when scanning these events.
MESSAGE="onesignal onboarding [$PLATFORM/$SKILL_NAME]: $MILESTONE $STATUS"
if [ -n "$FAILURE_CLASS" ]; then
  MESSAGE="$MESSAGE $FAILURE_CLASS"
fi

# ---------------------------------------------------------------------------
# The request, defined once. The dry run prints this array and the send passes
# it to curl, so what you inspect is what goes out.
#
# curl percent-encodes each value: `message` holds spaces and brackets, and a
# `platform` or `source` override is not otherwise constrained. Building the
# query string by hand here would put that encoding in shell, where a missed
# character silently truncates a value at the server.
#
# An absent failure class omits the KEY. Sending an empty one would create a
# class named "" that every count of failure classes then has to exclude.
# failure_detail follows the same rule: the key is absent unless a sanitized
# slug survived, and `message` never includes it.
# ---------------------------------------------------------------------------
QUERY_ARGS=(
  --data-urlencode "app_id=$APP_ID"
  --data-urlencode "schema=$SCHEMA_VERSION"
  --data-urlencode "source=$SOURCE_TAG"
  --data-urlencode "run_id=$RUN_ID"
  --data-urlencode "seq=$SEQ"
  --data-urlencode "skill=$SKILL_NAME"
  --data-urlencode "milestone=$MILESTONE"
  --data-urlencode "status=$STATUS"
  --data-urlencode "platform=$PLATFORM"
  --data-urlencode "runtime=$RUNTIME"
  --data-urlencode "os=$OS_NAME"
  --data-urlencode "skill_version=$SKILL_VERSION"
  --data-urlencode "timestamp=$PAYLOAD_TS"
  --data-urlencode "message=$MESSAGE"
)
if [ -n "$FAILURE_CLASS" ]; then
  QUERY_ARGS+=( --data-urlencode "failure_class=$FAILURE_CLASS" )
fi
if [ -n "$FAILURE_DETAIL" ]; then
  QUERY_ARGS+=( --data-urlencode "failure_detail=$FAILURE_DETAIL" )
fi

# ---------------------------------------------------------------------------
# Dry run: print the exact request and stop. Nothing is sent, nothing is logged.
# For comparing this request against what the endpoint expects.
# ---------------------------------------------------------------------------
if [ "${ONESIGNAL_SKILL_DRY_RUN:-0}" = "1" ]; then
  echo "GET $ENDPOINT"
  echo "  [endpoint from $ENDPOINT_SRC]"
  # No custom header is sent, and none may be added here either. An earlier
  # version advertised an X-OneSignal-Skill header that the real request
  # deliberately omits, which reads as license to add it back. Doing so trips a
  # Cloudflare WAF rule and returns a 403 HTML block page.
  echo "  no request body, and no header beyond curl's own"
  echo
  echo "--- query parameters, before curl percent-encodes each value ---"
  for arg in "${QUERY_ARGS[@]}"; do
    [ "$arg" = "--data-urlencode" ] && continue
    printf '  %s\n' "$arg"
  done
  if [ -z "$APP_ID" ]; then
    echo
    echo "  NOTE: no App ID yet, so a real run would buffer this event instead of sending it."
  fi
  echo
  echo "--- the same event as it is recorded locally ---"
  printf '%s\n' "$PAYLOAD"
  echo
  echo "(dry run — nothing sent, nothing logged)"
  exit 0
fi

# ---------------------------------------------------------------------------
# Local record. Written before any network attempt, so the user can always see
# what would be or was sent. Exactly one row per milestone reported: a flush
# re-send sets ONESIGNAL_SKILL_SKIP_LOCAL_RECORD, because the event was already
# recorded when it was buffered. transport.log is where attempts accumulate.
# ---------------------------------------------------------------------------
if [ "${ONESIGNAL_SKILL_SKIP_LOCAL_RECORD:-0}" != "1" ]; then
  if ! printf '%s\n' "$PAYLOAD" >> "$STATE_DIR/checkpoints.jsonl" 2>/dev/null; then
    echo "checkpoint: WARNING — could not write the local record to $STATE_DIR/checkpoints.jsonl"
  fi
fi

# Hold this event for a later flush after a TRANSPORT failure. Buffering used to
# be gated only on a missing App ID, so once Step 2 wrote it (5 of 7 milestones),
# a blocked network dropped every event with exit 0 while the contract promised
# a later flush "loses nothing". Which outcomes hold and which drop is decided in
# the `case "$HTTP_CODE"` arms below: a transient failure is held, and a request
# the service already rejected is not. A 4xx that is not 429 rejects this exact
# request, so a re-send would fail in the same way on every future flush.
#
# A flush child must NOT re-append: the parent collects the rows that failed and
# rewrites the buffer, so appending here would duplicate the row.
rebuffer() {
  if [ "${ONESIGNAL_SKILL_SKIP_LOCAL_RECORD:-0}" = "1" ]; then
    return 0
  fi
  if printf '%s\n' "$PAYLOAD" >> "$STATE_DIR/pending.jsonl" 2>/dev/null; then
    echo "  Held in $STATE_DIR/pending.jsonl — run 'bash scripts/checkpoint.sh flush' when the network allows."
  else
    note "buffer_write_failed" "cannot append to $STATE_DIR/pending.jsonl"
    echo "  NOT held — cannot write $STATE_DIR/pending.jsonl; this event will not send later."
  fi
}

# Opt-out is recorded, not silent. Previously this branch returned before note()
# was even defined, so a declined checkpoint left no trace in transport.log —
# indistinguishable from the agent skipping the checkpoint altogether. Anyone
# auditing whether a refusal was honoured needs to see it, and needs the source:
# an env opt-out, a recorded opt-out, and an answer nobody gave are three
# different findings, and only the middle one is a durable refusal.
if telemetry_disabled; then
  case "$TELEMETRY_SOURCE" in
    env)  note "telemetry_disabled" "ONESIGNAL_SKILL_TELEMETRY=0 for this invocation — no network call attempted"
          echo "checkpoint: $MILESTONE=$STATUS (reporting disabled; logged locally)" ;;
    file) note "telemetry_disabled" "opt-out recorded in $TELEMETRY_FILE — no network call attempted"
          echo "checkpoint: $MILESTONE=$STATUS (reporting disabled; logged locally)" ;;
    # "Unset" is not an opt-out: nobody answered. Left as a bare "disabled"
    # line, this state is indistinguishable from a refusal, so a "send" answer
    # that was never recorded stays a silent opt-out for the rest of the run.
    # Direct the caller to the recovery, and HOLD the event: an unanswered
    # question can still resolve to "send", and a later `flush` under a
    # recorded 1 then loses nothing. A recorded 0 never reaches this arm, and
    # a buffer held under an answer of 0 is skipped by `flush` forever.
    *)    note "telemetry_unset" "no usable consent answer in env or file — no network call attempted"
          echo "checkpoint: $MILESTONE=$STATUS (no consent answer recorded; logged locally)"
          echo "  Fail-closed: nothing sends until an answer exists. If the user already"
          echo "  answered the consent question, record it, then run 'flush':"
          echo "    printf '1\n' > $TELEMETRY_FILE   # 0 for keep-local"
          echo "  If they were never asked, ask once (setup SKILL.md, checkpoint consent)."
          rebuffer ;;
  esac
  exit 0
fi

# The dangerous state sends fine, which is exactly why it goes unnoticed until
# the session ends and takes the env value with it. The flush child skips the
# warning: its parent already printed it once for the whole flush.
if [ "$CONSENT_ENV_ONLY" -eq 1 ] && [ "${ONESIGNAL_SKILL_SKIP_LOCAL_RECORD:-0}" != "1" ]; then
  note "consent_env_only" "ONESIGNAL_SKILL_TELEMETRY=1 but $TELEMETRY_FILE has no answer"
  warn_consent_env_only
fi

# ---------------------------------------------------------------------------
# Misconfiguration guard.
#
# The built-in default is intentionally unresolvable. Because endpoint.conf
# ships with the production endpoint, this branch is normally dead — it exists
# for installs where that file was deleted or emptied. If it fires, say so
# unambiguously — do NOT let this look like a blocked network. During
# validation those two conclusions are opposites: one means "fix your config",
# the other means "this runtime denies egress". Conflating them wastes a whole
# test round.
# ---------------------------------------------------------------------------
if [ "$ENDPOINT" = "$DEFAULT_ENDPOINT" ]; then
  note "not_configured" "no endpoint configured from any source"
  echo "checkpoint: $MILESTONE=$STATUS"
  echo "  ENDPOINT NOT CONFIGURED — nothing was sent, and this is NOT evidence of a"
  echo "  blocked network. No endpoint was found in any of:"
  echo "    1. \$ONESIGNAL_SKILL_ENDPOINT   (not set in this shell)"
  echo "    2. $PROJECT_CONF   (absent or empty)"
  echo "    3. endpoint.conf next to the skill  (absent or empty)"
  echo
  echo "  To configure for testing, write the file — an 'export' in your own terminal"
  echo "  will NOT reach the shell this script runs in:"
  echo "    mkdir -p $STATE_DIR && echo 'https://your-host/checkpoints' > $PROJECT_CONF"
  echo
  echo "  Payload logged locally to $STATE_DIR/checkpoints.jsonl."
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  note "no_curl" "curl not on PATH"
  echo "checkpoint: $MILESTONE=$STATUS (curl unavailable; logged locally)"
  exit 0
fi

# ---------------------------------------------------------------------------
# The ingestion service requires an app_id query parameter. Without one it
# returns 400, so don't waste a request.
# ---------------------------------------------------------------------------
# BUFFER, don't fake. Early milestones (setup.preflight) fire before Step 2 has an
# App ID, and the endpoint requires one as a query parameter. Hold the event and send
# it on the next `flush` rather than substituting a placeholder (safety contract §19).
# "Buffered" is only claimed when the append actually succeeded. On an
# unwritable .onesignal the payload is gone — saying "held for flush" would
# promise a delivery that can never happen.
if [ -z "$APP_ID" ]; then
  if printf '%s\n' "$PAYLOAD" >> "$STATE_DIR/pending.jsonl" 2>/dev/null; then
    note "buffered" "no app_id yet — held for flush"
    echo "checkpoint: $MILESTONE=$STATUS (buffered — no App ID yet)"
    echo "  Held in $STATE_DIR/pending.jsonl. Once the App ID is known:"
    echo "    echo '<uuid>' > $APP_ID_PROJECT_CONF && bash scripts/checkpoint.sh flush"
  else
    note "buffer_write_failed" "cannot append to $STATE_DIR/pending.jsonl"
    echo "checkpoint: $MILESTONE=$STATUS (NOT buffered — cannot write $STATE_DIR/pending.jsonl)"
    echo "  The event was NOT held and will not send later. Check permissions on $STATE_DIR."
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Send. Every field rides in the query string, so curl alone can build the whole
# request: -G moves the --data-urlencode values into the URL and sends a GET.
#
# Add NO header. Adding an X-OneSignal-Skill identification header caused
# Cloudflare to return a 403 HTML block page before the request reached the
# service, while a byte-identical request without it returned 202. Unrecognised
# custom headers on this path trip a WAF rule. Nothing is lost by leaving them
# out: source, skill_version, runtime and run_id are parameters already.
#
# The endpoint keeps any query the URL already carries, so an endpoint override
# that includes one still works.
# ---------------------------------------------------------------------------
RESP_FILE="$STATE_DIR/last_response"
HTTP_CODE=$(curl -sS -o "$RESP_FILE" -w '%{http_code}' \
  --max-time "$TIMEOUT" \
  -G "$ENDPOINT" \
  "${QUERY_ARGS[@]}" 2>"$STATE_DIR/last_curl_error" )
CURL_RC=$?

# ---------------------------------------------------------------------------
# Diagnose transport failures precisely. Which curl exit code you get is the
# whole signal during validation:
#
#   6  DNS did not resolve       -> bad hostname, or DNS-level egress blocking
#   5  could not resolve proxy   -> proxy env var set but proxy host is wrong
#   7  connection refused/failed -> firewall REJECTing, or nothing listening
#   28 timed out                 -> firewall silently DROPping (most common in
#                                    sandboxed agent runtimes)
#   35/60 TLS failure            -> intercepting proxy, or cert problem
#   52/56 empty reply / recv fail -> a proxy accepted the connection then killed
#                                    it. This is what a block looks like in a
#                                    PROXIED sandbox, where you never see 6/7/28.
#
# 7, 28, 52 and 56 are the interesting ones for "does this runtime allow egress".
# 6 against a hostname you know is real also counts; 6 against a typo does not.
#
# Observed: in a proxied sandbox, a request to a nonexistent host returns 56, not
# 6 — the proxy resolves on your behalf and then drops. So do NOT read 56 as
# proof of a deliberate policy block without checking the hostname is real first.
# ---------------------------------------------------------------------------
if [ "$CURL_RC" -ne 0 ]; then
  case "$CURL_RC" in
    6)     DIAG="dns_unresolved";     HINT="host did not resolve. If the same host resolves from a normal terminal on this machine, this is a sandbox policy block, not a bad URL — confirmed Codex behaviour" ;;
    5)     DIAG="proxy_unresolved";   HINT="proxy host did not resolve — check http_proxy/https_proxy" ;;
    7)     DIAG="connection_refused"; HINT="connection refused or actively rejected — consistent with an egress firewall" ;;
    28)    DIAG="timeout";            HINT="timed out after ${TIMEOUT}s — consistent with a silently dropping firewall" ;;
    35|60) DIAG="tls_error";          HINT="TLS failure — possible intercepting proxy or certificate problem" ;;
    52|56) DIAG="connection_killed";  HINT="connection accepted then dropped — typical of a proxied sandbox blocking egress; confirm the hostname is real before calling it policy" ;;
    *)     DIAG="curl_rc_$CURL_RC";   HINT="see $STATE_DIR/last_curl_error" ;;
  esac
  note "$DIAG" "curl rc=$CURL_RC via $ENDPOINT_SRC"
  echo "checkpoint: $MILESTONE=$STATUS (not sent: $DIAG; logged locally)"
  echo "  endpoint: $ENDPOINT  [from $ENDPOINT_SRC]"
  echo "  $HINT"
  case "$ENDPOINT" in
    *127.0.0.1*|*localhost*|*::1*)
      echo "  NOTE: this is a loopback address. If the agent runs in a sandbox or"
      echo "  container, its 127.0.0.1 is not your machine's. Use a public host or a"
      echo "  tunnel to test egress meaningfully." ;;
  esac
  rebuffer
  exit 0
fi

# Print what the server actually said. Do NOT guess at a cause: a status code alone
# cannot tell us WHICH hop answered. Requests to a public host may be rejected by a
# CDN, an API gateway, or a load balancer long before reaching the ingestion service,
# and those hops return their own 4xx with their own semantics. An earlier version of
# this script asserted ingestion-service meanings for every code and produced a
# confidently wrong diagnosis.
explain_http_error() {
  if [ -s "$RESP_FILE" ]; then
    echo "  response body:"
    head -c 400 "$RESP_FILE" 2>/dev/null | sed 's/^/    /'
    echo
  else
    echo "  (empty response body)"
  fi
  # Only interpret when the body is recognisably from one side or the other.
  # HTML is checked first: a block page is neither the service nor the JSON API,
  # and the earlier branches would otherwise mislabel it.
  if head -c 200 "$RESP_FILE" 2>/dev/null | grep -qiE '<!DOCTYPE|<html'; then
    echo "  ^ an HTML error page, so a CDN or WAF blocked this before it reached any"
    echo "    application. Triggered by request SHAPE, not content — the known cause"
    echo "    here is an unexpected custom header. Send no header at all."
  elif grep -qiE 'status (Enabled|Disabled|Unknown)|Missing required parameter: app_id|Invalid UUID format|Missing query parameters' "$RESP_FILE" 2>/dev/null; then
    echo "  ^ this is the ingestion service responding. Its codes: 202 accepted;"
    echo "    400 = the app_id query parameter is missing or is not a UUID;"
    echo "    403 = the app exists but is not Enabled."
  elif grep -qiE 'parse JSON|Authorization|API key' "$RESP_FILE" 2>/dev/null; then
    echo "  ^ NOT the ingestion service. A JSON-parse or API-key error means the"
    echo "    general OneSignal JSON API handled it, i.e. nothing is routed at this"
    echo "    path. The real path is /sdk/agent-progress — check the URL."
  fi
}

case "$HTTP_CODE" in
  # "Sent" means the ingestion service accepted it, and its handler answers 202
  # (StatusCode::ACCEPTED) — 200 is tolerated as its likeliest drift. A blanket
  # 2* match counted captive portals and proxy interstitials as delivered: a
  # portal answers 200 with an HTML sign-in page having ingested nothing, and in
  # exactly the networks where sends fail. HTML from a 2xx is therefore treated
  # as not delivered and re-buffered, like any other transport failure.
  200|202)
    if head -c 200 "$RESP_FILE" 2>/dev/null | grep -qiE '<!DOCTYPE|<html'; then
      note "http_2xx_html" "HTTP $HTTP_CODE with an HTML body — an interstitial answered, not the service"
      echo "checkpoint: $MILESTONE=$STATUS (HTTP $HTTP_CODE but the body is HTML; logged locally)"
      echo "  A captive portal or proxy interstitial accepted this request; the ingestion"
      echo "  service never saw it. Not counted as sent."
      rebuffer
    else
      note "sent" "HTTP $HTTP_CODE app_id_src=$APP_ID_SRC"
      echo "checkpoint: $MILESTONE=$STATUS (reported, HTTP $HTTP_CODE)"
    fi ;;
  2*)
    note "http_2xx_unexpected" "HTTP $HTTP_CODE — the service answers 202; delivery unconfirmed"
    echo "checkpoint: $MILESTONE=$STATUS (unexpected HTTP $HTTP_CODE; logged locally)"
    rebuffer ;;
  000)
    note "no_response" "curl rc=0 but no status line"
    echo "checkpoint: $MILESTONE=$STATUS (no HTTP response; logged locally)"
    rebuffer ;;
  # 429 and 5xx are transient: the service asked us to slow down, or it failed on its
  # own side. Hold the event and let a later flush try again. 429 must be matched before
  # the 4* arm below, because `case` takes the first pattern that matches.
  429|5*)
    note "http_retryable" "HTTP $HTTP_CODE"
    echo "checkpoint: $MILESTONE=$STATUS (HTTP $HTTP_CODE, transient; logged locally)"
    echo "  The request left this machine — egress is NOT blocked. The server refused it"
    echo "  in a way that can succeed later, so the event is held for a retry."
    explain_http_error
    rebuffer ;;
  # Every other 4xx rejects THIS payload permanently. A malformed app_id or a wrong
  # Content-Type fails identically on every attempt, so a re-buffered event would be
  # retried forever and would block the rows behind it.
  4*)
    note "http_rejected" "HTTP $HTTP_CODE"
    echo "checkpoint: $MILESTONE=$STATUS (HTTP $HTTP_CODE, permanent; logged locally)"
    echo "  The request left this machine — egress is NOT blocked. Something rejected it,"
    echo "  and the same payload would be rejected again, so it is NOT held for a retry."
    explain_http_error ;;
  *)
    note "http_other" "HTTP $HTTP_CODE"
    echo "checkpoint: $MILESTONE=$STATUS (endpoint returned HTTP $HTTP_CODE; logged locally)" ;;
esac

exit 0
