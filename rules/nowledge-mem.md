---
trigger: always_on
description: Core invariants for Nowledge Mem context reuse, MCP-first retrieval, host agent IDs, and Space safety.
---

# Nowledge Mem for Google Antigravity

Use Nowledge Mem MCP tools when exposed (no terminal permission prompts, lower token overhead); fall back to the `nmem` CLI only when MCP is unavailable.

## Core Invariants

1. **Reuse Startup Context Once:** `SessionStart` injects the Context Bundle (or Working Memory fallback) and resolved `host_agent_id` (`antigravity-XXXXXXXX`). Reuse it across turns—do not re-call `read_context_bundle` or `read_working_memory` on every turn unless context materially changes or the user asks.
2. **Preserve Identity & Space:**
   - Pass `host_agent_id` to MCP calls (`read_context_bundle`, `memory_add`, etc.) when supported by the tool schema, or `NMEM_HOST_AGENT_ID="antigravity-XXXXXXXX"` to CLI commands.
   - Do not assume `default` is private: on Team Lite `default` is shared, and even on Nowledge Cloud Team workspaces a scoped key granted `default` can write there (`Only me` / `default` fallback routing is specific to Nowledge Cloud Team workspaces). Check the active deployment's resolved write target and audience (`list_spaces`, `nmem spaces`, or the save confirmation) before relying on privacy, and pass an explicit `--space "<name>"` (after the subcommand, not before it) or `--space-id <id>` when targeting a specific Space.
3. **Retrieve Narrowly:**
   - For `mem_fs`, start broad (`recall` / `find` / `grep`), check size with `stat`, then read windows with `cat --line N --lines M`.
   - For `thread_fetch_messages`, always pass a small `limit` (e.g., `5`–`10`) and page with `offset`.
   - When `memory_search` indicates a newer Memory updated an older one, prefer the current statement.
4. **Deduplicate Before Writing:** Query `memory_search` before `memory_add`; use `memory_update` when refining an existing memory. Do not save routine chatter or skill drafts as memories.
5. **Do Not Misuse Catchup:** Only call `trigger_memory_catchup` when the user explicitly requests memory maintenance/decay re-scoring or after a multi-day offline gap—never for routine turns, standard search, or `unsynced.json` offline queue warnings (hooks retry those automatically).

## On-Demand Skills

Load the dedicated skill when needed rather than guessing procedures:
`nmem-bridge-chat`, `nmem-memory-working`, `nmem-memory-search`, `nmem-graph-explore`, `nmem-memory-distill`, `nmem-fs-explore`, `nmem-thread-save`, `nmem-thread-handoff`, `nmem-skill-load`, `nmem-skill-manage`, `nmem-skill-propose`, `nmem-status`.
