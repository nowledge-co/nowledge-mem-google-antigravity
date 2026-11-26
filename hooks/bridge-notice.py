"""Expose fresh local mailbox hints at a normal model turn boundary."""

import nmem_shared
from nmem_bridge_notice import response


def main():
    payload = nmem_shared.read_hook_input()
    # PreInvocation also runs during tool loops; only the first model call of a user turn checks.
    if payload.get("invocationNum") != 0:
        nmem_shared.emit({})
        return
    notice = response(
        {"hook_event_name": "UserPromptSubmit", "session_id": payload.get("conversationId")}, host="antigravity"
    )
    hint = notice.get("hookSpecificOutput", {}).get("additionalContext")
    nmem_shared.emit({"injectSteps": [{"systemMessage": hint}]} if hint else {})
