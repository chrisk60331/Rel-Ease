### v0.2.0 - Minor Release

- Initial commit of rel-ease tool including all base modules.

## v0.2.1

- Updated assistant.py with refactored functions.
- Improved parsing logic for JSON extraction.

## v0.2.2

- Added `uv.lock` to manage dependencies.

## v0.2.3

["Enhanced error message display for 'twine upload' failures.", 'Filtered build artifacts to only include .whl and .gz files.']

## v0.2.4

['Rename project and package name to `release-cli` for consistency.', 'Update `pyproject.toml` and `uv.lock` to reflect new package name.']

## v0.2.5

- Updated `release-cli` package version in `uv.lock` file for consistency.

## v0.3.0

- Added git tagging and pushing capability to the release command.
- Improved error handling and user feedback for release steps.

## v0.4.0

- Enhanced `git_push` to handle missing upstream branches.
- Introduced `git_current_branch` function for better branch management.

## v0.5.0

['Integrate GitHub release creation in the release process.', 'Updated dependency in uv.lock.']

## [0.6.0] — 2026-04-02

- Enhanced release notes format with current date and package name.
- Extracted and normalized bullet point logic for release notes.
- Included installation command in GitHub release for Python projects.

---
*Released by [Rel-Ease](https://github.com/chrisk60331/Rel-Ease) · [release-cli](https://pypi.org/project/release-cli/0.6.0/)*

## [0.7.0] — 2026-04-16

- Switched AI backend from `backboard-sdk` to `ai-layer` for all LLM interactions
- Updated API key environment variable from `BACKBOARD_API_KEY` to `AI_LAYER_KEY` / `AI_LAYER_API_KEY`
- Updated agent ID environment variable from `REL_EASE_ASSISTANT_ID` to `REL_EASE_AGENT_ID`
- Added `AI_LAYER_URL` environment variable support for configuring the ai-layer base URL
- Added `REL_EASE_MODEL` environment variable to override the default LLM model
- Updated `doctor` command to check for `AI_LAYER_KEY` instead of `BACKBOARD_API_KEY`

---
*Released by [Rel-Ease](https://github.com/chrisk60331/Rel-Ease) · [release-cli](https://pypi.org/project/release-cli/0.7.0/)*
