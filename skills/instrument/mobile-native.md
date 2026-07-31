# Android (Kotlin/Java) & iOS (Swift/Obj-C) — instrumentation snippets

Verified against the mobile-sdk-reference (v5 line). Data calls use `OneSignal.User.*`; identity is top-level `OneSignal.login/logout`. Custom events require iOS `5.4.0` / Android `5.6.1` minimum. Comment marker: `// onesignal:managed v1`.

## Placement

Instrument at the customer's existing sites, not a new setup file:
- **Identity** → their auth-success callback (post sign-in), or wherever their own analytics identify runs.
- **Tags** → alongside the same identity site (user traits/claims).
- **Events** → at the domain action (checkout complete, screen viewed, upgrade tapped).
- **Email/SMS** → inside their existing consent conditional only.
`login()` must execute before tags/aliases/email/sms/events at runtime.

---

## Android — Kotlin

```kotlin
// Tier 1 — identity (at auth success)
// onesignal:managed v1
OneSignal.login(user.id)                       // external_id
// sign-out:
OneSignal.logout()

// Tier 2 — aliases (after login)
// onesignal:managed v1
OneSignal.User.addAlias("stripe_id", stripeCustomerId)
OneSignal.User.addAliases(mapOf("stripe_id" to stripeCustomerId, "crm_id" to crmId))

// Tier 3 — tags (STRING values only — coerce)
// onesignal:managed v1
OneSignal.User.addTags(
    mapOf(
        "account_plan" to plan,                // String already
        "seats" to seatCount.toString(),       // Int -> String
        "is_trial" to if (isTrial) "1" else "0", // Bool -> "1"/"0"
        "signup_ts" to (signupEpochMillis / 1000).toString() // date -> unix-seconds String
    )
)

// Tier 4 — events (properties = typed Map, do NOT stringify)
// onesignal:managed v1
OneSignal.User.trackEvent(
    name = "content_viewed",
    properties = mapOf("content_id" to contentId, "read_time_sec" to readTimeSec)
)
// revenue — numeric value property + conversions-skill handoff:
OneSignal.User.trackEvent(
    name = "purchase_completed",
    properties = mapOf("amount" to order.total, "currency" to order.currency, "order_id" to order.id)
)
```

## Android — Java

```java
// identity
// onesignal:managed v1
OneSignal.login(user.getId());
OneSignal.logout();

// aliases
OneSignal.getUser().addAlias("stripe_id", stripeCustomerId);

// tags (String values only)
Map<String, String> tags = new HashMap<>();
tags.put("account_plan", plan);
tags.put("seats", String.valueOf(seatCount));
tags.put("is_trial", isTrial ? "1" : "0");
OneSignal.getUser().addTags(tags);

// events (typed Map<String,Object>)
Map<String, Object> props = new HashMap<>();
props.put("content_id", contentId);
props.put("read_time_sec", readTimeSec);
OneSignal.getUser().trackEvent("content_viewed", props);
```

Note the Java accessor is `OneSignal.getUser()`; Kotlin uses the `OneSignal.User` property. Verified in mobile-sdk-reference (`getUser().addTag/addTags/trackEvent`).

## Android — consent-gated channels

```kotlin
// onesignal:managed v1
if (user.hasMarketingConsent) {                // customer's OWN flag
    OneSignal.User.addEmail(user.email)
    user.phone?.let { OneSignal.User.addSms(it) } // E.164, e.g. "+15551234567"
}
```

---

## iOS — Swift

```swift
// Tier 1 — identity (at auth success)
// onesignal:managed v1
OneSignal.login(user.id)                        // external_id
OneSignal.logout()

// Tier 2 — aliases (after login)
// onesignal:managed v1
OneSignal.User.addAlias(label: "stripe_id", id: stripeCustomerId)
OneSignal.User.addAliases(["stripe_id": stripeCustomerId, "crm_id": crmId])

// Tier 3 — tags (STRING values only — coerce)
// onesignal:managed v1
OneSignal.User.addTags([
    "account_plan": plan,                       // String already
    "seats": String(seatCount),                 // Int -> String
    "is_trial": isTrial ? "1" : "0",            // Bool -> "1"/"0"
    "signup_ts": String(Int(signupDate.timeIntervalSince1970)) // date -> unix-seconds String
])

// Tier 4 — events (properties = typed dictionary, do NOT stringify)
// onesignal:managed v1
OneSignal.User.trackEvent(name: "content_viewed",
                          properties: ["content_id": contentId, "read_time_sec": readTimeSec])
// revenue — numeric value + conversions handoff:
OneSignal.User.trackEvent(name: "purchase_completed",
                          properties: ["amount": order.total, "currency": order.currency, "order_id": order.id])
```

## iOS — Objective-C

```objc
// identity
// onesignal:managed v1
[OneSignal login:user.id];
[OneSignal logout];

// aliases
[OneSignal.User addAliasWithLabel:@"stripe_id" id:stripeCustomerId];

// tags (String values only)
[OneSignal.User addTags:@{ @"account_plan": plan, @"seats": [@(seatCount) stringValue], @"is_trial": isTrial ? @"1" : @"0" }];

// events (typed NSDictionary)
[OneSignal.User trackEventWithName:@"content_viewed"
                        properties:@{ @"content_id": contentId, @"read_time_sec": @(readTimeSec) }];
```

Verified Obj-C selectors: `login:`, `logout`, `addAliasWithLabel:id:`, `addTags:`, `trackEventWithName:properties:` (mobile-sdk-reference).

## iOS — consent-gated channels

```swift
// onesignal:managed v1
if user.hasMarketingConsent {                   // customer's OWN flag
    OneSignal.User.addEmail(user.email)
    if let phone = user.phone { OneSignal.User.addSms(phone) } // E.164
}
```

Verified signatures: Swift `addEmail(_:)` / `addSms(_:)`; the reference confirms "Call `login()` before `addEmail()`/`addSms()`". Never map email/phone to a tag or alias.
