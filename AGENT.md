# AGENT.md

Guidance for coding agents that work on this repository. Read this file before you change anything.

## What this repository is

This repository is a customer-facing Claude Code plugin. The plugin onboards a customer's own
codebase onto OneSignal. The plugin ships skills (Markdown playbooks), deterministic Python
scripts, and reference documents. The plugin contains no build step and no application code.

Two audiences read this repository:

- **Customers' agents** run the skills against customer repositories.
- **The OneSignal Engineering team** edits the skills, scripts, and references.

`README.md` is the customer-facing document. Keep it current when your change affects what
it describes. Roadmap and planning notes live in the internal project tracker, not in this
repository.

## Repository layout

| Path | Contents |
|------|----------|
| `skills/<name>/SKILL.md` | One skill per directory. Frontmatter holds `name` and `description`. Supporting docs sit beside the `SKILL.md`. |
| `skills/setup/assets/` | Integration templates per platform. Every SDK API call in a template was validated against SDK source. |
| `references/` | Shared contracts and verified facts. Skills link to them with relative paths. |
| `scripts/` | Deterministic helpers: version resolution, platform detection, secret scan, structural verification, checkpoint transport. |
| `.claude-plugin/plugin.json` | Plugin manifest. The `version` field controls when Claude Code pulls updates. |
| `.claude-plugin/marketplace.json` | Marketplace catalog. This repository is its own marketplace, named `onesignal`. |
| `.codex-plugin/plugin.json` | Codex manifest. The `interface` block holds the listing copy, legal URLs, and brand assets. |
| `.cursor-plugin/plugin.json` | Cursor manifest. `mcpServers` points at `.mcp.json`, because Cursor looks for `mcp.json` by default. |
| `assets/` | Brand assets for the listings. The files come from the official OneSignal media kit. |
| `.mcp.json` | Declares the hosted OneSignal MCP endpoint. |
| `endpoint.conf` | The checkpoint ingestion endpoint. The comment block in the file explains the path. |

## Binding contracts

Two reference files are contracts, not documentation. Skills must obey them, and edits to a
skill must not contradict them:

- `references/safety-contract.md` — how skills touch customer repositories and secrets.
- `references/telemetry-contract.md` — the milestone vocabulary and the checkpoint rules.

If your change needs a new rule, change the contract first, then the skills.

## Invariants

Do not break these without a decision from the team:

- **API ground truth lives in `references/api-reference.md`.** If an endpoint or parameter is
  not verified there, the skill must say "verify against docs" instead of asserting it.
- **Scripts are Python 3.7+, stdlib only.** No pip installs. The floor comes from
  `subprocess.run(capture_output=True)` in `scripts/scan_secrets.py`; if you add a construct
  that needs a newer Python, raise the floor here, in the setup preflight, and in the README
  together. Two scripts are bash:
  `scripts/checkpoint.sh` and `scripts/compile_check_ios.sh` (the compile gate for the iOS
  templates — do not rewrite it in another language).
- **Version pins are exact.** Skills and templates never emit a version range.
  `scripts/resolve_sdk_version.py` resolves the pin.
- **Relative links stay inside the plugin directory.** Claude Code copies the plugin into a
  cache on install. A skill link such as `../../references/api-reference.md` must resolve after
  the copy. Never link outside the repository root.
- **Script paths in skills use the `<plugin>` placeholder.** Every skill defines `<plugin>`
  as the directory two levels above its `SKILL.md`, and commands read
  `bash <plugin>/scripts/checkpoint.sh ...`. Never build a path from a host environment
  variable such as `${CLAUDE_PLUGIN_ROOT}`: Claude Code substitutes it in skill text, but
  Codex and other agents read the skill text verbatim and the path breaks.
- **Secrets never enter the repository.** No REST API keys, org keys, `.p8` contents, or
  service-account JSON in any file or example. The App ID is public and can appear in examples.
- **`checkpoint.sh` owns the run ID.** Skills never read, write, or reset `.onesignal/run_id`.

## How to verify a change

This repository has no test suite. The eval harness lives in an internal repository and runs
the real agent against fixture apps. For local checks:

1. Python scripts: `python3 -m py_compile scripts/*.py`.
2. JSON files: `python3 -m json.tool` on `.claude-plugin/plugin.json`,
   `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`,
   `.cursor-plugin/plugin.json`, and `.mcp.json`.
3. Skill links: confirm every relative link in a changed `SKILL.md` resolves to a file.
   Then run the portability check; it must print nothing:
   `grep -rn 'CLAUDE_PLUGIN_ROOT\|PLUGIN_ROOT}\|/Users/\|/home/' skills/ references/ --include='*.md'`
4. iOS templates: run `scripts/compile_check_ios.sh` when a file under
   `skills/setup/assets/ios/` changes.
5. Behavior changes: load the plugin with `claude --plugin-dir .` and run the changed skill
   against a scratch project. For Cursor, symlink the checkout into
   `~/.cursor/plugins/local/` and reload the window.

If a change affects setup, credentials, or verify behavior, ask for an eval run before merge.
Do not trust a skill edit on read-through alone.

## Releases

The plugin version lives in 4 places. Bump all 4 together — they must never disagree:

- `version` in `.claude-plugin/plugin.json` — Claude Code pulls updates only when this
  field changes.
- `version` in `.codex-plugin/plugin.json`.
- `version` in `.cursor-plugin/plugin.json`.
- `PLUGIN_VERSION` in `scripts/checkpoint.sh` — every checkpoint reports this value as
  `skill_version`.

Keep the version current: when a change set affects plugin behavior (skills, scripts,
references, templates, or manifests), bump the version in that same change set. Do not
leave the version stale.

## Git conventions

- Commit subjects use conventional prefixes: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.
- Use Simplified Technical English in all commits, comments, and printed messaging used by any skills.
- Do not reference internal ticket IDs inside skills, scripts, or references. This repository
  is customer-facing. Ticket links belong in the PR description.
