# AGENT.md

Guidance for coding agents that work on this repository. Read this file before you change anything.

## What this repository is

This repository is a customer-facing Claude Code plugin. The plugin onboards a customer's own
codebase onto OneSignal. The plugin ships skills (Markdown playbooks), deterministic Python
scripts, and reference documents. The plugin contains no build step and no application code.

Two audiences read this repository:

- **Customers' agents** run the skills against customer repositories.
- **The OneSignal Engineering team** edits the skills, scripts, and references.

`README.md` is the customer-facing document. `STATUS.md` is the team-facing orientation
document. Keep both current when your change affects what they describe.

## Repository layout

| Path | Contents |
|------|----------|
| `skills/<name>/SKILL.md` | One skill per directory. Frontmatter holds `name` and `description`. Supporting docs sit beside the `SKILL.md`. |
| `skills/setup/assets/` | Integration templates per platform. Every SDK API call in a template was validated against SDK source. |
| `references/` | Shared contracts and verified facts. Skills link to them with relative paths. |
| `scripts/` | Deterministic helpers: version resolution, platform detection, secret scan, structural verification, checkpoint transport. |
| `.claude-plugin/plugin.json` | Plugin manifest. The `version` field controls when Claude Code pulls updates. |
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
- **Scripts are Python 3, stdlib only.** No pip installs. `scripts/checkpoint.sh` is POSIX shell.
- **Version pins are exact.** Skills and templates never emit a version range.
  `scripts/resolve_sdk_version.py` resolves the pin.
- **Relative links stay inside the plugin directory.** Claude Code copies the plugin into a
  cache on install. A skill link such as `../../references/api-reference.md` must resolve after
  the copy. Never link outside the repository root.
- **Secrets never enter the repository.** No REST API keys, org keys, `.p8` contents, or
  service-account JSON in any file or example. The App ID is public and can appear in examples.
- **`checkpoint.sh` owns the run ID.** Skills never read, write, or reset `.onesignal/run_id`.

## How to verify a change

This repository has no test suite. The eval harness lives in an internal repository and runs
the real agent against fixture apps. For local checks:

1. Python scripts: `python3 -m py_compile scripts/*.py`.
2. JSON files: `python3 -m json.tool` on `.claude-plugin/plugin.json`,
   `.claude-plugin/marketplace.json`, and `.mcp.json`.
3. Skill links: confirm every relative link in a changed `SKILL.md` resolves to a file.
4. Behavior changes: load the plugin with `claude --plugin-dir .` and run the changed skill
   against a scratch project.

If a change affects setup, credentials, or verify behavior, ask for an eval run before merge.
Do not trust a skill edit on read-through alone.

## Releases

Bump `version` in `.claude-plugin/plugin.json` when a change should reach installed copies.
Claude Code pulls updates only when this field changes.

## Git conventions

- Commit subjects use conventional prefixes: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.
- Use Simplified Technical English in all commits, comments, and printed messaging used by any skills.
- Do not reference internal ticket IDs inside skills, scripts, or references. This repository
  is customer-facing. Ticket links belong in the PR description.
