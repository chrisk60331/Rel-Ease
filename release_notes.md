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
