# Web integration (OneSignal Web Push SDK v16)

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file is the Web install detail. There is no upstream sdk-ai-prompts flow for web — this is authored from the platform-matrix and the verified official docs (`web-push-custom-code-setup`, `onesignal-service-worker`) plus the OneSignal-Website-SDK repo. Do not contradict [../../references/platform-matrix.md](../../references/platform-matrix.md).

## What the agent does vs. the human (matrix)

- **Agent:** injects the SDK page script + `OneSignal.init` via `OneSignalDeferred`; creates the service-worker file; wires the wrapper + verification file.
- **Human (dashboard, you cannot do it):** configure the web platform in the OneSignal dashboard — the **Site URL must EXACTLY match the deployed origin** (scheme + host, no trailing path; avoid `www.` unless the site actually serves `www.`). Then confirm the deployed site serves the worker same-origin over HTTPS with `Content-Type: application/javascript`.

**⚠️ The signup/onboarding flow does NOT provision the web platform.** Until the dashboard step above happens (or the credentials skill provisions it via the write-once endpoint's `chrome_web_origin`), the SDK fails at init with `App not configured for web push` — the single most common web onboarding wall. Definitive check (free, unauthenticated): `GET https://api.onesignal.com/sync/<APP_ID>/web` → `success: true` = configured; `{"code":2,...}` = not provisioned. Responses are CDN-cached ~1 h — append `?fresh=<timestamp>` to read current state, and don't probe before the config exists (it primes the cache with the error). Details: api-reference "Web platform config probe".

## Two files, both verified

### 1. Page SDK + init

Add to the page `<head>` (or the framework's root document/layout). The two-line pattern is verified in `web-push-custom-code-setup.mdx` and the SDK repo's `index.html`:

```html
<!-- onesignal:managed v1 -->
<script src="https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js" defer></script>
<script>
  window.OneSignalDeferred = window.OneSignalDeferred || [];
  OneSignalDeferred.push(async function (OneSignal) {
    await OneSignal.init({
      appId: "YOUR_ONESIGNAL_APP_ID",
      // allowLocalhostAsSecureOrigin: true, // ONLY for a separate localhost testing app
    });
  });
</script>
```

- `v16` is the current major line (matrix). Confirm the version track only from https://onesignal.github.io/sdk-releases/releases.json (Website entry — never the human-readable page); the CDN `v16` path auto-serves the latest v16 — do not pin a patch into the URL.
- `appId` is public — committing it here is fine (safety contract). Never put a REST key or org key in this file.
- `allowLocalhostAsSecureOrigin: true` is ONLY for a **separate** localhost-testing OneSignal app, never the production app (matrix). Leave it commented out and tell the user.

### 2. Service worker (must be same-origin)

Create `OneSignalSDKWorker.js` containing exactly one line (verified in `onesignal-service-worker.mdx`):

```js
importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js");
```

The SW **must be served from the site's own origin over HTTPS** — never a CDN or subdomain (matrix). Placement, by build tool:

| Framework | Put the file in | Serves at origin root as |
|---|---|---|
| Next.js | `public/OneSignalSDKWorker.js` | `/OneSignalSDKWorker.js` |
| Create React App / Vite / Vue / Svelte | `public/OneSignalSDKWorker.js` | `/OneSignalSDKWorker.js` |
| Angular | `src/` + add to `angular.json` `assets` | `/OneSignalSDKWorker.js` |
| Plain static site | repo/site root | `/OneSignalSDKWorker.js` |

The SDK looks for `/OneSignalSDKWorker.js` at the origin root by default (verified). If the file must live in a subdirectory (e.g. a PWA already owns root scope), host it in a dedicated path like `/push/onesignal/` and pass BOTH options in `init` (verified param names):

```js
await OneSignal.init({
  appId: "YOUR_ONESIGNAL_APP_ID",
  serviceWorkerPath: "push/onesignal/OneSignalSDKWorker.js", // relative, no leading slash
  serviceWorkerParam: { scope: "/push/onesignal/" },
});
```

**PWA / existing service worker conflict:** only one service worker can be active per scope (matrix). If the site already registers a SW at root, either combine (add the `importScripts` line into the existing worker and point OneSignal at that file) or give OneSignal its own subdirectory scope as above. Detect an existing SW by grepping for `serviceWorker.register` / an existing `sw.js` / `service-worker.js` in the repo and ASK before touching it — do not silently clobber a PWA worker.

## Framework npm packages (optional, only if the repo already uses the framework)

For React/Vue/Angular SPAs you MAY use the official wrapper packages instead of the raw script (matrix): `react-onesignal`, `onesignal-vue3`, `onesignal-ngx`. Use only if it fits the repo's conventions; the raw two-line script above always works. Pin the exact package version from releases.json (each wrapper has its own entry), not npm — no ranges/carets. If you use a wrapper, the wrapper's `init(appId, options)` replaces the inline `<script>` init block, but the service-worker file is still required in `public/`.

## Centralized wrapper (Web)

One module isolating all OneSignal calls (signatures verified in api-reference "SDK data surface" — tag values are strings on every platform):

```js
// onesignal-wrapper.js  // onesignal:managed v1
// All OneSignal SDK access goes through here (except the deletable verification file).
export const OneSignalWrapper = {
  login: (externalId) => window.OneSignalDeferred.push((os) => os.login(externalId)),
  logout: () => window.OneSignalDeferred.push((os) => os.logout()),
  addEmail: (email) => window.OneSignalDeferred.push((os) => os.User.addEmail(email)),
  addSms: (e164) => window.OneSignalDeferred.push((os) => os.User.addSms(e164)),
  addTag: (key, value) => window.OneSignalDeferred.push((os) => os.User.addTag(key, String(value))),
};
```

Call `login()` BEFORE tags/email/sms or data attaches to the anonymous user (api-reference, data-mapping-rules ordering rule).

## Deletable verification file (Web)

Web has no `local-` placeholder gate identical to mobile, but the same shape applies: confirm a real subscription, then let the user self-send. Requesting permission is the ONLY permission prompt; do NOT prompt on page load. Gate on a debug/dev signal (e.g. `location.hostname === "localhost"` or a build env flag) so it never ships to production.

```js
// onesignal-verify.js — TEMPORARY SCAFFOLDING. Delete this file and remove its
// import/call from <root> once you've confirmed a self-sent web push arrives.
// Runs in dev only — the guard below early-returns in production.
export function installOneSignalVerify() {
  const isDev = location.hostname === "localhost" || location.hostname === "127.0.0.1";
  if (!isDev) return;
  let shown = false;
  window.OneSignalDeferred = window.OneSignalDeferred || [];
  window.OneSignalDeferred.push(async function (OneSignal) {
    const APP_ID = "YOUR_ONESIGNAL_APP_ID";
    async function maybeShow() {
      const id = OneSignal.User?.PushSubscription?.id;
      if (!id || shown) return;
      shown = true;
      if (confirm("Your OneSignal SDK integration is complete!\n\nEnable web push and send yourself a test?")) {
        await OneSignal.Notifications.requestPermission();
        const subId = OneSignal.User?.PushSubscription?.id;
        const msg = prompt("Type a test message:") || "Hello from OneSignal";
        if (subId) {
          // Unauthenticated self-send relies on permit_unauth_notif_create (enabled for
          // AI-integration-flow apps; UNVERIFIED otherwise). On 401, use a dashboard test send.
          await fetch("https://api.onesignal.com/notifications", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              app_id: APP_ID,
              contents: { en: msg },
              include_subscription_ids: [subId],
            }),
          });
        }
      }
    }
    OneSignal.User.PushSubscription.addEventListener("change", maybeShow);
    maybeShow(); // ID may already exist before the listener attaches
  });
}
```

Verify the exact `PushSubscription` accessor names against the current web-sdk-reference doc before finalizing if anything looks off — the SDK's public surface is authoritative.

## Verify / troubleshoot

- Browser must be Chrome/Firefox/Edge/Safari over HTTPS (or localhost). Push does not work on plain `http://`.
- **SDK init fails / console shows `App not configured for web push`** (sync `{"code":2}`) → the web platform was never provisioned in the dashboard — NOT a bug in the code you wrote. Route to the human dashboard step (or the credentials skill's `chrome_web_origin` upload), then re-check with the sync probe **with a `?fresh=` param** — the error response is CDN-cached up to 1 h, so the plain URL (and the SDK) can keep failing for up to an hour after the fix. Say that plainly so nobody re-diagnoses a stale error.
- Worker not registering → open `https://<origin>/OneSignalSDKWorker.js` in a browser; you must see the one `importScripts` line and the response `Content-Type` must be `application/javascript` (verified requirement). A wrong content type or 404 is the most common failure *once the platform is provisioned*.
- No prompt → confirm the dashboard **Site URL exactly matches** the origin the user is browsing (a `www`/apex mismatch silently blocks subscription).
- Handoffs (SKILL.md Step 7): web push needs no APNs/FCM credentials, but the **web platform config is the equivalent gate** — if the sync probe says `code: 2`, hand off to **credentials** (write-once `chrome_web_origin`) or the dashboard step before **verify**; otherwise go straight to **verify** after the human confirms the dashboard Site URL + worker.
