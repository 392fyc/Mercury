# UPSTREAM — gpt-image-2

## Origin

Adapter wraps [`wuyoscar/gpt_image_2_skill`](https://github.com/wuyoscar/gpt_image_2_skill)
via `uvx`-pinned-SHA. No upstream source is vendored — `invoke.py` is
original Mercury code that shells out to `uvx --from git+<repo>@<SHA>
gpt-image <args>`.

## Pin

| Field | Value |
|---|---|
| Repo | `wuyoscar/gpt_image_2_skill` (MIT) |
| Pinned SHA | `6fdd7243dc9605efcf6d66e9394d3d10fc5141f6` |
| Pinned date | 2026-05-08 |
| Upstream version | `gpt-image-cli` v0.2.0 |
| Console script | `gpt-image` (entry: `gpt_image_cli.cli:main`) |
| Python required | ≥ 3.11 |
| Upstream deps | `openai>=1.55`, `python-dotenv>=1.0` |

SHA verified against `gh api repos/wuyoscar/gpt_image_2_skill/commits/main`
on 2026-05-08 during S87 Phase 2 pre-conditions verify (Issue #351).

## Drift policy

`scripts/upstream-drift-check.sh` compares the upstream `pyproject.toml`
blob SHA at the pinned import SHA against the same file at upstream
`HEAD`. The manifest entry sets `upstream_path: "pyproject.toml"` because
that file is the canonical version/deps/entry-point contract — any change
there is a strong signal that the adapter's pin needs review. The full
upstream tree is not file-by-file tracked (we mount via uvx, not by
vendoring source).

When drift-check reports `CHANGED`: review the upstream diff for license /
scope changes (e.g. CC BY 4.0 prompt-material subdirs under top-level
MIT — see ADR §2 / §7.2.1) before bumping the SHA in `invoke.py` and the
manifest.

## License

Upstream `gpt_image_2_skill` repo is MIT. Mercury invokes it as a runtime
dependency via `uvx`; no upstream source is redistributed by this
repository. Upstream prompt-material subdirs (gallery images, etc.) may
carry CC BY 4.0 or other licenses — those are out of scope for this
adapter (only the `gpt-image` Python package is invoked).

Cherry-pick protocol §1–6 compliance is recorded in the import commit
message and `.mercury/state/upstream-manifest.json` entry.
