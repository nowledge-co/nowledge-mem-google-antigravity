#!/usr/bin/env python3
"""PostToolUse hook handler for Nowledge Mem Google Antigravity plugin."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared


def main():
    try:
        hook_input = nmem_shared.read_hook_input()
        conversation_id = hook_input.get("conversationId")
        transcript_path = hook_input.get("transcriptPath")
        artifact_directory_path = hook_input.get("artifactDirectoryPath")
        workspace_root = nmem_shared.activate_hook_workspace(hook_input)

        space = nmem_shared.resolve_space(workspace_root, validate_explicit=True)

        # Check for learning proposals after file modifications
        if conversation_id and transcript_path and artifact_directory_path:
            try:
                nmem_shared.sync_learnings_if_any(conversation_id, transcript_path, artifact_directory_path, space)
            except Exception:
                pass

        nmem_shared.emit({})
    except nmem_shared.SpaceResolutionError as error:
        sys.stderr.write(nmem_shared.format_space_resolution_error(error) + "\n")
        nmem_shared.emit({})
    except Exception:
        nmem_shared.emit({})


if __name__ == "__main__":
    main()
