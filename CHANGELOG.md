# Changelog

All notable changes to the Nowledge Mem Google Antigravity plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.3] - 2026-07-30

### Added
- Added `restore-merge` subcommand in the skill manager CLI (`manage_skills.py`) to reverse accidental skill merges.
- Enforced proper gating classifications in `nmem-gate.py` to auto-allow read-only MCP queries and properly gate new mutations/destructive actions.

### Fixed
- Added Git worktree and submodule compatibility by resolving `.git` file pointers during skill installations.
- Implemented robust token rotation auto-reload and retries on REST HTTP 401/403 errors.

## [0.1.2] - 2026-06-23

### Added
- Implemented `nmem-propose-skill` agent skill to allow creating new skill submissions or proposing skill improvements to Nowledge Mem.
- Guided creation/improvements via user-facing review drafts and interactive "Proceed" button approvals.

## [0.1.1] - 2026-06-18

### Added
- Implemented `nmem-manage-skills` agent skill.
- Added `manage_skills.py` script to list, recommend, compile, and install skills locally.
- Added local Git exclude support via `.git/info/exclude` to support ignoring user-specific skills.

## [0.1.0] - 2026-06-12

### Added
- Initial release of the `nowledge-mem-google-antigravity` plugin.
- Added `plugin.json`, `mcp_config.json`, and `hooks.json` mapping for Google Antigravity.
- Ported memory rules to `rules/nowledge-mem.md`.
- Implemented `PreInvocation` hook in `hooks/session-start.mjs` to inject Context Bundle / Working Memory startup briefings.
- Implemented `Stop` hook in `hooks/session-end.mjs` to extract conversation logs from `transcript.jsonl` and sync them into `nmem`.
- Implemented validation and packaging scripts (`scripts/validate-plugin.mjs`, `scripts/package-plugin.mjs`).
- Ported the 5 core memory skills: `distill-memory`, `read-working-memory`, `save-handoff`, `save-thread`, and `search-memory`.
