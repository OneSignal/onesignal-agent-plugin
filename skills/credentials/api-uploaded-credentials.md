# API-uploaded credentials: Apple APNs .p8 and Firebase FCM v1

Portal-level detail for the two credentials the agent finishes via the OneSignal apps API. The SKILL.md owns the flow, safety rules, and validation loop — this file is the deep reference for the human's console steps and the failure modes. Verified against the OneSignal docs (July 2026): `ios-p8-token-based-connection-to-apns.mdx`, `android-firebase-credentials.mdx`, and the Create/Update App API reference pages.

Both credentials are Base64-encoded before upload and validated server-side on upload. Upload goes to the **write-once provisioning endpoint** `POST /api/v1/apps/{app_id}/credentials` — preferably through the `provision_app_credentials` MCP tool when the OneSignal MCP is connected (it forwards the session auth; you pass only the base64 params), otherwise via a direct call authenticated with an **app-scoped key** (`Authorization: Key <key>` — the onboarding setup token or the app's REST API key). Either way the payload is the same base64 strings. **One complete platform set per call:** APNs and Firebase are provisioned in separate calls — do not batch two platforms into one request (the MCP tool rejects it, and a multi-platform direct request is all-or-nothing). An org key is only needed to *replace* existing credentials, which happens via the dashboard or the org-key update API, never through this endpoint. See [../../references/api-reference.md](../../references/api-reference.md) for the full contract (including the MCP transport note) and [../../references/safety-contract.md](../../references/safety-contract.md) for secret handling.

---

## Apple APNs .p8

### Prerequisites (verify in Step 0)
- A **paid** Apple Developer account with **Admin** access (needed to create keys). Free accounts cannot create APNs keys.
- The app's **Push Notification capability** enabled on the App ID (Xcode-side / Identifiers).

### Human steps in the Apple Developer portal
1. Log into the Apple Developer account.
2. Go to **Certificates, Identifiers & Profiles → Keys**.
3. Click the blue **+**. (If it's not visible, the account lacks Admin — resolve access first.)
4. Select **Apple Push Notifications service (APNs)**. Ensure **Sandbox & Production** is selected.
5. Name the key → **Continue** → **Register**.
6. **Download the `.p8`.** This is a one-time download — Apple will not let it be downloaded again. Store it securely, outside the repo.

### The four values the agent needs
The `.p8` file path plus:

| Value | Where the human finds it | Shape |
|---|---|---|
| **Key ID** (`apns_key_id`) | Next to the key name in the **Keys** section | 10-char alphanumeric, e.g. `ABC123DEFG` |
| **Team ID** (`apns_team_id`) | Next to the team name, top-right of the Apple Developer account | 10-char alphanumeric, e.g. `9A1B2C3D4E` |
| **Bundle ID** (`apns_bundle_id`) | **Identifiers** section, or Xcode → Signing & Capabilities | reverse-domain, e.g. `com.example.app` |

**Key ID vs Team ID is the #1 misconfiguration.** Both are 10-char strings from different places. If the user is unsure which is which, have them re-read each from its portal location before you upload.

### Propagation
A newly created key can take **10–15 minutes** before Apple honors it for external authentication. An auth error *immediately* after creating the key is expected — wait and re-validate the same upload. Do not conclude the key is bad on the first immediate failure.

### Upload parameters (verified — Create/Update App reference)
All four are required for the p8 flow:
- `apns_p8` — Base64 of the `.p8` file body
- `apns_key_id`
- `apns_team_id`
- `apns_bundle_id`

All four verified against the merged endpoint; a missing field returns a 400 naming it.

**.p12 certificates are NOT supported through the provisioning endpoint** — p8 only. If the user only has a .p12 (and cannot create a .p8), route them to the dashboard (Settings > Push Platforms) for the upload. .p8 is recommended anyway: no annual expiry, works across all apps under the account.

### .p8 failure modes → what to tell the user (surface the raw error first)
| Symptom | Likely cause | Fix |
|---|---|---|
| Auth error seconds after creating the key | 10–15 min propagation window | Wait, retry same upload |
| "invalid key id" / auth rejected | Swapped Key ID / Team ID | Re-confirm each 10-char value against its portal location |
| Wrong file type rejected | Downloaded a `.p12` from **Certificates**, not a `.p8` from **Keys** | Create a `.p8` in the Keys section |
| Key has no APNs capability | Key created without APNs selected | Revoke, create a new key with APNs + Sandbox & Production |
| Persistent, values verified | — | Revoke & recreate the key; contact `support@onesignal.com` with App ID, Key ID, Team ID, Bundle ID |

The valid `.p8` body looks like:
```
-----BEGIN PRIVATE KEY-----
<64-char lines>
-----END PRIVATE KEY-----
```

---

## Firebase FCM v1

### Prerequisites (verify in Step 0)
- Owner/Editor on the Firebase (Google Cloud) project.
- An Android app distributed via the Google Play Store (Huawei App Gallery uses a different flow — out of scope here).

### Human steps in the Firebase console
1. Open the [Firebase console](https://console.firebase.google.com/). Create the project (**Add project**) or select the existing one.
2. Gear icon → **Project settings**.
3. **Enable-FCM-v1 detour — only if it's disabled:** open the **Cloud Messaging** tab. If **Firebase Cloud Messaging API (V1)** is shown as **disabled**, use the 3-dot menu → **Open in Cloud Console** → click **Enable**. Wait a few minutes for it to reflect back in Firebase. (If it's already enabled, skip this.)
4. Back in **Project settings → Service accounts** → **Generate new private key** → confirm **Generate key** in the popup.
5. A `.json` file downloads. **This is a secret** — store it securely, outside the repo. The human gives the agent its **file path**.

### Required service-account permissions (default is fine)
Included by default:
- `cloudmessaging.messages.create`
- `firebase.projects.get`

If it's a custom service account, it needs `roles/firebasemessaging.admin` and `roles/firebase.viewer`.

### Upload parameter (verified — Create/Update App reference)
- `fcm_v1_service_account_json` — **Base64 of the JSON file**. This is the only required Android push param.

### google-services.json is NOT needed
OneSignal authenticates to FCM entirely server-side using the service-account JSON above. `google-services.json` is a **client-side** Firebase config file and is **not** a OneSignal credential:
- Do not ask the user for it.
- Do not upload it.
- It only matters if the app *itself* embeds Firebase client SDKs (Analytics, Firestore, etc.) — that's the app's own setup, unrelated to OneSignal push.

The upstream OneSignal ai-install-prompt that asks for `google-services.json` is a **known bug** (see [../../references/platform-matrix.md](../../references/platform-matrix.md) Android notes). Follow this file, not that prompt.

### FCM failure modes → what to tell the user (surface the raw error first)
| Symptom | Likely cause | Fix |
|---|---|---|
| "This configuration is for a different Firebase Project…" / Sender ID mismatch | JSON is from the wrong Firebase project | Upload the JSON from the project whose **Sender ID** matches the OneSignal app (Firebase → Cloud Messaging → Sender ID). Switching projects invalidates existing push tokens until users reopen the app. |
| Upload rejected as legacy | It's a legacy GCM server key (`AIz…`), not a v1 service account | Create a new Firebase project and generate a v1 **service-account JSON** |
| FCM v1 API not enabled | Skipped the enable detour | Do step 3 (Open in Cloud Console → Enable), wait, retry |
| 401 on upload | The key doesn't belong to this app | Confirm the env var holds this app's setup token or REST API key |
| 409 on upload | Platform already has credentials (write-once) | Relay the response message verbatim; replacement happens in the dashboard (Settings > Push Platforms) or with an org key — never retry this endpoint |
| 404 on upload | Feature flag off for this app | Fall back to the dashboard upload walkthrough |

### Verifying which apps still use legacy
Per the docs, the [View apps](/docs/en/) API distinguishes:
- `"gcm_key"` present → legacy, needs migration
- `"fcm_v1_service_account_json"` present → on v1 (good)
- neither → the app doesn't use Android push

---

## After either upload
- Confirm success from the API response body (2xx). A 2xx means the credential is stored and passed server-side validation — it is **not** proof of end-to-end delivery. Real proof is a test send to a subscribed device; hand that to the SDK-setup / verify skill.
- Confirm the secret file never entered the repo (gitignored or external) and never appeared in chat.
- Do not auto-commit any `.gitignore` change — offer the command.
