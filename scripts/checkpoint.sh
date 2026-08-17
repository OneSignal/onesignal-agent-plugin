#!/usr/bin/env bash
# checkpoint.sh — report one onboarding milestone for the OneSignal agent plugin.
#
# Usage:
#   bash scripts/checkpoint.sh <skill.milestone> <ok|ok_after_fix|fail> [class]
#   bash scripts/checkpoint.sh flush        # send events buffered before the App ID existed
#
# Milestone vocabulary, failure classes and the funnel model:
#   references/telemetry-contract.md
# Safety rules that bind this script:
#   references/safety-contract.md §15-20
#
# CONTRACT — do not break these three properties:
#   1. Always exits 0. Telemetry never fails the user's install.
#   2. Never sends source code, file contents, file paths, or project/package names.
#      NOTE: the App ID *is* sent — the ingestion endpoint requires it as a query
#      parameter. Earlier versions did not send it and both this comment and
#      SKILL.md described the payload as containing no App ID. That was true then
#      and is false now; any description given to a user must say so.
#   3. Honours ONESIGNAL_SKILL_TELEMETRY=0 as a full opt-out.
#
# Payload (exactly this, nothing more):
#   {
#     "schema": 2,
#     "source":         "onesignal-agent-plugin",   <- discriminator, see below
#     "run_id":         "<random hex, stable for one funnel run>",
#     "skill_version":  "0.3.0",
#     "milestone":      "credentials_gate",
#     "status":         "ok" | "ok_after_fix" | "fail",
#     "failure_class":  "kotlin_stdlib_floor" | ... | null,
#     "runtime":        "claude-code" | "codex" | ... | "unknown",
#     "os":             "darwin" | "linux" | ...,
#     "ts":             "2026-07-27T12:00:00Z",
#     "app_id":         "<uuid>",
#     "platform":       "android" | "web" | ...,
#     "skill":          "setup"
#   }
#
# This JSON is the internal representation. It is encoded into an OTLP LogsData
# protobuf by otlp_encode.py before sending — the endpoint accepts nothing else.
# `ts` is the EVENT time and is what the encoder stamps onto the wire record, so
# a buffered event flushed minutes later still reports the moment it happened.
# The send moment is carried separately in observed_time_unix_nano.
#
# "source" exists so these events can be separated from real SDK traffic on the
# shared ingestion endpoint. Override it with $ONESIGNAL_SKILL_SOURCE if the
# ingestion service wants a different discriminator.
#
# Set ONESIGNAL_SKILL_DRY_RUN=1 to print the exact request without sending.

set -uo pipefail

PLUGIN_VERSION="0.3.0"
SKILL_VERSION="$PLUGIN_VERSION"
SOURCE_TAG="${ONESIGNAL_SKILL_SOURCE:-onesignal-agent-plugin}"
DEFAULT_ENDPOINT="https://example.invalid/skill-checkpoints"
TIMEOUT="${ONESIGNAL_SKILL_TIMEOUT:-5}"
UUID_RE='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'

RAW_MILESTONE="${1:-unknown}"
STATUS="${2:-unknown}"
FAILURE_CLASS="${3:-}"

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
    # Re-qualify as "<skill>.<milestone>". Passing the bare milestone made the child
    # derive skill="unknown", erasing the agent.skill label for every buffered event.
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
      bash "$0" "$QUALIFIED" "$S" "$FC" 2>/dev/null
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
    echo "    bash scripts/checkpoint.sh $RAW_MILESTONE <ok|ok_after_fix|fail> [failure_class]"
    exit 0 ;;
esac

if [ -n "$FAILURE_CLASS" ] && ! printf '%s' "$FAILURE_CLASS" | grep -qE '^[a-z][a-z0-9_]{0,39}$'; then
  echo "checkpoint: failure_class '$FAILURE_CLASS' is not a valid class — reporting 'unknown' instead."
  echo "  Classes are lowercase snake_case from references/telemetry-contract.md."
  echo "  Free text (paths, error messages) must never reach the wire."
  FAILURE_CLASS="unknown"
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
# must be Enabled (else 403), unless the UUID is in the ConfigCat
# `allowed_listed_uuids` list, which bypasses the status check.
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

PAYLOAD=$(cat <<JSON
{"schema":2,"source":"$E_SOURCE","run_id":"$E_RUN_ID","skill_version":"$SKILL_VERSION","milestone":"$E_MILESTONE","status":"$E_STATUS","failure_class":$FC_JSON,"runtime":"$E_RUNTIME","os":"$E_OS","ts":"$E_TS","app_id":"$E_APP_ID","platform":"$E_PLATFORM","skill":"$E_SKILL"}
JSON
)

# ---------------------------------------------------------------------------
# Dry run: print the exact request and stop. Nothing is sent, nothing is logged.
# For comparing this payload against a target endpoint's expected schema.
# ---------------------------------------------------------------------------
if [ "${ONESIGNAL_SKILL_DRY_RUN:-0}" = "1" ]; then
  echo "POST ${ENDPOINT}?app_id=${APP_ID:-<UNSET>}"
  echo "  [endpoint from $ENDPOINT_SRC]"
  # Content-Type is the ONLY header sent. Do not print others here either — an
  # earlier version advertised an X-OneSignal-Skill header that the real request
  # deliberately omits, which reads as license to add it back. Doing so trips a
  # Cloudflare WAF rule and returns a 403 HTML block page.
  echo "Content-Type: application/x-protobuf"
  echo
  echo "--- checkpoint fields (encoded into OTLP LogsData) ---"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$PAYLOAD" | python3 -m json.tool 2>/dev/null || printf '%s\n' "$PAYLOAD"
    ENC="$SCRIPT_DIR/otlp_encode.py"
    if [ -f "$ENC" ]; then
      SIZE=$(printf '%s' "$PAYLOAD" | python3 "$ENC" 2>/dev/null | wc -c | tr -d ' ')
      echo
      echo "--- encoded body: ${SIZE} bytes of application/x-protobuf ---"
    fi
  else
    printf '%s\n' "$PAYLOAD"
  fi
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
# a later flush "loses nothing". Scope: the outcomes marked as held in the retry
# matrix. An encoder error, or a 4xx that is not 429, rejects this exact payload,
# so a re-send would fail in the same way on every future flush.
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
# auditing whether a refusal was honoured needs to see it.
if [ "${ONESIGNAL_SKILL_TELEMETRY:-1}" = "0" ]; then
  note "telemetry_disabled" "ONESIGNAL_SKILL_TELEMETRY=0 — no network call attempted"
  echo "checkpoint: $MILESTONE=$STATUS (reporting disabled; logged locally)"
  exit 0
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
# Body must be an OTLP LogsData protobuf. curl cannot build that, so shell out
# to the dependency-free encoder. python3 is the one external requirement for
# reporting; without it we degrade to local logging like any other failure.
# ---------------------------------------------------------------------------
ENCODER="$SCRIPT_DIR/otlp_encode.py"

if ! command -v python3 >/dev/null 2>&1; then
  note "no_python3" "python3 required to encode OTLP protobuf"
  echo "checkpoint: $MILESTONE=$STATUS (not sent: python3 unavailable; logged locally)"
  echo "  The endpoint accepts only OTLP protobuf, which needs python3 to encode."
  exit 0
fi
if [ ! -f "$ENCODER" ]; then
  note "no_encoder" "otlp_encode.py missing at $ENCODER"
  echo "checkpoint: $MILESTONE=$STATUS (not sent: encoder missing; logged locally)"
  exit 0
fi

BODY_FILE="$STATE_DIR/last_body.pb"
if ! printf '%s' "$PAYLOAD" | python3 "$ENCODER" > "$BODY_FILE" 2>"$STATE_DIR/last_encode_error"; then
  note "encode_failed" "otlp_encode.py returned nonzero"
  echo "checkpoint: $MILESTONE=$STATUS (not sent: protobuf encoding failed; logged locally)"
  echo "  see $STATE_DIR/last_encode_error"
  exit 0
fi

# app_id goes in the query string, not the body. Preserve any existing query.
case "$ENDPOINT" in
  *\?*) URL="${ENDPOINT}&app_id=${APP_ID}" ;;
  *)    URL="${ENDPOINT}?app_id=${APP_ID}" ;;
esac

# Content-Type is compared with strict equality server-side; anything other than
# exactly "application/x-protobuf" returns 415.
# Send ONLY Content-Type. Adding an X-OneSignal-Skill identification header here
# caused Cloudflare to return a 403 HTML block page before the request reached the
# service, while a byte-identical request without it returned 202. Unrecognised
# custom headers on this path trip a WAF rule.
#
# Nothing is lost: the payload already carries source, skill_version, runtime and
# run_id, so identification does not need to live in a header. Match the shape of
# a real SDK request as closely as possible and add nothing.
RESP_FILE="$STATE_DIR/last_response"
HTTP_CODE=$(curl -sS -o "$RESP_FILE" -w '%{http_code}' \
  --max-time "$TIMEOUT" \
  -X POST "$URL" \
  -H 'Content-Type: application/x-protobuf' \
  --data-binary "@$BODY_FILE" 2>"$STATE_DIR/last_curl_error" )
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
    echo "    here is an unexpected custom header. Send only Content-Type."
  elif grep -qiE 'status (Enabled|Disabled|Unknown)|Missing required parameter: app_id|Invalid UUID format' "$RESP_FILE" 2>/dev/null; then
    echo "  ^ this is the ingestion service responding. Its codes: 202 accepted;"
    echo "    400 = missing or malformed app_id query param; 415 = Content-Type"
    echo "    is not exactly application/x-protobuf."
  elif grep -qiE 'parse JSON|Authorization|API key' "$RESP_FILE" 2>/dev/null; then
    echo "  ^ NOT the ingestion service. A JSON-parse or API-key error means the"
    echo "    general OneSignal JSON API handled it, i.e. nothing is routed at this"
    echo "    path. The real path is /sdk/log — check the URL."
  fi
}

case "$HTTP_CODE" in
  # "Sent" means the ingestion service accepted it, and the service answers 202
  # (INGESTION_CONTRACT.md) — 200 is tolerated as its likeliest drift. A blanket
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
