#!/usr/bin/env bash
# Regression tests for scripts/checkpoint.sh transport and buffering.
#
# Hermetic: every request goes to a loopback mock that speaks the ingestion service's
# contract (202 on POST /sdk/log with an app_id query param and an exact
# application/x-protobuf Content-Type). No real network egress, no real App ID.
#
# This is the eval convention for checkpoints. `ONESIGNAL_SKILL_TELEMETRY=0` cannot be
# the default: it returns before the buffering branch, so pending.jsonl is never written
# and the flush path — where the bugs were — is unreachable. The opt-out is exercised
# here as its own case instead.
#
#   bash evals/checkpoint-transport-test.sh
#
# Exits non-zero if any assertion fails. Requires bash, python3, curl.
set -uo pipefail

PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
CHECKPOINT="$PLUGIN/scripts/checkpoint.sh"
PORT="${CHECKPOINT_TEST_PORT:-8817}"
DEAD_PORT="${CHECKPOINT_TEST_DEAD_PORT:-8818}"
# Must be a syntactically valid UUID: the ingestion service (and the mock,
# which mirrors it) rejects anything that does not parse as one with a 400.
APP_ID="6fdc6a30-0000-4000-8000-00000e7a0001"
CANARY="DO_NOT_READ_CANARY_a1b2c3"

# The script under test reads these from the environment with priority over its
# project files. Anything inherited from the caller's shell (a leftover export
# from a manual mock session, say) would silently redirect every assertion.
unset ONESIGNAL_SKILL_ENDPOINT ONESIGNAL_SKILL_APP_ID ONESIGNAL_SKILL_TELEMETRY \
      ONESIGNAL_SKILL_RUN_ID ONESIGNAL_SKILL_PLATFORM ONESIGNAL_SKILL_DRY_RUN \
      ONESIGNAL_SKILL_SOURCE ONESIGNAL_SKILL_RESULT_FILE ONESIGNAL_SKILL_SKIP_LOCAL_RECORD \
      ONESIGNAL_SKILL_TS ONESIGNAL_SKILL_TIMEOUT

TMP="$(mktemp -d)"
PASS=0
FAIL=0

cleanup() {
  [ -n "${MOCK_PID:-}" ] && kill "$MOCK_PID" 2>/dev/null
  rm -rf "$TMP"
}
trap cleanup EXIT

ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '        %s\n' "$2"; }
want() { # want <description> <expected> <actual>
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$2], got [$3]"; fi
}

# --- mock ingestion endpoint -------------------------------------------------
cat > "$TMP/mock.py" <<'PY'
import http.server, json, socketserver, sys, threading, time, urllib.parse

RECORD = sys.argv[2]
requests = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        requests.append(
            {
                "path": parsed.path,
                "app_id": query.get("app_id", [None])[0],
                "content_type": self.headers.get("Content-Type"),
                "headers": sorted(k.lower() for k in self.headers.keys()),
                "body_hex": body.hex(),
            }
        )
        with open(RECORD, "w") as fh:
            json.dump(requests, fh)
        if parsed.path == "/portal":
            # A captive portal: HTTP 200, HTML body, nothing ingested.
            page = b"<!DOCTYPE html><html><body>Sign in to this network</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        # The real service answers 202, not 200.
        self.send_response(202)
        self.end_headers()

    def log_message(self, *args):
        pass


socketserver.TCPServer.allow_reuse_address = True
server = socketserver.TCPServer(("127.0.0.1", int(sys.argv[1])), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
with open(sys.argv[3], "w") as fh:
    fh.write("ready")
time.sleep(int(sys.argv[4]))
PY

# --- OTLP decoder used by the assertions ------------------------------------
cat > "$TMP/decode.py" <<'PY'
"""Decode the OTLP LogsData requests the mock captured into flat attribute dicts."""
import json, struct, sys


def varint(buf, i):
    result = shift = 0
    while True:
        byte = buf[i]
        result |= (byte & 0x7F) << shift
        i += 1
        shift += 7
        if not byte & 0x80:
            return result, i


def fields(buf):
    out = []
    i = 0
    while i < len(buf):
        tag, i = varint(buf, i)
        num, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = varint(buf, i)
        elif wire == 1:
            value, i = buf[i : i + 8], i + 8
        elif wire == 2:
            length, i = varint(buf, i)
            value, i = buf[i : i + length], i + length
        elif wire == 5:
            value, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.append((num, value))
    return out


def first(buf, number):
    for num, value in fields(buf):
        if num == number:
            return value
    return None


def every(buf, number):
    return [value for num, value in fields(buf) if num == number]


def key_value(buf):
    key = first(buf, 1).decode("utf-8")
    any_value = first(buf, 2)
    string_value = first(any_value, 1) if any_value else None
    return key, (string_value.decode("utf-8") if string_value else None)


def decode(body):
    resource_logs = first(body, 1)
    resource = first(resource_logs, 1)
    scope_logs = first(resource_logs, 2)
    log_record = first(scope_logs, 2)

    attrs = {}
    for raw in every(resource, 1):
        k, v = key_value(raw)
        attrs[k] = v
    for raw in every(log_record, 6):
        k, v = key_value(raw)
        attrs[k] = v

    scope = first(scope_logs, 1)
    attrs["_scope_name"] = first(scope, 1).decode("utf-8")
    attrs["_severity_text"] = first(log_record, 3).decode("utf-8")
    attrs["_body"] = first(first(log_record, 5), 1).decode("utf-8")
    # time_unix_nano carries epoch SECONDS (consumer bug, see otlp_encode.py)
    attrs["_time_epoch"] = struct.unpack("<Q", first(log_record, 1))[0]
    return attrs


captured = json.load(open(sys.argv[1]))
print(json.dumps([decode(bytes.fromhex(r["body_hex"])) for r in captured], indent=1))
PY

start_mock() {
  RECORD="$TMP/requests.json"
  rm -f "$RECORD" "$TMP/ready"
  python3 "$TMP/mock.py" "$PORT" "$RECORD" "$TMP/ready" 120 &
  MOCK_PID=$!
  for _ in $(seq 1 50); do
    [ -f "$TMP/ready" ] && return 0
    sleep 0.2
  done
  echo "mock failed to start on port $PORT" >&2
  exit 1
}

# Fresh project dir with a configured endpoint and the canary present, mimicking a
# fixture. $1 = port to point the endpoint at.
new_project() {
  local dir="$TMP/proj$RANDOM$RANDOM"
  mkdir -p "$dir/.onesignal"
  printf 'http://127.0.0.1:%s/sdk/log\n' "$1" > "$dir/.onesignal/endpoint"
  printf 'android\n' > "$dir/.onesignal/platform"
  printf 'SUPER_SECRET_CANARY="%s"\n' "$CANARY" > "$dir/.env"
  printf '%s' "$dir"
}

decoded() { python3 "$TMP/decode.py" "$TMP/requests.json"; }

field() { # field <request index> <attribute>
  decoded | python3 -c '
import json, sys
records = json.load(sys.stdin)
index = int(sys.argv[1])
print(records[index].get(sys.argv[2], "<absent>") if index < len(records) else "<no such request>")
' "$1" "$2"
}

count_requests() {
  [ -f "$TMP/requests.json" ] || { echo 0; return; }
  python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$TMP/requests.json"
}

# count_lines <file> [pattern] — `grep -c` exits 1 on zero matches, so a naive
# `|| echo 0` fallback prints twice.
count_lines() {
  [ -f "$1" ] || { echo 0; return; }
  grep -c "${2:-.}" "$1" 2>/dev/null || true
}

start_mock

# ---------------------------------------------------------------------------
echo
echo "1. a pre-App-ID milestone buffers, keeping its class and skill locally"
# ---------------------------------------------------------------------------
P="$(new_project "$PORT")"
( cd "$P" && bash "$CHECKPOINT" setup.preflight ok_after_fix prior_install >/dev/null 2>&1 )
want "pending.jsonl holds 1 event" "1" "$(count_lines "$P/.onesignal/pending.jsonl")"
want "checkpoints.jsonl holds 1 event" "1" "$(count_lines "$P/.onesignal/checkpoints.jsonl")"
want "buffered failure_class kept" "prior_install" \
  "$(python3 -c 'import json,sys; print(json.loads(open(sys.argv[1]).readline())["failure_class"])' "$P/.onesignal/pending.jsonl")"
want "buffered skill kept" "setup" \
  "$(python3 -c 'import json,sys; print(json.loads(open(sys.argv[1]).readline())["skill"])' "$P/.onesignal/pending.jsonl")"
want "nothing sent yet" "0" "$(count_requests)"

# ---------------------------------------------------------------------------
echo
echo "2. flush preserves failure_class, skill, and event time on the wire"
# ---------------------------------------------------------------------------
printf '%s\n' "$APP_ID" > "$P/.onesignal/app_id"
# The pause makes event time and flush time distinguishable: a regression that
# stamps the wire record with the send clock will be off by at least this much.
sleep 2
( cd "$P" && bash "$CHECKPOINT" flush >/dev/null 2>&1 )
want "one request reached the endpoint" "1" "$(count_requests)"
want "agent.failure_class survives flush" "prior_install" "$(field 0 agent.failure_class)"
want "agent.skill survives flush" "setup" "$(field 0 agent.skill)"
want "agent.milestone is the bare milestone" "preflight" "$(field 0 agent.milestone)"
want "ok_after_fix maps to WARN severity" "WARN" "$(field 0 _severity_text)"
BUFFERED_EPOCH="$(python3 -c '
import datetime, json, sys
ts = json.loads(open(sys.argv[1]).readline())["ts"]
parsed = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
print(int(parsed.replace(tzinfo=datetime.timezone.utc).timestamp()))
' "$P/.onesignal/checkpoints.jsonl")"
want "wire timestamp is the event time, not the flush time" "$BUFFERED_EPOCH" "$(field 0 _time_epoch)"
want "buffer cleared after a successful flush" "0" \
  "$(count_lines "$P/.onesignal/pending.jsonl")"

# ---------------------------------------------------------------------------
echo
echo "3. flush does not duplicate the local record"
# ---------------------------------------------------------------------------
want "checkpoints.jsonl still holds 1 event" "1" \
  "$(count_lines "$P/.onesignal/checkpoints.jsonl")"
want "transport.log holds both attempts" "2" \
  "$(count_lines "$P/.onesignal/transport.log")"

# ---------------------------------------------------------------------------
echo
echo "4. request shape matches the ingestion contract"
# ---------------------------------------------------------------------------
SHAPE="$(python3 -c '
import json, sys
r = json.load(open(sys.argv[1]))[0]
print(r["path"], r["app_id"], r["content_type"], ",".join(r["headers"]))
' "$TMP/requests.json")"
want "path is /sdk/log" "/sdk/log" "$(echo "$SHAPE" | cut -d' ' -f1)"
want "app_id is a query parameter" "$APP_ID" "$(echo "$SHAPE" | cut -d' ' -f2)"
want "Content-Type is exactly application/x-protobuf" "application/x-protobuf" \
  "$(echo "$SHAPE" | cut -d' ' -f3)"
# A custom header here triggers a Cloudflare WAF 403 before the service is reached.
want "no custom headers beyond curl's defaults" "accept,content-length,content-type,host,user-agent" \
  "$(echo "$SHAPE" | cut -d' ' -f4)"
want "scope_name is the GCP filter key" "onesignal-agent-skill" "$(field 0 _scope_name)"

# ---------------------------------------------------------------------------
echo
echo "5. a blocked network keeps the buffer instead of dropping it"
# ---------------------------------------------------------------------------
B="$(new_project "$DEAD_PORT")"
( cd "$B" && bash "$CHECKPOINT" setup.preflight fail dirty_tree >/dev/null 2>&1 )
printf '%s\n' "$APP_ID" > "$B/.onesignal/app_id"
FLUSH_OUT="$( cd "$B" && bash "$CHECKPOINT" flush 2>&1 )"
want "buffer retained when the send fails" "1" \
  "$(count_lines "$B/.onesignal/pending.jsonl")"
if printf '%s' "$FLUSH_OUT" | grep -q 'buffer kept for a later flush'; then
  ok "flush reports the failure"
else
  bad "flush reports the failure" "output was: $FLUSH_OUT"
fi
if printf '%s' "$FLUSH_OUT" | grep -q 'buffer cleared'; then
  bad "flush must not claim the buffer was cleared" "output was: $FLUSH_OUT"
else
  ok "flush does not claim the buffer was cleared"
fi
# A later flush, once egress works, must still send the original class.
sed -i.bak "s#$DEAD_PORT#$PORT#" "$B/.onesignal/endpoint" && rm -f "$B/.onesignal/endpoint.bak"
BEFORE="$(count_requests)"
( cd "$B" && bash "$CHECKPOINT" flush >/dev/null 2>&1 )
want "retried flush sends the event" "$((BEFORE + 1))" "$(count_requests)"
want "class survives the retry" "dirty_tree" "$(field "$BEFORE" agent.failure_class)"
want "fail maps to ERROR severity" "ERROR" "$(field "$BEFORE" _severity_text)"

# ---------------------------------------------------------------------------
echo
echo "6. flush works regardless of how the script is invoked"
# ---------------------------------------------------------------------------
S="$(new_project "$PORT")"
cp "$PLUGIN/scripts/checkpoint.sh" "$PLUGIN/scripts/otlp_encode.py" "$S/"
( cd "$S" && bash "$CHECKPOINT" setup.preflight ok >/dev/null 2>&1 )
printf '%s\n' "$APP_ID" > "$S/.onesignal/app_id"
NOSLASH_OUT="$( cd "$S" && bash checkpoint.sh flush 2>&1 )"
if printf '%s' "$NOSLASH_OUT" | grep -q 'flushing'; then
  ok "invocation without a path separator still flushes"
else
  bad "invocation without a path separator still flushes" "output was: $NOSLASH_OUT"
fi
if grep -q '"milestone":"flush"' "$S/.onesignal/checkpoints.jsonl" 2>/dev/null; then
  bad "flush must not be recorded as a milestone" "found a milestone named flush"
else
  ok "flush is not recorded as a milestone"
fi

# ---------------------------------------------------------------------------
echo
echo "7. ONESIGNAL_SKILL_TELEMETRY=0 keeps the local record and sends nothing"
# ---------------------------------------------------------------------------
R="$(new_project "$PORT")"
BEFORE="$(count_requests)"
( cd "$R" && ONESIGNAL_SKILL_TELEMETRY=0 bash "$CHECKPOINT" setup.preflight ok >/dev/null 2>&1 )
( cd "$R" && ONESIGNAL_SKILL_TELEMETRY=0 bash "$CHECKPOINT" setup.complete ok >/dev/null 2>&1 )
want "no request was made" "$BEFORE" "$(count_requests)"
want "both milestones recorded locally" "2" \
  "$(count_lines "$R/.onesignal/checkpoints.jsonl")"
want "refusal is auditable in transport.log" "2" \
  "$(count_lines "$R/.onesignal/transport.log" telemetry_disabled)"

# ---------------------------------------------------------------------------
echo
echo "8. safety invariants"
# ---------------------------------------------------------------------------
if grep -rq "$CANARY" "$TMP"/proj*/.onesignal/ 2>/dev/null; then
  bad "canary must never appear in checkpoint state" "found $CANARY under .onesignal/"
else
  ok "canary never appears in checkpoint state"
fi
# The hex needle is built with python3, a declared dep — xxd is not. With xxd
# missing, the substitution yielded an empty pattern and `grep -q ""` matched
# every line, so the check fired on every run instead of only on a leak.
CANARY_HEX="$(printf '%s' "$CANARY" | python3 -c 'import sys; print(sys.stdin.buffer.read().hex())')"
if [ -z "$CANARY_HEX" ]; then
  bad "canary hex needle must never be empty" "python3 hex conversion produced nothing"
elif [ -f "$TMP/requests.json" ] && grep -q "$CANARY_HEX" "$TMP/requests.json" 2>/dev/null; then
  bad "canary must never reach the endpoint" "found the canary in a request body"
else
  ok "canary never reaches the endpoint"
fi
# The project gets an app_id up front so these assertions exercise curl
# against the dead port. Without one, both commands returned at guards before
# any socket and passed identically against a working endpoint.
E="$(new_project "$DEAD_PORT")"
printf '%s\n' "$APP_ID" > "$E/.onesignal/app_id"
( cd "$E" && bash "$CHECKPOINT" setup.preflight ok >/dev/null 2>&1 ); want "exits 0 on a blocked network" "0" "$?"
want "the blocked send really reached the transport" "1" \
  "$(count_lines "$E/.onesignal/transport.log" connection_refused)"
( cd "$E" && bash "$CHECKPOINT" >/dev/null 2>&1 );                    want "exits 0 with no arguments" "0" "$?"
( cd "$E" && bash "$CHECKPOINT" flush >/dev/null 2>&1 );              want "exits 0 on a failed flush" "0" "$?"
want "the failed flush really attempted a send" "2" \
  "$(count_lines "$E/.onesignal/transport.log" connection_refused)"

# ---------------------------------------------------------------------------
echo
echo "9. hostile field values cannot corrupt the local record"
# ---------------------------------------------------------------------------
# A double quote in an interpolated field used to yield invalid JSON: a
# permanently broken row and encode_failed on the wire path.
J="$(new_project "$PORT")"
( cd "$J" && bash "$CHECKPOINT" setup.preflight fail 'foo"bar\baz' >/dev/null 2>&1 )
( cd "$J" && ONESIGNAL_SKILL_SOURCE='src"quote' bash "$CHECKPOINT" setup.app_id ok >/dev/null 2>&1 )
want "hostile rows still parse as JSON" "2" \
  "$(python3 -c '
import json, sys
n = 0
for line in open(sys.argv[1]):
    json.loads(line)
    n += 1
print(n)
' "$J/.onesignal/checkpoints.jsonl" 2>/dev/null || echo parse_error)"
want "hostile failure_class is sanitized to unknown" "unknown" \
  "$(python3 -c 'import json,sys; print(json.loads(open(sys.argv[1]).readline())["failure_class"])' "$J/.onesignal/checkpoints.jsonl" 2>/dev/null)"
want "quoted source round-trips" 'src"quote' \
  "$(python3 -c 'import json,sys; rows=[json.loads(l) for l in open(sys.argv[1])]; print(rows[1]["source"])' "$J/.onesignal/checkpoints.jsonl" 2>/dev/null)"

# ---------------------------------------------------------------------------
echo
echo "10. inputs are validated at the door"
# ---------------------------------------------------------------------------
V="$(new_project "$PORT")"
printf '%s\n' "$APP_ID" > "$V/.onesignal/app_id"
IDX="$(count_requests)"
( cd "$V" && bash "$CHECKPOINT" setup.install_applied fail 'src/App.tsx conflict' >/dev/null 2>&1 )
want "free-text failure_class never reaches the wire" "unknown" "$(field "$IDX" agent.failure_class)"

BEFORE="$(count_requests)"
( cd "$V" && bash "$CHECKPOINT" setup.complete okay >/dev/null 2>&1 ); want "invalid status exits 0" "0" "$?"
want "invalid status sends nothing" "$BEFORE" "$(count_requests)"
want "invalid status records nothing (a corrected re-run makes one row)" "1" \
  "$(count_lines "$V/.onesignal/checkpoints.jsonl")"

W="$(new_project "$PORT")"
printf 'not-a-uuid\n' > "$W/.onesignal/app_id"
BEFORE="$(count_requests)"
( cd "$W" && bash "$CHECKPOINT" setup.preflight ok >/dev/null 2>&1 )
want "invalid app_id sends nothing" "$BEFORE" "$(count_requests)"
want "invalid app_id buffers the event instead" "1" \
  "$(count_lines "$W/.onesignal/pending.jsonl")"
( cd "$W" && bash "$CHECKPOINT" flush >/dev/null 2>&1 ); want "flush with invalid app_id exits 0" "0" "$?"
want "flush with invalid app_id keeps the buffer" "1" \
  "$(count_lines "$W/.onesignal/pending.jsonl")"

# ---------------------------------------------------------------------------
echo
echo "11. a transport failure after the App ID exists re-buffers the event"
# ---------------------------------------------------------------------------
# Buffering used to be gated only on a missing App ID; a blocked network after
# Step 2 dropped every event with exit 0.
X="$(new_project "$DEAD_PORT")"
printf '%s\n' "$APP_ID" > "$X/.onesignal/app_id"
( cd "$X" && bash "$CHECKPOINT" setup.install_applied ok >/dev/null 2>&1 )
want "failed send lands in pending.jsonl" "1" \
  "$(count_lines "$X/.onesignal/pending.jsonl")"
( cd "$X" && bash "$CHECKPOINT" flush >/dev/null 2>&1 )
want "failed flush does not duplicate the re-buffered row" "1" \
  "$(count_lines "$X/.onesignal/pending.jsonl")"
IDX="$(count_requests)"
printf 'http://127.0.0.1:%s/sdk/log\n' "$PORT" > "$X/.onesignal/endpoint"
( cd "$X" && bash "$CHECKPOINT" flush >/dev/null 2>&1 )
want "recovered flush delivers the held event" "install_applied" "$(field "$IDX" agent.milestone)"
want "buffer cleared after the recovery" "0" \
  "$(count_lines "$X/.onesignal/pending.jsonl")"

# ---------------------------------------------------------------------------
echo
echo "12. a captive portal's HTTP 200 does not count as sent"
# ---------------------------------------------------------------------------
# The mock's /portal path answers 200 with an HTML sign-in page, like a hotel
# network. A blanket 2* match used to record that as delivered.
Y="$(new_project "$PORT")"
printf 'http://127.0.0.1:%s/portal\n' "$PORT" > "$Y/.onesignal/endpoint"
printf '%s\n' "$APP_ID" > "$Y/.onesignal/app_id"
( cd "$Y" && bash "$CHECKPOINT" setup.sdk_dependency ok >/dev/null 2>&1 ); want "portal response exits 0" "0" "$?"
want "portal 200 is not recorded as sent" "0" \
  "$(count_lines "$Y/.onesignal/transport.log" sent)"
want "portal 200 is diagnosed as an interstitial" "1" \
  "$(count_lines "$Y/.onesignal/transport.log" http_2xx_html)"
want "the event is re-buffered for a real network" "1" \
  "$(count_lines "$Y/.onesignal/pending.jsonl")"
IDX="$(count_requests)"
printf 'http://127.0.0.1:%s/sdk/log\n' "$PORT" > "$Y/.onesignal/endpoint"
( cd "$Y" && bash "$CHECKPOINT" flush >/dev/null 2>&1 )
want "flush off the portal delivers the event" "sdk_dependency" "$(field "$IDX" agent.milestone)"

# ---------------------------------------------------------------------------
echo
echo "13. flush fails closed when the result file is unwritable"
# ---------------------------------------------------------------------------
# The result file is the only success channel (the child always exits 0). A
# stale "sent" in an unwritable file used to clear the buffer with nothing
# delivered.
Z="$(new_project "$DEAD_PORT")"
( cd "$Z" && bash "$CHECKPOINT" setup.preflight ok >/dev/null 2>&1 )
printf 'sent' > "$Z/.onesignal/.flush_result"
chmod 444 "$Z/.onesignal/.flush_result"
printf '%s\n' "$APP_ID" > "$Z/.onesignal/app_id"
( cd "$Z" && bash "$CHECKPOINT" flush >/dev/null 2>&1 ); want "flush exits 0 on an unwritable result file" "0" "$?"
want "a stale 'sent' cannot clear the buffer" "1" \
  "$(count_lines "$Z/.onesignal/pending.jsonl")"

# ---------------------------------------------------------------------------
echo
echo "14. every request carries exactly the contract's field set"
# ---------------------------------------------------------------------------
# Positive allow-list over every request this suite produced. The canary check
# above only proves the agent never read a secret; it says nothing about what
# checkpoint.sh itself emits. An attribute added to otlp_encode.py — say
# ("agent.cwd", os.getcwd()) — passes the canary check but fails here.
# The list mirrors the resource_attrs + record_attrs tables in otlp_encode.py,
# which implement the payload contract in telemetry-contract.md.
# agent.failure_class is the one optional key: the encoder omits it when the
# payload carries null. Everything else must match exactly, on every request.
GOT="$(decoded | python3 -c '
import json, sys
ALLOWED = {
    "agent.milestone", "agent.status", "agent.run_id", "agent.schema",
    "agent.source", "agent.skill", "agent.skill.version", "agent.runtime",
    "agent.platform", "os.name", "ossdk.app_id", "ossdk.sdk_base",
    "service.name",
}
reqs = json.load(sys.stdin)
if not reqs:
    print("<no requests>")
    sys.exit()
problems = set()
for r in reqs:
    keys = {k for k in r if not k.startswith("_")}
    keys.discard("agent.failure_class")
    if keys != ALLOWED:
        extra, missing = keys - ALLOWED, ALLOWED - keys
        problems.add("+" + ",".join(sorted(extra)) + " -" + ",".join(sorted(missing)))
print("ok" if not problems else " ".join(sorted(problems)))
')"
want "wire attribute set equals the allow-list, on every request" "ok" "$GOT"

echo
echo "----------------------------------------"
printf '%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
