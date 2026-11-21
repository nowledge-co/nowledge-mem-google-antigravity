"""Ask the current Antigravity session to check an approved Agent mailbox."""

import json
import os


def main():
    enabled = os.environ.get("NMEM_BRIDGE_INBOX_ON_START") == "1"
    context = os.environ.get("NMEM_AGENT_CONTEXT", "").strip()
    output = {}
    if enabled and context:
        output = {
            "injectSteps": [
                {
                    "ephemeralMessage": (
                        "Nowledge Bridge inbox check requested. Use nmem-bridge-chat to inspect the approved "
                        "NMEM_AGENT_CONTEXT with the CLI and check one page in its exact Space. "
                        "Stop if verification fails. This startup check is CLI-only."
                    )
                }
            ]
        }
    print(json.dumps(output))
