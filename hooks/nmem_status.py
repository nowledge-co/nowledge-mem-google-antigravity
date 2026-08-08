#!/usr/bin/env python3
import sys
import os
import argparse
import json
from pathlib import Path

# Add the hooks directory to sys.path to allow importing nmem_shared
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared

def main():
    parser = argparse.ArgumentParser(description="Nowledge Mem Status Plugin")
    parser.add_argument("--conv-id", required=False, default=None, help="Conversation ID to check status for")
    args = parser.parse_args()
    conv_id = args.conv_id or os.environ.get("NMEM_CONVERSATION_ID") or os.environ.get("CONVERSATION_ID") or "active-session"

    # 1. Read environment variables / resolve active space
    space = nmem_shared.resolve_space()
    host_agent_id = os.environ.get("NMEM_HOST_AGENT_ID")
    if not host_agent_id:
        try:
            host_agent_id = nmem_shared.get_host_agent_fingerprint()
        except Exception:
            host_agent_id = "unknown"

    # 2. Run 'nmem status'
    nmem_status = "Unknown"
    nmem_connected = False
    try:
        status_res = nmem_shared.run_nmem_command(["status"], timeout=10)
        if status_res.returncode == 0:
            nmem_status = status_res.stdout.strip()
            nmem_connected = True
        else:
            nmem_status = f"Failed to run status (exit code {status_res.returncode}): {status_res.stderr.strip()}"
    except FileNotFoundError:
        nmem_status = "nowledge-mem: nmem command not found in PATH"
    except Exception as e:
        nmem_status = f"Error running status: {str(e)}"

    # 3. Check thread sync status
    thread_synced = False
    thread_details = ""
    try:
        t_args = ["t", "show", conv_id]
        if space and space != "default":
            t_args.extend(["--space", space])
        t_res = nmem_shared.run_nmem_command(t_args, timeout=10)
        if t_res.returncode == 0:
            thread_synced = True
            thread_details = t_res.stdout.strip()
        else:
            thread_details = t_res.stderr.strip()
    except FileNotFoundError:
        thread_details = "nmem command not found"
    except Exception as e:
        thread_details = f"Error: {str(e)}"

    # 4. Check local offline queue
    unsynced_path = Path("~/.nowledge-mem/antigravity_unsynced.json").expanduser()
    unsynced_count = 0
    is_current_pending = False
    unsynced_sessions = []
    
    if unsynced_path.exists():
        try:
            unsynced_data = json.loads(unsynced_path.read_text(encoding="utf-8"))
            if isinstance(unsynced_data, dict):
                unsynced_count = len(unsynced_data)
                unsynced_sessions = list(unsynced_data.keys())
                if conv_id in unsynced_data:
                    is_current_pending = True
        except Exception:
            pass

    # 5. Format into beautiful Markdown
    conn_status = "🟢 Connected" if nmem_connected else "🔴 Disconnected"
    
    if thread_synced:
        sync_status = "🟢 Synced"
    elif is_current_pending:
        sync_status = f"🟡 Pending Sync (Local Queue - {unsynced_count} total)"
    else:
        sync_status = "⚪ Unsynced / Not Found"

    # Format the thread details block
    if thread_synced:
        thread_info_block = f"```\n{thread_details}\n```"
    elif is_current_pending:
        thread_info_block = "> [!IMPORTANT]\n> This conversation has not been pushed to the remote Nowledge Mem server yet. It is queued locally in the unsynced buffer."
    else:
        thread_info_block = "> [!WARNING]\n> No record of this conversation found in the remote server or the local offline queue."

    # Format the queue list block
    if unsynced_count > 0:
        queue_info = f"There are **{unsynced_count}** unsynced session(s) pending in the local offline queue:\n"
        for q_id in unsynced_sessions:
            suffix = " (this conversation)" if q_id == conv_id else ""
            queue_info += f"- `{q_id}`{suffix}\n"
    else:
        queue_info = "*No pending sessions in local queue.*"

    report = f"""### 🌐 Nowledge Mem Integration Status

> [!NOTE]
> This status report displays the connection status, workspace environment, and sync queue for Google Antigravity.

#### 🖥️ Environment & Session Info
| Parameter | Value |
| :--- | :--- |
| **Current Conversation ID** | `{conv_id}` |
| **Active Space (Workspace)** | `{space}` |
| **Host Agent ID** | `{host_agent_id}` |

#### 🔄 Synchronization Status
- **Connection to Nowledge Mem Service**: {conn_status}
- **Current Thread Sync Status**: {sync_status}

{thread_info_block}

#### 📦 Local Offline Queue
{queue_info}

#### ⚙️ Service Output
```
{nmem_status}
```
"""
    print(report)

if __name__ == "__main__":
    main()
