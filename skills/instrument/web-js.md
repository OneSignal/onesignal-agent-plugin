# Web (JS / React / Vue / Angular) — instrumentation snippets

Verified against `OneSignal-Website-SDK/src/onesignal/{OneSignal,UserNamespace,User}.ts` and the mobile-sdk-reference. Web SDK v16. All data calls go through the `OneSignal.User.*` namespace; identity through top-level `OneSignal.login/logout`.

## Async access pattern (this is how you reach the API on Web)

The page SDK loads deferred. Access it via the `OneSignalDeferred` queue — this is the correct, verified pattern; do NOT assume a synchronous global `OneSignal`.

```js
window.OneSignalDeferred = window.OneSignalDeferred || [];
OneSignalDeferred.push(async function (OneSignal) {
  // onesignal:managed v1
  await OneSignal.login(externalId);        // Tier 1 — identity FIRST
});
```

In a React/Vue/Angular app that uses the `react-onesignal` / `onesignal-vue3` / `onesignal-ngx` wrapper, the wrapper exposes the same `OneSignal.login` / `OneSignal.User.*` surface after its own init resolves — instrument at the point where the wrapper reports ready (e.g. after `await OneSignal.init(...)` has resolved in the provider), not before.

## Tier 1 — identity (place at their auth-success / identify site)

```js
// onesignal:managed v1
OneSignalDeferred.push(async (OneSignal) => {
  await OneSignal.login(user.id);            // external_id = your primary internal user id
});
```

Sign-out site:

```js
// onesignal:managed v1
OneSignalDeferred.push(async (OneSignal) => {
  await OneSignal.logout();
});
```

Verified signature: `OneSignal.login(externalId: string, jwtToken?: string)` (`OneSignal.ts:97`). Pass `jwtToken` only if the app uses Identity Verification.

## Tier 2 — aliases (after login)

```js
// onesignal:managed v1
OneSignal.User.addAlias("stripe_id", stripeCustomerId);
// or several at once:
OneSignal.User.addAliases({ stripe_id: stripeCustomerId, crm_id: crmId });
```

Signatures: `addAlias(label: string, id: string)`, `addAliases({ [k]: string })` (`UserNamespace.ts:37,41`). ≤10 aliases; label+value ≤128 chars; `external_id`/`onesignal_id` reserved.

## Tier 3 — tags (string values ONLY — coerce)

```js
// onesignal:managed v1
OneSignal.User.addTags({
  account_plan: plan,                        // already a string
  seats: String(seatCount),                  // number -> string
  is_trial: isTrial ? "1" : "0",             // bool -> "1"/"0"
  signup_ts: String(Math.floor(signupDate.getTime() / 1000)), // date -> unix-seconds string
});
```

Signatures: `addTag(key: string, value: string)`, `addTags({ [k]: string })` (`UserNamespace.ts:69,73`). Remove a tag with `OneSignal.User.removeTag(key)` (sends empty string server-side). Reserved keys — see SKILL.md table; rename with a namespace.

## Tier 4 — events (verbs; properties = real typed JSON, do NOT stringify)

```js
// onesignal:managed v1
OneSignal.User.trackEvent("content_viewed", {
  content_id: contentId,
  category: category,
  read_time_sec: readTimeSec,                // numbers OK here — events take typed JSON
});
```

Revenue event (numeric value property + conversions handoff):

```js
// onesignal:managed v1
OneSignal.User.trackEvent("purchase_completed", {
  amount: order.total,                       // numeric — required for revenue
  currency: order.currency,                  // e.g. "USD"
  order_id: order.id,
});
// After writing: tell the user to run the conversions skill to make this a Conversion Metric.
```

Signature: `trackEvent(name: string, properties?: Record<string, unknown>)` (`UserNamespace.ts:103`). The SDK verified-refuses trackEvent before login (logs "User not logged in", `User.ts:227-235`) — login MUST precede it at runtime. Never name events `os.`/`os__`.

## Consent-gated channels (bucket b — wrap in THEIR consent check)

```js
// onesignal:managed v1
if (user.hasMarketingConsent) {              // the customer's OWN existing consent flag
  OneSignal.User.addEmail(user.email);
  if (user.phone) OneSignal.User.addSms(user.phone); // E.164, e.g. "+15551234567"
}
```

Signatures: `addEmail(email: string)`, `addSms(smsNumber: string)` (`UserNamespace.ts:53,61`). If no consent flag exists in the repo, do NOT emit these — flag and ask. Never a tag/alias for email or phone.

## Placement notes

- If they use Segment/analytics.js: instrument at the `analytics.identify(userId, traits)` call (login + tags) and `analytics.track(name, props)` calls (trackEvent).
- SPA: put `login` in the auth context/provider effect that fires on authenticated session, not on every render.
- Do not add data calls inside `OneSignal.init` — init is the setup skill's territory; you attach at the app's identify/action sites.
