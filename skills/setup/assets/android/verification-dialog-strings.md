# Verification dialog — exact strings (copy verbatim)

The verification file itself is the template
[OneSignalSetupVerification.kt.tmpl](OneSignalSetupVerification.kt.tmpl) — the
single source of truth for the plumbing (observer, race guard, coroutine call,
Activity handling). Write it as-is; do not hand-write it from memory. This file
exists only to pin the **exact strings** and the **behavioral contract**, because
eval trials showed models paraphrasing the strings when they arrive via a
summarized URL fetch ("Push Notification Setup Complete", "Enable"/"Not Now",
etc.). Reproduce every literal below character-for-character.

## Exact strings

Success dialog:

- Title: `Your OneSignal SDK integration is complete!`
- Message: `Tap 'Got it' to enable push notifications and send yourself a test message.`
- Single button label: `Got it` — this button is the **only** place push
  permission is requested (`OneSignal.Notifications.requestPermission(...)` on
  tap); never prompt at launch.

Test-message dialog (shown after permission is granted):

- Title: `Send Test Push`
- Text-field hint: `Enter message body`
- Positive button: `Send`
- Negative button: `Cancel`
- Default body when the field is left empty: `Test from OneSignal!`

## Behavioral contract (the template already satisfies these)

- Guard the whole flow behind `if (!BuildConfig.DEBUG) return`.
- Register the push-subscription observer AND evaluate
  `OneSignal.User.pushSubscription.id` immediately (the ID can be assigned before
  the observer attaches — a race guard).
- Treat the device as registered only when the subscription ID is non-empty AND
  does **not** start with `local-`.
- Show the dialog exactly once (an `AtomicBoolean` shown-once guard).
- Resolve the presenting Activity at show time (not the one captured at install)
  and remove the observer once shown — a captured Activity crashes with
  `BadTokenException` after a rotation and leaks through the SDK's observer.
