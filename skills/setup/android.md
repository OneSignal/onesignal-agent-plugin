# Android native integration (OneSignal Android SDK 5.x)

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file is the Android install detail. Mirrors the proven flow in `sdk-ai-prompts/docs/android/integrate.md` with ONE correction from the matrix (below). Do not contradict [../../references/platform-matrix.md](../../references/platform-matrix.md).

## google-services.json is NOT required (matrix correction)

The upstream Android prompt tells you to add `google-services.json` + the Google Services Gradle plugin. **That is a known bug.** OneSignal delivers push via server-side **FCM v1 credentials** (a service-account JSON the human uploads to the OneSignal app, handled by the **credentials** skill). Do NOT add `google-services.json`, the `com.google.gms:google-services` classpath, or `apply plugin: 'com.google.gms.google-services'` — **unless the app itself already uses Firebase client SDKs** (grep for `com.google.firebase` deps / an existing `google-services.json`). If it does, leave the existing Firebase setup alone; still don't add it for OneSignal's sake.

`POST_NOTIFICATIONS` (Android 13+) is manifest-merged by the SDK — do not add it. Only `INTERNET` needs to be present (it usually already is).

## What the agent does vs. the human (matrix)

- **Agent:** Gradle dependency; `Application` subclass with `OneSignal.initWithContext`; register it in `AndroidManifest`; `requestPermission` call (inside the verification helper only); wrapper + verification helper.
- **Human (Firebase console):** create a Firebase project if none exists and generate the **service-account JSON** → handed to the **credentials** skill for upload. Push will not deliver until that FCM v1 credential is on the OneSignal app.

## Dependency (exact pin — NEVER a Gradle version range)

Do not compose the dependency line by hand — that is how ranges (`[5.6.1, 5.9.99]`) slip in, which caused real Android build failures in every mobile eval trial. Detect Groovy vs. Kotlin DSL by file name, then **run the resolver and paste its line verbatim:**

```bash
# Kotlin DSL (build.gradle.kts):
${CLAUDE_PLUGIN_ROOT}/scripts/resolve_sdk_version.py android --format line
# Groovy DSL (build.gradle):
${CLAUDE_PLUGIN_ROOT}/scripts/resolve_sdk_version.py android --format line --line-format gradle-groovy
```

The script emits an exact pin and cannot emit a range. Drop the output inside the module's `dependencies { }` block unchanged. Example of the shape it returns (the version will be the current Stable, not necessarily this one):
```kotlin
dependencies {
    implementation("com.onesignal:OneSignal:5.9.1") // onesignal:managed v1
}
```

### Kotlin floor (check it deterministically — don't guess, don't half-bump)

The OneSignal SDK transitively pulls in a **newer `kotlin-stdlib` than its own POM admits** — 5.9.1's POM declares 1.9.25, but its OpenTelemetry submodule (`com.onesignal:otel`) drags the resolved graph up to `kotlin-stdlib 2.2.20`. Gradle's highest-wins resolution then pins the whole app there, so the app must be compiled by a Kotlin toolchain that can **read 2.2 metadata**. A host on Kotlin 1.9 fails; a host bumped to an intermediate 2.0 **still fails** (the 2.0 compiler reads metadata only up to 2.1) — an eval trial did exactly this, mutating the customer's build for nothing.

The exact floor is NOT fetchable from Maven metadata (it lives deep in OpenTelemetry's graph), so read it from the resolved project and let the script decide:

```bash
./gradlew -q :app:dependencies --configuration debugRuntimeClasspath \
  | ${CLAUDE_PLUGIN_ROOT}/scripts/android_kotlin_check.py . --deps -
```

It reads the host's declared Kotlin version and the resolved `kotlin-stdlib`, then returns `compatible` (build it) or `bump_or_blocker` with the exact `required_floor`. On `bump_or_blocker`, **do not silently change the toolchain** (safety contract §7) — surface the decision the script spells out:
- **(A)** bump the host Kotlin Gradle plugin to at least the `required_floor` (e.g. `2.2.x`) as an explicit, separately-approved change — never to an intermediate version below the floor; or
- **(B)** if they can't move off their Kotlin version, it's a hard compatibility blocker. The resolver only serves the `stable`/`current` pins — it will **not** produce an older line, and you must **not** guess one. Ask the user to name a specific older OneSignal SDK version from the [release notes](https://github.com/OneSignal/OneSignal-Android-SDK/releases), confirm it resolves a `kotlin-stdlib` their compiler can read, and pin exactly that.

If the toolchain lacks the Android SDK/JDK to run `:app:dependencies`, say so and present the same A/B decision using the host Kotlin version alone (the script's host-only mode reports it); do not assert the build will pass.

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

Start from the bundled templates rather than retyping — they carry the verified signatures and the `onesignal:managed` marker:
- Wrapper: [assets/android/OneSignalManager.kt.tmpl](assets/android/OneSignalManager.kt.tmpl) — substitute `__PACKAGE__`.
- Application subclass: [assets/android/Application.kt.tmpl](assets/android/Application.kt.tmpl) — substitute `__PACKAGE__`, `__APP_CLASS__`, and `__APP_ID__` (the real App ID from Step 2).

The wrapper's shape:
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
A Hilt `@Singleton` variant and a Java variant are in the upstream android/integrate.md; use them only to match an existing pattern. No direct OneSignal calls outside this wrapper (except the verification helper).

## Debug-only verification helper (`OneSignalSetupVerification.kt`)

Use the verified template [assets/android/OneSignalSetupVerification.kt.tmpl](assets/android/OneSignalSetupVerification.kt.tmpl) — substitute `__PACKAGE__` and write it as-is. Do NOT hand-write this file: eval trials fabricated `OneSignal.Notifications.requestPermission(true) { ... }` as a callback (it is a `suspend fun` with no callback overload — does not compile) and/or dropped the `BuildConfig.DEBUG` guard (ships to release). The template calls `requestPermission` correctly from a coroutine and carries the guard; every API in it is verified against the SDK source. **`BuildConfig.DEBUG` needs the app module's buildConfig feature** — on AGP 8+ it is off by default, so ensure `android { buildFeatures { buildConfig = true } }` is present in the app's `build.gradle.kts` (add it if missing, or the guard won't compile). **The template also needs `kotlinx.coroutines` on the compile classpath** — the OneSignal SDK ships it only as a runtime (`implementation`) dependency, not `api` (verified against `com.onesignal:core` Gradle module metadata: coroutines is in the `java-runtime` variant, absent from `java-api`), so it is NOT transitively available to the app's own code. If the app doesn't already use coroutines, add `implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")` (the version the SDK resolves at runtime; newer is fine) or the `CoroutineScope`/`launch` calls won't resolve. The self-check flags both gaps (`android_buildconfig_feature_enabled`, `android_coroutines_on_classpath`). Non-negotiable properties the template already satisfies (SKILL.md Step 6):
- `if (!BuildConfig.DEBUG) return` guard.
- `OneSignal.Notifications.requestPermission(false)` called from a coroutine — the ONLY permission prompt. `fallbackToSettings` stays `false`: the call runs at launch with no user gesture, and `true` would send a previously-denied user to the OS Settings screen on every debug start.
- Register `IPushSubscriptionObserver` AND evaluate `OneSignal.User.pushSubscription.id` immediately (race guard — the ID can be assigned before the observer attaches).
- `isRegistered` = non-empty AND not `startsWith("local-")`.
- Log the subscription ID exactly once (an `AtomicBoolean` logged-once guard). The observer stays registered — the object holds no Activity reference, and removal from inside the callback could race the SDK's observer iteration.
- No dialog and no network call — the verify skill confirms the subscription server-side and sends the test push from chat.
- Top-of-file comment naming the file + the `MainActivity.onCreate()` call site, and saying the file is debug-only and safe to keep.

**Correction — `BuildConfig` on AGP 8+:** AGP no longer generates `BuildConfig` by default
for application modules, so the `BuildConfig.DEBUG` guard fails to compile on a default
project. Either enable it — `android { buildFeatures { buildConfig = true } }` — and report
`setup.install_applied ok_after_fix buildconfig_disabled`, or gate on
`applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE` instead, which needs no build
change and keeps the milestone a plain `ok`.

**Correction — permission callback from Kotlin:** the upstream flow's `Continue.with { }`
is a Java-interop shim; from Kotlin it returns a `kotlin.coroutines.Continuation` you cannot
pass explicitly, and it does not compile. `requestPermission(fallbackToSettings: Boolean)`
is a plain suspend function — call it from a coroutine (`lifecycleScope.launch` on a
`ComponentActivity`; `activity-ktx` is already present on any modern template). Use
`Continue.with` only in the Java variant.

Wire it with ONE line at the end of `MainActivity.onCreate()`:
```kotlin
// Debug-only OneSignal setup verification (see OneSignalSetupVerification.kt).
OneSignalSetupVerification.installIfDebug()
```

## Handoffs

- Almost always hand off to **credentials** next — Android push does not deliver without the FCM v1 service-account JSON on the OneSignal app.
- Then **verify** for the activation ladder. Test on a device/emulator WITH Google Play Services.
