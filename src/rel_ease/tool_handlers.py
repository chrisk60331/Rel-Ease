# tool_handlers.py — not used in the current hardcoded workflow.
#
# The old architecture passed these as Backboard tool definitions and ran a
# multi-turn LLM tool loop. That was over-engineered: the LLM would pause and
# ask the user questions instead of executing. The new approach is:
#
#   1. cli.py drives the full release sequence deterministically.
#   2. assistant.py makes a single LLM call: analyze diff → JSON with
#      (semver_part, commit_summary, release_notes_md, reasoning).
#
# The actual operations now live in:
#   git_ops.py       — git status / diff / add / commit
#   version_bump.py  — pyproject.toml / package.json / Cargo.toml bumps
#   release_build.py — uv build, twine upload, release_notes_write
#
# Keeping this file so git history shows what was removed.
