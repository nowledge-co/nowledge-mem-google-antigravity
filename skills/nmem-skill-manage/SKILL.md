---
name: nmem-skill-manage
description: Install, update, or synchronize skills from Nowledge Mem into the local workspace directory under `.agents/skills/`. Use when the user asks to import, install, or update skills, or when recommending relevant skills for the workspace. Triggered by /nmem-skill-manage.
---

# Nowledge Mem Skill Manager

This skill allows the agent to list, suggest, and install skills from the user's Nowledge Mem knowledge base into the current project workspace.

## Preferred Execution Hierarchy

1. **REST API (Primary)**: The `manage_skills.py` script queries the `nmem` HTTP server (default `http://127.0.0.1:14242`).
2. **CLI Fallback**: If the server is unreachable or disabled, the script automatically falls back to invoking `nmem skills list` and `nmem skills show` to retrieve and install skills.

## When to Use

- Use when the user asks to "install a skill", "update a skill", or "import skills from nmem".
- Use when setting up a new workspace to see what existing compiled procedures (ruling, check-lists, styling conventions) from Nowledge Mem would help this project.
- Use to keep local workspace skills in sync with updates on the Nowledge Mem server.

## Workspace Target

All skills installed via this pipeline are placed in:
`<workspace-root>/.agents/skills/<skill-folder>/SKILL.md`

## Workflow

### Step 1: Suggest and Analyze Relevance
To find out what skills might be relevant to the current workspace, run:
```bash
python3 hooks/nmem_entrypoint.py skill-manage suggest <workspace-root>
```
This script scans for Makefiles, workflow files, Flatpak/AetherPak configurations, and Git status to score and match skills on the server.

### Step 2: Prompt the User (Rich Interface & Feedback Loop Optimization)
Do not install skills silently. Use Antigravity's rich interaction interfaces to solicit approval in a single turn:

- **Option A (Interactive Prompt)**: Use the native `ask_question` tool to present a list of skills.
  - Set `is_multi_select: true` to let the user select multiple skills at once.
  - If a specific skill is highly recommended, list it first with `(Recommended)` prefix.
  - Follow up with a second `ask_question` or option on whether to commit them to the repository or keep them local.
- **Option B (Proceed Artifact)**: For larger installations, write a `skills_installation_plan.md` artifact under `<appDataDir>/brain/<conversation-id>`.
  - Set `RequestFeedback: true` and `UserFacing: true` in the `ArtifactMetadata` to present a "Proceed" button.
  - Present the suggested skills in a markdown Table showing the Skill ID, Trust Badge (`Proven`, `Checked`, `Draft`), Description, Relevance reasons, and git commit strategy (e.g., local git exclude vs. committed).
  - The user can click "Proceed" to approve and install the plan in one click.

### Step 3: Install/Update the Skill
Run the install command:
```bash
python3 hooks/nmem_entrypoint.py skill-manage install <skill_id> <workspace-root> [--ignore]
```
* If the skill is in `candidate` stage, the script will automatically trigger compile and wait for it.
* It will download the body directly using `GET /skills/<id>?include_body=true` (which avoids modifying the global activation state on the server).
* If `--ignore` is specified, it automatically appends `.agents/skills/<skill-folder>/` to `<workspace-root>/.git/info/exclude`.

### Step 4: Verify
Verify that the files have been written under `<workspace-root>/.agents/skills/` and notify the user.

### Step 5: Undo a Skill merge
If you need to revert a Skill merge (restore the absorbed Skill and remove the merge's pending evidence from the kept Skill), run:
```bash
python3 hooks/nmem_entrypoint.py skill-manage restore-merge <skill_id>
```
Note: This option is only available until that evidence is applied or changed.
