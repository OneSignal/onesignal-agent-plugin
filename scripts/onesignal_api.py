#!/usr/bin/env python3
"""Deterministic OneSignal API probes for the setup / status / verify skills.

This encodes the fiddly, verified details that models keep getting wrong:
  - the web sync probe is CDN-cached ~1h, so we always append ?fresh=<ts>
  - android_params must be polled AFTER credentials upload (caller's job to
    order; this just polls with a timeout and never primes an empty cache)
  - response codes carry meaning: 409 = already configured (relay, don't retry),
    404 = feature flag off (fall back to dashboard), 401 = wrong app key
  - delivery stat `failed` counts UNSUBSCRIBED targets, not errors

Prefer the OneSignal MCP server's tools when connected (better auth story);
this script is the generic curl-equivalent fallback that needs no MCP.

Endpoints and their exact hosts are taken from references/api-reference.md.
Only endpoints whose full host is documented there are implemented. Keys are
read from --key or the environment; never printed back.

Usage:
    onesignal_api.py web-probe <app_id>
    onesignal_api.py android-params <app_id> [--timeout 60]
    onesignal_api.py notification-stats <notif_id> <app_id> --key KEY
    onesignal_api.py app <app_id> --key KEY
    onesignal_api.py subscribers <app_id> --key KEY

Key sources (in order): --key, $ONESIGNAL_REST_API_KEY, $ONESIGNAL_SETUP_TOKEN
Exit: 0 ok (see JSON `status`), 2 usage error, 3 network error, 4 auth missing
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.onesignal.com"


def _get(url, key=None, timeout=20):
    req = urllib.request.Request(url)
    if key:
        req.add_header("Authorization", f"Key {key}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"ERROR: network failure: {e}\n")
        sys.exit(3)


def _json(body):
    try:
        return json.loads(body)
    except Exception:
        return None


def _resolve_key(args):
    return (getattr(args, "key", None)
            or os.environ.get("ONESIGNAL_REST_API_KEY")
            or os.environ.get("ONESIGNAL_SETUP_TOKEN"))


def _require_key(args):
    k = _resolve_key(args)
    if not k:
        sys.stderr.write(
            "ERROR: no API key. Pass --key, or export ONESIGNAL_REST_API_KEY / "
            "ONESIGNAL_SETUP_TOKEN. Never paste the key into committed files.\n"
        )
        sys.exit(4)
    return k


def cmd_web_probe(args):
    # Always cache-bust: the plain URL (and its code-2 error) is CDN-cached ~1h.
    url = f"{API}/sync/{args.app_id}/web?fresh={int(time.time())}"
    status, body = _get(url)
    data = _json(body) or {}
    if data.get("success") is True:
        result = {"status": "provisioned", "detail": "Web platform is live."}
    elif data.get("code") == 2:
        result = {"status": "not_configured",
                  "detail": "Web push NOT provisioned. The dashboard web-platform "
                            "step (Site URL) never happened — most common web wall."}
    elif data.get("code") == 1:
        result = {"status": "no_such_app", "detail": "No app with that ID."}
    else:
        result = {"status": "unknown", "detail": f"HTTP {status}", "raw": data}
    result.update({"probe": "web", "app_id": args.app_id, "cache_busted": True})
    print(json.dumps(result, indent=2))


def cmd_android_params(args):
    # Poll only AFTER credentials upload. android_sender_id present => FCM live.
    deadline = time.time() + args.timeout
    url = f"{API}/apps/{args.app_id}/android_params.js"
    attempt = 0
    while True:
        attempt += 1
        status, body = _get(url)
        data = _json(body) or {}
        if data.get("android_sender_id"):
            print(json.dumps({"probe": "android_params", "app_id": args.app_id,
                              "status": "fcm_live", "attempts": attempt}, indent=2))
            return
        if time.time() >= deadline:
            print(json.dumps({"probe": "android_params", "app_id": args.app_id,
                              "status": "not_live",
                              "detail": "android_sender_id absent — FCM v1 credential "
                                        "not yet on the app (or still propagating).",
                              "attempts": attempt}, indent=2))
            return
        time.sleep(min(5, max(1, args.timeout // 12)))


def cmd_notification_stats(args):
    key = _require_key(args)
    url = f"{API}/notifications/{args.notif_id}?app_id={args.app_id}"
    status, body = _get(url, key=key)
    data = _json(body) or {}
    if status == 401:
        print(json.dumps({"status": "auth_error", "detail": "Key does not belong to this app."}, indent=2)); return
    out = {
        "probe": "notification_stats", "app_id": args.app_id, "notif_id": args.notif_id,
        "successful": data.get("successful"),
        "errored": data.get("errored"),
        "failed_unsubscribed": data.get("failed"),  # NOT delivery errors
        "converted_clicks": data.get("converted"),
        "received_confirmed": data.get("received"),
        "note": "`failed` = unsubscribed targets, not delivery failures. "
                "`errored` = real delivery errors (dashboard 'Failed').",
    }
    print(json.dumps(out, indent=2))


def cmd_app(args):
    key = _require_key(args)
    status, body = _get(f"{API}/api/v1/apps/{args.app_id}", key=key)
    data = _json(body) or {}
    if status == 401:
        print(json.dumps({"status": "auth_error", "detail": "Key does not belong to this app."}, indent=2)); return
    if status == 404:
        print(json.dumps({"status": "not_found", "detail": "No app with that ID (or route unavailable)."}, indent=2)); return
    # Surface which platforms look configured without asserting exact schema.
    out = {"probe": "app", "app_id": args.app_id, "http": status,
           "keys_present": sorted(data.keys()) if isinstance(data, dict) else None}
    print(json.dumps(out, indent=2))


def cmd_subscribers(args):
    key = _require_key(args)
    # Presence pattern: limit 1, non-empty => a subscriber exists.
    status, body = _get(f"{API}/api/v1/players?app_id={args.app_id}&limit=1", key=key)
    data = _json(body) or {}
    if status == 401:
        print(json.dumps({"status": "auth_error", "detail": "Key does not belong to this app."}, indent=2)); return
    total = data.get("total_count")
    players = data.get("players") or []
    out = {"probe": "subscribers", "app_id": args.app_id,
           "has_subscriber": bool(players) or bool(total),
           "total_count": total}
    print(json.dumps(out, indent=2))


def main():
    ap = argparse.ArgumentParser(description="OneSignal API probes (generic curl fallback; prefer MCP if connected).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("web-probe"); p.add_argument("app_id"); p.set_defaults(fn=cmd_web_probe)
    p = sub.add_parser("android-params"); p.add_argument("app_id"); p.add_argument("--timeout", type=int, default=60); p.set_defaults(fn=cmd_android_params)
    p = sub.add_parser("notification-stats"); p.add_argument("notif_id"); p.add_argument("app_id"); p.add_argument("--key"); p.set_defaults(fn=cmd_notification_stats)
    p = sub.add_parser("app"); p.add_argument("app_id"); p.add_argument("--key"); p.set_defaults(fn=cmd_app)
    p = sub.add_parser("subscribers"); p.add_argument("app_id"); p.add_argument("--key"); p.set_defaults(fn=cmd_subscribers)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
