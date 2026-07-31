# Cross-platform wrappers — React Native, Expo, Flutter, Unity, Cordova/Ionic, Capacitor

Verified against the mobile-sdk-reference. The React Native, Expo, Flutter, and Cordova/Ionic wrappers all expose the same `OneSignal.User.*` surface and top-level `OneSignal.login/logout`; Unity (C#) uses PascalCase. Custom-event minimums: RN `5.3.0`, Flutter `5.4.0`, Unity `5.2.0`. Marker: `// onesignal:managed v1`.

## Placement

Instrument at the customer's existing sites — their auth callback / `analytics.identify` (identity + tags) and their action handlers / `analytics.track` (events). In RN/Expo that is usually the entry file (`App.tsx`, `_layout.tsx`) auth effect or the auth context; in Flutter, the auth state listener / `main.dart` post-login path. `login()` must run before tags/aliases/email/sms/events.

---

## React Native / Expo (JavaScript/TypeScript — `react-native-onesignal`)

```js
// Tier 1 — identity
// onesignal:managed v1
OneSignal.login(user.id);                       // external_id
OneSignal.logout();

// Tier 2 — aliases (after login)
// onesignal:managed v1
OneSignal.User.addAlias("stripe_id", stripeCustomerId);
OneSignal.User.addAliases({ stripe_id: stripeCustomerId, crm_id: crmId });

// Tier 3 — tags (STRING values only — coerce)
// onesignal:managed v1
OneSignal.User.addTags({
  account_plan: plan,
  seats: String(seatCount),
  is_trial: isTrial ? "1" : "0",
  signup_ts: String(Math.floor(signupDate.getTime() / 1000)),
});

// Tier 4 — events (properties = typed JSON, do NOT stringify)
// onesignal:managed v1
OneSignal.User.trackEvent("content_viewed", { content_id: contentId, read_time_sec: readTimeSec });
OneSignal.User.trackEvent("purchase_completed", { amount: order.total, currency: order.currency, order_id: order.id });
// revenue -> tell user to run the conversions skill.

// consent-gated channels — wrap in THEIR flag
// onesignal:managed v1
if (user.hasMarketingConsent) {
  OneSignal.User.addEmail(user.email);
  if (user.phone) OneSignal.User.addSms(user.phone); // E.164
}
```

Expo uses the identical `react-native-onesignal` API surface (the Expo plugin only changes install/prebuild, not the data calls). Verified signatures: `OneSignal.User.addTag/addTags/addAlias/trackEvent/addEmail/addSms`, `OneSignal.login/logout` (mobile-sdk-reference React Native column).

## Flutter (Dart — `onesignal_flutter`)

```dart
// identity
// onesignal:managed v1
OneSignal.login(user.id);
OneSignal.logout();

// aliases (after login)
// onesignal:managed v1
OneSignal.User.addAlias("stripe_id", stripeCustomerId);
OneSignal.User.addAliases({"stripe_id": stripeCustomerId, "crm_id": crmId});

// tags (STRING values only)
// onesignal:managed v1
OneSignal.User.addTags({
  "account_plan": plan,
  "seats": seatCount.toString(),
  "is_trial": isTrial ? "1" : "0",
  "signup_ts": (signupDate.millisecondsSinceEpoch ~/ 1000).toString(),
});

// events (typed Map, do NOT stringify)
// onesignal:managed v1
OneSignal.User.trackEvent("content_viewed", {"content_id": contentId, "read_time_sec": readTimeSec});
OneSignal.User.trackEvent("purchase_completed", {"amount": order.total, "currency": order.currency, "order_id": order.id});

// consent-gated channels
// onesignal:managed v1
if (user.hasMarketingConsent) {
  OneSignal.User.addEmail(user.email);
  if (user.phone != null) OneSignal.User.addSms(user.phone!); // E.164
}
```

Verified: mobile-sdk-reference Flutter column (`OneSignal.User.addTag/addTags/addAlias/trackEvent/setLanguage`, `OneSignal.login/logout`).

## Unity (C# — PascalCase)

```csharp
// identity
// onesignal:managed v1
OneSignal.Login(user.Id);
OneSignal.Logout();

// aliases (after login)
// onesignal:managed v1
OneSignal.User.AddAlias("stripe_id", stripeCustomerId);
OneSignal.User.AddAliases(new Dictionary<string, string> { { "stripe_id", stripeCustomerId } });

// tags (STRING values only) — use the confirmed batch form
// onesignal:managed v1
OneSignal.User.AddTags(new Dictionary<string, string> {
    { "account_plan", plan },
    { "seats", seatCount.ToString() },
    { "is_trial", isTrial ? "1" : "0" }
});

// events (typed Dictionary<string, object>)
// onesignal:managed v1
OneSignal.User.TrackEvent("purchase_completed", new Dictionary<string, object> {
    { "amount", order.Total }, { "currency", order.Currency }, { "order_id", order.Id }
});
```

Verified: mobile-sdk-reference C# column (`OneSignal.User.AddAlias/AddAliases/AddTags/TrackEvent`, `OneSignal.Login/Logout`). Prefer the `AddTags(Dictionary)` batch form — it is unambiguously confirmed. If you need the single-key form, verify the exact method name (`AddTag` vs `AddTagWithKey`) against the installed Unity SDK version's docs before emitting it rather than asserting one.

## Cordova / Ionic (JavaScript)

```js
// Ionic  — OneSignal.User.*    |   Cordova — window.plugins.OneSignal.User.*
// onesignal:managed v1
OneSignal.login(user.id);                                   // Ionic
window.plugins.OneSignal.login(user.id);                    // Cordova
OneSignal.User.addTag("account_plan", plan);                // Ionic
window.plugins.OneSignal.User.addTag("account_plan", plan); // Cordova
OneSignal.User.addAlias("stripe_id", stripeCustomerId);
OneSignal.User.trackEvent("purchase_completed", { amount: order.total, currency: order.currency });
```

Verified: mobile-sdk-reference Cordova/Ionic column — Ionic uses `OneSignal.User.*`, Cordova uses `window.plugins.OneSignal.User.*`. Same string-coercion and login-first rules as everywhere else.

## Capacitor (`@onesignal/capacitor-plugin`)

Capacitor exposes the same `OneSignal.User.*` / `OneSignal.login` JS surface as the RN/Ionic wrappers. If the exact plugin method name for a data call is not confirmed for the installed plugin version, say "verify against the @onesignal/capacitor-plugin docs" rather than asserting it — do not invent a signature.

---

## Rules that apply to ALL wrappers

- Tag values are strings — coerce numbers/bools/dates before passing (see per-language coercions above).
- Event `properties` is the one place typed/nested JSON is allowed — do NOT stringify event properties.
- `login()` before any tags/aliases/email/sms/events.
- Revenue events: numeric value property, then hand off to the conversions skill. Never `addOutcome*`.
- Email/SMS only inside the customer's own consent check; E.164 phone format; never a tag/alias.
- Never prefix an event name with `os.`/`os__`.
