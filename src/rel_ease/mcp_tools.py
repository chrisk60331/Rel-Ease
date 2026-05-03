"""MCP tool schemas and dispatch for Rel-Ease."""

from __future__ import annotations

from typing import Any

from rel_ease.api import ReleaseAPI

_api = ReleaseAPI()


def release(directory: str, version: str) -> dict[str, Any]:
    return _api.release(directory=directory, version=version)


def get_version(directory: str) -> dict[str, Any]:
    return _api.get_version(directory=directory)


def get_incremental_version(directory: str) -> dict[str, Any]:
    return _api.get_incremental_version(directory=directory)


def describe_diff(directory: str) -> dict[str, Any]:
    return _api.describe_diff(directory=directory)


def list_recent_releases() -> list[dict[str, Any]]:
    return _api.list_recent_releases()


DISPATCH = {
    "release": release,
    "get_version": get_version,
    "get_incremental_version": get_incremental_version,
    "describe_diff": describe_diff,
    "list_recent_releases": list_recent_releases,
}

TOOLS = [
    {
        "function": {
            "name": "release",
            "description": (
                "Run the full deterministic release workflow for a project: set the requested "
                "semantic version, write release notes, commit, tag, push, create a GitHub "
                "release, and publish Python packages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Project directory name under REL_EASE_PROJECTS_ROOT.",
                    },
                    "version": {
                        "type": "string",
                        "description": "New semantic version greater than the current version, e.g. 1.2.3.",
                    },
                },
                "required": ["directory", "version"],
                "additionalProperties": False,
            },
        }
    },
    {
        "function": {
            "name": "get_version",
            "description": "Get the detected project type and current semantic version for a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Project directory name under REL_EASE_PROJECTS_ROOT.",
                    },
                },
                "required": ["directory"],
                "additionalProperties": False,
            },
        }
    },
    {
        "function": {
            "name": "get_incremental_version",
            "description": "Return the deterministic next patch version for a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Project directory name under REL_EASE_PROJECTS_ROOT.",
                    },
                },
                "required": ["directory"],
                "additionalProperties": False,
            },
        }
    },
    {
        "function": {
            "name": "describe_diff",
            "description": (
                "Read staged and unstaged git diffs for a project and return an LLM-written "
                "summary, release notes, commit summary, and risk notes. This is read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Project directory name under REL_EASE_PROJECTS_ROOT.",
                    },
                },
                "required": ["directory"],
                "additionalProperties": False,
            },
        }
    },
    {
        "function": {
            "name": "list_recent_releases",
            "description": "List recent tagged releases across git projects under REL_EASE_PROJECTS_ROOT.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    },
]
