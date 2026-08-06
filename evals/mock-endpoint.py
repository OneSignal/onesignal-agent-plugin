#!/usr/bin/env python3
"""Standalone mock of the OneSignal log-ingestion endpoint, for live agent runs.

Run it in its own terminal, point checkpoints at it, and watch events arrive:

    python3 evals/mock-endpoint.py
    export ONESIGNAL_SKILL_ENDPOINT="http://127.0.0.1:8817/sdk/log"

Prints one line per event as it arrives and appends the decoded fields to
mock-events.jsonl in the current directory. Mimics the real service's gate:
415 unless Content-Type is exactly application/x-protobuf, 400 unless app_id
is a UUID query parameter, 202 otherwise.

The eval harness (checkpoint-transport-test.sh) embeds its own copy of this
logic; this file exists so a human driving a real agent session has something
to run and read without extracting it from the test.
"""

import argparse
import http.server
import json
import re
import socketserver
import sys
import time
import urllib.parse

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# --- minimal OTLP LogsData decoder (field numbers in otlp_encode.py) ---------

def _varint(buf, i):
    result = shift = 0
    while True:
        byte = buf[i]
        result |= (byte & 0x7F) << shift
        i += 1
        shift += 7
        if not byte & 0x80:
            return result, i


def _fields(buf):
    out = []
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        num, wire = tag >> 3, tag & 7
        if wire == 0:
            value, i = _varint(buf, i)
        elif wire == 1:
            value, i = buf[i : i + 8], i + 8
        elif wire == 2:
            length, i = _varint(buf, i)
            value, i = buf[i : i + length], i + length
        elif wire == 5:
            value, i = buf[i : i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.append((num, value))
    return out


def _first(buf, number):
    for num, value in _fields(buf):
        if num == number:
            return value
    return None


def _every(buf, number):
    return [value for num, value in _fields(buf) if num == number]


def _key_value(buf):
    key = _first(buf, 1).decode("utf-8")
    any_value = _first(buf, 2)
    string_value = _first(any_value, 1) if any_value else None
    return key, (string_value.decode("utf-8") if string_value else None)


def decode_logs_data(body):
    resource_logs = _first(body, 1)
    resource = _first(resource_logs, 1)
    scope_logs = _first(resource_logs, 2)
    log_record = _first(scope_logs, 2)

    attrs = {}
    for raw in _every(resource, 1):
        key, value = _key_value(raw)
        attrs[key] = value
    for raw in _every(log_record, 6):
        key, value = _key_value(raw)
        attrs[key] = value
    attrs["_severity"] = _first(log_record, 3).decode("utf-8")
    attrs["_body"] = _first(_first(log_record, 5), 1).decode("utf-8")
    return attrs


# --- server -------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    count = 0
    out_path = "mock-events.jsonl"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        parsed = urllib.parse.urlparse(self.path)
        app_id = urllib.parse.parse_qs(parsed.query).get("app_id", [None])[0]
        content_type = self.headers.get("Content-Type", "")

        # Same gate order as the real service: content type, then app_id.
        if content_type != "application/x-protobuf":
            print(f"  REJECTED 415: Content-Type was {content_type!r}", flush=True)
            self.send_response(415)
            self.end_headers()
            return
        if not app_id or not UUID_RE.match(app_id):
            print(f"  REJECTED 400: app_id was {app_id!r}", flush=True)
            self.send_response(400)
            self.end_headers()
            return

        Handler.count += 1
        try:
            attrs = decode_logs_data(body)
        except Exception as exc:  # undecodable body is worth seeing, not crashing on
            attrs = {"_decode_error": str(exc), "_raw_hex": body.hex()}

        record = {"n": Handler.count, "received_at": time.strftime("%H:%M:%S"), "path": parsed.path, "app_id": app_id, **attrs}
        with open(Handler.out_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")

        print(
            "[{n}] {milestone}={status}{cls}  skill={skill} platform={platform} runtime={runtime} run_id={run}".format(
                n=Handler.count,
                milestone=attrs.get("agent.milestone", "?"),
                status=attrs.get("agent.status", "?"),
                cls=" class=" + attrs["agent.failure_class"] if attrs.get("agent.failure_class") else "",
                skill=attrs.get("agent.skill", "?"),
                platform=attrs.get("agent.platform", "?"),
                runtime=attrs.get("agent.runtime", "?"),
                run=attrs.get("agent.run_id", "?"),
            ),
            flush=True,
        )
        self.send_response(202)
        self.end_headers()

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8817)
    parser.add_argument("--out", default="mock-events.jsonl", help="decoded events, one JSON object per line")
    args = parser.parse_args()

    Handler.out_path = args.out
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"mock ingestion endpoint on http://127.0.0.1:{args.port}/sdk/log")
        print(f"decoded events -> {args.out}")
        print("point checkpoints at it with:")
        print(f'  export ONESIGNAL_SKILL_ENDPOINT="http://127.0.0.1:{args.port}/sdk/log"')
        print("waiting (Ctrl-C to stop)...", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print(f"\n{Handler.count} event(s) received")
            return 0


if __name__ == "__main__":
    sys.exit(main())
