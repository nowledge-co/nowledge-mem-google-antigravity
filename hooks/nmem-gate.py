#!/usr/bin/env python3
import json
import os
import re
import sys


def read_hook_input():
    try:
        content = sys.stdin.read().strip()
        return json.loads(content) if content else {}
    except Exception:
        return {}


def emit(payload):
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def main():
    data = read_hook_input()
    tool_call = data.get("toolCall") if isinstance(data, dict) else None
    if not isinstance(tool_call, dict):
        tool_call = {}
    tool_name = tool_call.get("name")
    tool_args = tool_call.get("args")
    if not isinstance(tool_args, dict):
        tool_args = {}

    if (
        tool_name == "run_command"
        and isinstance(tool_args.get("CommandLine"), str)
        and "nmem_status.py" in tool_args.get("CommandLine")
    ):
        emit({"decision": "allow", "reason": "Auto-allowing plugin status command"})
        return

    # Detect if calling nowledge-mem
    is_nmem = False
    sub_tool = ""
    if tool_name == "call_mcp_tool" and tool_args.get("ServerName") == "nowledge-mem":
        is_nmem = True
        sub_tool = tool_args.get("ToolName")
    elif isinstance(tool_name, str) and tool_name.startswith("mcp_nowledge-mem_"):
        is_nmem = True
        sub_tool = tool_name[len("mcp_nowledge-mem_") :]

    if not is_nmem:
        emit({"decision": "allow"})
        return

    # 1. Read-only tools (auto-allow)
    read_only = {
        "memory_search",
        "thread_search",
        "read_context_bundle",
        "read_working_memory",
        "list_memory_labels",
        "thread_fetch_messages",
        "search_thread_messages",
        "list_crystals",
        "graph_stats",
        "list_communities",
        "get_community_details",
        "get_wiki_page",
        "explore_graph",
        "query_sources",
        "query_library",
        "read_source_content",
        "read_artifact_content",
        "search_source_chunks",
        "search_artifact_chunks",
        "analyze_source_data",
        "analyze_artifact_data",
        "mem_fs",
        "get_memory_by_id",
        "memory_neighbors",
        "report_skill_outcome",
        "memory_evolves_chain",
        "memory_relation_suggest",
        "memory_relation_list",
        "find_skills",
        "suggest_skill_check",
        "list_spaces",
        "get_space_profile",
        "query_by_labels",
        "get_node_details",
        "find_entity_relations",
        "compute_subgraph_pagerank",
        "find_bridge_nodes",
        "query_shortest_path",
        "check_claims",
        "list_timeline_reviews",
        "trigger_memory_catchup",
    }
    if sub_tool in read_only:
        emit(
            {
                "decision": "allow",
                "reason": f"Auto-allowing read-only tool {sub_tool}",
                "permissionOverrides": [f"mcp(nowledge-mem/{sub_tool})"],
            }
        )
        return

    # 2. Destructive operations (hard confirmation)
    if sub_tool in {"memory_delete", "thread_delete", "memory_relation_delete", "entity_delete", "label_delete"}:
        emit({"decision": "force_ask", "reason": f"Confirmation required to delete knowledge graph data ({sub_tool})"})
        return

    # 3. Writes/Mutations (intent-based)
    if sub_tool in {
        "memory_add",
        "memory_update",
        "memory_relation_add",
        "memory_supersede",
        "memory_relation_update",
        "memory_evolves_revise",
        "create_artifact",
        "create_skill",
        "propose_skill_improvement",
        "entity_merge",
        "label_merge",
        "schedule_follow_up",
        "resolve_timeline_review",
    }:
        transcript_path = data.get("transcriptPath")
        if transcript_path and os.path.exists(transcript_path):
            try:
                user_authorized = False
                with open(transcript_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        step = json.loads(line)
                        if step.get("source") == "USER_EXPLICIT":
                            content = step.get("content") or ""
                            # Look for keywords signifying explicit memory commands
                            if re.search(
                                r"\b(save|remember|memorize|store|nmem|add to memory|distill|checkpoint|handoff)\b",
                                content,
                                re.IGNORECASE,
                            ):
                                user_authorized = True
                                break
                if user_authorized:
                    emit(
                        {
                            "decision": "allow",
                            "reason": f"Explicit user intent detected for {sub_tool} in recent conversation.",
                            "permissionOverrides": [f"mcp(nowledge-mem/{sub_tool})"],
                        }
                    )
                    return
            except Exception:
                pass

        # Fallback to ask if no recent intent is found
        emit({"decision": "ask", "reason": f"Save memory checkpoint request: {sub_tool}"})
        return

    # Default fallback
    emit({"decision": "ask"})


if __name__ == "__main__":
    main()
