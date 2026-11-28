#!/usr/bin/env python3
import json
import os
import re
import sys
import time
from pathlib import Path

# Add the hooks directory to sys.path to allow importing nmem_shared
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared


def thread_title(content, conversation_id):
    outer = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
    text = (outer.group(1) if outer else content).strip()
    # Only strip host metadata at the envelope boundaries. Tags in prose are literal.
    text = re.sub(r"^<t3_code_instructions>.*?</t3_code_instructions>", "", text, flags=re.DOTALL).strip()
    if text.endswith("</runtime_info>"):
        runtime = text.rfind("<runtime_info>")
        if runtime >= 0:
            text = text[:runtime].rstrip()
    inner = re.fullmatch(r"<user_request>(.*)</user_request>", text, re.DOTALL)
    title = " ".join((inner.group(1) if inner else text).split())
    if len(title) > 60:
        return title[:60] + "..."
    return title or f"Antigravity Session {conversation_id[:8]}"


def main():
    hook_input = nmem_shared.read_hook_input()
    conversation_id = hook_input.get("conversationId")
    transcript_path = hook_input.get("transcriptPath")
    artifact_directory_path = hook_input.get("artifactDirectoryPath")
    workspace_root = nmem_shared.activate_hook_workspace(hook_input)

    with nmem_shared.pin_capture_connection():
        capture(hook_input, conversation_id, transcript_path, artifact_directory_path, workspace_root)


def capture(hook_input, conversation_id, transcript_path, artifact_directory_path, workspace_root):
    fully_idle = hook_input.get("fullyIdle")
    if fully_idle is False:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(
                "session-end: fullyIdle is False (background tasks still running). Skipping thread capture.\n"
            )
        nmem_shared.emit({})
        return

    if not conversation_id or not transcript_path:
        nmem_shared.emit({})
        return

    try:
        space = nmem_shared.resolve_space(workspace_root, validate_explicit=True)
    except nmem_shared.SpaceResolutionError as error:
        sys.stderr.write(nmem_shared.format_space_resolution_error(error) + "\n")
        nmem_shared.emit({})
        return
    host_agent_id = os.environ.get("NMEM_HOST_AGENT_ID", "").strip()
    if not host_agent_id:
        host_agent_id = nmem_shared.get_host_agent_fingerprint()
    os.environ["NMEM_HOST_AGENT_ID"] = host_agent_id

    try:
        delays = (0.0, 0.5, 1.0)
        success = False
        messages = []
        title = f"Antigravity Session {conversation_id[:8]}"

        for delay in delays:
            if delay > 0:
                time.sleep(delay)

            if not os.path.exists(transcript_path):
                continue

            try:
                current_messages = []
                with open(transcript_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            step = json.loads(line)
                            source = step.get("source")
                            content = step.get("content")
                            step_type = step.get("type")

                            if source == "USER_EXPLICIT" and isinstance(content, str):
                                current_messages.append({"role": "user", "content": content})
                            elif source == "MODEL" and step_type == "PLANNER_RESPONSE" and isinstance(content, str):
                                current_messages.append({"role": "assistant", "content": content})
                        except Exception:
                            pass
                messages = current_messages
            except Exception:
                pass

            if not messages:
                continue

            first_request = next((m["content"] for m in messages if m["role"] == "user"), "")
            title = thread_title(first_request, conversation_id)

            success, stop_retrying = nmem_shared.sync_thread_capture(
                conversation_id, title, messages, space, host_agent_id
            )
            if success or stop_retrying:
                break

        if not success:
            nmem_shared.save_unsynced_session(conversation_id, messages, title, space, host_agent_id)

        # Seamlessly capture /learn learnings to nmem
        try:
            nmem_shared.sync_learnings_if_any(conversation_id, transcript_path, artifact_directory_path, space)
        except Exception as e:
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write(f"Learning sync failed: {e}\n")

    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Hook execution failed: {e}\n")

    nmem_shared.emit({})


if __name__ == "__main__":
    main()
