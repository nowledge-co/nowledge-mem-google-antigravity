---
name: nmem-fs-explore
description: Browse and navigate Nowledge Mem's virtual filesystem (mem_fs) to locate, preview, and read memories, threads, wiki pages, and library artifacts. Triggered by /nmem-fs-explore.
---

# Nowledge FS Explorer

Use this skill when you need to navigate the virtual filesystem `mem_fs` or CLI command `nmem fs` to find, inspect, or manage knowledge.

## File System Structure

Nowledge FS projects data as a virtual file tree:
- `/memories/`: Durable personal memories.
  - `/memories/by-id/`: All memories indexed by their canonical ID (e.g. `by-id/<uuid>.memory.md`).
  - `/memories/by-date/`: Memories organized chronologically.
  - `/memories/by-label/`: Memories grouped by label directories.
  - `/memories/by-type/`: Memories grouped by unit types (e.g. `fact`, `preference`, `decision`, `plan`, `procedure`, `learning`, `context`, `event`).
- `/threads/`: Saved conversation threads (e.g. `by-id/<thread-id>.thread.jsonl`).
- `/wiki/`: Read-only wiki entities, topics, and crystal syntheses.
- `/working-memory/`: The active briefing surface.
- `/context/`: Startup rule assets and profile contexts.
- `/artifacts/`: Documents, outputs, and uploaded library resources.
- `/skills/`: Currently active user/system skills.

## Best Practices

1. **Start with capabilities**: Run `capabilities --json` (or `mem_fs` caps) to see roots, limits, and supported verbs.
2. **Orient with metadata (`stat` & `ls`)**:
   - Run `ls PATH` to explore directories and find files.
   - Run `stat PATH` to inspect metadata (type, size, update time) without loading file bodies. Always do this for large threads/documents.
3. **Limit context bloat (`cat --line --lines`)**:
   - For long threads or files, do not load the whole body. Use windowed reading: `cat PATH --line START --lines COUNT` to inspect only the required window.
4. **Search and Locate**:
   - Use `recall QUERY --in /memories` for semantic searching of memories.
   - Use `find PATH --unit-type TYPE --label LABEL --since YYYY-MM-DD --mentions ENTITY` to search by structure/metadata.
   - Use `grep QUERY PATH` for exact-string matching across memories, threads, and artifacts (case-insensitive by default; add `-E` for regex or `--case-sensitive`).
   - Use `grep QUERY PATH --jsonl` to stream matching objects with both `path` and `line` numbers for automated piping into `cat`.
5. **Write canonical files**:
   - Canonical writable paths (`/memories/by-id/<id>.memory.md`) can be written or deleted.
   - Alias and derived paths (`/memories/by-label/`, `/memories/by-type/`, `/memories/by-date/`) stay read-only. Edit memories through their canonical `/memories/by-id/...` path.
6. **Knowledge & Wiki Export**:
   - Inspect `/wiki/` entities, topics, and crystal pages to view compiled reference knowledge.
   - Use Open Knowledge Format (OKF) or Wiki exports (`/api/library/okf-export` or `/api/library/wiki-export`) to bundle synthesized knowledge graphs into portable Markdown sets for the local workspace.

## Commands Reference

Run the MCP tool `mem_fs` with the `command` argument (or `nmem fs <command>` in CLI):

- `capabilities` or `caps`: Discover roots, verbs, limits, and flags.
- `ls /memories/by-type/procedure`: List directory contents.
- `ls /wiki/`: List compiled wiki entities, topics, and crystals.
- `stat /threads/by-id/<thread-id>.thread.jsonl`: Check metadata/line count without reading body.
- `cat /memories/by-id/<uuid>.memory.md`: Read file body and frontmatter.
- `cat /threads/by-id/<thread-id>.thread.jsonl --line 50 --lines 20`: Read a 20-line window of a thread.
- `cat /wiki/<entity-or-topic>.md`: Inspect synthesized wiki article.
- `find /memories --unit-type decision --label project-x --since 2026-01-01`: Find specific memories.
- `grep "JWT rotation" /threads --jsonl`: Exact string search with path and line numbers.
- `grep -E "JWT|token" /threads`: Regex search across thread conversations.
- `recall "session state strategy" --in /memories`: Find relevant memories semantically.
- `write /memories/by-id/<id>.memory.md --body "..."`: Write a canonical memory.
- `rm /memories/by-id/<id>.memory.md`: Delete a canonical memory (always require explicit user confirmation before deletion).
