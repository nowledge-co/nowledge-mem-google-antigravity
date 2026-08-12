#!/usr/bin/env python3
"""PostInvocation hook handler for Nowledge Mem Google Antigravity plugin."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared


def main():
    try:
        hook_input = nmem_shared.read_hook_input()
        # PostInvocation handler can return injectSteps or terminationBehavior.
        # Check if there are pending unsynced offline sessions or warning signals
        # that should be injected mid-turn.
        unsynced = nmem_shared.get_unsynced_sessions()
        if isinstance(unsynced, dict) and len(unsynced) > 0:
            count = len(unsynced)
            msg = (
                f"[Nowledge Mem Warning] There are {count} pending offline session(s) "
                "queued in ~/.nowledge-mem/antigravity_unsynced.json waiting to be synchronized."
            )
            nmem_shared.emit({"injectSteps": [{"ephemeralMessage": msg}]})
            return

        nmem_shared.emit({})
    except Exception:
        nmem_shared.emit({})


if __name__ == "__main__":
    main()
