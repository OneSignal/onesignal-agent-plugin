# Android native integration (OneSignal Android SDK 5.x)

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file is the Android install detail. Mirrors the proven flow in `sdk-ai-prompts/docs/android/integrate.md` with ONE correction from the matrix (below). Do not contradict [../../references/platform-matrix.md](../../references/platform-matrix.md).

## google-services.json is NOT required (matrix correction)

The upstream Android prompt tells you to add `google-services.json` + the Google Services Gradle plugin. **That is a known bug.** OneSignal delivers push via server-side **FCM v1 credentials** (a service-account JSON the human uploads to the OneSignal app, handled by the **credentials** skill). Do NOT add `google-services.json`, the `com.google.gms:google-services` classpath, or `apply plugin: 'com.google.gms.google-services'` — **unless the app itself already uses Firebase client SDKs** (grep for `com.google.firebase` deps / an existing `google-services.json`). If it does, leave the existing Firebase setup alone; still don't add it for OneSignal's sake.

`POST_NOTIFICATIONS` (Android 13+) is manifest-merged by the SDK — do not add it. Only `INTERNET` needs to be present (it usually already is).

## What the agent does vs. the human (matrix)

- **Agent:** Gradle dependency; `Application` subclass with `OneSignal.initWithContext`; register it in `AndroidManifest`; `requestPermission` call (inside the verification file only); wrapper + verification file.
- **Human (Firebase console):** create a Firebase project if none exists and generate the **service-account JSON** → handed to the **credentials** skill for upload. Push will not deliver until that FCM v1 credential is on the OneSignal app.

## Dependency (exact pin — NEVER a Gradle version range)

Detect Groovy vs. Kotlin DSL by file name. Read the exact Stable version from https://onesignal.github.io/sdk-releases/releases.json (the Android entry's `channels.stable.version`; SKILL.md Step 4 — do not use the human-readable page, do not guess). **Do not emit a range like `[5.6.1, 5.9.99]`** — eval runs showed ranges causing real Android build failures, and the upstream prompt forbids them.

`build.gradle.kts` (example — `5.9.1` was Stable at authoring; read the current value from releases.json):
```kotlin
dependencies {
    implementation("com.onesignal:OneSignal:5.9.1") // onesignal:managed v1 — exact Stable from releases.json
}
```
`build.gradle` (Groovy):
```groovy
dependencies {
    implementation 'com.onesignal:OneSignal:5.9.1' // onesignal:managed v1 — exact Stable from releases.json
}
```

## Initialize in `Application.onCreate()` (only reliable place)

Never initialize from a ViewModel/Activity/Fragment — that breaks cold-start push and deep links. Match the repo's language (`.kt`→Kotlin, `.java`→Java) and existing DI (add `@HiltAndroidApp` only if Hilt is ALREADY present; never introduce Hilt).

```kotlin
class MyApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        OneSignalManager.initialize(this, "YOUR_ONESIGNAL_APP_ID") // onesignal:managed v1
    }
}
```

Register in `AndroidManifest.xml`:
```xml
<application android:name=".MyApplication" ...>
```
If an `android:name` Application class already exists, add the init call to its `onCreate()` instead of creating a new one.

## Centralized wrapper (Kotlin, no-DI default)

Signatures verified against api-reference "SDK data surface". Tag values are strings only. `login()` before tags/email/sms (ordering rule).

```kotlin
object OneSignalManager { // onesignal:managed v1
    private var initialized = false
    fun initialize(context: Context, appId: String) {
        if (initialized) return
        OneSignal.Debug.logLevel = LogLevel.VERBOSE // remove for production
        OneSignal.initWithContext(context.applicationContext, appId)
        initialized = true
    }
    fun login(externalId: String) = OneSignal.login(externalId)
    fun logout() = OneSignal.logout()
    fun setEmail(email: String) = OneSignal.User.addEmail(email)
    fun setSmsNumber(number: String) = OneSignal.User.addSms(number)
    fun setTag(key: String, value: String) = OneSignal.User.addTag(key, value)
}
```
A Hilt `@Singleton` variant and a Java variant are in the upstream android/integrate.md; use them only to match an existing pattern. No direct OneSignal calls outside this wrapper (except the verification file).

## Deletable verification file (`OneSignalSetupVerification.kt`)

Full verified implementation is in `sdk-ai-prompts/docs/android/integrate.md` (Kotlin and Java). Reproduce it faithfully. Non-negotiable properties (SKILL.md Step 6):
- `if (!BuildConfig.DEBUG) return` guard.
- Register `IPushSubscriptionObserver` AND evaluate `OneSignal.User.pushSubscription.id` immediately (race guard — the ID can be assigned before the observer attaches).
- `isRegistered` = non-empty AND not `startsWith("local-")`.
- Shown-once `AtomicBoolean`; native `AlertDialog` titled "Your OneSignal SDK integration is complete!" with a single **"Got it"** button.
- On tap → `OneSignal.Notifications.requestPermission(true)` (the ONLY permission prompt) → text-input dialog → unauthenticated `POST https://api.onesignal.com/notifications` with `include_subscription_ids` (no Authorization header; relies on `permit_unauth_notif_create` — on HTTP 401 fall back to a dashboard/REST-key send, api-reference).
- Top-of-file comment naming the file + the `MainActivity.onCreate()` call site to delete.

Wire it with ONE line at the end of `MainActivity.onCreate()`:
```kotlin
// TODO: Remove this line and delete OneSignalSetupVerification.kt once verified.
OneSignalSetupVerification.installIfDebug(this)
```

## Handoffs

- Almost always hand off to **credentials** next — Android push does not deliver without the FCM v1 service-account JSON on the OneSignal app.
- Then **verify** for the activation ladder. Test on a device/emulator WITH Google Play Services.
