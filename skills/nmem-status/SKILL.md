---
name: nmem-status
description: Check the status of Nowledge Mem server connection, active workspace space, current conversation thread sync, and local offline sync queue. Use when the user requests status, diagnostics, or to verify if their session is being captured correctly.
---

# Nowledge Mem Status

Check connection status and inspect session synchronizations.

## Preferred Execution Hierarchy

To construct the status report, execute the native status command:

```bash
python3 hooks/nmem_entrypoint.py status
```

Output the printed stdout report directly to the user.

## Output Formatting

Synthesize the collected information into a beautiful, premium visual interface using standard markdown tables, GitHub-style alerts, and list blocks:

- **Server Connection**: Use `[!NOTE]` or `[!IMPORTANT]` alerts for connection state.
- **Active Context**: Use a Markdown Table for variables like space, host ID, and session directory.
- **Sync Status**: Use a `[!TIP]` alert or table to show thread messages and queue status.

Example structure of the printed rich status output:

> [!NOTE]
> **Nowledge Mem Server**: Connected to `http://127.0.0.1:14242` (Local)
> **Database Status**: Online
> **API Version**: 0.7.15

### Active Workspace Context

| Parameter | Value |
| :--- | :--- |
| **Active Space** | `Default` |
| **Host Agent ID** | `antigravity-3a2b1c` |
| **Conversation ID** | `ec33ebf9-0cba-4100-8142-c61503f6c587` |

### Conversation Sync Status

- **Synced Thread**: Yes (Thread contains 12 messages)

### In-Flight Background Tasks

- 🏃 `[community_detection]` Community detection (started 2026-08-20T18:00:00Z)
- 📅 `[scheduled_follow_up]` Migration check on staging (next: 2026-08-21T09:00:00Z)
- 🔄 `[recurring_task]` Morning research digest `[active]` (next: 2026-08-21T07:00:00Z)
### Memory Health & Maintenance

Only when the user explicitly requests memory maintenance (or during deep offline gap audits), trigger a maintenance pass using the MCP `trigger_memory_catchup` tool:
- `trigger_memory_catchup(horizon="today")` for explicit 24-hour maintenance.
- `trigger_memory_catchup(horizon="3")` or `horizon="7"` when resuming after offline gaps (3+ days) or deep audits.

> [!NOTE]
> Offline session queue synchronization is handled automatically by background hooks (`session-start.py` / `session-end.py`) and does NOT require invoking `trigger_memory_catchup`.


---

<!-- nmem:skill-outcome-footer v1 -->
## Improving this skill

Nowledge Mem compiled this skill from the user's own experience. When you apply it, print one line so it keeps getting sharper (replace `<name>` with this skill's name from the frontmatter above):

    nmem-skill-used: nmem-status

If a step was wrong or missing, or it didn't work, also record that: call `report_skill_outcome` with `skill_id="nmem-status"`, `skill_version=1`, an `outcome` of `completed` / `completed_with_deviation` / `failed` / `abandoned`, and a one-line note on what was off. This is the user's own feedback loop, not telemetry.
(Or use CLI fallback: `nmem skills outcome nmem-status --version 1 --outcome <outcome> [--deviations "<note>" | --failure "<reason>"]`)
