#!/usr/bin/env python3
"""otlp_encode.py — encode one onboarding checkpoint as an OTLP LogsData protobuf.

Writes raw protobuf to stdout. Reads the checkpoint as a JSON object on stdin.

    echo '{"milestone":"build_passed", ...}' | python3 otlp_encode.py > body.pb

WHY THIS EXISTS
    OneSignal has a publicly accessible HTTP endpoint that accepts SDK telemetry
    data such as logs. It implements the OpenTelemetry Protocol (OTLP) over HTTP,
    using Protocol Buffers (protobuf) as the serialization format, per the OTLP
    specification (https://opentelemetry.io/docs/specs/otlp/) — the endpoint
    accepts only `application/x-protobuf` holding an OTLP LogsData message.
    curl cannot produce that, so we build the wire format by hand. Deliberately
    dependency-free — no protobuf library, no pip install — because this runs on
    an end user's machine inside an agent session where we control nothing about
    the environment.

    Protobuf wire format is simple enough to emit directly: every field is a
    varint tag (field_number << 3 | wire_type) followed by a varint length and the
    payload for length-delimited types. Encoding bottom-up avoids any need to
    patch lengths after the fact.

FIELD NUMBERS (opentelemetry/proto/logs/v1/logs.proto, common/v1/common.proto)
    LogsData.resource_logs            = 1  (repeated message)
    ResourceLogs.resource             = 1  (message)
    ResourceLogs.scope_logs           = 2  (repeated message)
    Resource.attributes               = 1  (repeated message)
    ScopeLogs.scope                   = 1  (message)
    ScopeLogs.log_records             = 2  (repeated message)
    InstrumentationScope.name         = 1  (string)
    InstrumentationScope.version      = 2  (string)
    LogRecord.time_unix_nano          = 1  (fixed64)
    LogRecord.severity_number         = 2  (enum/varint)
    LogRecord.severity_text           = 3  (string)
    LogRecord.body                    = 5  (AnyValue)
    LogRecord.attributes              = 6  (repeated KeyValue)
    LogRecord.observed_time_unix_nano = 11 (fixed64)
    KeyValue.key                      = 1  (string)
    KeyValue.value                    = 2  (AnyValue)
    AnyValue.string_value             = 1  (string)

TIMESTAMP CAVEAT — READ THIS
    The consumer converts timestamps with
        DateTime::from_timestamp(time_unix_nano as i64, 0)
    which interprets the value as SECONDS, not nanoseconds, despite the field
    name. Their own unit tests pass 1609459200 and expect 2021-01-01. Passing
    true nanoseconds puts the value out of chrono's range, so it silently falls
    back to Utc::now() and increments `log_ingestion_consumer_bad_timestamp`.

    So `time_unix_nano` is populated with SECONDS here, to get a correct
    timestamp in GCP. `observed_time_unix_nano` gets true nanoseconds, matching
    what the device SDK sends. If the service is ever fixed, change
    `TIME_FIELD_IS_SECONDS` to False.

    `time_unix_nano` carries the EVENT time, taken from the checkpoint's own
    `ts` field. Buffered events are flushed minutes after they happen; stamping
    them with the send clock made a recovered failure appear in GCP *after* the
    recovery that followed it. `observed_time_unix_nano` is the send clock.
"""

import datetime
import json
import os
import struct
import sys
import time

TIME_FIELD_IS_SECONDS = True

# Severity numbers per OTLP: 9 = INFO, 13 = WARN, 17 = ERROR. The consumer
# maps the standard OTLP bands to GCP severity: 9-12 -> "INFO",
# 13-16 -> "WARNING", 17-20 -> "ERROR". The WARN band is verified in
# production: an ok_after_fix (13) sent 2026-08-10 (run_id 73832ff7632b0476)
# landed in GCP Logs Explorer with severity WARNING.
SEVERITY_INFO = 9
SEVERITY_WARN = 13
SEVERITY_ERROR = 17


# --- primitive encoders ------------------------------------------------------

def _varint(n: int) -> bytes:
    if n < 0:
        raise ValueError("negative varint")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _len_delim(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _string(field: int, value: str) -> bytes:
    return _len_delim(field, value.encode("utf-8"))


def _fixed64(field: int, value: int) -> bytes:
    return _tag(field, 1) + struct.pack("<Q", value)


def _varint_field(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


# --- OTLP composites --------------------------------------------------------

def any_value_string(value: str) -> bytes:
    """AnyValue { string_value = 1 }"""
    return _string(1, value)


def key_value(key: str, value: str) -> bytes:
    """KeyValue { key = 1, value = 2 } where value is always a string AnyValue."""
    return _string(1, key) + _len_delim(2, any_value_string(value))


def attributes(field: int, pairs) -> bytes:
    """Repeated KeyValue at the given field number."""
    out = b""
    for k, v in pairs:
        if v is None:
            continue
        out += _len_delim(field, key_value(k, str(v)))
    return out


def _event_epoch(ts, fallback: float) -> float:
    """Epoch seconds of the event itself, parsed from the checkpoint's ts.

    checkpoint.sh writes ts as `date -u +%Y-%m-%dT%H:%M:%SZ`; anything else
    (missing field, `unknown` because date failed) falls back to the send clock.
    strptime instead of fromisoformat because the trailing Z is only accepted
    by fromisoformat on Python >= 3.11, and this runs on machines we don't control.
    """
    if isinstance(ts, str):
        try:
            parsed = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            pass
    return fallback


def build_logs_data(checkpoint: dict) -> bytes:
    """Assemble a complete LogsData containing exactly one LogRecord."""

    milestone = checkpoint.get("milestone", "unknown")
    status = checkpoint.get("status", "unknown")
    failure_class = checkpoint.get("failure_class")
    run_id = checkpoint.get("run_id", "")
    skill_version = checkpoint.get("skill_version", "")
    runtime = checkpoint.get("runtime", "unknown")
    os_name = checkpoint.get("os", "unknown")
    source = checkpoint.get("source", "onesignal-agent-plugin")
    app_id = checkpoint.get("app_id", "")
    platform = checkpoint.get("platform", "unknown")
    skill = checkpoint.get("skill", "unknown")

    # Resource attributes become GCP *labels* (indexed, fast to filter on).
    # service.name is deliberately NOT "OneSignalDeviceSDK" — these are
    # build-time events from a developer machine, not runtime logs from a device,
    # and conflating them would corrupt existing dashboards.
    resource_attrs = [
        ("service.name", "OneSignalAgentSkill"),
        ("ossdk.app_id", app_id),
        ("ossdk.sdk_base", platform),   # was hardcoded "android" in the install prototype
        ("agent.source", source),
        ("agent.skill.version", skill_version),
        ("agent.runtime", runtime),
        ("agent.platform", platform),
        ("agent.skill", skill),
        ("os.name", os_name),
    ]
    resource = _len_delim(1, attributes(1, resource_attrs))  # ResourceLogs.resource

    # Scope name lands in labels.scope_name — the cheapest thing to filter on.
    scope = _len_delim(
        1,
        _string(1, "onesignal-agent-skill") + _string(2, skill_version),
    )

    # Log record attributes are flattened into jsonPayload at the root.
    record_attrs = [
        ("agent.milestone", milestone),
        ("agent.status", status),
        ("agent.failure_class", failure_class),
        ("agent.run_id", run_id),
        ("agent.schema", checkpoint.get("schema", 2)),
    ]

    # ok -> INFO, ok_after_fix -> WARNING (13), fail -> ERROR.
    # ok_after_fix must not read as clean: the install succeeded but only after the
    # agent changed something, which is exactly the friction worth surfacing.
    if status == "fail":
        severity, severity_text = SEVERITY_ERROR, "ERROR"
    elif status == "ok_after_fix":
        severity, severity_text = SEVERITY_WARN, "WARN"
    else:
        severity, severity_text = SEVERITY_INFO, "INFO"

    message = f"onesignal onboarding [{platform}/{skill}]: {milestone}={status}"
    if failure_class:
        message += f" ({failure_class})"

    now = time.time()
    event_time = _event_epoch(checkpoint.get("ts"), now)
    time_value = int(event_time) if TIME_FIELD_IS_SECONDS else int(event_time * 1_000_000_000)

    log_record = (
        _fixed64(1, time_value)
        + _varint_field(2, severity)
        + _string(3, severity_text)
        + _len_delim(5, any_value_string(message))
        + attributes(6, record_attrs)
        + _fixed64(11, int(now * 1_000_000_000))
    )

    scope_logs = _len_delim(2, scope + _len_delim(2, log_record))  # ResourceLogs.scope_logs
    resource_logs = _len_delim(1, resource + scope_logs)           # LogsData.resource_logs
    return resource_logs


def main() -> int:
    raw = sys.stdin.read()
    try:
        checkpoint = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"otlp_encode: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 1

    if not isinstance(checkpoint, dict):
        print("otlp_encode: expected a JSON object", file=sys.stderr)
        return 1

    body = build_logs_data(checkpoint)

    if os.environ.get("OTLP_ENCODE_DEBUG") == "1":
        print(f"otlp_encode: {len(body)} bytes", file=sys.stderr)

    out = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else sys.stdout
    out.write(body)
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
