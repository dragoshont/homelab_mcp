# [Rivet] state.json artifact contains workstation-local paths and placeholder values

**Tool / Version:** `rivet 2.9.23` (`Azure.Rivet` from `https://github.com/azure-core/upgrade-assistant`)

**Affected file:** `out/Rivet/sdd/<sdd-name>/state.json` (created by `rivet sdd init`, kept current by `rivet sdd step`)

## Summary

Two related issues with the `state.json` artifact that Rivet writes during the SDD workflow. Both surfaced during a real PR review (CodeRabbit on `dragoshont/homelab_mcp#15`), and both are blocked from being fixed downstream by Rivet's own operational rule that forbids hand-editing files under `out/Rivet/sdd/`.

This issue is filed because **the agent that addresses this in the future will not have the conversation context that surfaced it.** All facts needed to reproduce and fix are below.

## Issue 1 — `wikiRepoPath` leaks the developer's absolute filesystem path

### Reproduction

1. On a fresh checkout, run `rivet sdd init --name my-feature --issue 1` (any name).
2. Open `out/Rivet/sdd/my-feature/state.json`.
3. Find the `wikiRepoPath` field. Observed value (Windows example):
   ```json
   "wikiRepoPath": "C:\\src\\upgrade-assistant-wiki",
   ```
   On macOS/Linux it would be `/home/<user>/...` or similar.

### Why this is a problem

- The artifact is committed to git as part of normal SDD practice (so reviewers can see what was approved).
- The path embeds the developer's username and local directory layout. CodeRabbit (and any other secret-leak / artifact scanner) flags this as workstation metadata leak.
- Reproducible: every new SDD run on a different machine writes a different absolute path, producing churn in the committed artifact for no semantic reason.

### Suggested fix

Pick one (in order of preference):

1. **Omit the field entirely.** Wiki path is derived from `rivet init` config and is not part of the SDD's contract; consumers of `state.json` don't need it persisted per-SDD.
2. **Store a relative path** (relative to the repo root) when the wiki sits under the repo. Otherwise:
3. **Store an environment-variable token** like `${WIKI_REPO_PATH}` or `<wiki>` placeholder, and resolve at read time.
4. **Sanitise on write.** Strip leading drive/home prefix; substitute `~` or `<HOME>`.

## Issue 2 — `trackedTests` ships with `["ID"]` placeholder

### Reproduction

Same setup as above. After `rivet sdd init`:
```json
"trackedTests": [
  "ID"
]
```
After completing all 11 SDD steps with full test evidence, the value persists unchanged.

### Why this is a problem

- `"ID"` looks like template/placeholder content to any consumer (CodeRabbit flagged it as such).
- If downstream tooling consumes `trackedTests` as a list of test identifiers (which the field name strongly implies), `"ID"` is malformed data — not a real test ID and not an explicit empty signal.
- Workflows that try to count/iterate tracked tests get `len(trackedTests) == 1` with garbage content.

### Suggested fix

- **Initialise as `[]`** when no tests are tracked at init time.
- **Populate at Step 7 (Build + Test)** from the design doc's test plan, or accept user-provided test IDs via `rivet sdd step --complete --evidence "..."`.
- **Or:** rename the field to `trackedTestsNote` if it's free-form, but a typed empty list is cleaner.

## Why we can't patch this in the consuming repo

Rivet's own operational rule (loaded into every agent session via the `rivet` skill):

> **NEVER directly edit any file in `out/Rivet/sdd/{name}/`.** This includes
> `state.json`, `verify-result.json`, … Only `rivet sdd` CLI commands may
> modify workflow state and artifacts.

And the CLI enforces this via a checksum guard: hand-editing `state.json` produces

> `BLOCKED: state.json was modified outside the CLI (checksum mismatch).
>  Manual tampering detected. Delete the workflow and re-initialize.`

So the only fix path is upstream in Rivet itself.

## Repro details

| Item | Value |
|---|---|
| Rivet version | 2.9.23 |
| OS | Windows 11 |
| Workflow | `rivet sdd init --name fastapi-phase1 --issue 0` |
| Steps that touched state.json | All 11 (init through F10 compliance) |
| Artifacts where the leak appears | `out/Rivet/sdd/fastapi-phase1/state.json` lines 108 (`wikiRepoPath`) and 113-115 (`trackedTests`) |
| External evidence | CodeRabbit review comments on https://github.com/dragoshont/homelab_mcp/pull/15 (the only 2 of 9 actionable comments we could not address — see PR review reply for context) |

## Acceptance criteria for the fix

A change to Rivet 2.9.x or 2.10 that:
1. Removes the absolute-path leak from `wikiRepoPath` (or omits the field) on **all 3 OSes** (Windows, macOS, Linux).
2. Initialises `trackedTests` to `[]` (not `["ID"]`) on `rivet sdd init`.
3. Maintains backwards compat: existing in-flight SDD workflows must continue to load (graceful migration of older `state.json` formats).
4. Re-runs the integration tests in `Azure.Rivet.Tests` (or the equivalent) covering `state.json` round-trip.

## Workaround for users on current 2.9.23

None at the user level. Hand-editing the file trips the checksum guard. The PR reviewer must accept these two artifact issues as a known upstream-tracked limitation. Document in the PR description with a link to this issue.
