# TSG: Phase 0.5 source-lift (`homelab` → `homelab_mcp`)

> **Status:** completed 2026-05-01. Source `homelab` HEAD `5c25c3e9c7cd` lifted into `homelab_mcp`.

## What this TSG covers

The one-time operation that copied the 133-tool MCP monolith from the
private `dragoshont/homelab` repository into the public
`dragoshont/homelab_mcp` repository, byte-faithfully and with a leak
scan, so the public repo can host the OSS-bound code while the homelab
keeps running on the existing image.

## Context (when you would run this again)

You **don't** run Phase 0.5 again. It is a one-shot. If you need a
similar operation (e.g., a new sub-package gets carved out of `homelab`
and needs to migrate to a public home), follow this TSG as the
template — write a new SDD with its own scope, then run the same
preflight → leak-scan → binary-copy → manifest → verify chain.

## What runs

[`tools/lift_phase_0_5.py`](../../../tools/lift_phase_0_5.py) is the
implementation. It:

1. Reads source repo HEAD and asserts `git status --porcelain mcp/` is empty.
2. Enumerates source files via `git ls-files mcp/`.
3. Reads each file as raw bytes (no line-ending normalisation).
4. Computes SHA-256 over the bytes; runs an in-memory leak scan
   (hostnames, RFC1918 IPs outside comments, third-party copyright
   headers, high-entropy tokens).
5. Refuses to write anything if any finding has severity
   `real-secret` or `non-operator-copyright`.
6. Writes each file to the destination via `WriteAllBytes` and re-hashes
   to confirm round-trip equality.
7. Writes `mcp/.lift-manifest.json` with source commit, capture time,
   and per-file SHA-256.
8. Writes `out/Rivet/sdd/phase-0.5-source-lift/leak-scan.json` with the
   full scan result (empty `findings: []` if clean).

## Verification chain

| Gate | Command | What it proves |
|------|---------|----------------|
| G-2 source unchanged | `python tools/validate_inventory.py --source-repo C:\src\homelab` | The source repo's tool name set still matches `docs/migration/tool-inventory.json` (133/29/104). |
| G-5 lift complete | `python tools/verify_lift.py` | The **lifted** `mcp/src/homelab_mcp/server.py` AST-scans to the same 133 tool names. Catches a partial copy that G-2 alone would miss. |
| Test parity | `cd mcp && PYTHONPATH=$PWD/src python -m pytest tests -q` | All 127 tests pass in the new home (102 baseline + 25 new from Phase 0.4 env contract). |
| Manifest round-trip | `python -c "..."` (one-liner in `design.md` §2.5) | Recomputing SHA-256 of every lifted file equals the recorded manifest hash. |

## Diagnostic recipes (when something goes wrong)

### "Hash mismatch on `<path>`"

The destination file's bytes don't match the source. The lift script
aborts on the first mismatch. Cause is almost always git
`core.autocrlf=true` rewriting line endings on text files.

The lift script uses `[System.IO.File]::ReadAllBytes` /
`WriteAllBytes` precisely to avoid this. If you see a hash mismatch:

1. Verify your shell isn't intercepting bytes (`git config --global core.autocrlf input` is recommended on Windows).
2. Inspect the offending file: `python -c "import pathlib,hashlib; print(hashlib.sha256(pathlib.Path('mcp/<file>').read_bytes()).hexdigest())"` and compare to the source side.
3. If the destination is `.gitattributes`-affected (e.g. `* text=auto`), add the path to `.gitattributes` in this repo with `binary` until lift completes.

### "Leak scan: N blocking finding(s)"

The lift refused to commit because the source still contains
operator-specific values. Triage in this order:

1. **`real-secret` private-hostname**: a homelab hostname (e.g.
   `home.hont.ro`) appears in a code path. Move it to env: read via a
   helper in `mcp/src/homelab_mcp/settings.py`, fail loudly if unset
   in deployment.yaml.
2. **`real-secret` rfc1918-ip**: a private IP appears in a non-comment
   non-example context. Same fix: env var.
3. **`non-operator-copyright`**: a `Copyright (c) <name>` or
   `SPDX-License-Identifier` header is present. Either:
   - Confirm the snippet is permissively licensed and document the
     attribution before relifting, OR
   - Rewrite the function originally without the copied snippet.
4. **`high-entropy-token`**: a long base64-ish string in a `.py` file.
   Often a hash literal in a test fixture (low severity); occasionally
   an API key checked in by accident (high). Inspect manually.

After fixing the source (in a new branch in `homelab` with proper SDD),
re-run `python tools/lift_phase_0_5.py --source-repo <path> --dry-run`
until findings are 0.

### "G-5 fail: in inventory but NOT in lifted server.py"

The lift skipped a file or the source file lost a tool. Check:

1. `git diff` in the source repo against the pinned commit.
2. If a tool was legitimately removed in source, update
   `docs/migration/tool-inventory.json` and log the diff in
   `docs/migration/inventory-history.json`.

### "G-5 fail: in lifted server.py but NOT in inventory"

Source added a tool we didn't anticipate. Same fix path: update
inventory + history.

## What NOT to do

- Do **not** edit lifted files in `homelab_mcp/mcp/` directly. They are
  byte-identical with source by construction; future re-lifts (if
  needed) will overwrite local edits. Make changes in `homelab` and
  re-run the lift, OR — once Phase 1+ packages exist — make changes in
  `packages/homelab-mcp-{server}/` instead, which are NOT subject to
  byte-equality.
- Do **not** disable `tools/verify_lift.py`. If it fails, inventory is
  drifting from code; that's the bug to fix.
- Do **not** commit the lifted tree without `mcp/.lift-manifest.json`.
  The manifest is how Phase 0.7 (cluster cutover) and Phase 0.8
  (source cleanup) prove provenance.

## Related

- [`out/Rivet/sdd/phase-0.5-source-lift/`](../../../out/Rivet/sdd/phase-0.5-source-lift/) — full SDD trail
- [`docs/migration/tool-inventory.json`](../../migration/tool-inventory.json) — pinned snapshot
- [`docs/migration/inventory-history.json`](../../migration/inventory-history.json) — re-pin log
- [`mcp/.lift-manifest.json`](../../../mcp/.lift-manifest.json) — per-file SHA-256 manifest
