"""Ask the current Antigravity session to check an approved Agent mailbox."""

import json
import os

import nmem_shared
from agent_bridge_notices import bridge_session_hint


def main():
    payload = nmem_shared.read_hook_input()
    enabled = os.environ.get("NMEM_BRIDGE_INBOX_ON_START") == "1"
    context = os.environ.get("NMEM_AGENT_CONTEXT", "").strip()
    output = {}
    if enabled and context:
        output = {
            "injectSteps": [
                {
                    "systemMessage": (
                        "Nowledge Bridge inbox check requested. Use nmem-bridge-chat to inspect the approved "
                        "NMEM_AGENT_CONTEXT with the CLI and check one page in its exact Space. "
                        "Stop if verification fails. This startup check is CLI-only. "
                        + bridge_session_hint("antigravity", payload.get("conversationId"))
                    )
                }
            ]
        }
    print(json.dumps(output))
