# Developer Agent Guidelines for `nowledge-mem-google-antigravity`

Welcome! This document outlines the project architecture, developer workflows, performance constraints, and UI guidelines for agents contributing to this repository.

---

## 1. Project Stack & Architecture

This plugin is a hybrid integration of the user's Nowledge Mem knowledge base and Google Antigravity:
* **Lifecycle Hooks**: Triggered before/after sessions and tool calls to inject context (Context Bundle / Working Memory) and save transcripts.
* **MCP Server Connection**: Exposes structured JSON tools (`nowledge-mem` server) for low-friction memory read/write operations.
* **Agent Skills**: Bundled markdown instructions (`skills/`) that teach agents when and how to search, distill, or checkpoint progress.

---

## 2. Hook Development Constraints

When modifying or adding hook scripts under `hooks/` and configuring them in `hooks.json`, adhere to the following rules:

* **POSIX Path Safety**: Do NOT use dynamic path variables (such as `${extensionPath}`) inside `hooks.json`. They are not supported by the Antigravity hook runner. Keep invocations generic and POSIX-compliant (e.g. `python3 hooks/session-start.py || python hooks/session-start.py || echo {}`).
* **Cross-Platform Compatibility (Windows & WSL Bridging)**: All hooks that run CLI commands must route through `nmem_shared.run_nmem_command()` to dynamically handle Windows shims (`nmem.cmd`), WSL path translations (mapping `/mnt/c/...` to `C:\...`), and command-wrapping (`cmd.exe /s /c`).
* **Log Flush Retry Backoff**: When parsing transcripts or saving threads (such as in `hooks/session-end.py`), wrap execution in a backoff delay loop `(0.0, 0.5, 1.5, 3.0)` to ensure that log buffers written asynchronously by the host are fully written to `transcript.jsonl`.
* **Latency Optimization**: Spawning subprocesses adds significant overhead (~300-500ms).
  - Pre-invocation, pre-tool, and stop hooks must run quickly (<50ms).
  - In hook scripts, prefer direct HTTP requests to the Nowledge Mem server using python's native `urllib` module rather than spawning `nmem` CLI subprocesses.
* **Intent-Based Gating & Command Auto-Allowing**: Preserve the auto-allow logic in `hooks/nmem-gate.py`.
  - It reads `transcript.jsonl` to scan for explicit user intent keywords (`save`, `remember`, `store`, etc.).
  - Safe memory writes with detected user intent should be auto-allowed (`"decision": "allow"`) to minimize user permission prompts, while destructive edits require a prompt (`"decision": "force_ask"`).
  - Memory health catchup (`trigger_memory_catchup`) is debounced (1-hour cooldown / session debounce via `nmem_shared.should_allow_catchup`) to prevent repetitive server-side compaction, and requires explicit user intent keywords (`catch up`, `rescore`, `decay`, `compaction`, `maintenance`) to auto-allow.
  - Under `PreToolUse` in `hooks.json`, the `run_command` tool is monitored. Auto-approve specific commands (such as the native status script `nmem_status.py`) to bypass command confirmation prompts and provide a premium, prompt-free UX.
* **Notification Throttling & Async Background Retries**: In `hooks/post-invocation.py`, throttle informational warnings (e.g. pending offline sessions) to at most once per conversation session using `should_emit_unsynced_warning()` to prevent agent context nag loops, and always trigger non-blocking background queue drainage via `retry_unsynced_sessions_async()`.
* **Local Plugin Configuration (`.config.json`)**: Support a git-ignored `.config.json` file at the root of the plugin or workspace root.
  - Used for configuring server endpoints (`apiUrl`), credentials (`apiKey`), and default project space (`space`).
  - Precedence hierarchy: `env vars > workspace config (.config.json) > plugin storage config (~/.nowledge-mem/plugins/antigravity/config.json) > global config (~/.nowledge-mem/config.json) > defaults`.
* **Space Auto-Detection Heuristics**: Automatically map workspace directories to Nowledge Mem spaces.
  - Respect explicit override environment variables (`NMEM_SPACE` or `NMEM_SPACE_ID`), local config (`.config.json`), or workspace config files (`.nmemspace` / `.nowledge/config.json`) if set by the user.
  - Dynamically detected candidate spaces from workspace directory names must be verified against existing backend spaces (`get_existing_spaces()`).
  - If a dynamically detected space does NOT exist on the backend and the user has not explicitly configured a project space, hooks must fall back gracefully to the `default` space.
* **Host Agent ID Fingerprinting**: Use the shared hooks utility `hooks/nmem_shared.py` to resolve and propagate the host agent ID.
  - Fingerprinting must remain cross-platform and zero-dependency, supporting Windows, macOS, Linux, and container environments (overlay mounts).
  - Always export the resolved fingerprint to `os.environ['NMEM_HOST_AGENT_ID']` inside hooks so spawned CLI subprocesses inherit the environment context automatically.
* **MCP Config Git Index Ignored**: `mcp_config.json` is a tracked template file.
  - Runtime hooks dynamically synchronize `mcp_config.json` on disk with effective client credentials.
  - To prevent local key modifications from appearing in `git status` / `git diff`, developers and agents should run `git update-index --skip-worktree mcp_config.json` (or `make setup`).

---

## 3. Git Commit Standards

Maintain a clean, linear repository history by following these commit guidelines:

* **Conventional Commits**: Prefix all commit titles with Conventional Commit tags (e.g., `feat:`, `fix:`, `docs:`, `refactor:`).
* **Summary-Only Preference**: Prioritize terse, summary-only commit messages.
  - If adding a description summary, keep it human-friendly and high-level.
  - Avoid listing file-by-file code changes that are easily inferred from the commit diffs.

---

## 4. CI/CD, Validation & Integration Testing

* Before staging or proposing any changes, always run the validation suite:
  ```bash
  npm run validate
  ```
  Or using `make`:
  ```bash
  make validate
  ```
* **Pytest & HTTPX Standards**:
  - All unit and integration tests under `tests/` MUST use function-style `pytest` test cases (`def test_*()`).
  - All HTTP interactions in tests must use `httpx` (or `httpx.Client`).
* **Container Integration Test Suite (`tests/integration/`)**:
  - Tests spin up `docker.io/nowledgelabs/mem:latest` on dynamic random host ports (`-p 14242`) using `podman` or `docker`.
  - Run integration tests via `make test-integration` (`uv run pytest -v tests/integration/`).
  - **Host Config Isolation**: Tests MUST set `NMEM_IGNORE_HOST_CONFIG=1` so container integration runs never parse or leak host `~/.nowledge-mem/config.json` credentials.
* **Linting & Code Quality**:
  - Run `make lint` (`uv run ruff check` + `uv run ty check`).
  - Run `uv run pre-commit run --all-files` to ensure zero pre-commit regressions.
* The validation flow (`scripts/validate-plugin.mjs`) checks:
  - That all required files (rules, skills, hooks, tests, and release notes) are present, including `tests/test_hooks.py`, `tests/integration/conftest.py`, `tests/integration/test_mem_container.py`, and `hooks/nmem_status.py`.
  - That the version declared in `plugin.json` matches the version in `package.json` exactly.
  - That configuration files like `mcp_config.json` and `hooks.json` contain valid JSON structure.
  - Runs unit and integration test suites using `pytest`.


---

## 5. Token Optimization Guidelines

Help agents minimize token usage and avoid bloating the conversation context:

* **Start Informed, Once**: Read the Context Bundle or Working Memory exactly once at the beginning of the session. Do not query them on every conversation turn.
* **Nowledge FS Priority**: Prioritize the unified path-first interface (MCP `mem_fs` tool or CLI `nmem fs`) for directory listings, grep-searching, and file actions.
* **Start Broad, Then Narrow**:
  1. Use `mem_fs` `recall` or `find` to retrieve file paths.
  2. Use `mem_fs` `stat` to check file sizes and metadata cheaply *before* loading bodies.
  3. Use `mem_fs` `cat` with `--line` and `--lines` (or paging limits/offsets for thread fetches) to load only the specific window of content needed.
* **Deduplication**: Query existing memories via `memory_search` before calling writes to prevent duplicate entries.
* **CLI/Hook-Based Thread Saving**: The `nmem-save-thread` skill is uniquely designed to use host-side CLI hooks (such as `hooks/session-end.py`) rather than MCP tools. This prevents the agent from loading large, multi-megabyte `transcript.jsonl` files into its context window and serializing them over MCP, optimizing token usage and eliminating context limits on long conversations.

---

## 6. Premium UI/UX & Feedback Loops

Avoid raw text chat loops for drafting, confirmation, and multi-choice selections. Leverage Antigravity's native rich interfaces:

* **Proceed Approvals (Artifacts)**:
  - For drafting distilled memories or session handoff summaries, write them to a structured Markdown artifact (e.g. `distilled_memories_draft.md` or `handoff_draft.md`) under `<appDataDir>/brain/<conversation-id>`.
  - Set `RequestFeedback: true` and `UserFacing: true` in the `ArtifactMetadata` to present the user with a single-click "Proceed" button.
* **Interactive Prompts (`ask_question`)**:
  - Use the native `ask_question` tool with `is_multi_select: true` when asking the user to choose from multiple options (such as selecting which suggested skills to install or which memories to save).
  - List recommended options first, prefixed with `(Recommended)`.
* **Visual Formatting**: Format all structured content using GitHub-style Alerts (`[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, `[!CAUTION]`), Markdown Tables, and Mermaid diagrams to represent graph relationships.

---

## 7. Skill Development Guidelines

When authoring or modifying skills under `skills/`, follow these principles:

* **File Location & Naming**: Each skill must be placed in its own folder under `skills/<skill-folder>/SKILL.md` (e.g. `skills/nmem-memory-search/SKILL.md`).
* **Unified Naming Strategy (`nmem-<domain>-<action>`)**: All skills must follow the strict kebab-case pattern `nmem-<domain>-<action>`:
  - **Skill Domain**: `nmem-skill-load`, `nmem-skill-manage`, `nmem-skill-propose`
  - **Memory Domain**: `nmem-memory-search`, `nmem-memory-distill`, `nmem-memory-working`
  - **Thread Domain**: `nmem-thread-save`, `nmem-thread-handoff`
  - **Filesystem Domain**: `nmem-fs-explore`
  - **System Domain**: `nmem-status`
  Slash command triggers must align with the skill name (e.g. `/nmem-skill-load <query>`, `/nmem-thread-save`).
* **YAML Frontmatter Trigger Matching**: Every `SKILL.md` must start with a YAML frontmatter block containing:
  - `name`: Kebab-cased unique name matching the folder name exactly (e.g. `nmem-skill-load`).
  - `description`: A clear, third-person explanation of what behavior the skill teaches and when the agent should use it. Include slash command triggers in the description. This description is parsed by Antigravity at startup to dynamically discover and load relevant skills.
* **Primary and Fallback Interaction Paths**: Every skill that interacts with the `nmem` backend (whether reading working memory, searching, saving handoffs, or distilling memories) should define:
  1. A **Primary Interaction Mode**: Usually native MCP tools (or specialized helper scripts) to prevent terminal permission prompts and keep execution silent.
  2. A **CLI Fallback**: A documented fallback command using the `nmem` CLI to ensure the skill remains operational if MCP tools or APIs are disabled or unavailable.
* **Preferred Execution Hierarchy**: Document these paths clearly in a "Preferred Retrieval Hierarchy" or "Preferred Execution Hierarchy" section within the skill's `SKILL.md` file.
* **Outcome Feedback Footers**: Include the standard `<!-- nmem:skill-outcome-footer v1 -->` section at the bottom of the document. This teaches using agents to output `nmem-skill-used: <skill-name>` when applying the skill and instructs them to report deviations or failures using `report_skill_outcome` or the `nmem skills outcome` CLI fallback.

---

## 8. Documentation Maintenance & Sync Standards

Whenever adding, modifying, or refactoring features, lifecycle hooks, transport logic, configuration keys, or agent skills, contributing agents **must keep documentation synchronized across all 3 key documentation files**:

1. **User Documentation ([`README.md`](./README.md))**:
   - Update user-facing feature descriptions, installation instructions, available skills list, and slash command triggers (e.g. `/nmem-load-skill`).
   - Keep `README.md` clean, approachable, and focused on user setup and usage.
2. **Technical Deep-Dive ([`ARCHITECTURE.md`](./ARCHITECTURE.md))**:
   - Document deep technical details, system diagrams (`mermaid` flowcharts), hook execution flow, transport precedence, path resolution rules, and gating security policies.
   - Update `ARCHITECTURE.md` whenever modifying internal hook mechanics, transport protocols, or configuration resolution order.
3. **Developer & Maintainer Guidelines ([`AGENTS.md`](./AGENTS.md))**:
   - Document developer rules, hook development constraints, validation requirements, token optimization guidelines, and skill authoring principles.
4. **Validation Enforcement ([`scripts/validate-plugin.mjs`](./scripts/validate-plugin.mjs))**:
   - When adding new skills, scripts, or core documentation files, agents **must** register the new file paths in the `requiredPaths` array inside `scripts/validate-plugin.mjs`.
   - Run `npm run validate` or `make validate` to verify that all documentation and code assets pass validation cleanly before completing the turn.
