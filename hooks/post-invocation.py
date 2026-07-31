#!/usr/bin/env python3
"""PostInvocation hook handler for Nowledge Mem Google Antigravity plugin."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared

def main():
    try:
        _hook_input = nmem_shared.read_hook_input()
        # PostInvocation handler can return injectSteps or terminationBehavior.
        # Default empty response allows standard turn progression.
        nmem_shared.emit({})
    except Exception:
        nmem_shared.emit({})

if __name__ == "__main__":
    main()
