---
name: nmem-skill-propose
description: Propose a new agent skill or submit a concrete improvement to an existing skill. Use ONLY when the user explicitly requests to write, create, teach, or save an agent skill, or when proposing a concrete improvement to an existing skill. Triggered by /nmem-skill-propose.
---

# Propose Skill / Submit Improvement

Use this skill ONLY when the user explicitly asks you to write, create, teach, or save a reusable agent Skill, or when you identify a concrete opportunity to propose an improvement to an existing skill's procedure. Do NOT use it for standard codebase development, project documentation, or logging memories.

## Good Candidates

- Procedures or guidelines you've refined in the session that would be valuable as reusable agent instructions.
- Additions of safety checks, gotchas, or release steps to existing skills.
- Custom checklists or orders of operations the user wants to codify.

## When NOT to Use

- **Do NOT use** for general repository or codebase development (e.g., writing Python/JS code, configuring project files, or adding codebase features).
- **Do NOT use** for drafting or updating standard project-level documentation (such as README.md, CONTRIBUTING.md, or architecture files).
- **Do NOT use** for logging project memories, decisions, facts, or settings (use `nmem-distill-memory` or `nmem-save-handoff` instead).
- **Do NOT use** unless the target of the creation or modification is specifically an agent skill file (residing in `.agents/skills/` or a plugin's `skills/` directory).

## Workflow

### Step 1: Check for Existing Skills First (Mandatory)
Before creating a new skill, **always search Nowledge Mem for existing skills** matching the name, topic, or domain:
```bash
python3 hooks/nmem_entrypoint.py skill-load search "<skill query/name>"
```
Or call `find_skills` / `mem_fs ls /skills`.

### Step 2: Evaluate Intent & Handle Ambiguity
- **Intent is CLEAR to Update**:
  - The user explicitly asked to "update", "edit", or "fix" an existing skill.
  - OR the skill was active/loaded in the current turn and the user is refining its procedure.
  - $\rightarrow$ **Action**: Include `id: <skill_id>` in `skill_draft.md` frontmatter and proceed to Step 3.
- **Intent is CLEAR to Create New**:
  - The user explicitly requested "create a brand new separate skill for X".
  - OR no matching existing skills were found during the pre-creation check.
  - $\rightarrow$ **Action**: Omit `id` from `skill_draft.md` frontmatter (or pass `--create-new`) and proceed to Step 3.
- **Intent is AMBIGUOUS**:
  - A matching or closely related skill exists (e.g. `update-fastflowlm-assets`), but the user's request is ambiguous (e.g. "save a skill for fastflow updates").
  - $\rightarrow$ **Action**: Ask the user to clarify before proceeding:
    > An existing skill `<skill_name>` (`<skill_id>`) was found. Would you like to update this existing skill in-place or create a new separate skill?

### Step 3: Show the Draft to the User (Rich UI/UX Review)
Do not submit or create the skill silently. Write the proposed draft to a user-facing artifact named `skill_draft.md` under `<appDataDir>/brain/<conversation-id>`.
- Set `RequestFeedback: true` and `UserFacing: true` in the `ArtifactMetadata` to present the user with a visual "Proceed" button.
- Format the draft using a premium layout:
  - Organize using GitHub-style Alerts (`[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`) to describe the skill's name, ID (if updating), and purpose.
  - Use markdown Tables to show the fields (e.g. Name, ID, Purpose, Evidence).
  - Use Mermaid flowcharts (`mermaid` block) to visualize the skill's workflow/steps if appropriate.

### Step 3: Await Confirmation and Dispatch
1. Notify the user to review the drafted `skill_draft.md` artifact.
2. Wait for the user to click the "Proceed" button or give explicit approval in the conversation.
3. Once approved, dispatch the request.

## Preferred Execution Hierarchy

1. **Direct REST API In-Place Router (Primary - Most Reliable)**:
   - Run the proposal python script to upload the fully drafted skill markdown directly to Nowledge Mem:
     ```bash
     python3 hooks/nmem_entrypoint.py skill-propose <appDataDir>/brain/<conversation-id>/skill_draft.md
     ```
     - **In-Place Update**: If `id: <skill_id>` is specified in the draft frontmatter (or `--skill-id <id>` flag is passed), the entrypoint automatically executes `POST /agent/skill-builder/edit-body` + `POST /skills/{id}/apply-version` to update the skill in-place without creating a duplicate.
     - **New Skill Creation**: If no `id` is specified, it executes `POST /agent/skill-builder/import` to register a new skill draft.
2. **MCP Tools (Fallback)**:
   - **For a new skill**: Call the `create_skill` tool with the drafted parameters (`name`, `purpose`, and optional `memory_ids`, `thread_ids`, `source_ids`).
   - **For a skill improvement**: Call the `propose_skill_improvement` tool with `skill_id` and the drafted `what` description.
3. **CLI Fallback (Only if python script and MCP are unavailable)**:
   - **For an in-place skill update**:
     ```bash
     nmem skills refine <skill_id> "<instruction>" && nmem skills apply <skill_id>
     ```
   - **For a new skill**:
     ```bash
     nmem skills create -y --name "<name>" --note "<purpose>" [--memory <id>] [--thread <id>] [--source <id>]
     ```

Report whether the skill submission was successfully created or proposed.

---

<!-- nmem:skill-outcome-footer v1 -->
## Improving this skill

Nowledge Mem compiled this skill from the user's own experience. When you apply it, print one line so it keeps getting sharper (replace `<name>` with this skill's name from the frontmatter above):

    nmem-skill-used: nmem-skill-propose

If a step was wrong or missing, or it didn't work, also record that: call `report_skill_outcome` with `skill_id="nmem-skill-propose"`, `skill_version=1`, an `outcome` of `completed` / `completed_with_deviation` / `failed` / `abandoned`, and a one-line note on what was off. This is the user's own feedback loop, not telemetry.
(Or use CLI fallback: `nmem skills outcome nmem-skill-propose --version 1 --outcome <outcome> [--deviations "<note>" | --failure "<reason>"]`)
