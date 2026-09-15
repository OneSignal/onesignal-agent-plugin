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
| `scripts/` | Deterministic helpers: version resolution, platform detection, secret scan, structural verification, checkpoint transport. Two files are development tooling and never ship to customers: `compile_check_ios.sh` and `package_directory_bundle.py`. |
| `.claude-plugin/plugin.json` | Plugin manifest. The `version` field controls when Claude Code pulls updates. |
| `.claude-plugin/marketplace.json` | Marketplace catalog. This repository is its own marketplace, named `onesignal`. |
| `.codex-plugin/plugin.json` | Codex manifest. The `interface` block holds the listing copy, legal URLs, and brand assets. |
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
  with the same walk-up rule: start in the directory that contains the `SKILL.md`, and walk
  up to the first directory that contains a `scripts/` folder, and stop at the filesystem
  root if there is none. Commands read
  `bash <plugin>/scripts/checkpoint.sh ...`. The rule holds in this repository tree, where
  it resolves to the repository root, and in the self-contained bundle for the OpenAI
  directory, where each skill folder carries its own `scripts/`. Never build a path from a
  host environment variable such as `${CLAUDE_PLUGIN_ROOT}`: Claude Code substitutes it in
  skill text, but Codex and other agents read the skill text verbatim and the path breaks.
- **Shared files have one spelling in skill text.** A skill refers to a shared script only as
  `<plugin>/scripts/<name>`, and to a reference only as `[…](../../references/<name>.md)`.
  No other path in a skill may contain `../`. `scripts/package_directory_bundle.py --check`
  enforces this convention, and any other spelling fails the check. The convention is what
  lets the packager build the self-contained bundle with one rewrite (see "The OpenAI
  directory bundle" below).
- **Secrets never enter the repository.** No REST API keys, org keys, `.p8` contents, or
  service-account JSON in any file or example. The App ID is public and can appear in examples.
- **`checkpoint.sh` owns the run ID.** Skills never read, write, or reset `.onesignal/run_id`.

## How to verify a change

This repository has no test suite. The eval harness lives in an internal repository and runs
the real agent against fixture apps. `.github/workflows/ci.yml` runs checks 1 to 4 below on
every pull request, runs check 5 when an iOS template or its script changes, and adds
`claude plugin validate` and a Python 3.7 pass. Branch protection on `main` must require
the `checks` and `python-floor` jobs. For local checks:

1. Python scripts: `python3 -m py_compile scripts/*.py`.
2. JSON files: `python3 -m json.tool` on `.claude-plugin/plugin.json`,
   `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, and `.mcp.json`. Then
   confirm the 3 version fields agree (see "Releases"); `package_directory_bundle.py --check`
   in step 4 fails on a mismatch.
3. Skill links: confirm every relative link in a changed `SKILL.md` resolves to a file. The
   bundle gate in step 4 checks every link in `skills/` for you. Then run the portability
   check; it must print nothing:
   `grep -rn 'CLAUDE_PLUGIN_ROOT\|PLUGIN_ROOT}\|/Users/\|/home/' skills/ references/ --include='*.md'`
4. Bundle gate: `python3 scripts/package_directory_bundle.py --check`. The check builds the
   directory bundle in a temporary directory and fails on any path that does not resolve
   inside its skill folder. Run it after any change under `skills/`, `references/`, or
   `scripts/`.
5. iOS templates: run `scripts/compile_check_ios.sh` when a file under
   `skills/setup/assets/ios/` changes.
6. Behavior changes: load the plugin with `claude --plugin-dir .` and run the changed skill
   against a scratch project.

If a change affects setup, credentials, or verify behavior, ask for an eval run before merge.
Do not trust a skill edit on read-through alone.

## The OpenAI directory bundle

The OpenAI plugin directory does not accept the plugin tree. Its Skills tab accepts a zip
whose top level is a directory of self-contained skill roots. `scripts/package_directory_bundle.py`
builds that artifact from this tree at release time:

- It copies each skill folder to the top level of the bundle (`setup/`, `credentials/`,
  `verify/`), and gives each one its own `references/`, `endpoint.conf`, and the runtime
  scripts that skill calls (a table in the script names them).
- It rewrites `../../references/` to `references/` in the skill Markdown. The `<plugin>`
  walk-up rule resolves to the skill folder without a rewrite.
- It runs a structural gate on the result: every link and every `<plugin>/scripts/<name>`
  call must resolve inside its skill folder, and the 3 version fields must agree.

Commands:

```bash
python3 scripts/package_directory_bundle.py --check                 # gate only; writes nothing
python3 scripts/package_directory_bundle.py --out /tmp/bundle       # unzipped bundle, for a local load
python3 scripts/package_directory_bundle.py                         # onesignal-skills-<version>.zip in the cwd
python3 scripts/package_directory_bundle.py --ref 1.1.0             # build from a tag instead of the working tree
python3 scripts/package_directory_bundle.py --self-test             # gate against known-good and known-bad trees
```

The build step is optional for local work. It touches only the directory artifact; the
repository layout, the Claude Code install, and the Codex marketplace install do not use it.
To load the bundle by hand, put the `--out` result under `skills/` next to a copy of
`.claude-plugin/plugin.json` and run `claude --plugin-dir <that directory>`.

## Releases

The plugin version lives in 3 places. Bump all 3 together — they must never disagree:

- `version` in `.claude-plugin/plugin.json` — Claude Code pulls updates only when this
  field changes.
- `version` in `.codex-plugin/plugin.json`.
- `PLUGIN_VERSION` in `scripts/checkpoint.sh` — every checkpoint reports this value as
  `skill_version`.

Keep the version current: when a change set affects plugin behavior (skills, scripts,
references, templates, or manifests), bump the version in that same change set. Do not
leave the version stale. One bump per release is enough: if an unreleased change set on
`main` already moved the version past the last release, later change sets in the same
release do not bump it again.

Each release ships 2 artifacts: the tagged tree (Claude Code and the Codex marketplace
install from it) and `onesignal-skills-<version>.zip` (the OpenAI directory upload). The
tag is the bare version (`1.1.0`, no `v` prefix), the same as the other SDK repositories.

### The release process

The workflows in `.github/workflows/` reuse `OneSignal/sdk-shared`:

1. Run **Create Release PR** (`create-release-pr.yml`) from the Actions tab. It reads the
   merged PR titles since the latest stable version tag and picks the bump (`feat:` →
   minor, `fix:`/`perf:` → patch, `!:` → major), or takes a version override. It creates or
   rebases `rel/<version>`, writes the version into the 3 files, commits
   `chore: Release <version>`, and opens the `chore: Release <version>` PR with release
   notes built from the PR titles.
2. Before merge, the release PR needs 2 eval runs, both recorded in the PR: one against the
   repository tree, and one against the bundle built from the same commit. The per-PR gate
   `package_directory_bundle.py --check` runs in CI without an agent and does not replace
   the bundle-arm eval. Smoke-test the tree in Claude Code and Codex by hand.
3. Merge the release PR. `cd.yml` creates the GitHub Release and the tag from the PR body,
   builds `onesignal-skills-<version>.zip` from the tag, and attaches it to the Release.
4. `linear-deployed.yml` moves every `SDK-####` in the release body to Deployed.
5. Download the zip from the Release page and submit it on the Skills tab of the OneSignal
   listing in the OpenAI directory. Directory installs pin to the reviewed snapshot, so
   every release needs a new submission.

The automation needs the org secret `GH_PUSH_TOKEN` granted to this repository. Without it
the workflows fall back to `github.token`: `ci.yml` does not run on the release PR that
the workflow opens, and the Release that `cd.yml` creates does not trigger
`linear-deployed.yml`.

`cd.yml` runs on every merged PR whose title starts with `chore: Release `, with or without
the token. Do not tag or create the Release by hand after such a merge; the workflow does
both, and a tag that already exists makes it skip the Release and the zip. To release by
hand instead, open the release PR with a different title (for example
`chore: manual release <version>`), merge it, then run
`git tag <version> && git push origin <version>`, create the GitHub Release from the tag,
build the zip with `python3 scripts/package_directory_bundle.py --ref <version>`, and
attach it. If only the zip step of `cd.yml` failed, build the zip the same way and attach
it with `gh release upload <version> onesignal-skills-<version>.zip`.

## Git conventions

- Commit subjects use conventional prefixes: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`.
- Use Simplified Technical English in all commits, comments, and printed messaging used by any skills.
- Do not reference internal ticket IDs inside skills, scripts, or references. This repository
  is customer-facing. Ticket links belong in the PR description.
