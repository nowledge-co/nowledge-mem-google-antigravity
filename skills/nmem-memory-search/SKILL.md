---
name: nmem-memory-search
description: Search the user's personal knowledge base when past insights would improve the response. Trigger proactively for continuity, recurring bugs, design rationale, and remembered workflows. Triggered by /nmem-memory-search.
---

# Search Memory

Use Nowledge Mem proactively when prior knowledge would materially improve the answer.

## Strong Signals

Search when:

- the user references previous work, a prior fix, or an earlier decision
- the task resumes a named feature, bug, refactor, incident, or subsystem
- a debugging pattern resembles something solved earlier
- the user asks for rationale, preferences, procedures, or recurring workflow details
- the user uses implicit recall language: "that approach", "like before"

**Contextual signals — consider searching when:**

- complex debugging where prior context would narrow the search space
- architecture discussion that may intersect with past decisions
- domain-specific conventions the user has established before
- the current result is ambiguous and past context would make the answer sharper

## Token-Efficient Retrieval Routing & Graph Neighborhoods

To minimize token usage, avoid bloating the conversation context, and prevent terminal permission prompts, follow this interface hierarchy:

1. **Nowledge FS (MCP `mem_fs`) - Best for Unified File Actions**:
   - For listing, searching, checking size, or loading chunks of memories, threads, wiki pages, or documents, call the MCP `mem_fs` tool.
   - Use the **"Start broad, then narrow"** pattern:
     - Run `mem_fs` with `command: "recall", path: "/memories", query: "..."` for semantic searches.
     - Run `mem_fs` with `command: "grep", query: "..."` to search text across memories, threads, and library sources.
     - Run `mem_fs` with `command: "stat"` to check the file size and line counts cheaply before loading.
     - Run `mem_fs` with `command: "cat", path: "...", line: START, lines: COUNT` to fetch only the required window, avoiding context bloat.
2. **Specialized MCP Tools & Retrieval Modes**:
   - **Normal (default)**: Use `memory_search` (limit `5`) for concrete facts, recent decisions, or straightforward recall.
   - **Deep**: Pass `mode="deep"` only when `mode` is advertised in the `memory_search` tool schema and Normal returns empty/weak/conflicting results or when the query is conceptual, historical, or asks *why* a choice was made. On hosted profiles that expose only `query`, `limit`, and `space_id`, omit `mode` and use a supported search on the same endpoint and Space.
   - **Threads**: Use `thread_search` followed by `thread_fetch_messages` (always passing a small `limit` like `5` or `10` and paging with `offset`).
   - **Progressive Graph**: When starting from a Memory ID or exploring related lineage, use `nmem-graph-explore` (`explore_graph` with `depth=1, limit=15` or 1-hop `memory_neighbors` expansion) after verifying Space/owner scope.
   - **Supersession**: When `memory_search` indicates a newer Memory updated an older one, prefer the newer statement.
3. **CLI Fallback (Only if MCP is unavailable)**:
   - **Important**: `nmem fs` CLI commands take **positional arguments** (do NOT pass `--query` or `--path` flags):
     - Recall: `nmem fs recall "<query>"`
     - Read/Cat: `nmem fs cat "<path>" --line START --lines COUNT`
     - Grep: `nmem fs grep "<pattern>" <path>`
     - Stat/Find/Ls: `nmem fs stat "<path>"`, `nmem fs find "<query>"`, `nmem fs ls /`
   - For direct question asking/synthesis across all sources: `nmem ask "question"` (use `--ephemeral` to avoid creating a Timeline entry, and `--json` for structured sources).
   - For memory/thread lookups: `nmem --json m search "<query>"` (add `--mode deep` when needed), `nmem --json m list --importance-min 0.7`, `nmem --json t list`, or `nmem --json t search "<query>" --limit 5`.
   - Pass `--space "<space name>"` or `--space-id <id>` **after** the subcommand when scoping to a specific Space.

Prefer the smallest retrieval surface that answers the question. In Team workspaces, `Only me` is private and `default` ("Before the team") does not imply shared visibility—use an explicit Space when needed.

Mention source threads when they add useful historical context.
