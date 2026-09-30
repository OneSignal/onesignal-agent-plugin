# Android native integration (OneSignal Android SDK 5.x)

Reference for the `setup` skill. Follow [SKILL.md](SKILL.md) Steps 0–8; this file is the Android install detail. `<plugin>` in the commands below is the directory SKILL.md defines: walk up from this file's directory to the first directory that contains a `scripts/` folder. Mirrors the proven flow in `sdk-ai-prompts/docs/android/integrate.md` with ONE correction from the matrix (below). Do not contradict [../../references/platform-matrix.md](../../references/platform-matrix.md).

## Firebase server credential and client config are separate

OneSignal always needs both sides of Firebase configuration:

- `google-services.json` configures the Android client and is required for every integration.
- The FCM v1 service-account JSON authorizes OneSignal's servers to send and is uploaded
  only after the SDK and client config have been applied.

Neither file replaces the other. Do not inspect `firebase_messaging_installation_id_enabled`
to decide whether to add the client file.

1. Have the user register the app's exact package name in Firebase **Project settings → General → Your apps**, then download `google-services.json`. Ask only for its file path.
2. Read the JSON and verify `client[].client_info.android_client_info.package_name` matches the app package. Retain `project_info.project_number`; after the final service-account upload, verify it matches OneSignal's live `android_sender_id`. A mismatch means the files came from different Firebase projects — stop instead of verifying the app.
3. Copy it to the Android application module root (normally `app/google-services.json`).
4. Enable the Google Services Gradle plugin. Follow the project's existing plugin style; when no version is already managed, use the exact Firebase-documented pin `4.4.4`: root plugin `com.google.gms.google-services` with `apply false`, then apply `com.google.gms.google-services` in the app module. The JSON file without the plugin does not produce the Firebase resources the FID path reads.

`google-services.json` contains client identifiers, not the service-account private key, and is normally committed with the app. Never copy the service-account JSON into the repo or confuse the two files.

`POST_NOTIFICATIONS` (Android 13+) is manifest-merged by the SDK — do not add it. Only `INTERNET` needs to be present (it usually already is).

## What the agent does vs. the human (matrix)

- **Agent:** validated Firebase client config; Gradle dependency; `Application` subclass with `OneSignal.initWithContext`; register it in `AndroidManifest`; `requestPermission` call (inside the verification helper only); wrapper + verification helper; then upload the server credential as the final step before verification.
- **Human (Firebase console):** register the Android package and download `google-services.json`; generate the **service-account JSON** when the final credentials step asks for it. Push will not deliver until that server credential is on the OneSignal app.

## Dependency (exact pin — NEVER a Gradle version range)

Do not compose the dependency line by hand — that is how ranges (`[5.6.1, 5.9.99]`) slip in, which caused real Android build failures in every mobile eval trial. Detect Groovy vs. Kotlin DSL by file name, then **run the resolver and paste its line verbatim:**

```bash
# Kotlin DSL (build.gradle.kts):
<plugin>/scripts/resolve_sdk_version.py android --format line
# Groovy DSL (build.gradle):
<plugin>/scripts/resolve_sdk_version.py android --format line --line-format gradle-groovy
```

The script emits an exact pin and cannot emit a range. Drop the output inside the module's `dependencies { }` block unchanged. For Android the resolver is currently **forced to `5.11.0-rc`**: that build supports the Firebase Installation ID (FID) path and is published only to the developer's local Maven repository, so it skips the release feed. It returns:
```kotlin
dependencies {
    implementation("com.onesignal:OneSignal:5.11.0-rc") // onesignal:managed v1
}
```

### `mavenLocal()` (required for the forced pin)

`5.11.0-rc` does not exist on Maven Central or Google, so add `mavenLocal()` as the **first** repository or resolution fails. Follow the project's style: in `settings.gradle(.kts)` inside `dependencyResolutionManagement { repositories { ... } }`, or else in the root `allprojects { repositories { ... } }` / the module `repositories { }` block. Include the line in the Step-5 reviewed change set. If `~/.m2/repository/com/onesignal/OneSignal/5.11.0-rc` does not exist on this machine, stop and ask the user to publish it locally (`publishToMavenLocal`) rather than falling back to another version. The build will not resolve on other machines or CI that lack that local artifact; say so in the summary.

### Kotlin floor (check it deterministically — don't guess, don't half-bump)

The OneSignal SDK transitively pulls in a **newer `kotlin-stdlib` than its own POM admits** — 5.9.1's POM declares 1.9.25, but its OpenTelemetry submodule (`com.onesignal:otel`) drags the resolved graph up to `kotlin-stdlib 2.2.20`. Gradle's highest-wins resolution then pins the whole app there, so the app must be compiled by a Kotlin toolchain that can **read 2.2 metadata**. A host on Kotlin 1.9 fails; a host bumped to an intermediate 2.0 **still fails** (the 2.0 compiler reads metadata only up to 2.1) — an eval trial did exactly this, mutating the customer's build for nothing.

The exact floor is NOT fetchable from Maven metadata (it lives deep in OpenTelemetry's graph), so read it from the resolved project and let the script decide:

```bash
./gradlew -q :app:dependencies --configuration debugRuntimeClasspath \
  | <plugin>/scripts/android_kotlin_check.py . --deps -
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

**Before you edit the manifest, open it and look at the `<application>` tag.** If it has `tools:node="replace"`:

1. **Stop and warn the user.** That merger marker drops all OneSignal manifest components (for example `PermissionsActivity`). The notification permission flow crashes with `ActivityNotFoundException`, and push registration also breaks.
2. **Fix:** remove `tools:node="replace"`. If they only need to override one attribute (theme, label, icon), use `tools:replace="android:theme"` (or that attribute) instead.
3. After the next build, confirm `com.onesignal.core.activities.PermissionsActivity` is in `app/build/intermediates/merged_manifests`.

Do not add `tools:node="replace"` yourself. `POST_NOTIFICATIONS` is already merged by the SDK.

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

## Checkpoint — `setup.platform_config` (permission state)

Report the Android permission state right after `setup.verification_added`, once the
Step-5 change set and the verification helper both exist (telemetry contract rules
apply, consent included):

- `INTERNET` present in the manifest, no manual `POST_NOTIFICATIONS` line (the SDK
  manifest-merges it), the `requestPermission` call wired in the helper, and
  `google-services.json` plus its Gradle plugin validated and applied:
  `bash <plugin>/scripts/checkpoint.sh setup.platform_config ok`
- `google-services.json` or its Gradle plugin is missing because the user
  declined that change:
  `bash <plugin>/scripts/checkpoint.sh setup.platform_config fail firebase_client_config`

If `INTERNET` was missing and the approved change set added it, that is part of the
minimal integration — still plain `ok`.

## Handoffs

- Hand off to **credentials** as the final setup step — Android push does not deliver without the FCM v1 service-account JSON on the OneSignal app.
- Then **verify** for the activation ladder. Test on a device/emulator WITH Google Play Services.
