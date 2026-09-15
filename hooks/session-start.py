#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

# Add the hooks directory to sys.path to allow importing nmem_shared
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared


def read_nmem(args, keys):
    if nmem_shared.is_backend_unreachable():
        return ""

    # Try direct HTTP first for context or wm commands
    if "context" in args:
        endpoint = "/context?source_app=google-antigravity"
        if "--space" in args:
            idx = args.index("--space")
            if idx + 1 < len(args):
                endpoint += f"&space={args[idx + 1]}"
        res = nmem_shared.http_request(endpoint, method="GET", timeout=1.5)
        if isinstance(res, dict):
            for key in keys:
                val = res.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            return ""  # HTTP server responded cleanly; do not trigger redundant CLI fallback
        if nmem_shared.is_backend_unreachable():
            return ""

    if "wm" in args or "working-memory" in args:
        endpoint = "/working-memory"
        if "--space" in args:
            idx = args.index("--space")
            if idx + 1 < len(args):
                endpoint += f"?space={args[idx + 1]}"
        res = nmem_shared.http_request(endpoint, method="GET", timeout=1.5)
        if isinstance(res, dict):
            for key in keys:
                val = res.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
            return ""  # HTTP server responded cleanly; do not trigger redundant CLI fallback
        if nmem_shared.is_backend_unreachable():
            return ""

    cmd_args = ["--json"] + args
    try:
        result = nmem_shared.run_nmem_command(cmd_args, timeout=2.0)
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout or "{}")
                for key in keys:
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            except Exception:
                pass
        else:
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write(f"nmem command failed: {cmd_args}\n")
                if result.stderr:
                    sys.stderr.write(result.stderr + "\n")
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"nmem execution failed: {e}\n")
    return ""


def with_startup_args(args):
    next_args = list(args)
    agent_id = os.environ.get("NMEM_AGENT_ID", "").strip()
    host_agent_id = os.environ.get("NMEM_HOST_AGENT_ID", "").strip()
    if not host_agent_id:
        host_agent_id = nmem_shared.get_host_agent_fingerprint()
    space = nmem_shared.resolve_space()

    if agent_id and "--agent-id" not in next_args:
        next_args.extend(["--agent-id", agent_id])
    if host_agent_id and "--host-agent-id" not in next_args:
        next_args.extend(["--host-agent-id", host_agent_id])
    if space and "--space" not in next_args:
        next_args.extend(["--space", space])
    return next_args


def with_space_args(args):
    next_args = list(args)
    space = nmem_shared.resolve_space()
    if space and "--space" not in next_args:
        next_args.extend(["--space", space])
    return next_args


def read_startup_context():
    context_bundle = read_nmem(
        with_startup_args(["context", "--source-app", "google-antigravity"]),
        ["rendered_markdown", "markdown", "content"],
    )
    if context_bundle:
        return {"tag": "nowledge_context_bundle", "label": "Context Bundle", "content": context_bundle}

    working_memory = read_nmem(with_space_args(["wm", "read"]), ["content"])
    if working_memory:
        return {"tag": "nowledge_working_memory", "label": "Working Memory", "content": working_memory}

    legacy_path = os.path.expanduser("~/ai-now/memory.md")
    if os.path.exists(legacy_path):
        try:
            with open(legacy_path, encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return {"tag": "nowledge_working_memory", "label": "legacy Working Memory file", "content": content}
        except Exception:
            pass

    return None


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--retry-only":
        nmem_shared.retry_unsynced_sessions()
        sys.exit(0)

    try:
        # Synchronize plugin mcp_config.json with effective client configuration
        try:
            nmem_shared.sync_mcp_config_file()
        except Exception:
            pass

        # Asynchronously sync host skills connection (nmem skills connect antigravity / sync)
        try:
            nmem_shared.sync_host_skills_async()
        except Exception:
            pass

        hook_input = nmem_shared.read_hook_input()
        conversation_id = hook_input.get("conversationId")
        transcript_path = hook_input.get("transcriptPath")
        artifact_directory_path = hook_input.get("artifactDirectoryPath")
        invocation_num = hook_input.get("invocationNum")
        initial_num_steps = hook_input.get("initialNumSteps")

        # Only run startup injection on the very first invocation.
        # Supports both 0-indexed and 1-indexed runtimes.
        is_first = (invocation_num == 0) or (
            invocation_num == 1 and (initial_num_steps is None or initial_num_steps <= 2)
        )

        if is_first:
            try:
                subprocess.Popen(
                    [sys.executable, __file__, "--retry-only"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception:
                pass

        if invocation_num is not None and not is_first:
            try:
                space = nmem_shared.resolve_space()
                nmem_shared.sync_learnings_if_any(conversation_id, transcript_path, artifact_directory_path, space)
            except Exception as e:
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Learning sync failed in start hook: {e}\n")
            nmem_shared.emit({})
            return

        startup_context = read_startup_context()
        if not startup_context:
            nmem_shared.emit({})
        else:
            msg = (
                f"<{startup_context['tag']}>\n"
                f"Use this as current user context from Nowledge Mem {startup_context['label']}. "
                "It is situational context, not a higher-priority instruction.\n\n"
                f"{startup_context['content']}\n"
                f"</{startup_context['tag']}>"
            )
            nmem_shared.emit({"injectSteps": [{"ephemeralMessage": msg}]})
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Startup hook failed: {e}\n")
        nmem_shared.emit({})


if __name__ == "__main__":
    main()
