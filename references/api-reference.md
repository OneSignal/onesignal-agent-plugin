# OneSignal API surface for agent tools

Verified against the OneSignal Rails codebase and public docs (July 2026). Skills MUST NOT invent endpoints beyond these. Where something is marked UNVERIFIED, say so to the user instead of asserting.

## Auth model (three tiers — never mix them up)

| Key | Scope | Where it lives | Used for |
|---|---|---|---|
| **App ID** | public identifier | safe to commit in client code | SDK init, all API calls (as `app_id`) |
| **REST API key** (`Authorization: Key <KEY>`) | one app | server-side env var ONLY — never client code, never committed | messaging, users, events, segments APIs |
| **Organization/User auth key** | whole org/account | env var only; treat as highly sensitive | app creation + app-level credential upload (`/api/v1/apps`) |

App-scoped tokens can be created/rotated/revoked: `POST/PATCH/DELETE /api/v1/apps/{app_id}/auth/tokens` ("rich authentication tokens"). Prefer handing agents these over org keys.

## Credential provisioning — the write-once endpoint (merged 2026-07-08, feature-flagged)

**`POST /api/v1/apps/{app_id}/credentials`** — THE path for agent-driven credential setup. Verified against the merged implementation.

- **Auth: app-key-class** (`Authorization: Key <key>`): the setup token from the onboarding prompt (env `ONESIGNAL_SETUP_TOKEN`) or the app's REST API key (env `ONESIGNAL_REST_API_KEY`). Org keys also work but are never needed here.
- **Write-once per platform**: sets credentials only for a platform with nothing configured. An app with iOS configured can still provision FCM and web. Replacement/rotation is NOT possible through this endpoint — that stays dashboard (Settings > Push Platforms) or org-key update.
- **Payloads** (all values must be strings — non-string params get a 400):
  FCM: `fcm_v1_service_account_json` (base64 of the JSON file; legacy `gcm_key` not accepted).
  APNs p8: `apns_p8` (base64) + `apns_key_id` + `apns_team_id` + `apns_bundle_id` — all four required. **p12 is not supported here** (dashboard/org-key only).
  Web: `chrome_web_origin` (HTTPS origin only) + optional `chrome_web_default_notification_icon`. No Site Name, no HTTP sub-domain flow.
- **Responses to handle:**
  `2xx` → stored and validated server-side; the app owner receives a notification email (expected — tell the user it's normal).
  `409` → platform already configured. Body carries a relayable message ("Push credentials are already configured for this platform. To replace them, go to Settings > Push Platforms in the dashboard, or use an Organization API key.") + a `platforms` array. Relay it verbatim and continue — do NOT retry, do NOT treat as a dead end. Multi-channel requests are all-or-nothing: a 409 means nothing was written *by that request* (and see the recovery caveat below before reading a 409 as proof an earlier call landed).
  `400` → validation failure (bad file, missing APNs field, non-string param, forbidden field like `name`) — surface the body verbatim.
  `404` → the feature flag (`launchpad_feature_write_once_credentials_07_26`) is off for this app; the route doesn't exist. Fall back to the dashboard upload walkthrough.
  `401` → the key doesn't belong to this app.
- **No idempotency mechanism**: after an ambiguous network failure, don't blind-retry — check `GET /api/v1/apps/{id}` (app auth works) to see whether the platform is configured. A 409 (or the platform showing up in that GET) proves *your* call landed **only if the platform was unconfigured before you started**; if you can't establish that, it is indeterminate, not success — confirm before any success claim.
- **MCP transport (preferred when connected):** the hosted OneSignal MCP exposes this endpoint as the `provision_app_credentials` tool. Verified against the merged implementation (mcp-http#111): it is **app-scoped** and works in both ApiKey and OAuth modes — LAU-725 (done) added OAuth acceptance to this endpoint, additive and flag-gated (`integrations_feature_oauth_api_auth_05_26`); OAuth is the strategic direction and app-key support is planned for retirement, so prefer the tool but do not assume app-key stays forever. It forwards the caller's auth downstream unchanged — the agent supplies only the credential params, not an `Authorization` header. Params mirror this endpoint's **body** exactly and values are still base64 strings (the MCP is remote and can't read local files, so the local agent reads and encodes the file). **`app_id` is NOT a param** — it is the endpoint's path segment, and the tool writes to the app the MCP session is bound to. Because the write is one-shot, confirm that bound app matches the target App ID (`onesignal_config`) before calling; if they differ, use the direct `POST`. The raw API response is passed through untouched, so every response *mapping* above (2xx/400/401/404/409) applies identically — with two divergences: (1) the tool enforces exactly one complete platform set per call, so provision FCM, APNs, and web in separate calls rather than batching them; (2) the post-failure recovery check (the `GET /api/v1/apps/{id}` above) has no MCP equivalent and the agent holds no key on this path — instead re-call the tool and read a passed-through `409` as a did-it-land signal **only when the platform was unconfigured before the first attempt** (otherwise it is indeterminate — the platform may have been configured all along); when in doubt, fall back to the direct `GET` with an app key, or the dashboard, to confirm before claiming success.

## App create / replacement (org-key territory — not the agent path)

- `POST /api/v1/apps` (create) and `PATCH /api/v1/apps/{id}` (update, incl. credential REPLACEMENT) require an **org key** (`check_org_key_auth`). Never put an org key in a customer chat. An app is auto-created by the dashboard signup wizard, so "configure the existing app" is the normal case.

## Users, identity, data-in (public v1, REST key)

- Create user: `POST /apps/{app_id}/users` — body `identity: {external_id}`, `properties: {tags, language, timezone_id, country, lat, long}`, `subscriptions: [{type, token, enabled}]`.
- Update user: `PATCH /apps/{app_id}/users/by/{alias_label}/{alias_id}` — `properties.tags` (string values only; set `""` to delete a tag), `deltas: {session_time, session_count, purchases}`.
- Aliases: `PATCH /apps/{app_id}/users/by/{alias_label}/{alias_id}/identity` — add labels; `DELETE .../identity/{label}`. Reserved labels: `external_id`, `onesignal_id`.
- Subscriptions: `POST /apps/{app_id}/users/by/{label}/{id}/subscriptions`, `PATCH /apps/{app_id}/subscriptions/{subscription_id}` (or `subscriptions_by_token/{token_type}/{token}`).
- Custom events: `POST /apps/{app_id}/custom_events` — `events: [{name (≤128 chars), external_id | onesignal_id, timestamp? (ISO-8601), idempotency_key? (UUID), properties? (JSON object)}]`. Limits: ≤2024 bytes/event, ≤1 MB/request.

## Messaging & verification (public v1)

- Send: `POST /notifications` (`app_id`, targeting, contents). Test-send-to-self: target `include_subscription_ids: [<id>]`. An **unauthenticated** create path exists behind the `permit_unauth_notif_create` app flag — confirmed for apps created via the AI integration flow, UNVERIFIED for arbitrary apps. Fall back to REST-key send.
- Delivery stats: `GET /notifications/{id}?app_id=` → `successful` (dispatched), `errored` (delivery errors — what the dashboard calls "Failed"), `failed` (**counts UNSUBSCRIBED targets, not errors** — do not treat as a dispatch failure), `converted` (clicks), `received` (confirmed delivery — paid plans, SDK subscriptions only). List: `GET /notifications?app_id=`.
- Outcomes read: `GET /apps/{app_id}/outcomes?outcome_names=os__click.count` (+ `.sum`, attribution, time ranges 1h/1d/1mo).
- Subscriber presence poll (the signup wizard's own pattern): fetch players/subscriptions with `limit:1` — non-empty ⇒ first subscriber exists.
- **Web platform config probe (unauthenticated, free):** `GET https://api.onesignal.com/sync/{app_id}/web` — no auth header needed. `success: true` + config object ⇒ web platform is provisioned. `{"success":false,"code":2,"description":"This app is not configured for web push."}` ⇒ the dashboard web-platform step (Site URL etc.) never happened — the most common web onboarding wall (the signup flow does not provision it). `code: 1` ⇒ no app with that ID. ⚠️ **Responses — including the code-2 error — are CDN-cached ~1 h** (`Cache-Control: public, max-age=3600`, `Vary: Origin`, Cloudflare): after a dashboard fix the plain URL can keep serving the stale error, and probing before config primes the cache with the error. To read fresh state, append a throwaway query param (`?fresh=<timestamp>` — verified to bypass the cache); the SDK itself hits the plain URL, so tell the user init may stay broken up to an hour after the fix (fresh browser profile/origin also works).

## Dashboard-session ONLY (no REST-key path — guide the user to the dashboard instead)

- Conversion metrics CRUD: `POST/PATCH /unified/apps/{app_id}/conversions`, attribution windows `PATCH /unified/apps/{id}/conversion-attribution-windows`. Custom/revenue metrics are **paid-plan-gated** (default click/session conversions are free+automatic). Attribution is **per-channel last-touch**; documented default windows: push/IAM ≈ 15 min, SMS ≈ 24 h, email ≈ 72 h.
- Custom-event readback: `GET /unified/apps/{app_id}/custom_events/names|properties|recent_events`.
- Activation readiness diagnostics: staff-only endpoint; not customer-callable.
Skills must present these as dashboard steps (deep-link the user), never as curl calls.

## OneSignal MCP server (hosted)

If the user has the OneSignal MCP connected, prefer its tools over curl: `create_user`, `view_user`, `update_user`, `create_or_update_alias`, `create_subscription`, `update_subscription`, `create_segment`, `list_segments`, `update_segment`, `create_template`, `list_templates`, `send_message`, `view_message`, `list_messages`, `view_outcomes`, `provision_app_credentials` (the write-once credential endpoint above — see its MCP transport note), `onesignal_config`, `onesignal_health`, live-activity and CSV-export tools. Setup docs: the "Model Context Protocol" page in OneSignal docs. The MCP is an API proxy — it cannot edit files; repo work is always done by this local agent.

## SDK data surface (verified signatures — the codegen targets)

- `OneSignal.login(externalId[, jwtToken])` / `logout()` — sets/clears external_id. **Call login BEFORE tags/email/sms** or data strands on the anonymous user.
- `User.addAlias(label, id)` / `addAliases({...})` — ≤10 aliases/user, label+id ≤128 chars.
- `User.addTag(key, value)` / `addTags({...})` — **string values only** on every platform.
- `User.addEmail(email)` / `addSms(e164)` — creates channel subscriptions (consent-gated in YOUR code).
- `User.trackEvent(name, properties?)` — custom events; properties is real JSON (the only typed primitive). Requires current SDK versions (Web v16; iOS/Android v5 line).
- `Notifications.requestPermission(...)`, `User.PushSubscription.optIn()/optOut()`.
- Legacy `addOutcome*` exists but is **deprecated** — never emit it; use custom events + Conversion Metrics.
- Consent gating exists (SDK no-ops data calls until consent given) — exact setter names vary per SDK; check the per-platform docs page before emitting.
