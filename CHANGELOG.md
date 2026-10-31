# Changelog

All notable changes to the Nowledge Mem Google Antigravity plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Offline session retry now removes empty queue markers, preserves malformed queues as timestamped recovery files, and avoids overlapping workers without letting a recycled process ID block retries forever.
- Project-specific Space settings now follow the workspace opened in Antigravity, including for globally installed plugins whose hooks run from the plugin directory. Invalid project settings are reported instead of silently sending memories to Default.
- Hooks and skill scripts now run on the macOS system Python 3.9. Previously every hook failed there, and the PreToolUse fallback printed invalid JSON, so Antigravity aborted every terminal command and MCP call the hook matches.
- Python older than 3.9 is not supported. The hooks now say so and try `python` next; with no usable Python, Antigravity asks for confirmation before each matched tool call, even one the user chose to Always Allow, instead of aborting it. A PreToolUse hook that fails while running answers the same way, and the other hooks also answer with valid JSON.
- The PreToolUse gate no longer auto-allows every `mem_fs` command. Reads (`ls`, `cat`, `stat`, `find`, `grep`, `recall`, `capabilities`) are still allowed. `rm` and `delete` now require confirmation every time, like other deletes, and so does a command the gate cannot classify. `write` follows the same rule as other memory writes.
- The PreToolUse gate now requires confirmation every time for a tool call it cannot read, instead of allowing it.
- On Windows with a code page such as Chinese GBK, the hooks now read payloads that contain non-ASCII text, such as a user name in a path. They used to fail to decode them, so the PreToolUse gate allowed the call and Stop returned without saving the session.

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
