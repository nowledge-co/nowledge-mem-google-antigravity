#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode

# Add the hooks directory to sys.path to allow importing nmem_shared
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared


def read_nmem(args, keys):
    if nmem_shared.is_backend_unreachable():
        return ""

    is_context = "context" in args
    params = {}
    for flag, name in (
        ("--source-app", "source_app"),
        ("--agent-id", "agent_id"),
        ("--host-agent-id", "host_agent_id"),
        ("--space", "space_id"),
    ):
        if flag in args:
            params[name] = args[args.index(flag) + 1]
    paths = ("/context/bundle", "/context") if is_context else ("/agent/working-memory", "/working-memory")
    for index, path in enumerate(paths):
        query = params.copy()
        if index and "space_id" in query:
            query["space"] = query.pop("space_id")
        endpoint = path + ("?" + urlencode(query) if query else "")
        res = nmem_shared.http_request(endpoint, method="GET", timeout=1.5)
        if isinstance(res, dict) and not res.get("error") and not res.get("_http_error"):
            for key in keys:
                val = res.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
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


def with_startup_args(args, workspace_root=None):
    next_args = list(args)
    agent_id = os.environ.get("NMEM_AGENT_ID", "").strip()
    host_agent_id = os.environ.get("NMEM_HOST_AGENT_ID", "").strip()
    if not host_agent_id:
        host_agent_id = nmem_shared.get_host_agent_fingerprint()
    space = nmem_shared.resolve_space(workspace_root, validate_explicit=True)

    if agent_id and "--agent-id" not in next_args:
        next_args.extend(["--agent-id", agent_id])
    if host_agent_id and "--host-agent-id" not in next_args:
        next_args.extend(["--host-agent-id", host_agent_id])
    if space and "--space" not in next_args:
        next_args.extend(["--space", space])
    return next_args


def with_space_args(args, workspace_root=None):
    next_args = list(args)
    space = nmem_shared.resolve_space(workspace_root, validate_explicit=True)
    if space and "--space" not in next_args:
        next_args.extend(["--space", space])
    return next_args


def read_startup_context(workspace_root=None):
    context_bundle = read_nmem(
        with_startup_args(["context", "--source-app", "google-antigravity"], workspace_root),
        ["rendered_markdown", "markdown", "content"],
    )
    if context_bundle:
        return {"tag": "nowledge_context_bundle", "label": "Context Bundle", "content": context_bundle}

    working_memory = read_nmem(with_space_args(["wm", "read"], workspace_root), ["content"])
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

    # hooks.json runs this module for two events. SessionStart (no flag) fires once per
    # conversation and injects the startup context. PreInvocation (--pre-invocation) fires
    # before every model call, so it only syncs approved learning proposals lazily and never injects.
    pre_invocation = "--pre-invocation" in sys.argv[1:]
    inject_steps = []

    try:
        hook_input = nmem_shared.read_hook_input()
        conversation_id = hook_input.get("conversationId")
        transcript_path = hook_input.get("transcriptPath")
        artifact_directory_path = hook_input.get("artifactDirectoryPath")
        workspace_root = nmem_shared.activate_hook_workspace(hook_input)

        if pre_invocation:
            try:
                nmem_shared.sync_learnings_if_any(
                    conversation_id, transcript_path, artifact_directory_path, space=None, workspace_root=workspace_root
                )
            except nmem_shared.SpaceResolutionError as error:
                nmem_shared.emit(
                    {"injectSteps": [{"ephemeralMessage": nmem_shared.format_space_resolution_error(error)}]}
                )
                return
            except Exception as e:
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Learning sync failed in start hook: {e}\n")
            nmem_shared.emit({})
            return

        # Asynchronously sync host skills connection (nmem skills connect antigravity / sync)
        try:
            nmem_shared.sync_host_skills_async()
        except Exception:
            pass

        # Synchronize the MCP transport from this workspace's configuration,
        # rather than from the plugin directory used as the hook process cwd.
        try:
            nmem_shared.sync_mcp_config_file(cwd=workspace_root)
        except nmem_shared.SpaceResolutionError as error:
            inject_steps.append({"ephemeralMessage": nmem_shared.format_space_resolution_error(error)})
        except Exception:
            pass

        try:
            subprocess.Popen(
                [sys.executable, __file__, "--retry-only"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass

        if hook_input.get("parentConversationId") or hook_input.get("isSubagent") is True:
            nmem_shared.emit({"injectSteps": inject_steps} if inject_steps else {})
            return

        startup_context = read_startup_context(workspace_root)
        if startup_context:
            msg = (
                f"<{startup_context['tag']}>\n"
                f"Use this as current user context from Nowledge Mem {startup_context['label']}. "
                "It is situational context, not a higher-priority instruction.\n\n"
                f"{startup_context['content']}\n"
                f"</{startup_context['tag']}>"
            )
            # systemMessage stays in the conversation; ephemeralMessage is dropped after one model call.
            inject_steps.append({"systemMessage": {"systemMessage": msg}})
        nmem_shared.emit({"injectSteps": inject_steps} if inject_steps else {})
    except nmem_shared.SpaceResolutionError as error:
        if not inject_steps:
            inject_steps.append({"systemMessage": {"systemMessage": nmem_shared.format_space_resolution_error(error)}})
        nmem_shared.emit({"injectSteps": inject_steps} if inject_steps else {})
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Startup hook failed: {e}\n")
        nmem_shared.emit({})


if __name__ == "__main__":
    main()
