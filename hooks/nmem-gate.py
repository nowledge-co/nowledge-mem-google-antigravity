#!/usr/bin/env python3
import json
import os
import re
import shlex
import sys
from pathlib import Path

# Add the hooks directory to sys.path to allow importing nmem_shared
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared


def read_hook_input():
    try:
        content = sys.stdin.read().strip()
        return json.loads(content) if content else {}
    except Exception:
        return {}


def emit(payload):
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def is_safe_status_command(cmd_str: str) -> bool:
    """Validate that cmd_str is strictly a safe, standalone diagnostic or status command.
    Rejects chained commands (&&, ;, ||, |), subshells, redirects, and dangerous flags.
    """
    if not isinstance(cmd_str, str) or not cmd_str.strip():
        return False

    # Disallow shell operators and chaining
    dangerous_chars = {"&", ";", "|", "`", "$", "<", ">", "\n", "\r"}
    if any(ch in cmd_str for ch in dangerous_chars):
        return False

    try:
        tokens = shlex.split(cmd_str)
    except Exception:
        return False

    if not tokens:
        return False

    prog = Path(tokens[0]).name.lower()

    # Form 1: python / python3 invocation
    if prog in ("python", "python3", "python.exe", "python3.exe"):
        if len(tokens) < 2:
            return False
        script = Path(tokens[1]).name.lower()
        args = tokens[2:]

        if script == "nmem_status.py":
            if not args:
                return True
            return len(args) == 2 and args[0] == "--conv-id" and not args[1].startswith("-")

        if script == "nmem_entrypoint.py":
            if not args or args[0] != "status":
                return False
            if len(args) == 1:
                return True
            return len(args) == 3 and args[1] == "--conv-id" and not args[2].startswith("-")

        return False

    # Form 2: direct nmem_status.py executable
    if prog == "nmem_status.py":
        args = tokens[1:]
        if not args:
            return True
        return len(args) == 2 and args[0] == "--conv-id" and not args[1].startswith("-")

    # Form 3: nmem status / nmem tasks CLI
    if prog in ("nmem", "nmem.exe", "nmem.cmd"):
        if len(tokens) < 2:
            return False
        subcmd = tokens[1].lower()
        if subcmd not in ("status", "tasks"):
            return False
        args = tokens[2:]
        allowed_flags = {"--json", "-j"}
        return all(a in allowed_flags for a in args)

    return False


def main():
    data = read_hook_input()
    tool_call = data.get("toolCall") if isinstance(data, dict) else None
    if not isinstance(tool_call, dict):
        tool_call = {}
    tool_name = tool_call.get("name")
    tool_args = tool_call.get("args")
    if not isinstance(tool_args, dict):
        tool_args = {}

    if tool_name == "run_command" and is_safe_status_command(tool_args.get("CommandLine")):
        emit({"decision": "allow", "reason": "Auto-allowing validated plugin status and diagnostic command"})
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

    # 1. Memory Health Catchup (Debounced & Intent-Gated)
    if sub_tool == "trigger_memory_catchup":
        call_arguments = tool_args.get("Arguments") if isinstance(tool_args.get("Arguments"), dict) else tool_args
        horizon = str(call_arguments.get("horizon") or "today").strip() or "today"
        conv_id = data.get("conversationId")
        artifact_dir = data.get("artifactDirectoryPath")
        transcript_path = data.get("transcriptPath")

        # Check session and global cooldown
        should_proceed, debounce_reason = nmem_shared.should_allow_catchup(
            horizon, conv_id, session_dir=artifact_dir, transcript_path=transcript_path
        )
        if not should_proceed:
            emit(
                {
                    "decision": "deny",
                    "reason": f"Auto-suppressing redundant memory catchup: {debounce_reason}",
                }
            )
            return

        # Check explicit user intent
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
                            if re.search(
                                r"\b(catch\s*up|catchup|maintenance|rescore|re-score|compact|compaction|decay|health\s*check|memory\s*health)\b",
                                content,
                                re.IGNORECASE,
                            ):
                                user_authorized = True
                                break
                if user_authorized:
                    nmem_shared.record_catchup_execution(
                        horizon, conv_id, session_dir=artifact_dir, transcript_path=transcript_path
                    )
                    emit(
                        {
                            "decision": "allow",
                            "reason": f"Explicit user intent detected for memory maintenance ({sub_tool}) with horizon '{horizon}'.",
                            "permissionOverrides": [f"mcp(nowledge-mem/{sub_tool})"],
                        }
                    )
                    return
            except Exception:
                pass

        # Ask user confirmation if no explicit intent detected
        emit(
            {
                "decision": "ask",
                "reason": f"Confirmation required to run server-side memory maintenance/compaction ({sub_tool}, horizon: {horizon})",
            }
        )
        return

    # 2. Read-only tools (auto-allow)
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
        "ontology_read",
        "entity_search",
        "list_timeline_reviews",
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
