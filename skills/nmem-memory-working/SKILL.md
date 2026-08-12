---
name: nmem-memory-working
description: Read the user's daily Working Memory briefing at session start or when recent priorities matter. This gives Google Antigravity cross-tool continuity without bloating the main prompt. Triggered by /nmem-memory-working.
---

# Read Working Memory

Always prefer native Model Context Protocol (MCP) or Nowledge FS paths for retrieval to prevent terminal command permission prompts and keep execution silent. Only fall back to CLI commands when MCP tools are disabled.

## Preferred Retrieval Hierarchy

1. **Context Bundle (MCP)**: Call MCP tool `read_context_bundle` to fetch startup identity, rules, and working memory.
2. **Working Memory (MCP)**: If context bundle is not needed, call `read_working_memory`.
3. **Nowledge FS (MCP)**: Call `mem_fs` tool with `command: "cat", path: "/working-memory/working-memory.md"` (or `context`).
4. **CLI Fallback**: Only if MCP is unavailable, execute:
   ```bash
   nmem --json wm read
   ```
   Or context bundle fallback:
   ```bash
   nmem --json context --source-app google-antigravity
   ```

If the runtime already knows the current project or agent lane, add `--space "<space name>"` to the CLI commands or set `space_id` in the MCP tools. Multi-agent orchestrators can set `NMEM_AGENT_ID="<agent-slug>"` before launching. Add `NMEM_SPACE` only when that whole run should override the identity's default space. Use `NMEM_HOST_AGENT_ID` only for advanced host-id aliases.

## When to Use

- At session start
- When resuming work after a break
- When the user asks what they are focused on now
- When the current task clearly depends on recent priorities or active initiatives
- **When updating focus areas or briefings**: Use `wm-update` entrypoint script.

## Updating Working Memory In-Place

To update daily focus areas or briefing in Working Memory safely:
```bash
python3 hooks/nmem_entrypoint.py wm-update /path/to/updated_working_memory.md
```
Or supply raw string content:
```bash
python3 hooks/nmem_entrypoint.py wm-update --content "# Focus Areas\n- Priority 1..."
```

## Usage Pattern

- Read Context Bundle or Working Memory once near the start of a session.
- If Context Bundle was already loaded and includes Working Memory, do not read Working Memory again.
- Reuse that context mentally instead of re-reading on every turn.
- Refresh or update only if the user asks, active focus areas change, or a long-running session needs it.
