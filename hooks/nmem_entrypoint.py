#!/usr/bin/env python3
"""Unified entrypoint router for Nowledge Mem Google Antigravity plugin."""
import sys
import os
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(HOOKS_DIR))
import nmem_shared

def main():
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python3 hooks/nmem_entrypoint.py <subcommand> [args...]\n")
        sys.stderr.write("Subcommands: status, session-start, session-end, gate, skill-load, skill-manage, skill-propose\n")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "status":
        import nmem_status
        sys.argv = [sys.argv[0]] + args
        nmem_status.main()

    elif cmd == "session-start":
        import importlib
        session_start = importlib.import_module("session-start")
        sys.argv = [sys.argv[0]] + args
        session_start.main()

    elif cmd == "session-end":
        import importlib
        session_end = importlib.import_module("session-end")
        sys.argv = [sys.argv[0]] + args
        session_end.main()

    elif cmd == "gate":
        import importlib
        nmem_gate = importlib.import_module("nmem-gate")
        sys.argv = [sys.argv[0]] + args
        nmem_gate.main()

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

    else:
        sys.stderr.write(f"Unknown subcommand: {cmd}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
