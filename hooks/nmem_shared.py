# Shared Nowledge Mem plugin hook utilities
import sys
import os
import json
import hashlib
import re
import subprocess
import uuid
import shutil
import time
from pathlib import Path

def read_hook_input() -> dict:
    """Read and parse JSON from stdin."""
    try:
        content = sys.stdin.read().strip()
        return json.loads(content) if content else {}
    except Exception:
        return {}

def emit(payload: dict) -> None:
    """Write JSON to stdout and flush."""
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()

def get_host_agent_fingerprint(prefix: str = "antigravity") -> str:
    """Derive a stable agent-identity fingerprint from system sources.
    
    Checks in order:
    1. /proc/1/mountinfo overlay ID (Linux containers / Docker / LazyCat)
    2. OS-specific machine identifier (machine-id, MachineGuid, IOPlatformUUID)
    3. Hardware MAC address (via uuid.getnode())
    4. Hostname
    """
    # 1. Container check
    overlay_id = _extract_overlay_id()
    if overlay_id:
        digest = hashlib.sha256(overlay_id.encode("utf-8")).hexdigest()[:8]
        return f"overlay-{digest}"

    # 2. Native OS IDs
    raw_id = ""
    if sys.platform.startswith("win"):
        raw_id = _get_windows_machine_guid()
    elif sys.platform == "darwin":
        raw_id = _get_macos_hardware_uuid()
    else:
        raw_id = _get_linux_machine_id()

    # 3. MAC address fallback
    if not raw_id:
        try:
            node = uuid.getnode()
            # uuid.getnode returns a 48-bit int.
            raw_id = str(node)
        except Exception:
            pass

    # 4. Hostname fallback
    if not raw_id:
        try:
            import socket
            raw_id = socket.gethostname()
        except Exception:
            raw_id = "default-fallback"

    digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"

def _extract_overlay_id() -> str | None:
    """Pull the overlay upperdir layer hash from /proc/1/mountinfo."""
    mountinfo = Path("/proc/1/mountinfo")
    if not mountinfo.is_file():
        return None
    try:
        content = mountinfo.read_text(encoding="utf-8")
        for line in content.splitlines():
            if "upperdir=" not in line:
                continue
            m = re.search(r"upperdir=([^,]+)", line)
            if not m:
                continue
            parts = m.group(1).rstrip("/").split("/")
            for part in reversed(parts):
                if len(part) >= 32 and all(c in "0123456789abcdef" for c in part):
                    return part
    except Exception:
        pass
    return None

def _get_windows_machine_guid() -> str:
    """Read Windows MachineGuid from Registry."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        )
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return str(value).strip()
    except Exception:
        pass

    try:
        out = subprocess.check_output(
            ["powershell.exe", "-Command", "(Get-ItemProperty -Path 'Registry::HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography').MachineGuid"],
            stderr=subprocess.DEVNULL,
            text=True
        )
        if out.strip():
            return out.strip()
    except Exception:
        pass
    return ""

def _get_macos_hardware_uuid() -> str:
    """Retrieve macOS Hardware UUID."""
    try:
        out = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            stderr=subprocess.DEVNULL,
            text=True
        )
        m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out)
        if m:
            return m.group(1).strip()
    except Exception:
        pass

    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "kern.uuid"],
            stderr=subprocess.DEVNULL,
            text=True
        )
        if out.strip():
            return out.strip()
    except Exception:
        pass
    return ""

def _get_linux_machine_id() -> str:
    """Read Linux machine-id."""
    for path_str in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        p = Path(path_str)
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    return content
            except Exception:
                pass
    return ""

def _windows_no_window_kwargs() -> dict[str, int]:
    if sys.platform != "win32":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}

def get_effective_config() -> tuple[str, str | None]:
    """Resolve effective API URL and API key following the hierarchy:
    1. NMEM_API_URL / NMEM_API_KEY environment variables
    2. ~/.nowledge-mem/config.json
    3. Fallback default http://127.0.0.1:14242
    """
    api_url = os.environ.get("NMEM_API_URL", "").strip()
    api_key = os.environ.get("NMEM_API_KEY", "").strip() or None

    if not api_url:
        config_file = Path("~/.nowledge-mem/config.json").expanduser()
        if config_file.is_file():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                api_url = data.get("apiUrl", "").strip()
                if not api_key:
                    api_key = data.get("apiKey", "").strip() or None
            except Exception:
                pass

    if not api_url:
        api_url = "http://127.0.0.1:14242"

    return api_url.rstrip("/"), api_key

def sync_mcp_config_file(mcp_config_path: str = None) -> bool:
    """Synchronize plugin mcp_config.json with effective client configuration
    (~/.nowledge-mem/config.json or NMEM_API_URL/NMEM_API_KEY env vars).
    Returns True if mcp_config.json was updated, False if already up to date.
    """
    if mcp_config_path is None:
        mcp_config_path = str(Path(__file__).parent.parent / "mcp_config.json")

    api_url, api_key = get_effective_config()
    clean_url = api_url.rstrip("/")
    server_url = f"{clean_url}/mcp/"

    headers = {"APP": "Google Antigravity"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-MEM-API-Key"] = api_key

    target_data = {
        "mcpServers": {
            "nowledge-mem": {
                "serverUrl": server_url,
                "headers": headers
            }
        }
    }

    p = Path(mcp_config_path)
    current_data = None
    if p.exists():
        try:
            current_data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass

    if current_data == target_data:
        return False

    try:
        p.write_text(json.dumps(target_data, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Warning: Failed to sync mcp_config.json: {e}\n")
        return False


def sync_host_skills_async() -> None:
    """Asynchronously runs 'nmem skills connect antigravity' and 'nmem skills sync'
    in a non-blocking background thread to ensure active skills are connected and refreshed.
    """
    import threading

    def _do_sync():
        try:
            cmd = _nmem_command()
            if not cmd:
                return
            # Connect host agent 'antigravity'
            run_nmem_command(["skills", "connect", "antigravity"], timeout=10)
            # Sync remote skill changes
            run_nmem_command(["skills", "sync"], timeout=10)
        except Exception as e:
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write(f"Background skills connect/sync failed: {e}\n")

    t = threading.Thread(target=_do_sync, daemon=True)
    t.start()

def http_request(endpoint: str, method: str = "GET", payload: dict | None = None, timeout: float = 5.0) -> dict | None:
    """Make a direct HTTP request to the Nowledge Mem backend prior to CLI fallback."""
    import urllib.request
    import urllib.error

    api_url, api_key = get_effective_config()
    url = f"{api_url}{endpoint}"

    headers = {
        "Content-Type": "application/json",
        "APP": "Google Antigravity"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-MEM-API-Key"] = api_key

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status in (200, 201):
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write("Authorization failure (401/403). Retrying with reloaded config...\n")
            api_url, api_key = get_effective_config()
            url = f"{api_url}{endpoint}"
            headers = {
                "Content-Type": "application/json",
                "APP": "Google Antigravity"
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
                headers["X-MEM-API-Key"] = api_key
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status in (200, 201):
                        body = resp.read().decode("utf-8")
                        e.close()
                        return json.loads(body) if body else {}
            except Exception as retry_err:
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Retry HTTP request to {url} failed: {retry_err}\n")
        else:
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write(f"HTTP request to {url} failed: {e}\n")
        e.close()
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"HTTP request to {url} failed: {e}\n")

    return None

def _nmem_command() -> str | None:
    candidate = shutil.which("nmem") or shutil.which("nmem.cmd")
    if candidate:
        try:
            resolved = Path(candidate).resolve()
            if resolved.is_file() and os.access(resolved, os.X_OK):
                return str(resolved)
        except Exception:
            pass

    # Fallback checking Linux system installation paths
    linux_paths = [
        "/usr/lib/nowledge-mem/nmem",
        "/usr/lib64/nowledge-mem/nmem",
        "/usr/local/bin/nmem",
        "/usr/bin/nmem",
        os.path.expanduser("~/.local/share/nowledge-mem/bin/nmem-wrapper"),
    ]
    for p_str in linux_paths:
        try:
            p = Path(p_str).resolve()
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
        except Exception:
            pass

    return None

def _cmd_exe_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if (
        len(parts) > 3
        and parts[0] == ""
        and parts[1] == "mnt"
        and len(parts[2]) == 1
    ):
        return f"{parts[2].upper()}:\\" + "\\".join(parts[3:])
    if len(path) >= 3 and path[1] == ":" and path[2] in ("\\", "/"):
        return path.replace("/", "\\")
    if normalized.startswith("/"):
        wslpath = shutil.which("wslpath")
        if wslpath:
            try:
                proc = subprocess.run(
                    [wslpath, "-w", path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=2,
                    check=False,
                )
                converted = proc.stdout.strip()
                if proc.returncode == 0 and converted:
                    return converted
            except Exception:
                pass
        distro = os.environ.get("WSL_DISTRO_NAME")
        if distro:
            return "\\\\wsl.localhost\\" + distro + normalized.replace("/", "\\")
    return "nmem.cmd" if Path(path).name.lower() == "nmem.cmd" else path

def _build_nmem_command(nmem: str, *args: str) -> list[str]:
    if nmem.lower().endswith(".cmd"):
        return [
            "cmd.exe",
            "/s",
            "/c",
            subprocess.list2cmdline([_cmd_exe_path(nmem), *args]),
        ]
    return [nmem, *args]

def run_nmem_command(args: list[str], env: dict | None = None, cwd: str | None = None, timeout: float | None = 15.0, input_str: str | None = None) -> subprocess.CompletedProcess:
    """Run an nmem command, finding the binary, translating path arguments if needed, and executing safely."""
    nmem = _nmem_command()
    if not nmem:
        raise FileNotFoundError("nowledge-mem: nmem command not found in PATH")
    
    is_cmd = nmem.lower().endswith(".cmd")
    
    processed_args = []
    for arg in args:
        if is_cmd and isinstance(arg, str) and (arg.startswith("/") or arg.startswith("./") or arg.startswith("../")):
            processed_args.append(_cmd_exe_path(arg))
        else:
            processed_args.append(arg)
            
    cmd = _build_nmem_command(nmem, *processed_args)
    
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
        
    return subprocess.run(
        cmd,
        input=input_str,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=run_env,
        cwd=cwd,
        timeout=timeout,
        **_windows_no_window_kwargs()
    )

class FileLock:
    """A simple platform-independent directory/file locking mechanism using exclusive creation."""
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.acquired = False
        
    def __enter__(self):
        retries = 25
        while retries > 0:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                time.sleep(0.1)
                retries -= 1
        raise TimeoutError(f"Could not acquire lock on {self.lock_path} within timeout.")
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception:
                pass

def save_unsynced_session(conv_id: str, messages: list, title: str, space: str | None, host_agent_id: str | None) -> None:
    """Save a failed session to the unsynced queue file."""
    config_dir = Path("~/.nowledge-mem").expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    queue_path = config_dir / "antigravity_unsynced.json"
    lock_path = queue_path.with_suffix(".lock")

    try:
        with FileLock(lock_path):
            # Load existing queue
            queue = {}
            if queue_path.exists():
                try:
                    queue = json.loads(queue_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            # Add/Update session
            queue[conv_id] = {
                "conversation_id": conv_id,
                "messages": messages,
                "title": title,
                "space": space,
                "host_agent_id": host_agent_id
            }

            # Save back
            try:
                queue_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
    except Exception as e:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write(f"Warning: Failed to write to unsynced sessions file: {e}\n")

def get_unsynced_sessions() -> dict:
    """Return dict of pending unsynced sessions from the queue file."""
    config_dir = Path("~/.nowledge-mem").expanduser()
    queue_path = config_dir / "antigravity_unsynced.json"
    if not queue_path.exists():
        return {}
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def retry_unsynced_sessions() -> None:
    """Attempt to sync any unsynced sessions in the queue."""
    config_dir = Path("~/.nowledge-mem").expanduser()
    queue_path = config_dir / "antigravity_unsynced.json"
    lock_path = queue_path.with_suffix(".lock")
    
    if not queue_path.exists():
        return

    try:
        with FileLock(lock_path):
            try:
                queue = json.loads(queue_path.read_text(encoding="utf-8"))
            except Exception:
                return

            if not queue:
                return

            updated_queue = dict(queue)
            for conv_id, data in queue.items():
                messages = data.get("messages", [])
                title = data.get("title", f"Antigravity Session {conv_id[:8]}")
                space = data.get("space")
                host_agent_id = data.get("host_agent_id")

                matched_count = 0
                success = False
                # Try HTTP transport first
                try:
                    space_param = f"?space={space}" if space else ""
                    check_res = http_request(f"/threads/{conv_id}{space_param}", method="GET", timeout=3.0)
                    if isinstance(check_res, dict) and (check_res.get("id") or check_res.get("thread_id") or "messages" in check_res):
                        existing_msgs = check_res.get("messages") or []
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
                        if matched_count == len(messages):
                            success = True
                        elif matched_count > 0:
                            rec_payload = {"matched_count": matched_count, "messages": messages[matched_count:]}
                            if space:
                                rec_payload["space"] = space
                            rec_res = http_request(f"/threads/{conv_id}/reconcile-tail", method="POST", payload=rec_payload, timeout=5.0)
                            if isinstance(rec_res, dict) and not rec_res.get("error"):
                                success = True
                        else:
                            app_payload = {"messages": messages}
                            if space:
                                app_payload["space"] = space
                            app_res = http_request(f"/threads/{conv_id}/append", method="POST", payload=app_payload, timeout=5.0)
                            if isinstance(app_res, dict) and not app_res.get("error"):
                                success = True
                    elif isinstance(check_res, dict) and (check_res.get("error") in ("not_found", "thread_not_found") or check_res.get("status") == 404 or not check_res):
                        # Explicit 404 / thread not found -> import new thread
                        import_payload = {
                            "id": conv_id,
                            "title": title,
                            "source": "google-antigravity",
                            "messages": messages
                        }
                        if space:
                            import_payload["space"] = space
                        imp_res = http_request("/threads/import", method="POST", payload=import_payload, timeout=5.0)
                        if isinstance(imp_res, dict) and not imp_res.get("error"):
                            success = True
                except Exception:
                    pass

                if not success:
                    # CLI Fallback
                    check_args = ['t', 'show', conv_id]
                    if space:
                        check_args.extend(['--space', space])

                    thread_exists = False
                    try:
                        result = run_nmem_command(check_args, timeout=5)
                        if result.returncode == 0:
                            thread_exists = True
                    except Exception:
                        pass

                    if thread_exists:
                        # Append trailing unmatched messages to existing thread
                        cli_append_msgs = messages[matched_count:] if matched_count > 0 else messages
                        if cli_append_msgs:
                            append_args = ['t']
                            if space:
                                append_args.extend(['--space', space])
                            append_args.extend([
                                'append',
                                conv_id,
                                '-m', json.dumps(cli_append_msgs)
                            ])
                            try:
                                result = run_nmem_command(append_args, timeout=5)
                                if result.returncode == 0:
                                    success = True
                            except Exception:
                                pass
                        else:
                            success = True
                    else:
                        # Import new thread
                        import_args = [
                            't', 'import',
                            '-m', json.dumps(messages),
                            '--id', conv_id,
                            '-t', title,
                            '-s', 'google-antigravity'
                        ]
                        if space:
                            import_args.extend(['--space', space])

                        env = {}
                        if host_agent_id:
                            env['NMEM_HOST_AGENT_ID'] = host_agent_id
                        try:
                            result = run_nmem_command(import_args, env=env, timeout=5)
                            if result.returncode == 0:
                                success = True
                        except Exception:
                            pass

                if success:
                    del updated_queue[conv_id]

            # Write back the remaining queue
            try:
                if updated_queue:
                    queue_path.write_text(json.dumps(updated_queue, indent=2, ensure_ascii=False), encoding="utf-8")
                else:
                    queue_path.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception as e:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write(f"Warning: Failed to lock unsynced sessions file for retry: {e}\n")

def sync_learnings_if_any(conversation_id: str, transcript_path: str, artifact_directory_path: str, space: str | None) -> None:
    """Scan for learning_proposal.md, verify approval in transcript, and sync to nmem (as rule, skill, or memory)."""
    if not artifact_directory_path or not os.path.exists(artifact_directory_path):
        return
        
    proposal_path = Path(artifact_directory_path) / "learning_proposal.md"
    if not proposal_path.exists():
        return
        
    # Check if the user approved the learning proposal in transcript
    if not transcript_path or not os.path.exists(transcript_path):
        return
        
    approved = False
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    step = json.loads(line)
                    if step.get("source") == "USER_EXPLICIT":
                        content = step.get("content") or ""
                        if "learning_proposal.md" in content and "approved this document" in content:
                            approved = True
                            break
                except Exception:
                    pass
    except Exception as e:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write(f"Error checking transcript for approval: {e}\n")
            
    if not approved:
        return
        
    # Parse learning_proposal.md
    try:
        content = proposal_path.read_text(encoding='utf-8')
    except Exception as e:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write(f"Error reading learning proposal: {e}\n")
        return
        
    # Extract title
    title_match = re.search(r'^#\s*Learning\s+Proposal\s*-\s*(.*)$', content, re.IGNORECASE | re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "Google Antigravity Learning"
    
    # Generate deterministic UUID v5 from conversation_id and title
    mem_name = f"nowledge-mem.learning.{conversation_id}.{title}"
    memory_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, mem_name))
    
    # Extract rule/markdown contents under "## Proposed Additions"
    proposed_additions_part = ""
    pos = content.lower().find("## proposed additions")
    if pos != -1:
        proposed_additions_part = content[pos:]
    else:
        proposed_additions_part = content
        
    # Find first code block in that part
    code_block_match = re.search(r'```(?:markdown|properties|text|bash|sh|json|yaml|diff|python)?\s*\n([\s\S]*?)\n```', proposed_additions_part, re.IGNORECASE)
    if code_block_match:
        rule_content = code_block_match.group(1).strip()
    else:
        lines = [line.strip() for line in proposed_additions_part.splitlines()]
        if lines and lines[0].lower().startswith("## proposed additions"):
            lines = lines[1:]
        rule_content = "\n".join(lines).strip()

    # Avoid repeated syncing (performance optimization)
    proposal_hash = hashlib.sha256(rule_content.encode('utf-8')).hexdigest()
    synced_state_file = Path(artifact_directory_path) / ".nmem_synced"
    synced_hashes = []
    if synced_state_file.exists():
        try:
            synced_hashes = json.loads(synced_state_file.read_text(encoding='utf-8'))
        except Exception:
            pass
            
    if proposal_hash in synced_hashes:
        if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
            sys.stderr.write(f"Learning proposal already synced (hash: {proposal_hash}). Skipping.\n")
        return

    # Detect skills and rules
    is_rule = False
    is_skill = False
    
    # Check classification type
    type_match = re.search(r'-\s*\*\*Type\*\*\s*:\s*(.*)', content, re.IGNORECASE)
    if type_match:
        type_str = type_match.group(1).lower()
        if "rule" in type_str:
            is_rule = True
        if "skill" in type_str:
            is_skill = True

    # Parse target files in the proposal to determine if rules or skills are modified
    skill_dirs = []
    file_urls = re.findall(r'file://([^\s\)\?\#]+)', content)
    for url in file_urls:
        try:
            path = Path(url)
            # If target file contains AGENTS.md or is in rules directory, it is a Rule
            if path.name.lower() == "agents.md" or "rules/" in str(path).replace("\\", "/"):
                is_rule = True
            # If target file is SKILL.md or is in skills directory, it is a Skill
            if path.name.lower() == "skill.md" or "skills/" in str(path).replace("\\", "/"):
                is_skill = True
                skill_dir = path.parent if path.is_file() else path
                if (skill_dir / "SKILL.md").exists() or (skill_dir / "skill.md").exists():
                    skill_dirs.append(str(skill_dir.resolve()))
        except Exception:
            pass

    # Execute appropriate sync command based on classification
    synced_any = False
    
    # 1. Sync Skills
    if is_skill and skill_dirs:
        for s_dir in set(skill_dirs):
            enroll_args = ['skills', 'enroll', s_dir, '-y']
            try:
                result = run_nmem_command(enroll_args, timeout=15)
                if result.returncode == 0:
                    synced_any = True
                    if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                        sys.stderr.write(f"Successfully enrolled skill to nmem: {s_dir}\n")
                else:
                    if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                        sys.stderr.write(f"Failed to enroll skill to nmem: {result.stderr}\n")
            except Exception as e:
                if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                    sys.stderr.write(f"Error enrolling skill: {e}\n")

    # 2. Sync Rules
    if is_rule:
        # Avoid CLI length limits by writing rule body to a temporary file
        temp_body_file = Path(artifact_directory_path) / f".temp_rule_{memory_id}.md"
        try:
            temp_body_file.write_text(rule_content, encoding='utf-8')
            upsert_args = [
                'rules', 'upsert',
                memory_id,
                '--title', title,
                '--body-file', str(temp_body_file)
            ]
            if space:
                upsert_args.extend(['--space', space])
                
            result = run_nmem_command(upsert_args, timeout=15)
            if result.returncode == 0:
                synced_any = True
                if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                    sys.stderr.write(f"Successfully upserted rule to nmem. ID: {memory_id}\n")
            else:
                if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                    sys.stderr.write(f"Failed to upsert rule to nmem: {result.stderr}\n")
        except Exception as e:
            if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                sys.stderr.write(f"Error executing nmem rules upsert: {e}\n")
        finally:
            try:
                temp_body_file.unlink(missing_ok=True)
            except Exception:
                pass

    # 3. Fallback to general Memory if not rule or skill
    if not synced_any:
        labels = ["google-antigravity", "learning"]
        for url in file_urls:
            try:
                path = Path(url)
                parent = path.parent
                if parent and parent.name:
                    if parent.name in ("skills", "rules", ".agents", "plugins") and parent.parent:
                        parent = parent.parent
                    if parent.name:
                        labels.append(parent.name.lower())
            except Exception:
                pass
        if is_rule:
            labels.append("rule")
        if is_skill:
            labels.append("skill")
            
        add_args = [
            'memories', 'add',
            '--id', memory_id,
            '--unit-type', 'learning',
            '--source-thread', conversation_id,
            '--source', 'google-antigravity',
            '--stdin'
        ]
        if space:
            add_args.extend(['--space', space])
        for label in set(labels):
            add_args.extend(['--label', label])
        add_args.extend(['--title', title])
        
        try:
            result = run_nmem_command(add_args, input_str=rule_content, timeout=15)
            if result.returncode == 0:
                synced_any = True
                if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                    sys.stderr.write(f"Successfully upserted memory to nmem. ID: {memory_id}\n")
            else:
                if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                    sys.stderr.write(f"Failed to upsert memory to nmem: {result.stderr}\n")
        except Exception as e:
            if os.environ.get('DEBUG') or os.environ.get('NMEM_DEBUG'):
                sys.stderr.write(f"Error executing nmem memories add: {e}\n")

    # Mark as synced to prevent repeated sync operations on subsequent steps
    if synced_any:
        if proposal_hash not in synced_hashes:
            synced_hashes.append(proposal_hash)
        try:
            synced_state_file.write_text(json.dumps(synced_hashes), encoding='utf-8')
        except Exception:
            pass
