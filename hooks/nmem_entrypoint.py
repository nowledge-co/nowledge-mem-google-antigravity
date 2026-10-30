#!/usr/bin/env python3
"""Unified entrypoint router for Nowledge Mem Google Antigravity plugin."""

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(HOOKS_DIR))

MIN_PYTHON = (3, 9)

# Lifecycle hook subcommands: the module each one runs and the JSON it answers
# with when that module cannot run. PreToolUse must always carry a decision,
# because Antigravity blocks the tool call on a missing or malformed one.
HOOKS = {
    "session-start": ("session-start", "{}"),
    "session-end": ("session-end", "{}"),
    "gate": ("nmem-gate", '{"decision": "ask"}'),
    "post-invocation": ("post-invocation", "{}"),
    "post_invocation": ("post-invocation", "{}"),
    "post-tool-use": ("post-tool-use", "{}"),
    "post_tool_use": ("post-tool-use", "{}"),
}


class _StdoutRecorder:
    """Pass writes through to stdout and remember whether any happened."""

    def __init__(self, stream):
        self._stream = stream
        self.written = False

    def write(self, data):
        if data:
            self.written = True
        return self._stream.write(data)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def run_hook(cmd, args):
    """Run a lifecycle hook so that Antigravity always receives valid JSON.

    hooks.json starts this file with whatever python3 the host provides. If the
    interpreter is too old, or the hook fails before writing anything, answer
    with the hook's fallback and exit 0 rather than leaving the reply to the
    shell. Keep this file free of syntax newer than Python 3.6 so it can.
    """
    module_name, fallback = HOOKS[cmd]
    recorder = _StdoutRecorder(sys.stdout)
    sys.stdout = recorder
    try:
        if sys.version_info < MIN_PYTHON:
            raise RuntimeError(
                f"Nowledge Mem hooks need Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, "
                f"found {sys.version_info[0]}.{sys.version_info[1]}"
            )
        import importlib

        hook = importlib.import_module(module_name)
        sys.argv = [sys.argv[0]] + args
        hook.main()
    except Exception as error:
        sys.stderr.write(f"nowledge-mem {cmd} hook failed: {error}\n")
        if not recorder.written:
            recorder.write(fallback)
            recorder.flush()
    finally:
        sys.stdout = recorder._stream


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python3 hooks/nmem_entrypoint.py <subcommand> [args...]\n")
        sys.stderr.write(
            "Subcommands: status, session-start, session-end, gate, skill-load, skill-manage, skill-propose\n"
        )
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd in HOOKS:
        run_hook(cmd, args)

    elif cmd == "status":
        import nmem_status

        sys.argv = [sys.argv[0]] + args
        nmem_status.main()

    elif cmd in ("skill-load", "load-skill"):
        load_skill_script = HOOKS_DIR.parent / "skills" / "nmem-skill-load" / "scripts" / "load_skill.py"
        if load_skill_script.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location("load_skill", str(load_skill_script))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.argv = [sys.argv[0]] + args
            mod.main()
        else:
            sys.stderr.write(f"Error: Could not find {load_skill_script}\n")
            sys.exit(1)

    elif cmd in ("skill-manage", "manage-skills"):
        manage_script = HOOKS_DIR.parent / "skills" / "nmem-skill-manage" / "scripts" / "manage_skills.py"
        if manage_script.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location("manage_skills", str(manage_script))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.argv = [sys.argv[0]] + args
            mod.main()
        else:
            sys.stderr.write(f"Error: Could not find {manage_script}\n")
            sys.exit(1)

    elif cmd in ("skill-propose", "propose-skill"):
        propose_script = HOOKS_DIR.parent / "skills" / "nmem-skill-propose" / "scripts" / "propose_skill.py"
        if propose_script.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location("propose_skill", str(propose_script))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.argv = [sys.argv[0]] + args
            mod.main()
        else:
            sys.stderr.write(f"Error: Could not find {propose_script}\n")
            sys.exit(1)

    elif cmd in ("artifact-update", "update-artifact"):
        artifact_script = HOOKS_DIR.parent / "scripts" / "update_artifact.py"
        if artifact_script.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location("update_artifact", str(artifact_script))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.argv = [sys.argv[0]] + args
            mod.main()
        else:
            sys.stderr.write(f"Error: Could not find {artifact_script}\n")
            sys.exit(1)

    elif cmd in ("wm-update", "update-working-memory", "working-memory-update"):
        wm_script = HOOKS_DIR.parent / "scripts" / "update_working_memory.py"
        if wm_script.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location("update_working_memory", str(wm_script))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.argv = [sys.argv[0]] + args
            mod.main()
        else:
            sys.stderr.write(f"Error: Could not find {wm_script}\n")
            sys.exit(1)

    elif cmd in ("rule-manage", "manage-rules", "rule", "rules"):
        rule_script = HOOKS_DIR.parent / "scripts" / "manage_rules.py"
        if rule_script.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location("manage_rules", str(rule_script))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.argv = [sys.argv[0]] + args
            mod.main()
        else:
            sys.stderr.write(f"Error: Could not find {rule_script}\n")
            sys.exit(1)

    else:
        sys.stderr.write(f"Unknown subcommand: {cmd}\n")
        sys.stderr.write(
            "Available subcommands: status, session-start, session-end, gate, skill-load, skill-manage, skill-propose, artifact-update, wm-update, rule-manage\n"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
