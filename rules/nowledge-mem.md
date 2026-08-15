---
description: Rules for using Nowledge Mem context, working memory, memories, threads, and skills.
---

# Nowledge Mem for Google Antigravity

You have access to the user's Nowledge Mem through the bundled MCP server and the `nmem` CLI.

Use MCP tools for retrieval and memory writes when Antigravity exposes them in this session. Use direct `nmem` commands for lifecycle capture, status checks, remote client config, and any workflow where the CLI is clearer.

## Core Memory Lifecycle

Treat Nowledge Mem as five linked surfaces:

1. Context Bundle for startup identity, active space, active rules, and current priorities
2. Working Memory for a lightweight current-focus briefing
3. Distilled memories for durable knowledge
4. Threads for full searchable conversation history
5. Handoff summaries for compact resumability when the user wants a manual handoff

Prefer the smallest surface that answers the user's need, then move upward only when more context is necessary.

## Connection Model

`nmem` resolves remote access in this order:

1. `--api-url` flag
2. `NMEM_API_URL` / `NMEM_API_KEY`
3. `~/.nowledge-mem/config.json`
4. local defaults

Preferred persistent remote setup:

```bash
nmem config client set url https://mem.example.com
nmem config client set api-key nmem_your_key
```

If Antigravity's MCP tools also need to target remote Mem, generate the host config with:

```bash
nmem config mcp show --host google-antigravity
```

Paste the generated JSON into Antigravity's custom MCP config (located at `~/.gemini/config/mcp_config.json` or managed via the MCP Store).

## Context Bundle And Working Memory

At the start of a session, or when recent priorities would help, read Context Bundle when identity, active space, active rules, or multi-agent behavior could matter:

Prefer the MCP `read_context_bundle` tool when it is available.

Otherwise use:

```bash
nmem --json context --source-app google-antigravity
```

Use Working Memory alone for the lighter daily briefing or compatibility fallback.

Prefer the MCP `read_working_memory` tool when it is available.

Otherwise use:

```bash
nmem --json wm read
```

If the command succeeds but returns `exists: false`, there is no Working Memory briefing yet. Say that clearly instead of pretending a briefing exists.

If the runtime already knows the current project or agent lane, add `--space "<space name>"` to either command. Multi-agent orchestrators can set `NMEM_AGENT_ID="<agent-slug>"` before launching. Add `NMEM_SPACE` only when that whole run should override the identity's default space. Use `NMEM_HOST_AGENT_ID` only for advanced host-id aliases.

**Using Host Agent ID**:
At session startup, the PreInvocation hook injects the Context Bundle containing the derived host agent ID (e.g. `Agent id: antigravity-XXXXXXXX`). Always read this ID from your session context.
- **For MCP Tool calls**: When invoking MCP tools (such as `read_context_bundle` or `memory_add`), always pass the resolved host ID in the `host_agent_id` parameter to maintain context and space isolation.
- **For CLI terminal commands**: If executing direct `nmem` shell commands in the terminal (such as adding, searching, or editing memories), prefix them with the resolved host ID:
  ```bash
  NMEM_HOST_AGENT_ID="antigravity-XXXXXXXX" nmem m search "..."
  ```

Only fall back to the legacy file below for older local-only **Default-space** setups where the user still keeps Working Memory there:

```bash
test -f ~/ai-now/memory.md && cat ~/ai-now/memory.md
```

Read Context Bundle or Working Memory once near the start of a session, then reuse that context mentally. If Context Bundle already included Working Memory, do not read Working Memory again immediately. Do not re-read on every turn unless the user asks, the session context changed materially, or a long-running session clearly needs a refresh.

## Search Memory

Search past knowledge when:

- the user references previous work, a prior fix, or an earlier decision
- the task resumes a named feature, bug, refactor, incident, or subsystem
- a debugging pattern resembles something solved earlier
- the user asks for rationale, preferences, procedures, or recurring workflow details
- the current result is ambiguous and prior context would make the answer sharper

Start with durable recall:

Prefer MCP retrieval tools when they are available:

- `memory_search` for durable knowledge
- `thread_search` for prior conversation lookup
- `thread_fetch_messages` for progressive thread inspection

Otherwise use:

```bash
nmem --json m search "query"
```

If the runtime already knows the active project or agent lane, add `--space "<space name>"` to Context Bundle, Working Memory, memory search, thread search, and save commands.

If you need to query or synthesize across memories, threads, and documents directly from the terminal, use:

```bash
# Ask a question across all sources (Timeline entry is created by default)
nmem ask "Your question"

# Ask without creating a Timeline entry (one-off check)
nmem ask "Your question" --ephemeral

# Get a structured JSON response with answer and sources
nmem --json ask "Your question"
```

If the recall need is conceptual or the first pass is weak, use deep search:

```bash
nmem --json m search "query" --mode deep
```

If the user is really asking about a previous conversation or session, search threads directly:

```bash
nmem --json t search "query" --limit 5
```

If a memory search result includes `source_thread`, or thread search finds the likely conversation, inspect it progressively instead of loading the whole thread at once:

```bash
nmem --json t show <thread_id> --limit 8 --offset 0 --content-limit 1200
```

Prefer the smallest retrieval surface that answers the question.

## Distill Memory

Distill only durable knowledge worth keeping after the current session ends.

Use MCP `memory_add` for genuinely new facts, preferences, decisions, plans, procedures, learnings, events, or context when available. Pass `unit_type` when the type is clear:

If MCP tools are not exposed, use:

```bash
nmem --json m add "Insight with enough context to stand on its own." -t "Searchable title" -i 0.8 --unit-type decision -l project-name -s google-antigravity
```

If an existing memory already captures the same decision, workflow, or preference and the new information refines it, update that memory instead of creating a duplicate. Prefer MCP `memory_update` when available; otherwise use:

```bash
nmem m update <id> -t "Updated title"
```

## Save Thread

The Stop hook automatically imports the current conversation thread when execution terminates. To run an explicit save or check of the thread, use the `nmem-save-thread` skill.

## Save Handoff

Only save a handoff when the user explicitly asks for a resumable summary rather than a full session import. Think of this as a handoff summary, not a transcript save.

Structure the checkpoint around:

- Goal
- Major decisions
- Files or surfaces touched
- Open questions or risks
- Next steps

Then store it with:

```bash
nmem --json t create -t "Antigravity Session - topic" -c "Goal: ... Decisions: ... Files: ... Risks: ... Next: ..." -s google-antigravity
```

## Propose Skill

Use this skill ONLY when the user explicitly asks you to create, write, teach, or save an agent Skill, or when proposing a concrete improvement to an existing skill's procedure.
- Do NOT use for general codebase modifications, project documentation, or conversational memories.
- A skill is a reusable procedure for agents, not a memory card, so do NOT use `memory_add` or save a skill draft as a memory card.
- Draft the skill's name, purpose, and grounding evidence (or the proposed improvement changes). If updating an existing skill, include `id: <skill_id>` in the frontmatter.
- Write the draft to `skill_draft.md` in the artifact directory, setting `RequestFeedback: true` and `UserFacing: true` to request approval via the interactive "Proceed" button.
- Once approved, ask the user whether to activate immediately (`--activate`, recommended) or keep as a proposal (`--no-activate`), then run `python3 skills/nmem-skill-propose/scripts/propose_skill.py <appDataDir>/brain/<conversation-id>/skill_draft.md [--activate | --no-activate]`. Use the MCP tool `create_skill` or `propose_skill_improvement` only as a fallback.

## Token Optimization Guidelines

To conserve the user's LLM tokens and prevent context window bloat:

- **Reuse Startup Context**: Read the Context Bundle or Working Memory exactly once at the beginning of a session. Do not call retrieval tools like `read_context_bundle` or `read_working_memory` on every turn.
- **Paging & Windowing**:
  - When calling `thread_fetch_messages`, always specify a reasonable `limit` (e.g., `5` or `10`) instead of pulling in the entire thread history at once. Use `offset` to page through older messages only when necessary.
  - When reading files or thread logs in `mem_fs`, use `stat` first to check file size/metadata, then use `cat` with `--line` and `--lines` to load only the specific window of content needed.
- **Deduplication**: Always query existing memories with `memory_search` before saving a new memory. If a matching memory exists, use `memory_update` to refine it rather than creating a duplicate memory.

## Rich UI/UX & Feedback Loop Optimization

Always leverage Antigravity's premium UI capabilities to make the experience feel integrated, responsive, and professional:

- **Proceed Approvals (Artifacts)**:
  - For complex draft operations (such as drafting distilled memories or handoff summaries), do not ask the user for approval via raw text chat.
  - Instead, write the draft to a structured markdown artifact (e.g., `distilled_memories_draft.md` or `handoff.md`) in the conversation's artifact directory (`<appDataDir>/brain/<conversation-id>`).
  - Set `RequestFeedback: true` and `UserFacing: true` in the `ArtifactMetadata` so that a visual "Proceed" button is rendered.
  - Use GitHub-style Alerts (`[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, `[!CAUTION]`) to categorize drafts and draw attention to critical actions.
  - Use Mermaid diagrams (`mermaid` blocks) to visualize relationships between memories or graph structures.
  - Use standard tables to cleanly present tabular structured data.
- **Interactive Multi-Choice (ask_question)**:
  - Use the native `ask_question` tool when the user needs to make a selection from multiple options (e.g., selecting which suggested skills to install, or checking which specific memories/relations to save).
  - List the recommended option first and prefix it with `(Recommended)`.

## API, CLI, and MCP Interface Selection Guidelines

To minimize user interruption (via terminal permission popups), optimize latency, and prevent context bloat, always select the best interface for the job:

1. **Active Agent Loop (Prefer MCP)**:
   - **Rule**: When executing within the conversation loop, always prefer MCP tools (like `memory_search`, `read_context_bundle`, etc.) over executing `nmem` CLI commands.
   - **Reasoning**: Natively integrated schemas prevent token waste, and **MCP tools do not trigger terminal command permission prompts** (which break agent autonomy and interrupt the user). Only fall back to `nmem` CLI commands when MCP tools are disabled.
2. **Unified Navigation & File Operations (Prefer Nowledge FS)**:
   - **Rule**: For path-based browsing, metadata checking, case-insensitive grep searching, or segment reading, prefer the **Nowledge FS (`mem_fs` MCP tool)** or CLI `nmem fs`.
   - **Reasoning**: Navigating via paths (`/memories/`, `/threads/`, `/wiki/`) is highly structured. Use the "Start broad, then narrow" pattern:
     - Run `mem_fs` `recall` or `find` to discover paths.
     - Run `mem_fs` `ls` to explore neighboring context.
     - Run `mem_fs` `stat` to check metadata/line count cheaply *before* loading the content.
     - Run `mem_fs` `cat --line N --lines M` to load only the specific window of content needed.
3. **Hooks & Background Scripts (Prefer Direct HTTP API)**:
   - **Rule**: In hook scripts (`session-start.py`, `session-end.py`, `nmem-gate.py`) and background helpers, make direct HTTP requests (e.g. using python's `urllib`) to the local/remote API instead of spawning `nmem` CLI subprocesses.
   - **Reasoning**: Spawning subprocesses adds 300-500ms of startup latency, whereas native HTTP requests run in <50ms, keeping the startup/shutdown hooks extremely fast and lightweight.
4. **Diagnostics & Troubleshooting (Prefer CLI)**:
   - **Rule**: Use the `nmem` CLI for human workflows, debugging, and initial setup diagnostics (e.g. `nmem status` or `nmem config client set`).

## Configuration Precedence Hierarchy

Settings for endpoints (`apiUrl`), authentication (`apiKey`), and default project space (`space`) are resolved using the following precedence:

1. **Environment Variables**: `NMEM_API_URL`, `NMEM_API_KEY`, `NMEM_SPACE` (or `NMEM_SPACE_ID`)
2. **Local Plugin / Workspace Config (`.config.json`)**: Git-ignored `.config.json` at plugin root or workspace root.
3. **Explicit Workspace Files**: `.nmemspace` or `.nowledge/config.json`.
4. **Global Client Config**: `~/.nowledge-mem/config.json`.
5. **Defaults**: `http://127.0.0.1:14242` and `default` space.

## Memory Health & Catchup (`trigger_memory_catchup`)

Use `trigger_memory_catchup` strictly for memory health maintenance, decay re-scoring, and index compaction. Do **not** use it as a generic background-task runner or standard search replacement.

### 1. WHEN to Trigger
- **User Request**: When the user explicitly asks to run memory maintenance, check memory health, refresh memory decay, or process review backlogs.
- **Offline Resumption**: When starting a session after a multi-day offline break (3+ days) or after flushing offline session buffers.
- **Contradictory Context**: When in-session retrieval finds contradictory facts or outdated decisions needing decay re-scoring.
- **Milestone Synthesis**: Prior to generating major milestone crystals, handoffs, or wiki articles.

### 2. WHY to Trigger
- **Decay Re-scoring**: Forces server-side decay re-evaluations (`POST /agent/trigger/decay-refresh`), boosting recent facts and deprecating obsolete context.
- **Review Inbox Generation**: Projects actionable review cards onto the Timeline Review Inbox (`GET /agent/feed/events?review_only=true`).
- **Index Compaction**: Compacts LanceDB vector indexes and updates KuzuDB graph importance weights.

### 3. WHEN NOT to Trigger
- Do **NOT** trigger on routine conversation turns or standard tool calls.
- Do **NOT** use for standard fact/thread retrieval (use `memory_search` or `mem_fs recall` instead).
- Do **NOT** trigger in response to offline session queue warnings (`unsynced.json`) or routine status checks (queue syncing is handled automatically in the background by hooks).
- Repeated calls within the same session or 1-hour cooldown window are debounced and auto-suppressed.

### 4. Horizon Parameter Selection
- `horizon: "today"` (last 24 hours) - default for daily re-syncs.
- `horizon: "3"` (last 3 days) - for resuming after a weekend or multi-day gap.
- `horizon: "7"` (last 7 days) - for weekly maintenance or deep health audits.
- `horizon: "off"` - to cancel or disable active catch-up passes.


## Status & Diagnostics

## Dynamic Skill Discovery & On-Demand Loading (`/nmem-skill-load`)

When the user runs `/nmem-skill-load <query>` or when Antigravity discovers an unhandled domain task (e.g., Makefile optimizations, Fedora RPM packaging, Flatpak setups, database migrations) where a relevant skill exists on Nowledge Mem:

1. **Query & Preview**: Search the Nowledge Mem server using `skills/nmem-skill-load/scripts/load_skill.py search "<query>"`.
2. **Present Choice**: Present matching skills via `ask_question` or present a `skill_load_plan.md` artifact.
3. **Fetch & Inject**: Fetch the skill body (`load_skill.py fetch "<id>"`) and inject it into the active conversation turn (Ephemeral Mode) or install it to `.agents/skills/<name>/SKILL.md` (Persistent Mode).

Be concise, use memory tools naturally, and avoid saving routine or low-value chatter.
