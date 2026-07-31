# Data-mapping rules: customer code → OneSignal primitives

Verified against OneSignal docs, protobuf schemas, and all three SDK sources. These rules are hard constraints for any generated instrumentation.

## Decision tree

1. Is it a **verb/action** (something the user did)? → **custom event** `trackEvent(name, properties)`. Carries money? → include a numeric value property and recommend wiring it to a **Conversion Metric** (dashboard step).
2. Is it a **noun/state** (something the user is/has)? → **tag** (string-only).
3. Is it an **identifier**? Primary internal user id → `login(externalId)`. Stable secondary id (Stripe/CRM/Firebase) → alias.
4. Is it **contact info** (email/phone)? → channel subscription (`addEmail`/`addSms`), consent-gated. NEVER a tag or alias.
5. Can't classify? → propose nothing; ask.

## Hard constraints

| Primitive | Constraint |
|---|---|
| Tags | Values are **strings everywhere** (SDK signatures + hstore storage). Stringify: `42`→`"42"`, `true`→`"1"`, dates→unix-seconds string `"1685400000"`. No arrays/objects/JSON. Per-user count is plan-capped (up to 1,000) and fails near-silently at the limit. |
| Reserved tag keys | `message, notification, subscription, user, template, app, org, dynamic_content, data_feed, journey, custom_data` — never use; rename with a namespace (`plan`→`account_plan`). |
| Aliases | ≤10 per user; label and value ≤128 chars; no `/?#&=`, whitespace, control chars, `..`; labels `external_id`/`onesignal_id` reserved; **external_id must be set before custom aliases work**. |
| Custom events | `name` ≤128 chars; ≤2024 bytes/event; properties = real JSON (typed values OK). Never name events with `os.` or `os__` prefixes (reserved, ingest-enforced). |
| Outcomes (legacy) | Deprecated. Values are numeric and **round to whole numbers** — unusable for cents. Do not emit `addOutcome*`. |
| Ordering | `login()` first, then tags/aliases/email/sms/events — otherwise data attaches to an anonymous user. |

## Discovery source ranking (richest → weakest)

1. Tracking plan / Avo / typed event unions — authored, complete; read first if present.
2. Event-constants files (`analytics/events.ts` etc.).
3. `analytics.identify(userId, traits)` / `.track(name, props)` call sites (Segment/Amplitude/Mixpanel/PostHog/Rudder) — traits map ≈1:1 to tags, events to `trackEvent`, userId to `login`.
4. Prisma / Rails `schema.rb` / typed ORM models — column names+types are tag candidates AFTER a semantic allow/deny pass.
5. Auth provider (Clerk/Auth0/Firebase/NextAuth/Devise) — the external-id source; custom claims often hold plan/role.
6. Feature-flag keys (LaunchDarkly/Statsig) — candidate boolean tags, human judgment required.
7. Redux/Zustand/untyped Mongoose — real shape, inferred value.
8. GA/gtag/Heap — events maybe; no reliable user traits.

Exclude from taxonomy extraction: `node_modules, dist, build, .next, vendor, Pods`, tests/stories, commented code. Watch for wrapper indirection (a local `lib/analytics.ts` wrapping the vendor — grep the wrapper's call sites too). Classify every finding by runtime owner: client / server / warehouse-import (server-only analytics won't appear in a client repo — absence ≠ no data).

## PII buckets (hard gating)

- **(a) Safe identifiers** (opaque internal ids) → external_id/alias; bulk-approvable. If the primary key IS an email, propose `login(sha256(email))`.
- **(b) Contact channels** (email/phone) → subscriptions only, wrapped in the customer's own consent check; explicit per-field approval.
- **(c) Sensitive PII** — legal name beyond display, street address, DOB, government IDs, health, financial account numbers, precise lat/long → **blocked; never auto-map**. Government/health/financial have no opt-in path. Offer coarse alternatives (`age_range`, `region`).
- **(d) Ambiguous** (`token`, `code`, freeform `name`, `location`) → never auto-mapped; ask with "skip" as the default.

Detection is schema/name-based only. NEVER read, sample, log, or transmit real user data values.
