# Rel-Ease

**Rel-Ease** (`release-cli` on PyPI) is a terminal release manager that pairs a [Click](https://click.palletsprojects.com/) CLI with an AI agent. The LLM reads your diff, proposes SemVer bumps, updates `release_notes.md`, stages commits, and for Python packages runs **`uv build`** and **`twine upload`** — all through explicit tools you control.

## Requirements

- Python 3.11+
- **Python projects:** `uv`, `twine` (and PyPI credentials via `TWINE_USERNAME` / `TWINE_PASSWORD` or standard `twine` config)
- **Node/TypeScript:** `npm` (for `npm install --package-lock-only` after version bumps)
- **Rust:** manual `cargo publish` after the assistant bumps `Cargo.toml` and commits (by design)

## Install

```bash
cd rel-ease
uv sync
uv pip install -e .
# or: uv tool install .
```

## Quick start

```bash

# Sanity-check environment and repo detection
rel-ease doctor .

# Show how the project is classified (no network)
rel-ease detect .

# Run the release assistant (creates/updates agent named `rel-ease`)
rel-ease release .

# Read-only rehearsal
rel-ease release . --dry-run

# Extra guidance for the model
rel-ease release . --hint "User asked for a minor bump for the new API."
```

Optional: set `REL_EASE_ASSISTANT_ID` to pin a specific agent UUID after the first run.

## Repo detection (deterministic)

Order of precedence from the repository root:

1. **Rust** — `Cargo.toml` with a `[package]` version  
2. **Python** — `pyproject.toml` with `[project].version`  
3. **Node/TS** — `package.json` with `version`  

If the kind is **Python**, `uv_build` and `twine_upload` run for real. For **Node** or **Rust**, those tools return a **skipped** result so the model does not pretend PyPI ran.

## Tools exposed to the assistant

| Tool | Role |
|------|------|
| `get_repo_context` | Refresh kind, version file, current version |
| `increment_version` | Bump `patch` / `minor` / `major` or set `explicit_version` |
| `git_diff` | `git diff` with optional `--stat` and path filters |
| `git_status_files` | Porcelain status per path |
| `git_add` | Stage explicit paths |
| `git_commit` | Commit with your message |
| `uv_build` | Clean `dist/` and `uv build` (Python only) |
| `release_notes_update` | Create/update `release_notes.md` (`replace` or `append`) |
| `twine_upload` | Upload artifacts from `dist/` (Python only) |

The language model interprets diffs and drives the sequence; every mutation goes through these tools on your machine.

## Environment

| Variable | Purpose |
|----------|---------|
| `REL_EASE_ASSISTANT_ID` | Optional UUID override |
| `TWINE_USERNAME` / `TWINE_PASSWORD` | PyPI upload (if not using another `twine` config) |

## What Rel-Ease does *not* do

- No automatic `git push`, tags, or GitHub releases (keep using `gh` or CI).
- No `cargo publish` wrapper yet — the assistant will remind you after a Rust bump.

## License

MIT
