# Web integration (OneSignal Web Push SDK v16)

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file is the Web install detail. There is no upstream sdk-ai-prompts flow for web — this is authored from the platform-matrix and the verified official docs (`web-push-custom-code-setup`, `onesignal-service-worker`) plus the OneSignal-Website-SDK repo. Do not contradict [../../references/platform-matrix.md](../../references/platform-matrix.md).

## What the agent does vs. the human (matrix)

- **Agent:** injects the SDK page script + `OneSignal.init` via `OneSignalDeferred`; creates the service-worker file; wires the wrapper + verification helper.
- **Human (dashboard, you cannot do it):** configure the web platform in the OneSignal dashboard — the **Site URL must EXACTLY match the deployed origin** (scheme + host, no trailing path; avoid `www.` unless the site actually serves `www.`). Then confirm the deployed site serves the worker same-origin over HTTPS with `Content-Type: application/javascript`.

**⚠️ The signup/onboarding flow does NOT provision the web platform.** Until the dashboard step above happens (or the credentials skill provisions it via the write-once endpoint's `chrome_web_origin`), the SDK fails at init with `App not configured for web push` — the single most common web onboarding wall. Definitive check (free, unauthenticated): `GET https://api.onesignal.com/sync/<APP_ID>/web` → `success: true` = configured; `{"code":2,...}` = not provisioned. Responses are CDN-cached ~1 h — append `?fresh=<timestamp>` to read current state, and don't probe before the config exists (it primes the cache with the error). Details: api-reference "Web platform config probe".

## Two files, both verified

### 1. Page SDK + init

Add to the page `<head>` (or the framework's root document/layout). Use the template [assets/web/init-snippet.html.tmpl](assets/web/init-snippet.html.tmpl) (substitute `__APP_ID__`); the pattern is verified in `web-push-custom-code-setup.mdx` and the SDK repo's `index.html`:

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

Create `OneSignalSDKWorker.js` containing exactly one line. **Copy it verbatim from [assets/web/OneSignalSDKWorker.js](assets/web/OneSignalSDKWorker.js) — do NOT download the worker from GitHub or a release link.** (Eval finding: an agent followed a download link and saved a 404 HTML page as the worker, silently breaking push.) The file is exactly:

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
// All OneSignal SDK access goes through here (except the verification helper).
export const OneSignalWrapper = {
  login: (externalId) => window.OneSignalDeferred.push((os) => os.login(externalId)),
  logout: () => window.OneSignalDeferred.push((os) => os.logout()),
  addEmail: (email) => window.OneSignalDeferred.push((os) => os.User.addEmail(email)),
  addSms: (e164) => window.OneSignalDeferred.push((os) => os.User.addSms(e164)),
  addTag: (key, value) => window.OneSignalDeferred.push((os) => os.User.addTag(key, String(value))),
};
```

Call `login()` BEFORE tags/email/sms or data attaches to the anonymous user (api-reference, data-mapping-rules ordering rule).

## Debug-only verification helper (Web)

Web has no `local-` placeholder gate identical to mobile, but the same shape applies: request permission, confirm a real subscription, and log its ID — the verify skill sends the test push from chat. Gate on a debug/dev signal (e.g. `location.hostname === "localhost"` or a build env flag) so it never runs in production. Browsers require a user gesture for the native permission prompt (Firefox and Safari enforce it), so request permission on the first click — do NOT prompt on page load. No dialog, no `confirm()`/`prompt()`, and no `fetch` to `api.onesignal.com` — the helper only observes and logs.

```js
// onesignal-verify.js — debug-only verification helper (onesignal:managed v1).
// Runs in local dev only — the guard below early-returns everywhere else — so
// the file is safe to keep. Delete it and its import/call only if you want to.
export function installOneSignalVerify() {
  const isDev = location.hostname === "localhost" || location.hostname === "127.0.0.1";
  if (!isDev) return;
  let logged = false;
  window.OneSignalDeferred = window.OneSignalDeferred || [];
  window.OneSignalDeferred.push(async function (OneSignal) {
    function report() {
      const id = OneSignal.User?.PushSubscription?.id;
      if (!id || logged) return;
      logged = true;
      console.info("[OneSignal] Push subscription registered:", id);
    }
    OneSignal.User.PushSubscription.addEventListener("change", report);
    report(); // ID may already exist before the listener attaches
    // The native permission prompt needs a user gesture in Firefox/Safari —
    // ask on the first click, never on page load.
    if (Notification.permission === "default") {
      console.info("[OneSignal] Click anywhere on the page to enable web push.");
      addEventListener(
        "click",
        () => { OneSignal.Notifications.requestPermission(); },
        { once: true }
      );
    }
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
