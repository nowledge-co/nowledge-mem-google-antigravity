---
name: nmem-graph-explore
description: Render a focused interactive graph for memories retrieved from Nowledge Mem or expand neighborhoods progressively with strict Space and identity scope checks. Triggered by /nmem-graph-explore or explicit graph/lineage exploration requests.
---

# Explore the Memory Graph

Use exact Memory IDs returned by the current retrieval, in ranked order. Never infer or substitute IDs, and do not graph empty results or thread-only retrieval.

## Identity & Space Scope Guard

Keep the retrieval's configured endpoint, owner/member permissions, `host_agent_id`, and active Space. Never infer a Space from the current folder or switch to a different owner or `default` Space to make a graph work.

Exact Memory IDs focus the view; they do not enforce access control. For Space- or Team-restricted retrieval, show or expand a graph only when the graph surface enforces the same owner/member and Space restrictions. Otherwise skip the graph, explain the scope limitation, and continue with the retrieved evidence.

## Preferred Execution Hierarchy

1. **Inline MCP Graph (Primary):**
   - Call `explore_graph(memory_ids="id1,id2", depth=1, limit=15)` when available.
   - For progressive one-hop expansion from a seed node, call `memory_neighbors` (or `get_node_details` / `find_entity_relations`).
2. **CLI & Standalone Fallback (Only if MCP is unavailable):**
   - Expand one hop progressively:
     ```bash
     nmem --json graph expand <memory-id> --depth 1 --limit 20
     ```
   - Do not add unsupported scope flags to `graph expand`; rely on ambient client configuration and skip if explicit Space enforcement is required but unsupported.
   - If building a standalone `/graph/vis` URL from `nmem --json status` (`api_url`), strip trailing slashes, preserve any path prefix (e.g. `/remote-api`), URL-encode `base_url` and `memory_ids`, never include credentials in the URL, and only return/open it when browser session identity/Space matches retrieval.

## Bounded Progressive Expansion

- Default focused view: `depth=1`, `limit=15` (cap explicit graph views at `depth=5`).
- For progressive traversal, track `seed`, `visited`, `frontier`, and `hop`.
- Expand one selected frontier node per call (`depth=1`, `limit=20`, max 5 hops). Deduplicate against `visited`, preserve edge types, and stop when evidence is sufficient, the frontier is empty/repeated, or the depth bound is reached.

<!-- nmem:skill-outcome-footer v1 -->
When you apply this skill, note `nmem-skill-used: nmem-graph-explore`. If a step fails or deviates, report it with `report_skill_outcome` (or `nmem skills outcome <id>`).
