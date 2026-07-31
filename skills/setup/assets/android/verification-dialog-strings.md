# Verification dialog — exact strings (copy verbatim)

The deletable verification file (SKILL.md Step 6, android.md) must use these
literals **exactly**. Eval trials showed models paraphrasing them when the
strings arrive via a summarized URL fetch ("Push Notification Setup Complete",
"Enable"/"Not Now", etc.); reproduce them character-for-character.

- Dialog title: `Your OneSignal SDK integration is complete!`
- Single button label: `Got it`
- The button is the **only** place push permission is requested
  (`OneSignal.Notifications.requestPermission(...)` on tap) — never prompt at launch.

Non-string behavioral contract (see android.md Step 6 for the full list):

- Guard the whole flow behind `if (!BuildConfig.DEBUG) return`.
- Register the push-subscription observer AND evaluate
  `OneSignal.User.pushSubscription.id` immediately (the ID can be assigned
  before the observer attaches — a race guard).
- Treat the device as registered only when the subscription ID is non-empty
  AND does **not** start with `local-`.
- Show the dialog exactly once (an `AtomicBoolean` shown-once guard).

> The full verification-file implementation is the verified upstream code in
> `sdk-ai-prompts/docs/android/integrate.md`. Reproduce it faithfully — do not
> hand-write the `requestPermission` / observer plumbing from memory (a
> documented source of APIs that compile but are functionally dead).
