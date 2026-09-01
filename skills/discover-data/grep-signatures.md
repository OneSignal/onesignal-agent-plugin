# grep-signatures — discovery search cookbook

Reference for the `discover-data` skill (`./SKILL.md`). These are the concrete search patterns for each source rank in `../../references/data-mapping-rules.md`. All searches are **read-only**: grep and read file contents for **names/schema only**, never data values, never secret files (see SKILL.md guardrails 3–4). Always apply the exclusion set from SKILL.md Step 1 (`node_modules`, `dist`, `.next`, `vendor`, `Pods`, tests/stories, etc.).

Prefer a fast recursive searcher (`rg`/ripgrep if available, else `grep -rniE`). Search with `-n` to capture `file:line`. Adjust globs per package in a monorepo — run each search rooted at the package, not the repo root, so the runtime owner is unambiguous.

> Patterns below are search heuristics, not verified APIs. The OneSignal targets they map to are the only verified part (see api-reference.md / data-mapping-rules.md). Vendor SDK method names may drift across versions — treat a miss as "verify against that vendor's docs," not "no data."

## Rank 1 — Tracking plan / typed event unions (richest)

Read these first if present; they are authored and near-complete.

- Files: `avo.json`, `tracking-plan*.{json,yml,yaml}`, `analytics/schema*.{ts,json}`, `events.schema.*`, generated Avo files (`Avo.ts`, `Avo.swift`, `Avo.kt`), Segment Protocols exports.
- TypeScript typed unions:
  - `type .*Event =` / `enum .*Event` / `as const` event maps
  - discriminated unions on an `event`/`name`/`type` literal field
- Signature hints: `rg -n "type\s+\w*Event\w*\s*=|enum\s+\w*Event|avo\.|trackingPlan"`

## Rank 2 — Event-constants files

- Files/dirs: `analytics/events.*`, `constants/events.*`, `lib/events.*`, `eventNames.*`, `**/events.{ts,js,py,rb,kt,swift}`
- Patterns: `export const .*EVENT`, `EVENTS = {`, `enum Event`, `ANALYTICS_EVENTS`, uppercase snake constants that read like event names (`ORDER_COMPLETED`, `SIGNED_UP`).
- `rg -n "EVENTS?\s*[:=]\s*[{(]|[A-Z0-9_]{3,}\s*[:=]\s*['\"][A-Za-z ]+['\"]"`

## Rank 3 — Analytics call sites (Segment / Amplitude / Mixpanel / PostHog / Rudderstack)

Mapping (verified against OneSignal's Segment integration doc + data-mapping-rules.md):
- `identify(userId, traits)` → `userId` = **external_id**, each trait = **tag** candidate.
- `track(name, props)` → `name` = **custom event**, props = event JSON (and OneSignal's Segment path also stores props as tags).
- `alias(...)` → **alias** candidate.
- `group(groupId, traits)` → org/account-level; usually **alias** (account id) + tags; human judgment.

Vendor call-site patterns:
- Segment / Rudderstack: `analytics\.(identify|track|page|screen|group|alias)\(`
- Amplitude: `amplitude\.(getInstance\(\))?\.(logEvent|identify|setUserId)\(`, `track\(`, `Identify\(`
- Mixpanel: `mixpanel\.(track|identify|people\.set|register)\(`
- PostHog: `posthog\.(capture|identify|people\.set|group)\(`
- Firebase Analytics: `logEvent\(`, `setUserId\(`, `setUserProperty\(`

`rg -n "\b(analytics|amplitude|mixpanel|posthog)\b\s*\.\s*(identify|track|capture|logEvent|setUserId|setUserProperty|group|alias)\("`

**Read the identify() trait object keys** (names only) — those are your tag candidates. **Read the track() first argument** (the event name literal or constant) — that is the event. Do NOT read the property *values*.

### Wrapper indirection (do not miss this)

Teams wrap the vendor in a local module so raw `analytics.*` calls are sparse. Two-pass approach:

1. **Find the wrapper.** Files like `lib/analytics.*`, `utils/tracking.*`, `services/analytics.*`, `hooks/useAnalytics.*`, `app/services/analytics.rb`. Signature: a module that imports the vendor SDK **and** exports its own `track`/`identify`/`logEvent`/`capture` function.
   `rg -ln "from ['\"](@segment|@amplitude|mixpanel|posthog|@rudderstack)" && rg -ln "export (function|const) (track|identify|logEvent|capture|trackEvent)"`
2. **Grep the wrapper's exported names as the real inventory.** If the wrapper exports `track(event, props)`, then `rg -n "\btrack\(" --glob '!lib/analytics.*'` across app code gives the true event list. The wrapper's own signature tells you the arg order (which arg is the event name, which is props).

Report wrapper-sourced findings at **med** confidence unless the wrapper is typed (typed wrapper → keep the underlying source's confidence).

## Rank 4 — ORM / typed schemas (column names = tag candidates after PII pass)

- **Prisma:** `prisma/schema.prisma` — read `model` blocks; each scalar field is a tag candidate. `@id`/`@unique String` id → external_id/alias candidate. Watch `DateTime`, `Decimal`, `Json` (stringify/skip per constraints). `rg -n "^\s*model\s+\w+|^\s*\w+\s+(String|Int|Boolean|DateTime|Decimal|Float|Json)"  prisma/schema.prisma`
- **Rails:** `db/schema.rb` — `create_table` + `t.<type> "col"`. `rg -n "create_table|t\.(string|integer|boolean|datetime|decimal|jsonb?)\s+\"" db/schema.rb`. Also `app/models/*.rb` for `enum`, `store_accessor`.
- **TypeORM / Sequelize / Mongoose(typed):** `@Column`, `@Entity`, `sequelize.define(`, typed Mongoose `Schema<>`.
- **Django / SQLAlchemy:** `models.py` `= models.CharField(`, `Column(String`.
- **Go / Java:** struct tags `json:"..."` / `gorm:"..."`, JPA `@Column`.

Column names are candidates; run the **semantic allow/deny pass** (see classification-reference.md) before proposing. Never read row data or migrations that embed seed values.

## Rank 5 — Auth provider (the external_id source)

- Clerk: `@clerk/`, `auth()`, `useUser()`, `sessionClaims`, `publicMetadata`/`privateMetadata` (custom claims = plan/role tags).
- Auth0: `@auth0/`, `useAuth0`, `getSession`, `user.sub` (the stable id → external_id), `app_metadata`/`user_metadata`.
- Firebase Auth: `firebase/auth`, `onAuthStateChanged`, `uid` (→ external_id), custom claims.
- NextAuth / Auth.js: `next-auth`, `getServerSession`, `session.user.id`, `callbacks.jwt`.
- Devise (Rails): `current_user`, `devise :`, `user.id`.
- Supabase: `supabase.auth`, `user.id`.

`rg -n "@clerk|@auth0|next-auth|firebase/auth|supabase\.auth|devise|sessionClaims|app_metadata|publicMetadata|user\.(sub|uid|id)"`

The user's **primary id here is the external_id**. Custom claims / metadata holding `plan`, `role`, `tier` are **tag** candidates (bucket a). If the id is an email, propose `login(sha256(email))` per data-mapping-rules.md.

## Rank 6 — Feature flags (candidate boolean tags — human judgment)

- LaunchDarkly: `launchdarkly`, `ldClient`, `variation(`, `useFlags(`.
- Statsig: `statsig`, `checkGate(`, `getConfig(`.
- Unleash: `unleash`, `isEnabled(`.
- Split, Flagsmith, GrowthBook, config JSON of flag keys.

`rg -n "launchdarkly|ldClient|statsig|checkGate|unleash|isEnabled\(|useFlags\(|flagsmith|growthbook"`

Flag **keys** are low-confidence boolean-tag candidates. Do not assume every flag is worth a tag — route to "Needs your call" unless the user asks to map flags.

## Rank 7 — Untyped client state (real shape, inferred value)

- Redux: `createSlice(`, `initialState`, action `type` strings.
- Zustand: `create(` store shape.
- Untyped Mongoose: `new Schema({` without generics.

Field names are candidates at **low** confidence (shape is real, semantics inferred).

## Rank 8 — GA / gtag / Heap (events maybe, no reliable traits)

- `gtag('event',`, `dataLayer.push(`, `ga(`, `heap.track(`.

Events only, low confidence; no reliable user-trait mapping.

## Secret-file skip list (never open — restate of SKILL.md guardrail 4)

`.env*` (except `.env.example`), `*.pem`, `*.key`, `*.p8`, `*.p12`, `*.jks`, `*.keystore`, `credentials.json`, `service-account*.json`, `google-services.json`, `GoogleService-Info.plist`, `.npmrc`, `.netrc`, anything under `secrets/`. Match by filename and skip — do not read to "check." If a secret-shaped token surfaces incidentally, redact it in output.

## Data-value skip list (schema-names-only)

Do not open to inspect contents: `*.csv`, `*.tsv`, `*.sql` dumps, `seeds.*`/`seed*.{rb,ts,js,sql}`, fixture/factory data, `db/*.sqlite*`, `*.parquet`, large `*.json` data blobs, snapshot files. You may read a schema *definition* file (`schema.prisma`, `schema.rb`) — that is structure, not values.
