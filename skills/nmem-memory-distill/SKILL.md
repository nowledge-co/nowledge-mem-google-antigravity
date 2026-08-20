---
name: nmem-memory-distill
description: Detect breakthrough moments, durable lessons, and decisions worth preserving. Suggest distillation sparingly, then store high-value knowledge as atomic memories. Triggered by /nmem-memory-distill.
---

# Distill Memory

Save proactively when the conversation produces a durable fact, preference, decision, plan, procedure, learning, event, or important context. Do not wait to be asked.

## Good Candidates

- decisions with rationale
- repeatable procedures
- lessons from debugging or incident work
- durable preferences or constraints
- plans that future sessions will need to resume cleanly

## Workflow

1. Scan the conversation history to identify candidates for distilled memories (facts, decisions, learnings, preferences, or repeatable procedures).
2. Draft a standalone memory card for each candidate with a clear title and context-complete description.
3. Write the drafted memories to a user-facing artifact named `distilled_memories_draft.md` under `<appDataDir>/brain/<conversation-id>`.
   - Set `RequestFeedback: true` and `UserFacing: true` in the `ArtifactMetadata` to present a "Proceed" button.
   - Format drafted memories using standard markdown header structure and list blocks (instead of tables or Alerts) to preserve paragraphs, lists, and code blocks, ensuring compatibility with nested code blocks:
     ```markdown
     ### Card: [Title of Memory]
     - **Unit Type**: `learning` (or `fact`, `preference`, `decision`, `plan`, `procedure`, `context`, `event`)
     - **Importance**: `0.8`
     - **Space**: `lemonade`
     - **When / Event Time**: `past` / `2026-08` (bi-temporal context if applicable)
     - **Provenance**: `thread: <thread-id>`, `msg: <msg-id>`, `range: 12:18` (if distilled from a thread)

     **Content**:
     [Rich markdown contents, paragraphs, lists, and fenced code blocks are written natively here]
     ```
   - Use the native `ask_question` tool if the user needs to select or exclude a subset of memories.
4. Identify semantic relationships between the new memories and existing knowledge (e.g., a new decision superseding an older plan).
   - Draw a Mermaid flowchart (`mermaid` block) inside the `distilled_memories_draft.md` artifact to visualize the relationship graph.
   - Allow the user to review the entire package of memories and relations at once.
5. Once the user clicks "Proceed" or approves, verify if a duplicate or similar memory exists:
   - If yes: update the existing memory (preferring MCP `memory_update` or `mem_fs` write to prevent terminal permission prompts).
   - If no: add it as a new memory (preferring MCP `memory_add` or `mem_fs` write).
   - Create the relationship edges (preferring MCP `memory_relation_add` or `mem_fs` write).
   - Only fall back to `nmem` CLI write commands if MCP tools are unavailable.

Always seek explicit confirmation before creating or updating memories. Do not perform write operations silently.
