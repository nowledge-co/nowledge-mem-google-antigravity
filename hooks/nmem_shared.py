# Shared Nowledge Mem plugin hook utilities
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
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
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        )
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return str(value).strip()
    except Exception:
        pass

    try:
        out = subprocess.check_output(
            [
                "powershell.exe",
                "-Command",
                "(Get-ItemProperty -Path 'Registry::HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography').MachineGuid",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
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
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], stderr=subprocess.DEVNULL, text=True, timeout=2.0
        )
        m = re.search(r'"IOPlatformUUID" = "([^"]+)"', out)
        if m:
            return m.group(1).strip()
    except Exception:
        pass

    try:
        out = subprocess.check_output(["sysctl", "-n", "kern.uuid"], stderr=subprocess.DEVNULL, text=True, timeout=2.0)
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


def get_local_config(cwd: str | Path | None = None) -> dict:
    """Read local .config.json from the plugin root or workspace root if present."""
    config = {}
    plugin_root = Path(__file__).parent.parent.resolve()
    target_dir = Path(cwd).resolve() if cwd else Path.cwd().resolve()

    # Check plugin root .config.json first, then workspace root .config.json
    for cfg_path in (plugin_root / ".config.json", target_dir / ".config.json"):
        if cfg_path.is_file():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config.update(data)
            except Exception:
                pass
    return config


def get_plugin_storage_dir() -> Path:
    """Return the dedicated storage directory for the Antigravity plugin.

    Location: ~/.nowledge-mem/plugins/antigravity
    """
    return Path("~/.nowledge-mem/plugins/antigravity").expanduser()


def get_plugin_config() -> dict:
    """Read dedicated plugin configuration from ~/.nowledge-mem/plugins/antigravity/config.json if present."""
    config_file = get_plugin_storage_dir() / "config.json"
    if config_file.is_file():
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def get_effective_config(cwd: str | Path | None = None) -> tuple[str, str | None]:
    """Resolve effective API URL and API key following the hierarchy:
    1. NMEM_API_URL / NMEM_API_KEY environment variables
    2. Local workspace .config.json at workspace root or plugin root
    3. Plugin storage configuration (~/.nowledge-mem/plugins/antigravity/config.json)
    4. NMEM_CONFIG_PATH if set, or global ~/.nowledge-mem/config.json (unless NMEM_IGNORE_HOST_CONFIG is set)
    5. Fallback default http://127.0.0.1:14242

    Guards against inadvertently picking up host ~/.nowledge-mem/config.json credentials
    when targeting custom server URLs or running isolated test environments.
    """
    env_url = os.environ.get("NMEM_API_URL", "").strip()
    env_key = os.environ.get("NMEM_API_KEY", "").strip() or None
    ignore_host = os.environ.get("NMEM_IGNORE_HOST_CONFIG", "").strip().lower() in ("1", "true", "yes")

    api_url = env_url
    api_key = env_key

    # 2. Check local workspace .config.json (workspace-local config is not ignored by NMEM_IGNORE_HOST_CONFIG)
    if not api_url or not api_key:
        local_cfg = get_local_config(cwd)
        local_url = str(local_cfg.get("apiUrl") or local_cfg.get("api_url") or "").strip().rstrip("/")
        local_key = str(local_cfg.get("apiKey") or local_cfg.get("api_key") or "").strip() or None
        if not api_url and local_url:
            api_url = local_url
        if not api_key and local_key:
            if not env_url or env_url.rstrip("/") == local_url:
                api_key = local_key

    # 3. Check plugin storage config file (~/.nowledge-mem/plugins/antigravity/config.json) if not ignored
    if not ignore_host and (not api_url or not api_key):
        plugin_cfg = get_plugin_config()
        plugin_url = str(plugin_cfg.get("apiUrl") or plugin_cfg.get("api_url") or "").strip().rstrip("/")
        plugin_key = str(plugin_cfg.get("apiKey") or plugin_cfg.get("api_key") or "").strip() or None
        if not api_url and plugin_url:
            api_url = plugin_url
        if not api_key and plugin_key:
            if not env_url or env_url.rstrip("/") == plugin_url:
                api_key = plugin_key

    # 4. Check global config file (~/.nowledge-mem/config.json)
    if not ignore_host and (not api_url or not api_key):
        custom_cfg = os.environ.get("NMEM_CONFIG_PATH", "").strip()
        config_file = Path(custom_cfg).expanduser() if custom_cfg else Path("~/.nowledge-mem/config.json").expanduser()
        if config_file.is_file():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                file_url = str(data.get("apiUrl") or data.get("api_url") or "").strip().rstrip("/")
                file_key = str(data.get("apiKey") or data.get("api_key") or "").strip() or None

                if not api_url and file_url:
                    api_url = file_url

                # Only reuse host API key if explicit NMEM_API_KEY was NOT set
                # AND either NMEM_API_URL was not set or matches host file_url
                if not api_key and file_key:
                    if not env_url or env_url.rstrip("/") == file_url:
                        api_key = file_key
            except Exception:
                pass

    # 5. Fallback default
    if not api_url:
        api_url = "http://127.0.0.1:14242"

    return api_url.rstrip("/"), api_key


_BACKEND_UNREACHABLE_UNTIL = 0.0


def is_backend_unreachable() -> bool:
    """Check if recent connection failures indicate the backend is currently unreachable."""
    global _BACKEND_UNREACHABLE_UNTIL
    return time.time() < _BACKEND_UNREACHABLE_UNTIL


def mark_backend_unreachable(cooldown: float = 30.0):
    """Mark backend as unreachable for a cooldown window to fail fast without repeated timeouts."""
    global _BACKEND_UNREACHABLE_UNTIL
    _BACKEND_UNREACHABLE_UNTIL = time.time() + cooldown


def reset_backend_unreachable():
    """Reset backend unreachability state."""
    global _BACKEND_UNREACHABLE_UNTIL
    _BACKEND_UNREACHABLE_UNTIL = 0.0


_SPACES_CACHE = {"timestamp": 0.0, "spaces": None}


def get_existing_spaces(ttl: float = 60.0) -> list[dict] | None:
    """Fetch known spaces from the Nowledge Mem backend with in-memory & file caching."""
    global _SPACES_CACHE
    now = time.time()
    if _SPACES_CACHE["spaces"] is not None and (now - _SPACES_CACHE["timestamp"]) < ttl:
        return _SPACES_CACHE["spaces"]

    plugin_dir = get_plugin_storage_dir()
    cache_dir = plugin_dir / "cache"

    # Clean up legacy root-level or top-level spaces_cache.json if present
    for legacy_cache in (
        Path("~/.nowledge-mem/spaces_cache.json").expanduser(),
        Path("~/.nowledge-mem/cache/spaces_cache.json").expanduser(),
    ):
        if legacy_cache.is_file():
            try:
                legacy_cache.unlink(missing_ok=True)
            except Exception:
                pass

    # 1. Try file cache (~/.nowledge-mem/plugins/antigravity/cache/spaces_cache.json) if valid
    cache_file = cache_dir / "spaces_cache.json"
    if cache_file.is_file():
        try:
            mtime = cache_file.stat().st_mtime
            if (now - mtime) < ttl:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    _SPACES_CACHE["spaces"] = data
                    _SPACES_CACHE["timestamp"] = now
                    return data
        except Exception:
            pass

    spaces_data = None

    # 2. Try HTTP GET /spaces
    res = http_request("/spaces", method="GET", timeout=1.5)
    if isinstance(res, dict):
        if "spaces" in res and isinstance(res["spaces"], list):
            spaces_data = res["spaces"]
        elif "items" in res and isinstance(res["items"], list):
            spaces_data = res["items"]
    elif isinstance(res, list):
        spaces_data = res

    # 3. Fallback to CLI nmem --json spaces list if backend is not marked unreachable
    if spaces_data is None and not is_backend_unreachable():
        try:
            cmd_res = run_nmem_command(["--json", "spaces", "list"], timeout=2.5)
            if cmd_res.returncode == 0 and cmd_res.stdout:
                parsed = json.loads(cmd_res.stdout)
                if isinstance(parsed, dict) and "spaces" in parsed and isinstance(parsed["spaces"], list):
                    spaces_data = parsed["spaces"]
                elif isinstance(parsed, list):
                    spaces_data = parsed
        except Exception:
            pass

    if spaces_data is not None:
        _SPACES_CACHE["spaces"] = spaces_data
        _SPACES_CACHE["timestamp"] = now
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(spaces_data), encoding="utf-8")
        except Exception:
            pass
        return spaces_data

    return None


def resolve_space(cwd: str | Path | None = None) -> str:
    """Resolve active space following priority:
    1. Explicit environment variables (NMEM_SPACE or NMEM_SPACE_ID)
    2. Local workspace configuration (<workspace_root>/.config.json)
    3. Explicit workspace configuration files (.nmemspace or .nowledge/config.json)
    4. Plugin root configuration (<plugin_root>/.config.json)
    5. Plugin storage configuration (~/.nowledge-mem/plugins/antigravity/config.json space)
    6. Global configuration (~/.nowledge-mem/config.json space)
    7. Dynamically detected space from workspace directory IF it exists on backend
    8. Fallback to 'default' space
    """
    # 1. Check explicit environment override
    env_space = os.environ.get("NMEM_SPACE", "").strip() or os.environ.get("NMEM_SPACE_ID", "").strip()
    if env_space:
        return env_space

    target_dir = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    plugin_root = Path(__file__).parent.parent.resolve()

    # 2. Check local workspace .config.json (at workspace root)
    ws_cfg_path = target_dir / ".config.json"
    if ws_cfg_path.is_file():
        try:
            data = json.loads(ws_cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                ws_space = data.get("space") or data.get("space_id")
                if isinstance(ws_space, str) and ws_space.strip():
                    return ws_space.strip()
        except Exception:
            pass

    # 3. Check explicit workspace config files (.nmemspace or .nowledge/config.json)
    for cfg_path in (target_dir / ".nmemspace", target_dir / ".nowledge" / "config.json"):
        if cfg_path.is_file():
            try:
                if cfg_path.name == ".nmemspace":
                    val = cfg_path.read_text(encoding="utf-8").strip()
                    if val:
                        return val
                else:
                    data = json.loads(cfg_path.read_text(encoding="utf-8"))
                    val = data.get("space") or data.get("space_id")
                    if isinstance(val, str) and val.strip():
                        return val.strip()
            except Exception:
                pass

    # 4. Check plugin root .config.json if distinct from workspace root
    if plugin_root != target_dir:
        plugin_root_cfg = plugin_root / ".config.json"
        if plugin_root_cfg.is_file():
            try:
                data = json.loads(plugin_root_cfg.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    p_space = data.get("space") or data.get("space_id")
                    if isinstance(p_space, str) and p_space.strip():
                        return p_space.strip()
            except Exception:
                pass

    # 5. Check plugin storage config file (~/.nowledge-mem/plugins/antigravity/config.json)
    plugin_cfg = get_plugin_config()
    plugin_space = plugin_cfg.get("space") or plugin_cfg.get("space_id")
    if isinstance(plugin_space, str) and plugin_space.strip():
        return plugin_space.strip()

    # 6. Check global config file (~/.nowledge-mem/config.json)
    config_file = Path("~/.nowledge-mem/config.json").expanduser()
    if config_file.is_file():
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            val = data.get("space") or data.get("space_id")
            if isinstance(val, str) and val.strip():
                return val.strip()
        except Exception:
            pass

    # 7. Dynamic space auto-detection from workspace directory
    candidate = target_dir.name.strip()
    if not candidate or candidate.lower() == "default":
        return "default"

    # Check if candidate space exists on backend
    existing_spaces = get_existing_spaces()
    if existing_spaces is not None:
        cand_lower = candidate.lower()
        for space_obj in existing_spaces:
            if not isinstance(space_obj, dict):
                continue
            s_id = str(space_obj.get("id", "")).strip()
            s_key = str(space_obj.get("key", "")).strip()
            s_name = str(space_obj.get("name", "")).strip()
            aliases = [str(a).strip().lower() for a in space_obj.get("aliases", []) if a]

            if cand_lower in (s_id.lower(), s_key.lower(), s_name.lower()) or cand_lower in aliases:
                return s_id or s_key or candidate

    # 6. Dynamically detected space does not exist on backend and user has not explicitly set project space -> fall back to default
    return "default"


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
        headers["X-NMEM-API-Key"] = api_key

    target_data = {"mcpServers": {"nowledge-mem": {"serverUrl": server_url, "headers": headers}}}

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


def http_request(
    endpoint: str,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 1.5,
    skip_circuit_breaker: bool = False,
) -> dict | None:
    """Make a direct HTTP request to the Nowledge Mem backend using httpx prior to CLI fallback."""
    import urllib.error
    import urllib.request

    try:
        import httpx
    except ImportError:
        httpx = None

    if not skip_circuit_breaker and is_backend_unreachable():
        return None

    api_url, api_key = get_effective_config()
    url = f"{api_url}{endpoint}"

    headers = {"Content-Type": "application/json", "APP": "Google Antigravity"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-NMEM-API-Key"] = api_key
        headers["X-MEM-API-Key"] = api_key

    if httpx is not None:
        try:
            resp = httpx.request(method, url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code in (200, 201):
                reset_backend_unreachable()
                return resp.json() if resp.text else {}
            elif resp.status_code in (401, 403):
                api_url, api_key = get_effective_config()
                url = f"{api_url}{endpoint}"
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                    headers["X-NMEM-API-Key"] = api_key
                    headers["X-MEM-API-Key"] = api_key
                resp_retry = httpx.request(method, url, json=payload, headers=headers, timeout=timeout)
                if resp_retry.status_code in (200, 201):
                    reset_backend_unreachable()
                    return resp_retry.json() if resp_retry.text else {}
        except Exception:
            pass

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status in (200, 201):
                reset_backend_unreachable()
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write("Authorization failure (401/403). Retrying with reloaded config...\n")
            api_url, api_key = get_effective_config()
            url = f"{api_url}{endpoint}"
            headers = {"Content-Type": "application/json", "APP": "Google Antigravity"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
                headers["X-NMEM-API-Key"] = api_key
                headers["X-MEM-API-Key"] = api_key
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status in (200, 201):
                        reset_backend_unreachable()
                        body = resp.read().decode("utf-8")
                        e.close()
                        return json.loads(body) if body else {}
            except Exception as retry_err:
                mark_backend_unreachable()
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Retry HTTP request to {url} failed: {retry_err}\n")
        else:
            if e.code >= 500:
                mark_backend_unreachable()
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write(f"HTTP request to {url} failed: {e}\n")
        e.close()
    except Exception as e:
        mark_backend_unreachable()
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
    if len(parts) > 3 and parts[0] == "" and parts[1] == "mnt" and len(parts[2]) == 1:
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


def run_nmem_command(
    args: list[str],
    env: dict | None = None,
    cwd: str | None = None,
    timeout: float | None = 5.0,
    input_str: str | None = None,
) -> subprocess.CompletedProcess:
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
    try:
        api_url, api_key = get_effective_config()
        if api_url and "NMEM_API_URL" not in run_env:
            run_env["NMEM_API_URL"] = api_url
        if api_key and "NMEM_API_KEY" not in run_env:
            run_env["NMEM_API_KEY"] = api_key
    except Exception:
        pass
    if env:
        run_env.update(env)

    # Safeguard spawned nmem CLI subprocess from accidentally picking up host ~/.nowledge-mem/config.json
    if run_env.get("NMEM_IGNORE_HOST_CONFIG", "").strip().lower() in ("1", "true", "yes"):
        if "NMEM_CONFIG_PATH" not in run_env:
            run_env["NMEM_CONFIG_PATH"] = os.devnull

    return subprocess.run(
        cmd,
        input=input_str,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=run_env,
        cwd=cwd,
        timeout=timeout,
        **_windows_no_window_kwargs(),
    )


def _is_pid_alive(pid: int) -> bool:
    """Check whether a given PID is currently active on the host system."""
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h_process:
                return False
            exit_code = ctypes.c_ulong()
            success = kernel32.GetExitCodeProcess(h_process, ctypes.byref(exit_code))
            kernel32.CloseHandle(h_process)
            STILL_ACTIVE = 259
            return bool(success and exit_code.value == STILL_ACTIVE)
        else:
            os.kill(pid, 0)
            return True
    except OSError as e:
        import errno

        return e.errno == errno.EPERM
    except Exception:
        return False


class FileLock:
    """A simple platform-independent directory/file locking mechanism using exclusive creation and PID ownership checking."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.acquired = False

    def __enter__(self):
        retries = 25
        while retries > 0:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, str(os.getpid()).encode("utf-8"))
                finally:
                    os.close(fd)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    if self.lock_path.exists():
                        content = self.lock_path.read_text(encoding="utf-8").strip()
                        owner_pid = int(content) if content.isdigit() else 0
                        if owner_pid > 0 and not _is_pid_alive(owner_pid):
                            # Stale orphan lock from a dead process -> safe to unlink
                            self.lock_path.unlink(missing_ok=True)
                            continue
                        elif owner_pid == 0 and (time.time() - self.lock_path.stat().st_mtime) > 10.0:
                            # Corrupted or unreadable lock file older than 10s -> safe to unlink
                            self.lock_path.unlink(missing_ok=True)
                            continue
                except Exception:
                    pass
                time.sleep(0.1)
                retries -= 1
        raise TimeoutError(f"Could not acquire lock on {self.lock_path} within timeout.")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception:
                pass


def get_unsynced_queue_path() -> Path:
    """Return the Path to the unsynced sessions queue file, migrating any legacy files if needed.

    New path: ~/.nowledge-mem/plugins/antigravity/unsynced.json
    Legacy paths:
      1. ~/.nowledge-mem/cache/antigravity_unsynced.json
      2. ~/.nowledge-mem/antigravity_unsynced.json

    If the new path does not exist but legacy paths do, migrates legacy files
    to the new path atomically so that existing backlogs are preserved.
    If both exist, merges legacy sessions into the new queue file.
    """
    plugin_dir = get_plugin_storage_dir()
    new_path = plugin_dir / "unsynced.json"

    legacy_paths = [
        Path("~/.nowledge-mem/cache/antigravity_unsynced.json").expanduser(),
        Path("~/.nowledge-mem/antigravity_unsynced.json").expanduser(),
    ]

    for old_path in legacy_paths:
        if old_path.exists():
            try:
                plugin_dir.mkdir(parents=True, exist_ok=True)
                old_lock = old_path.with_suffix(".lock")
                new_lock = new_path.with_suffix(".lock")
                with FileLock(old_lock), FileLock(new_lock):
                    if old_path.exists():
                        try:
                            old_data = json.loads(old_path.read_text(encoding="utf-8"))
                        except Exception:
                            old_data = None

                        if isinstance(old_data, dict) and old_data:
                            merged = {}
                            if new_path.exists():
                                try:
                                    merged = json.loads(new_path.read_text(encoding="utf-8"))
                                    if not isinstance(merged, dict):
                                        merged = {}
                                except Exception:
                                    merged = {}
                            for k, v in old_data.items():
                                if k not in merged:
                                    merged[k] = v
                            new_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

                        try:
                            old_path.unlink(missing_ok=True)
                        except Exception:
                            pass
            except Exception as e:
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Warning: Failed to migrate unsynced sessions queue from {old_path}: {e}\n")
                if not new_path.exists():
                    return old_path

    return new_path


def save_unsynced_session(
    conv_id: str, messages: list, title: str, space: str | None, host_agent_id: str | None
) -> None:
    """Save a failed session to the unsynced queue file."""
    if not space:
        space = resolve_space()
    queue_path = get_unsynced_queue_path()
    queue_path.parent.mkdir(parents=True, exist_ok=True)
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
                "host_agent_id": host_agent_id,
            }

            # Save back
            try:
                queue_path.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Warning: Failed to write to unsynced sessions file: {e}\n")


def get_unsynced_sessions() -> dict:
    """Return dict of pending unsynced sessions from the queue file."""
    queue_path = get_unsynced_queue_path()
    if not queue_path.exists():
        return {}
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def retry_unsynced_sessions() -> None:
    """Attempt to sync any unsynced sessions in the queue."""
    queue_path = get_unsynced_queue_path()
    lock_path = queue_path.with_suffix(".lock")

    if not queue_path.exists():
        return

    try:
        with FileLock(lock_path):
            try:
                raw_text = queue_path.read_text(encoding="utf-8").strip()
                queue = json.loads(raw_text) if raw_text else {}
            except Exception:
                try:
                    queue_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return

            if not queue:
                try:
                    queue_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return

            updated_queue = dict(queue)
            for conv_id, data in queue.items():
                if is_backend_unreachable():
                    break

                messages = data.get("messages", [])
                title = data.get("title", f"Antigravity Session {conv_id[:8]}")
                space = data.get("space")
                host_agent_id = data.get("host_agent_id")

                matched_count = 0
                success = False
                # Try HTTP transport first
                try:
                    space_param = f"?space={space}" if space else ""
                    check_res = http_request(f"/threads/{conv_id}{space_param}", method="GET", timeout=1.5)
                    if isinstance(check_res, dict) and (
                        check_res.get("id") or check_res.get("thread_id") or "messages" in check_res
                    ):
                        existing_msgs = check_res.get("messages") or []
                        if isinstance(existing_msgs, list):
                            for old_m, new_m in zip(existing_msgs, messages, strict=False):
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
                            rec_res = http_request(
                                f"/threads/{conv_id}/reconcile-tail", method="POST", payload=rec_payload, timeout=2.5
                            )
                            if isinstance(rec_res, dict) and not rec_res.get("error"):
                                success = True
                            else:
                                # Fallback to append if reconcile-tail fails (e.g. prefix divergence)
                                app_payload = {"messages": messages[matched_count:]}
                                if space:
                                    app_payload["space"] = space
                                app_res = http_request(
                                    f"/threads/{conv_id}/append", method="POST", payload=app_payload, timeout=2.5
                                )
                                if isinstance(app_res, dict) and not app_res.get("error"):
                                    success = True
                        else:
                            app_payload = {"messages": messages}
                            if space:
                                app_payload["space"] = space
                            app_res = http_request(
                                f"/threads/{conv_id}/append", method="POST", payload=app_payload, timeout=2.5
                            )
                            if isinstance(app_res, dict) and not app_res.get("error"):
                                success = True
                    elif isinstance(check_res, dict) and (
                        check_res.get("error") in ("not_found", "thread_not_found") or check_res.get("status") == 404
                    ):
                        # Explicit 404 / thread not found -> import new thread
                        import_payload = {
                            "id": conv_id,
                            "title": title,
                            "source": "google-antigravity",
                            "messages": messages,
                        }
                        if space:
                            import_payload["space"] = space
                        imp_res = http_request("/threads/import", method="POST", payload=import_payload, timeout=2.5)
                        if isinstance(imp_res, dict) and not imp_res.get("error"):
                            success = True
                except Exception:
                    pass

                if is_backend_unreachable():
                    break

                if not success:
                    # CLI Fallback
                    check_args = ["t", "show", conv_id]
                    if space:
                        check_args.extend(["--space", space])

                    thread_exists = False
                    try:
                        result = run_nmem_command(check_args, timeout=2.0)
                        if result.returncode == 0:
                            thread_exists = True
                    except Exception:
                        pass

                    if thread_exists:
                        # Append trailing unmatched messages to existing thread
                        cli_append_msgs = messages[matched_count:] if matched_count > 0 else messages
                        if cli_append_msgs:
                            append_args = ["t"]
                            if space:
                                append_args.extend(["--space", space])
                            append_args.extend(["append", conv_id, "-m", json.dumps(cli_append_msgs)])
                            try:
                                result = run_nmem_command(append_args, timeout=3.0)
                                if result.returncode == 0:
                                    success = True
                            except Exception:
                                pass
                        else:
                            success = True
                    else:
                        # Import new thread
                        import_args = [
                            "t",
                            "import",
                            "-m",
                            json.dumps(messages),
                            "--id",
                            conv_id,
                            "-t",
                            title,
                            "-s",
                            "google-antigravity",
                        ]
                        if space:
                            import_args.extend(["--space", space])

                        env = {}
                        if host_agent_id:
                            env["NMEM_HOST_AGENT_ID"] = host_agent_id
                        try:
                            result = run_nmem_command(import_args, env=env, timeout=3.0)
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
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Warning: Failed to lock unsynced sessions file for retry: {e}\n")


def sync_learnings_if_any(
    conversation_id: str, transcript_path: str, artifact_directory_path: str, space: str | None
) -> None:
    """Scan for learning_proposal.md, verify approval in transcript, and sync to nmem (as rule, skill, or memory)."""
    if not space:
        space = resolve_space()
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
        with open(transcript_path, encoding="utf-8") as f:
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
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Error checking transcript for approval: {e}\n")

    if not approved:
        return

    # Parse learning_proposal.md
    try:
        content = proposal_path.read_text(encoding="utf-8")
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Error reading learning proposal: {e}\n")
        return

    # Extract title
    title_match = re.search(r"^#\s*Learning\s+Proposal\s*-\s*(.*)$", content, re.IGNORECASE | re.MULTILINE)
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
    code_block_match = re.search(
        r"```(?:markdown|properties|text|bash|sh|json|yaml|diff|python)?\s*\n([\s\S]*?)\n```",
        proposed_additions_part,
        re.IGNORECASE,
    )
    if code_block_match:
        rule_content = code_block_match.group(1).strip()
    else:
        lines = [line.strip() for line in proposed_additions_part.splitlines()]
        if lines and lines[0].lower().startswith("## proposed additions"):
            lines = lines[1:]
        rule_content = "\n".join(lines).strip()

    # Avoid repeated syncing (performance optimization)
    proposal_hash = hashlib.sha256(rule_content.encode("utf-8")).hexdigest()
    synced_state_file = Path(artifact_directory_path) / ".nmem_synced"
    synced_hashes = []
    if synced_state_file.exists():
        try:
            synced_hashes = json.loads(synced_state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if proposal_hash in synced_hashes:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Learning proposal already synced (hash: {proposal_hash}). Skipping.\n")
        return

    # Detect skills and rules
    is_rule = False
    is_skill = False

    # Check classification type
    type_match = re.search(r"-\s*\*\*Type\*\*\s*:\s*(.*)", content, re.IGNORECASE)
    if type_match:
        type_str = type_match.group(1).lower()
        if "rule" in type_str:
            is_rule = True
        if "skill" in type_str:
            is_skill = True

    # Parse target files in the proposal to determine if rules or skills are modified
    skill_dirs = []
    file_urls = re.findall(r"file://([^\s\)\?\#]+)", content)
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
            enroll_args = ["skills", "enroll", s_dir, "-y"]
            try:
                result = run_nmem_command(enroll_args, timeout=15)
                if result.returncode == 0:
                    synced_any = True
                    if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                        sys.stderr.write(f"Successfully enrolled skill to nmem: {s_dir}\n")
                else:
                    if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                        sys.stderr.write(f"Failed to enroll skill to nmem: {result.stderr}\n")
            except Exception as e:
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Error enrolling skill: {e}\n")

    # 2. Sync Rules
    if is_rule:
        # Avoid CLI length limits by writing rule body to a temporary file
        temp_body_file = Path(artifact_directory_path) / f".temp_rule_{memory_id}.md"
        try:
            temp_body_file.write_text(rule_content, encoding="utf-8")
            upsert_args = ["rules", "upsert", memory_id, "--title", title, "--body-file", str(temp_body_file)]
            if space:
                upsert_args.extend(["--space", space])

            result = run_nmem_command(upsert_args, timeout=15)
            if result.returncode == 0:
                synced_any = True
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Successfully upserted rule to nmem. ID: {memory_id}\n")
            else:
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Failed to upsert rule to nmem: {result.stderr}\n")
        except Exception as e:
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
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
            "memories",
            "add",
            "--id",
            memory_id,
            "--unit-type",
            "learning",
            "--source-thread",
            conversation_id,
            "--source",
            "google-antigravity",
            "--stdin",
        ]
        if space:
            add_args.extend(["--space", space])
        for label in set(labels):
            add_args.extend(["--label", label])
        add_args.extend(["--title", title])

        try:
            result = run_nmem_command(add_args, input_str=rule_content, timeout=15)
            if result.returncode == 0:
                synced_any = True
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Successfully upserted memory to nmem. ID: {memory_id}\n")
            else:
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Failed to upsert memory to nmem: {result.stderr}\n")
        except Exception as e:
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write(f"Error executing nmem memories add: {e}\n")

    # Mark as synced to prevent repeated sync operations on subsequent steps
    if synced_any:
        if proposal_hash not in synced_hashes:
            synced_hashes.append(proposal_hash)
        try:
            synced_state_file.write_text(json.dumps(synced_hashes), encoding="utf-8")
        except Exception:
            pass


def resolve_session_dir(
    artifact_dir: str | Path | None = None,
    transcript_path: str | Path | None = None,
    conversation_id: str | None = None,
) -> Path | None:
    """Resolve the session/artifact directory for the active conversation."""
    if artifact_dir:
        p = Path(artifact_dir).expanduser()
        if p.exists() or p.parent.exists():
            return p

    if transcript_path:
        tp = Path(transcript_path).expanduser()
        if ".system_generated" in tp.parts:
            idx = tp.parts.index(".system_generated")
            return Path(*tp.parts[:idx])
        elif tp.parent.exists():
            return tp.parent

    if conversation_id:
        brain_path = Path("~/.gemini/antigravity/brain").expanduser() / conversation_id
        if brain_path.exists():
            return brain_path

    return None


def retry_unsynced_sessions_async(cooldown_seconds: float = 15.0) -> None:
    """Spawns an asynchronous background worker to retry unsynced sessions without blocking, guarded by a PID/cooldown lock."""
    try:
        plugin_dir = get_plugin_storage_dir()
        cache_dir = plugin_dir / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        pid_file = cache_dir / "retry_worker.pid"

        now = time.time()
        if pid_file.exists():
            try:
                content = pid_file.read_text(encoding="utf-8").strip()
                if content:
                    parts = content.split(":")
                    pid = int(parts[0]) if parts[0].isdigit() else 0
                    spawn_time = float(parts[1]) if len(parts) > 1 and parts[1].replace(".", "", 1).isdigit() else 0.0
                    # If process is still alive, or was spawned less than cooldown_seconds ago
                    if pid > 0 and _is_pid_alive(pid):
                        return
                    if (now - spawn_time) < cooldown_seconds:
                        return
            except Exception:
                pass

        session_start_script = Path(__file__).parent / "session-start.py"
        proc = subprocess.Popen(
            [sys.executable, str(session_start_script), "--retry-only"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            **_windows_no_window_kwargs(),
        )
        try:
            pid_file.write_text(f"{proc.pid}:{now}", encoding="utf-8")
        except Exception:
            pass
    except Exception:
        pass


def should_emit_unsynced_warning(
    conversation_id: str | None = None,
    cooldown_seconds: float = 1800.0,
    session_dir: str | Path | None = None,
    transcript_path: str | Path | None = None,
) -> bool:
    """Check if the unsynced session warning should be emitted, prioritizing session directory tracking."""
    sdir = resolve_session_dir(session_dir, transcript_path, conversation_id)
    if sdir:
        warning_file = sdir / ".nmem" / "warning_history.json"
        if warning_file.exists():
            try:
                data = json.loads(warning_file.read_text(encoding="utf-8"))
                now = time.time()
                last_emitted = data.get("last_unsynced_warning", 0.0)
                return (now - last_emitted) >= cooldown_seconds
            except Exception:
                return True
        return True

    # Fallback to global cache if session dir cannot be resolved
    config_dir = Path("~/.nowledge-mem").expanduser()
    cache_file = config_dir / "cache" / "warning_history.json"
    if not cache_file.exists():
        return True
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        now = time.time()
        if conversation_id and conversation_id in data.get("conversations", {}):
            return False
        last_emitted = data.get("last_unsynced_warning", 0.0)
        return (now - last_emitted) >= cooldown_seconds
    except Exception:
        return True


def record_unsynced_warning_emitted(
    conversation_id: str | None = None, session_dir: str | Path | None = None, transcript_path: str | Path | None = None
) -> None:
    """Record that an unsynced session warning was emitted in the session directory."""
    now = time.time()
    sdir = resolve_session_dir(session_dir, transcript_path, conversation_id)
    if sdir:
        try:
            nmem_dir = sdir / ".nmem"
            nmem_dir.mkdir(parents=True, exist_ok=True)
            warning_file = nmem_dir / "warning_history.json"
            data = {"last_unsynced_warning": now}
            warning_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # Also record in global fallback
    try:
        cache_dir = Path("~/.nowledge-mem/cache").expanduser()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / "warning_history.json"
        data = {}
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data["last_unsynced_warning"] = now
        if conversation_id:
            convs = data.get("conversations", {})
            convs[conversation_id] = now
            data["conversations"] = convs
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def should_allow_catchup(
    horizon: str = "today",
    conversation_id: str | None = None,
    cooldown_seconds: float = 3600.0,
    session_dir: str | Path | None = None,
    transcript_path: str | Path | None = None,
) -> tuple[bool, str]:
    """Check whether a memory catchup execution should proceed, checking session directory history first.

    Returns (should_proceed, reason_if_suppressed).
    """
    now = time.time()
    sdir = resolve_session_dir(session_dir, transcript_path, conversation_id)

    # 1. Check session directory catchup history
    if sdir:
        session_catchup_file = sdir / ".nmem" / "catchup_history.json"
        if session_catchup_file.exists():
            try:
                data = json.loads(session_catchup_file.read_text(encoding="utf-8"))
                if horizon in data:
                    last_time = data[horizon]
                    if (now - last_time) < cooldown_seconds:
                        return (
                            False,
                            f"Memory catch-up for horizon '{horizon}' has already been executed in this session.",
                        )
            except Exception:
                pass

    # 2. Check global horizon cooldown in cache
    config_dir = Path("~/.nowledge-mem").expanduser()
    cache_file = config_dir / "cache" / "catchup_history.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            global_runs = data.get("global", {})
            if horizon in global_runs:
                last_time = global_runs[horizon]
                if (now - last_time) < cooldown_seconds:
                    mins_ago = max(1, int((now - last_time) / 60))
                    return (
                        False,
                        f"Memory catch-up for horizon '{horizon}' was already executed {mins_ago}m ago (cooldown is {int(cooldown_seconds / 60)}m).",
                    )
        except Exception:
            pass

    return True, ""


def record_catchup_execution(
    horizon: str = "today",
    conversation_id: str | None = None,
    session_dir: str | Path | None = None,
    transcript_path: str | Path | None = None,
) -> None:
    """Record execution of trigger_memory_catchup in the session directory and global cache."""
    now = time.time()
    sdir = resolve_session_dir(session_dir, transcript_path, conversation_id)

    # 1. Record in session directory
    if sdir:
        try:
            nmem_dir = sdir / ".nmem"
            nmem_dir.mkdir(parents=True, exist_ok=True)
            session_file = nmem_dir / "catchup_history.json"
            data = {}
            if session_file.exists():
                try:
                    data = json.loads(session_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data[horizon] = now
            session_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # 2. Record in global cache
    try:
        config_dir = Path("~/.nowledge-mem").expanduser()
        cache_dir = config_dir / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / "catchup_history.json"
        data = {}
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        global_runs = data.get("global", {})
        global_runs[horizon] = now
        data["global"] = global_runs
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass
