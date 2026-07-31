# Status probe recipes — per rung

Exact reads for each activation-ladder rung. All are **read-only** (`GET`-shaped or app-config fetch). Never issue `POST/PATCH/DELETE` from this skill. Every endpoint/field below is drawn from [../../references/api-reference.md](../../references/api-reference.md) — do not invent fields beyond it; if you need something not listed, tell the user "verify against docs" rather than assert.

## Two ways to call

- **OneSignal MCP (preferred if connected).** Use the hosted tools — they hold the app + key context: `onesignal_config`, `list_messages`, `view_message`, `view_outcomes`, `view_user`. (The MCP is an API proxy; it reads, it cannot edit files.)
- **App-scoped key via curl (fallback).** Header `Authorization: Key <KEY>` where `<KEY>` is the key provided with the invocation/session or `$ONESIGNAL_REST_API_KEY` from env. Base host and exact paths per api-reference.md. `app_id` is passed as shown.

If neither the MCP nor a key is available, you cannot probe rungs 2–7 — see SKILL.md Step 0.

---

## Rung 1 — SDK initialized / platforms configured

**Signal:** the app has ≥1 messaging platform configured (FCM / APNs / web push keys present).

- **MCP:** `onesignal_config` → inspect the returned app/platform configuration.
- **REST:** fetch the app config (the "view app" / `onesignal_config` path in api-reference.md).
- **Web (works even with NO key):** `GET https://api.onesignal.com/sync/<APP_ID>/web?fresh=<timestamp>` — free and unauthenticated (api-reference "Web platform config probe"). `success: true` ⇒ web platform configured; `{"code":2,"description":"This app is not configured for web push."}` ⇒ web platform never provisioned (signup doesn't do it). Always append the throwaway `?fresh=` param — responses are CDN-cached ~1 h and a stale answer will mislead the ladder.

**Read:** which platform objects exist, and whether each has its credential populated.
- No platform objects at all → rung 1 **fail (no SDK/platform)** → route **setup**.
- A platform object exists but its push credential is empty/absent → rung 1 **fail (missing credential)** → route **credentials**.
- Web app + sync probe returns `code: 2` → rung 1 **fail (web platform not provisioned)** → route **credentials** (write-once `chrome_web_origin`) or the dashboard web-config step.
- ≥1 platform fully configured → rung 1 **pass**.

Do not print raw credential material even if the config echoes any — redact.

---

## Rung 2 — First subscriber

**Signal:** at least one subscription/device exists.

- Poll subscriptions/players with **`limit: 1`** (the signup wizard's own pattern, per api-reference.md). Non-empty result ⇒ ≥1 subscriber exists ⇒ **pass**.
- Empty ⇒ **fail**. Combined with an empty app at rung 1, treat as brand-new → **setup** (and prompt the user to accept the permission prompt on a real device).

You only need existence, so `limit: 1` is enough — do not page the whole base.

---

## Rung 3 — Identity set (SAMPLED — best-effort)

**Signal:** subscribers carry an `external_id` (are identified, not anonymous).

- Sample a **small** number of subscribers (e.g. the first handful from the `limit`-N poll) and, for each, check identity via `view_user` (MCP) or the view-user read (REST).
- **Inspect only presence** of `external_id` and any alias labels. **Do NOT read tag values, emails, names, or any field content** — presence of the `external_id` field is all you need. Redact everything else.
- ≥1 sampled subscriber has a non-empty `external_id` → **pass**.
- All sampled subscribers are anonymous → **fail** → route **instrument** (`OneSignal.login(externalId)` at the auth point; ordering rule: login before tags/email/sms).

**Report honestly:** "N of M *sampled* subscribers were identified." This is a sample, not the whole base — you cannot compute true coverage %. Never state a precise external_id-coverage percentage.

---

## Rung 4 — First send

**Signal:** at least one notification has ever been created for the app.

- **MCP:** `list_messages`.
- **REST:** `GET /notifications?app_id=<APP_ID>`.

Non-empty list → **pass**. Empty → **fail** → route **verify** (send a test push to yourself; api-reference.md notes test-send-to-self targets `include_subscription_ids: [<id>]`).

---

## Rung 5 — DELIVERED to an identified user = ACTIVATED

**Signal:** a notification actually delivered (`successful >= 1`).

- For a recent notification id from rung 4: **MCP** `view_message`, or **REST** `GET /notifications/{id}?app_id=<APP_ID>`.
- Read fields (per api-reference.md): `successful`, `failed`, `errored`, `converted` (clicks), `received` (confirmed delivery — **paid plans, SDK subscriptions only**).

- `successful >= 1` → **pass** (this is the activation line).
- Only `failed`/`errored`, `successful == 0` → **fail** → route **verify** to diagnose (credential mismatch, unsubscribed device, or platform-config gap).

**Delivery wording:** if `received` is present, you may say "confirmed received." If `received` is unavailable (free plan / non-SDK), say "sent successfully" — do NOT claim confirmed receipt from `successful` alone.

**Identified-audience nuance:** whether the successful send went specifically to an *identified* user is often not directly readable from the notification record. If you cannot confirm the audience was identified, report rung 5 as "delivered" and lean on rung 3's sampled identity result for the "to an identified user" half — say which half is inferred rather than asserting both.

---

## Rung 6 — First click

**Signal:** any click recorded.

- **Per-notification:** `converted` field from the rung-5 read (`converted >= 1`).
- **Aggregate:** `view_outcomes` (MCP) / `GET /apps/{app_id}/outcomes?outcome_names=os__click.count` (REST). `os__click.count > 0` → clicks exist.

Either positive → **pass**. Clicks are free/automatic, so this rung usually follows delivery quickly. Zero clicks when rung 5 passed is **not a failure** — the user is already activated; treat rung 6/7 as growth upsells.

---

## Rung 7 — Conversions configured (DASHBOARD-ONLY — usually ⚠️)

**Signal:** a Conversion Metric is set up.

- Conversion-metric CRUD (`/unified/apps/{app_id}/conversions`) is **dashboard-session only — no REST-key read path** (api-reference.md). You generally **cannot** confirm a metric exists from the REST key.
- Mark rung 7 **⚠️ unknown** unless the user tells you otherwise, and route to the **conversions** skill (which drives the dashboard step + the matching custom-event instrumentation).
- A weak positive proxy: if custom events are flowing (`os__click.count` and outcomes populated, or the user reports events), conversions are *possible* — but do not assert a metric exists.

---

## Reporting hygiene (applies to every rung)

- Emit `✅ pass` / `❌ fail` / `⚠️ unknown`. Use ⚠️ whenever a probe errored, auth was missing, or the signal is plan-gated / dashboard-only. Never convert an error into a false ✅ or ❌.
- Never print: the REST/org key, raw credential material, notification bodies, tag values, emails, names, or any end-user field content.
- Keep raw counts minimal and only where they aid the user (e.g. "3 subscribers, 1 identified in sample"). No full API-response dumps.
