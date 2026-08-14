#!/usr/bin/env python3
"""PostInvocation hook handler for Nowledge Mem Google Antigravity plugin."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared


def main():
    try:
        hook_input = nmem_shared.read_hook_input()
        conversation_id = hook_input.get("conversationId")
        artifact_dir = hook_input.get("artifactDirectoryPath")
        transcript_path = hook_input.get("transcriptPath")
        # PostInvocation handler can return injectSteps or terminationBehavior.
        # Check if there are pending unsynced offline sessions or warning signals
        # that should be injected mid-turn.
        unsynced = nmem_shared.get_unsynced_sessions()
        if isinstance(unsynced, dict) and len(unsynced) > 0:
            # Trigger asynchronous background retry
            nmem_shared.retry_unsynced_sessions_async()

            # Throttle warning to at most once per conversation/session to prevent context nag loops
            if nmem_shared.should_emit_unsynced_warning(
                conversation_id, session_dir=artifact_dir, transcript_path=transcript_path
            ):
                nmem_shared.record_unsynced_warning_emitted(
                    conversation_id, session_dir=artifact_dir, transcript_path=transcript_path
                )
                count = len(unsynced)
                msg = (
                    f"[Nowledge Mem Info] There are {count} pending offline session(s) "
                    "queued in ~/.nowledge-mem/plugins/antigravity/unsynced.json. Background synchronization "
                    "has been triggered automatically. (Do NOT call trigger_memory_catchup for offline queue sync)."
                )
                nmem_shared.emit({"injectSteps": [{"ephemeralMessage": msg}]})
                return

        nmem_shared.emit({})
    except Exception:
        nmem_shared.emit({})


if __name__ == "__main__":
    main()
