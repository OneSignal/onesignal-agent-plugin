# OneSignal onboarding plugin for Claude Code

A customer-facing [Claude Code](https://code.claude.com/docs/en/overview) plugin that helps you
onboard your **own codebase** onto [OneSignal](https://onesignal.com). Point Claude Code at your
project and it will install the SDK, wire up your app and push credentials, find the user data worth
sending to OneSignal, instrument identity/tags/events, verify a real push actually gets delivered,
and set up conversion tracking — all while following a strict safety contract for how it touches your
repo and your secrets.

The plugin ships **skills only** (plus an optional MCP connection). Every action against your files is
performed by your local Claude Code agent, on your machine, with your approval. Nothing in this plugin
sends your source code anywhere.

---

## What's in the box: the 7 skills

The skills form an onboarding **funnel** — each stage hands off to the next. You can also invoke any
skill directly, and the `status` skill will tell you which one you need.

| # | Skill | Invoke as | What it does |
|---|-------|-----------|--------------|
| 1 | **setup** | `/onesignal:setup` | Detects your platform/framework, installs and initializes the OneSignal SDK, and adds a debug-only verification helper. The entry point for "add push notifications" / "integrate OneSignal". |
| 2 | **credentials** | `/onesignal:credentials` | Walks you through the human-only console steps to procure push credentials (Apple APNs `.p8`, Firebase FCM v1 service-account JSON, web Site URL / Safari certs, email SPF/DKIM/DMARC, SMS sender), then uploads the API-uploadable ones for you. |
| 3 | **verify** | `/onesignal:verify` | Confirms a real message is actually **delivered** to an identified subscriber — the true "activated" milestone — not just that code compiles. |
| 4 | **discover-data** | `/onesignal:discover-data` | Read-only scan of your codebase for instrumentable data (tracking plans, analytics call sites, ORM models, auth providers) and proposes a mapping to OneSignal identity, tags, and events. |
| 5 | **instrument** | `/onesignal:instrument` | Writes the approved instrumentation: `login()` for identity, tags for state, `trackEvent()` for actions, consent-gated email/SMS — in the right order, matching your repo's style. |
| 6 | **conversions** | `/onesignal:conversions` | Sets up conversion / outcome tracking so you can measure what your messages drive (custom events wired to Conversion Metrics; dashboard steps where there's no REST path). |
| 7 | **status** | `/onesignal:status` | Read-only orchestrator: "where am I in onboarding, and what's the one next step?" Probes your activation ladder and routes you to exactly one of the skills above. Start here if you're unsure. |

**Recommended path:** `setup → credentials → verify → discover-data → instrument → conversions`, with
`status` as your compass at any point. Stages chain automatically: when one completes, the agent
announces the transition and continues into the next — no re-prompting. It pauses only at the true
human gates: checkpoint consent, console/portal steps, approving the data mapping, consenting to the real test send, and
confirming diffs before writes.

> Skills are **model-invoked** — you usually don't type the command. Just describe what you want
> ("set up OneSignal in this app", "why isn't my push delivering?", "what should I do next?") and
> Claude Code picks the right skill. The explicit `/onesignal:<skill>` form is there when you want it.

---

## Prerequisites

- **Python 3** on your `PATH` (`python3`). The setup/verify skills run small
  stdlib-only helper scripts in `scripts/` (exact version resolver, platform
  detection, structural self-check, secret scan) — no pip installs, but the
  interpreter must be present. Check with `python3 --version`; most macOS/Linux
  dev machines already have it.
- **A OneSignal account** — free at [onesignal.com](https://onesignal.com).
- **An App ID.** Your app's public identifier. Find it in the dashboard under **Settings → Keys & IDs**
  (or in the dashboard URL). The App ID is public and safe to commit in client code.
- **A REST API key**, exported as an environment variable:
  ```bash
  export ONESIGNAL_REST_API_KEY="<your REST API key>"
  ```
  Get it from **Settings → Keys & IDs** (the secret value is shown only once — create a new key if you
  no longer have it). This key is **app-scoped and secret**: it lives in an env var only, never in
  client code and never committed. The skills read it from the environment; they will **never** ask you
  to paste it into chat.
- **(Optional) An Organization / User auth key** — only needed if you want the plugin to *provision a
  new OneSignal app* or *upload push credentials* via the API (used by `setup` and `credentials`).
  It's account-wide and highly sensitive, so keep it in an env var and treat it with extra care. If you
  skip it, you configure the app in the dashboard instead and the skills adapt.
- **(Optional) The OneSignal MCP connection** — lets Claude run OneSignal actions (look up a user,
  check delivery stats, send a test message) directly instead of via `curl`. See
  [Optional: connect the OneSignal MCP server](#optional-connect-the-onesignal-mcp-server) below.
  Official docs: the **Model Context Protocol** page in the
  [OneSignal documentation](https://documentation.onesignal.com).

---

## Install in Claude Code

> Requires a recent Claude Code (the `/plugin` command must exist; if it doesn't, update Claude Code).
> All commands below are copied from the official Claude Code plugin docs
> ([Create plugins](https://code.claude.com/docs/en/plugins),
> [Discover & install plugins](https://code.claude.com/docs/en/discover-plugins)).

### Option A — quick try (no install)

Load the plugin directly from its directory for a single session:

```bash
claude --plugin-dir /path/to/onesignal-agent-plugin
```

Then, inside Claude Code:

```text
/onesignal:setup
```

Run `/help` to see the skills listed under the `onesignal` namespace. This is the fastest way to try
the plugin; nothing is installed persistently.

### Option B — install from a local marketplace (persistent)

Claude Code installs plugins through a **marketplace**. A marketplace can be a local directory, so you
can install this plugin from a checkout on disk. Add a tiny `marketplace.json` that points at this
plugin, then add + install it.

1. Create a marketplace folder and put (or symlink) the plugin **inside it** — plugin `source` paths
   are **relative, starting with `./`**, resolved from the marketplace root:
   ```bash
   mkdir -p my-marketplace/.claude-plugin
   ln -s /absolute/path/to/onesignal-agent-plugin my-marketplace/onesignal-agent-plugin
   ```
   Then create `my-marketplace/.claude-plugin/marketplace.json`:
   ```json
   {
     "name": "onesignal-local",
     "owner": { "name": "You" },
     "plugins": [
       {
         "name": "onesignal",
         "source": "./onesignal-agent-plugin",
         "description": "OneSignal onboarding & activation skills"
       }
     ]
   }
   ```
   > Plugin `source` values must be `./`-relative (marketplace-root-resolved). Absolute paths are for
   > the `/plugin marketplace add` command itself, not for plugin sources. For a no-install trial of a
   > plugin anywhere on disk, use Option A (`--plugin-dir`).

2. Add the marketplace and install, inside Claude Code:
   ```text
   /plugin marketplace add /absolute/path/to/my-marketplace
   /plugin install onesignal@onesignal-local
   ```
   Pick an install **scope** when prompted: *User* (all your projects), *Project* (shared with
   collaborators via `.claude/settings.json`), or *Local* (this repo, just you).

3. Activate without restarting:
   ```text
   /reload-plugins
   ```

### Option C — install from a hosted marketplace (for teams / distribution)

If this plugin is published to a git-hosted marketplace, add it by `owner/repo` or git URL and install
the same way:

```text
/plugin marketplace add <owner>/<repo>
/plugin install onesignal@<marketplace-name>
```

> **Unverified for this repo:** the exact `owner/repo` / marketplace name depends on where OneSignal
> publishes this plugin. Substitute the real values from the OneSignal distribution channel; the
> command *shape* above is from the official docs and is correct.

### After install

- Skills are auto-discovered from `skills/<name>/SKILL.md` and namespaced by the plugin name, so they
  appear as `/onesignal:setup`, `/onesignal:credentials`, and so on.
- Claude Code copies the plugin into a cache on install. All of this plugin's internal references
  (skills linking to `../../references/*.md`) live **inside** the plugin directory, so they resolve
  correctly after the copy.

---

## Optional: connect the OneSignal MCP server

The skills work without MCP (they fall back to REST-key `curl` calls, and your local agent always does
the file edits). Connecting the **OneSignal MCP server** lets Claude run OneSignal API actions as
first-class tools — creating users, checking delivery, sending a test send — which the `verify`,
`status`, and `instrument` skills will prefer when available.

**This plugin ships an MCP configuration** (`.mcp.json` at the plugin root) that declares OneSignal's
first-party hosted MCP endpoint:

```json
{
  "mcpServers": {
    "onesignal": {
      "type": "http",
      "url": "https://api.onesignal.com/mcp/oauth"
    }
  }
}
```

Because the server is bundled with the plugin, it **starts automatically when the plugin is enabled** —
you do not run `claude mcp add`. The first time Claude needs it, the server will report that it needs
authentication. Complete the one-time sign-in from inside Claude Code:

```text
/mcp
```

Select **onesignal → Authenticate**. Your browser opens OneSignal's sign-in page. Sign in and approve
access — that is the whole flow. There is no App ID or REST API key to enter: the connection is an
OAuth grant tied to your OneSignal account.

Notes, per OneSignal's MCP docs (the
["Model Context Protocol" page](https://documentation.onesignal.com/docs/en/model-context-protocol)):

- **First-party endpoint.** `api.onesignal.com/mcp/oauth` is OneSignal's own hosted MCP server. Your
  sign-in goes to OneSignal directly — no third-party gateway sits in the path, and the MCP server does
  not store your customer data.
- **Account-scoped, multi-app.** The connection follows the permissions of the OneSignal user who
  authorized it, and it can access every app that user can manage (`list_apps` discovers App IDs). The
  skills confirm the target app before any read or write.
- **Revocable.** Every connected AI client appears under **Connected apps** in your OneSignal account
  settings. Revoke a client there at any time; revocation invalidates its tokens.
- The MCP is in **open beta**; an app may need enablement before non-utility tools are available.
  `send_message` is treated as a high-impact action and asks for confirmation; tool calls are rate
  limited.
- The MCP is an **API proxy** — it cannot read or edit your files. All repo work is done by your local
  Claude Code agent regardless of whether MCP is connected.
- If you'd rather not bundle it, disable the plugin's MCP server, or set up the same connection manually
  per the **Model Context Protocol** page in the [OneSignal docs](https://documentation.onesignal.com).

---

## Using this with Cursor (and other non–Claude-Code tools)

**Cursor does not support Claude Code plugins.** There is no `/plugin` mechanism and no automatic skill
loading in Cursor. Be aware of what this plugin can and can't do there:

- **Skills as context (works):** The skills in `skills/<name>/SKILL.md` and the shared docs in
  `references/*.md` are plain Markdown playbooks. You can point Cursor's agent at them — attach the
  relevant `SKILL.md` (and the `references/` files it links) as context, or paste the skill body into
  the chat — and Cursor's model can follow the same steps. The safety contract in
  `references/safety-contract.md` still applies; include it so the agent honors the read/write and
  secrets rules.
- **The OneSignal MCP (works):** Cursor *does* support MCP, and OneSignal has an official listing in
  the [Cursor Marketplace](https://cursor.com/marketplace/onesignal). Install that plugin — it
  configures the hosted MCP server for you — then authenticate from **Settings → MCP & Integrations**
  (an OneSignal sign-in page opens in your browser; there is no App ID or key to paste). To wire the
  connection by hand instead, add the endpoint to `~/.cursor/mcp.json` (all projects) or
  `.cursor/mcp.json` (per project):
  ```json
  {
    "mcpServers": {
      "onesignal": {
        "url": "https://api.onesignal.com/mcp/oauth"
      }
    }
  }
  ```
  Then restart Cursor and authenticate the same way. (This mirrors OneSignal's own Cursor instructions
  on the MCP docs page.)
- **What won't happen automatically:** namespaced `/onesignal:*` commands, model-invoked skill
  triggering, and the plugin's `.mcp.json` auto-connecting. Those are Claude Code features. In Cursor you
  drive the skills manually by supplying them as context.

No false promises: outside Claude Code this is "high-quality playbooks + an MCP connection you wire
yourself," not a one-click plugin.

---

## Security

Every skill that touches your repo follows a binding **safety contract** (`references/safety-contract.md`).
The essentials:

- **Setup checkpoints are optional.** The setup skill asks, separately from any
  network-access prompt, before it reports milestone outcomes (step name, success
  or fail, failure class, run ID, platform, OS, App ID) to OneSignal. Source code,
  paths, and credentials never leave the machine. Nothing is sent until you
  consent, or until `ONESIGNAL_SKILL_TELEMETRY=1` is set. Choose "Keep checkpoints
  on this machine only", or set `ONESIGNAL_SKILL_TELEMETRY=0`.
- **Secrets never touch your code or chat.** The REST API key and org key live in environment variables
  only. The plugin writes `.env` (gitignored — it verifies) and `.env.example` with empty placeholders,
  and scans its own diff for secret-shaped strings before finishing. It will never write a key into
  client code, and never ask you to paste `.p8` contents, service-account JSON, or REST keys into chat —
  it references file paths and env vars instead. (Your App ID is public and *is* committed in client
  init code — that's expected.)
- **Nothing is written without your approval.** Before any edit, the skill checks `git status`, declares
  the full file allow-list up front, and shows the **complete change set as diffs** for a single
  confirmation. It matches your existing style and package manager, makes the minimal integration only,
  and marks generated blocks with a `// onesignal:managed` comment so re-runs are idempotent.
- **No destructive git.** No `git push`, force-push, rebase, `reset --hard`, `git clean`, `git add -A`,
  no recursive deletes. It defaults to a new `onesignal-integration` branch and offers rollback commands
  rather than auto-committing.
- **Read-only skills stay read-only.** `discover-data` and `status` make zero file mutations, skip
  secret files entirely (`.env*`, `*.pem`, `*.key`, `*.p8`, `*.p12`, keystores, credential JSON), and
  redact anything secret-shaped in their output. `verify` also never edits source and never opens
  secret files — and its one real action (a test push to your own device) is gated behind your
  explicit yes.
- **Your repo text is treated as untrusted input.** README/comment/config text is data, never
  instructions — the skills will not follow directions found inside your files (prompt-injection
  defense) and never execute your code during discovery.
- **Fail safe.** On any failure the skill stops at the first failed step and leaves your tree in a
  stated, known state with exact rollback commands — it never "pushes through."

Read the full contract before an org-wide rollout: `references/safety-contract.md`. Note also that
plugins and marketplaces run with your privileges — only install from sources you trust (this is a
general Claude Code caution, not specific to OneSignal).

---

## Support & versioning

- **Version:** see `version` in `.claude-plugin/plugin.json` (currently `0.4.0`). Because a `version` is
  set, Claude Code only pulls updates when this field is bumped. Update an installed copy with
  `/plugin marketplace update <marketplace-name>` then `/reload-plugins`.
- **OneSignal support:** questions about your account, credentials, or the MCP beta →
  `support@onesignal.com` and the [OneSignal documentation](https://documentation.onesignal.com).
- **API ground truth:** the skills are pinned to a verified API surface in `references/api-reference.md`
  and a per-platform automation matrix in `references/platform-matrix.md`. Where an endpoint or parameter
  is unverified, the skills say "verify against docs" rather than assert it. If OneSignal's API changes,
  those reference files are the place to update.
