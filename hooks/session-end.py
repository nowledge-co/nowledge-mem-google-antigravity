#!/usr/bin/env python3
import sys
import os
import json
import re
import time
import subprocess
import uuid
import hashlib
from pathlib import Path

# Add the hooks directory to sys.path to allow importing nmem_shared
sys.path.insert(0, str(Path(__file__).parent.resolve()))
import nmem_shared

def extract_messages_from_transcript(transcript_path: str) -> list:
    messages = []
    if not os.path.exists(transcript_path):
        return messages
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(line)
                    source = step.get('source')
                    content = step.get('content')
                    step_type = step.get('type')
                    
                    if source == 'USER_EXPLICIT' and isinstance(content, str):
                        messages.append({
                            'role': 'user',
                            'content': content
                        })
                    elif source == 'MODEL' and step_type == 'PLANNER_RESPONSE' and isinstance(content, str):
                        messages.append({
                            'role': 'assistant',
                            'content': content
                        })
                except Exception:
                    pass
    except Exception:
        pass
    return messages

def import_thread_data(conversation_id: str, title: str, messages: list, space: str | None, host_agent_id: str | None, agent_id: str | None = None) -> bool:
    if not messages:
        return False
        
    delays = (0.0, 0.5, 1.0)
    success = False
    
    for delay in delays:
        if delay > 0:
            time.sleep(delay)
            
        # Try HTTP transport first
        try:
            space_param = f"?space={space}" if space else ""
            check_res = nmem_shared.http_request(f"/threads/{conversation_id}{space_param}", method="GET", timeout=3.0)
            if isinstance(check_res, dict) and (check_res.get("id") or check_res.get("thread_id") or "messages" in check_res):
                existing_msgs = check_res.get("messages") or []
                matched_count = 0
                if isinstance(existing_msgs, list):
                    for old_m, new_m in zip(existing_msgs, messages):
                        old_role = old_m.get("role") or old_m.get("sender")
                        new_role = new_m.get("role") or new_m.get("sender")
                        old_text = old_m.get("content") or old_m.get("text")
                        new_text = new_m.get("content") or new_m.get("text")
                        if old_role == new_role and old_text == new_text:
                            matched_count += 1
                        else:
                            break

                # If all transcript messages match existing server messages, thread is already up to date!
                if matched_count == len(messages):
                    success = True
                    break

                # Try reconcile-tail if there are matched leading messages
                if matched_count > 0:
                    rec_payload = {
                        "matched_count": matched_count,
                        "messages": messages[matched_count:]
                    }
                    if space:
                        rec_payload["space"] = space
                    rec_res = nmem_shared.http_request(f"/threads/{conversation_id}/reconcile-tail", method="POST", payload=rec_payload, timeout=5.0)
                    if isinstance(rec_res, dict) and not rec_res.get("error"):
                        success = True
                        break

                append_payload = {"messages": messages[matched_count:] if matched_count > 0 else messages}
                if space:
                    append_payload["space"] = space
                app_res = nmem_shared.http_request(f"/threads/{conversation_id}/append", method="POST", payload=append_payload, timeout=5.0)
                if isinstance(app_res, dict) and not app_res.get("error"):
                    success = True
                    break
            elif isinstance(check_res, dict):
                import_payload = {
                    "id": conversation_id,
                    "title": title,
                    "source": "google-antigravity",
                    "messages": messages
                }
                if space:
                    import_payload["space"] = space
                if agent_id:
                    import_payload["agent_id"] = agent_id
                imp_res = nmem_shared.http_request("/threads/import", method="POST", payload=import_payload, timeout=5.0)
                if isinstance(imp_res, dict) and not imp_res.get("error"):
                    success = True
                    break
        except Exception:
            pass

        # Check if the thread exists (CLI Fallback)
        check_args = ['t', 'show', conversation_id]
        if space:
            check_args.extend(['--space', space])
            
        thread_exists = False
        try:
            result = nmem_shared.run_nmem_command(check_args, timeout=3)
            if result.returncode == 0:
                thread_exists = True
        except Exception as e:
            if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                sys.stderr.write(f"nmem t show execution failed: {e}\n")
                
        if thread_exists:
            # Append messages to existing thread
            append_args = ['t']
            if space:
                append_args.extend(['--space', space])
            append_args.extend([
                'append',
                conversation_id,
                '-m', json.dumps(messages)
            ])
            try:
                result = nmem_shared.run_nmem_command(append_args, timeout=5)
                if result.returncode == 0:
                    success = True
                    break
            except Exception:
                pass
        else:
            # Import new thread
            import_args = [
                't', 'import',
                '-m', json.dumps(messages),
                '--id', conversation_id,
                '-t', title,
                '-s', 'google-antigravity'
            ]
            if space:
                import_args.extend(['--space', space])
            if agent_id:
                import_args.extend(['--agent-id', agent_id])
                
            try:
                result = nmem_shared.run_nmem_command(import_args, timeout=5)
                if result.returncode == 0:
                    success = True
                    break
            except Exception:
                pass
                
    if not success:
        nmem_shared.save_unsynced_session(conversation_id, messages, title, space, host_agent_id, agent_id)
    return success

def find_matching_profile(role_name: str) -> str | None:
    profiles = nmem_shared.http_request("/settings/agent-profiles", method="GET", timeout=3.0)
    if not isinstance(profiles, list):
        try:
            res = nmem_shared.run_nmem_command(["agents", "list", "--json"], timeout=3)
            if res.returncode == 0:
                profiles = json.loads(res.stdout)
        except Exception:
            pass
    if not isinstance(profiles, list):
        return None
        
    role_clean = role_name.lower().strip()
    
    # 1. Exact Match check
    for prof in profiles:
        if not isinstance(prof, dict):
            continue
        p_id = str(prof.get("id", "")).lower().strip()
        p_name = str(prof.get("name", "")).lower().strip()
        p_role = str(prof.get("role", "")).lower().strip()
        if role_clean in (p_id, p_name, p_role):
            return prof.get("id")
            
    # 2. Substring Match check
    for prof in profiles:
        if not isinstance(prof, dict):
            continue
        p_name = str(prof.get("name", "")).lower().strip()
        p_role = str(prof.get("role", "")).lower().strip()
        if role_clean in p_name or role_clean in p_role or p_role in role_clean:
            return prof.get("id")
            
    return None

>>>>>>> 9827db4 (feat(hooks): optimize session-end with Stop idle guardrails and reconcile-tail thread sync)
def main():
    hook_input = nmem_shared.read_hook_input()
    conversation_id = hook_input.get('conversationId')
    transcript_path = hook_input.get('transcriptPath')
    artifact_directory_path = hook_input.get('artifactDirectoryPath')
    
    fully_idle = hook_input.get('fullyIdle')
    if fully_idle is False:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write("session-end: fullyIdle is False (background tasks still running). Skipping thread capture.\n")
        nmem_shared.emit({})
        return
        
    if not conversation_id or not transcript_path:
        nmem_shared.emit({})
        return
        
    space = os.environ.get('NMEM_SPACE', '').strip() or os.environ.get('NMEM_SPACE_ID', '').strip()
    host_agent_id = os.environ.get('NMEM_HOST_AGENT_ID', '').strip()
    if not host_agent_id:
        host_agent_id = nmem_shared.get_host_agent_fingerprint()
    os.environ['NMEM_HOST_AGENT_ID'] = host_agent_id
    
    try:
        delays = (0.0, 0.5, 1.0)
        success = False
        messages = []
        title = f"Antigravity Session {conversation_id[:8]}"
        
        for delay in delays:
            if delay > 0:
                time.sleep(delay)
                
            if not os.path.exists(transcript_path):
                continue
                
            try:
                current_messages = []
                with open(transcript_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            step = json.loads(line)
                            source = step.get('source')
                            content = step.get('content')
                            step_type = step.get('type')
                            
                            if source == 'USER_EXPLICIT' and isinstance(content, str):
                                current_messages.append({
                                    'role': 'user',
                                    'content': content
                                })
                            elif source == 'MODEL' and step_type == 'PLANNER_RESPONSE' and isinstance(content, str):
                                current_messages.append({
                                    'role': 'assistant',
                                    'content': content
                                })
                        except Exception:
                            pass
                messages = current_messages
            except Exception:
                pass
                
            if not messages:
                continue
                
            # Generate clean title from first user request
            title = f"Antigravity Session {conversation_id[:8]}"
            first_user_msg = next((m for m in messages if m['role'] == 'user'), None)
            if first_user_msg and first_user_msg.get('content'):
                clean_text = first_user_msg['content']
                match = re.search(r'<USER_REQUEST>([\s\S]*?)</USER_REQUEST>', clean_text)
                if match:
                    clean_text = match.group(1)
                clean_text = ' '.join(clean_text.strip().split())
                if len(clean_text) > 60:
                    title = clean_text[:60] + "..."
                elif len(clean_text) > 0:
                    title = clean_text
                    
            # Try HTTP transport first
            try:
                space_param = f"?space={space}" if space else ""
                check_res = nmem_shared.http_request(f"/threads/{conversation_id}{space_param}", method="GET", timeout=3.0)
                if isinstance(check_res, dict) and (check_res.get("id") or check_res.get("thread_id") or "messages" in check_res):
                    existing_msgs = check_res.get("messages") or []
                    matched_count = 0
                    if isinstance(existing_msgs, list):
                        for old_m, new_m in zip(existing_msgs, messages):
                            old_role = old_m.get("role") or old_m.get("sender")
                            new_role = new_m.get("role") or new_m.get("sender")
                            old_text = old_m.get("content") or old_m.get("text")
                            new_text = new_m.get("content") or new_m.get("text")
                            if old_role == new_role and old_text == new_text:
                                matched_count += 1
                            else:
                                break

                    # Try reconcile-tail if there are matched leading messages
                    if matched_count > 0:
                        rec_payload = {
                            "matched_count": matched_count,
                            "messages": messages[matched_count:]
                        }
                        if space:
                            rec_payload["space"] = space
                        rec_res = nmem_shared.http_request(f"/threads/{conversation_id}/reconcile-tail", method="POST", payload=rec_payload, timeout=5.0)
                        if isinstance(rec_res, dict) and not rec_res.get("error"):
                            success = True
                            break

                    append_payload = {"messages": messages[matched_count:] if matched_count > 0 else messages}
                    if space:
                        append_payload["space"] = space
                    app_res = nmem_shared.http_request(f"/threads/{conversation_id}/append", method="POST", payload=append_payload, timeout=5.0)
                    if isinstance(app_res, dict) and not app_res.get("error"):
                        success = True
                        break
                elif isinstance(check_res, dict):
                    import_payload = {
                        "id": conversation_id,
                        "title": title,
                        "source": "google-antigravity",
                        "messages": messages
                    }
                    if space:
                        import_payload["space"] = space
                    imp_res = nmem_shared.http_request("/threads/import", method="POST", payload=import_payload, timeout=5.0)
                    if isinstance(imp_res, dict) and not imp_res.get("error"):
                        success = True
                        break
            except Exception:
                pass

            # Check if the thread exists (CLI Fallback)
            check_args = ['t', 'show', conversation_id]
            if space:
                check_args.extend(['--space', space])
                
            thread_exists = False
            try:
                result = nmem_shared.run_nmem_command(check_args, timeout=3)
                if result.returncode == 0:
                    thread_exists = True
            except Exception as e:
                if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                    sys.stderr.write(f"nmem t show execution failed: {e}\n")
                    
            if thread_exists:
                # Append messages to existing thread
                append_args = ['t']
                if space:
                    append_args.extend(['--space', space])
                append_args.extend([
                    'append',
                    conversation_id,
                    '-m', json.dumps(messages)
                ])
                try:
                    result = nmem_shared.run_nmem_command(append_args, timeout=5)
                    if result.returncode == 0:
                        success = True
                        break
                    else:
                        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                            sys.stderr.write(f"nmem t append failed: {result.stderr}\n")
                except Exception as e:
                    if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                        sys.stderr.write(f"nmem t append execution failed: {e}\n")
            else:
                # Import new thread
                import_args = [
                    't', 'import',
                    '-m', json.dumps(messages),
                    '--id', conversation_id,
                    '-t', title,
                    '-s', 'google-antigravity'
                ]
                if space:
                    import_args.extend(['--space', space])
                    
                try:
                    result = nmem_shared.run_nmem_command(import_args, timeout=5)
                    if result.returncode == 0:
                        success = True
                        break
                    else:
                        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                            sys.stderr.write(f"nmem t import failed: {result.stderr}\n")
                except Exception as e:
                    if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                        sys.stderr.write(f"nmem t import execution failed: {e}\n")
                        
        if not success and messages:
            nmem_shared.save_unsynced_session(conversation_id, messages, title, space, host_agent_id)
            
        # Seamlessly capture /learn learnings to nmem
        try:
            nmem_shared.sync_learnings_if_any(conversation_id, transcript_path, artifact_directory_path, space)
        except Exception as e:
            if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                sys.stderr.write(f"Learning sync failed: {e}\n")
            
    except Exception as e:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write(f"Hook execution failed: {e}\n")
            
    nmem_shared.emit({})

if __name__ == '__main__':
    main()
