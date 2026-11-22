# Shared Nowledge Mem plugin hook utilities
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


def read_hook_payload() -> dict | None:
    """Return the hook payload from stdin as a dict, or None when it cannot be read.

    Read bytes and decode UTF-8 here: on Windows a piped stdin decodes with the
    locale code page, which fails on non-ASCII payloads.
    """
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_hook_input() -> dict:
    """Return the hook payload from stdin, or {} when it cannot be read."""
    payload = read_hook_payload()
    return payload if payload is not None else {}


def emit(payload: dict) -> None:
    """Write JSON to stdout and flush."""
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


class SpaceResolutionError(ValueError):
    """Raised when an explicit workspace Space configuration is unsafe to use."""


_ACTIVE_WORKSPACE_ROOT: Path | None = None


def workspace_root_from_hook_input(hook_input: dict) -> Path | None:
    """Return the workspace root supplied by Antigravity's hook payload.

    Hook commands run from the directory containing hooks.json, not from the
    user's project. Antigravity provides workspacePaths for this purpose. For a
    multi-root payload, prefer the root containing the session transcript and
    otherwise preserve the host-provided order.
    """
    raw_paths = hook_input.get("workspacePaths")
    if not isinstance(raw_paths, list):
        return None

    workspace_paths = []
    for raw_path in raw_paths:
        if isinstance(raw_path, str) and raw_path.strip():
            try:
                workspace_paths.append(Path(raw_path).expanduser().resolve())
            except (OSError, RuntimeError):
                continue
    if not workspace_paths:
        return None

    transcript_path = hook_input.get("transcriptPath")
    if isinstance(transcript_path, str) and transcript_path.strip():
        try:
            transcript = Path(transcript_path).expanduser().resolve()
            containing_roots = [root for root in workspace_paths if transcript.is_relative_to(root)]
            if containing_roots:
                return max(containing_roots, key=lambda root: len(root.parts))
        except (OSError, RuntimeError):
            pass

    return workspace_paths[0]


def activate_hook_workspace(hook_input: dict) -> Path | None:
    """Bind shared config/transport helpers to this hook process's workspace."""
    return activate_workspace_root(workspace_root_from_hook_input(hook_input))


def activate_workspace_root(workspace_root: str | Path | None) -> Path | None:
    """Bind shared helpers to an explicitly supplied workspace root."""
    global _ACTIVE_WORKSPACE_ROOT
    _ACTIVE_WORKSPACE_ROOT = Path(workspace_root).expanduser().resolve() if workspace_root is not None else None
    return _ACTIVE_WORKSPACE_ROOT


def _effective_cwd(cwd: str | Path | None) -> str | Path | None:
    return cwd if cwd is not None else _ACTIVE_WORKSPACE_ROOT


def format_space_resolution_error(error: Exception) -> str:
    return f"[Nowledge Mem] Project Space configuration error: {error}"


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
    cwd = _effective_cwd(cwd)
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


_capture_connection: ContextVar = ContextVar("capture_connection", default=None)


@contextmanager
def pin_capture_connection():
    connection = get_effective_config()
    token = _capture_connection.set(connection)
    try:
        yield connection
    finally:
        _capture_connection.reset(token)


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
    pinned = _capture_connection.get()
    if pinned is not None:
        return pinned
    cwd = _effective_cwd(cwd)
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
        prior_url = api_url
        plugin_cfg = get_plugin_config()
        plugin_url = str(plugin_cfg.get("apiUrl") or plugin_cfg.get("api_url") or "").strip().rstrip("/")
        plugin_key = str(plugin_cfg.get("apiKey") or plugin_cfg.get("api_key") or "").strip() or None
        if not api_url and plugin_url:
            api_url = plugin_url
        if not api_key and plugin_key:
            if not prior_url or prior_url.rstrip("/") == plugin_url:
                api_key = plugin_key

    # 4. Check global config file (~/.nowledge-mem/config.json)
    if not ignore_host and (not api_url or not api_key):
        prior_url = api_url
        custom_cfg = os.environ.get("NMEM_CONFIG_PATH", "").strip()
        cli_cfg_dir = os.environ.get("NMEM_CLI_CONFIG_DIR", "").strip()
        if custom_cfg:
            config_file = Path(custom_cfg).expanduser()
        elif cli_cfg_dir:
            config_file = Path(cli_cfg_dir).expanduser() / "config.json"
        else:
            config_file = Path("~/.nowledge-mem/config.json").expanduser()
        if config_file.is_file():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                file_url = str(data.get("apiUrl") or data.get("api_url") or "").strip().rstrip("/")
                file_key = str(data.get("apiKey") or data.get("api_key") or "").strip() or None

                if not api_url and file_url:
                    api_url = file_url

                # Only reuse host API key if no higher-precedence API key was set
                # AND any higher-precedence API URL matches host file_url
                if not api_key and file_key:
                    if not prior_url or prior_url.rstrip("/") == file_url:
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


_SPACES_CACHE: dict = {"timestamp": 0.0, "spaces": None}


def _spaces_cache_scope_id(config: tuple[str, str | None] | None = None) -> str:
    """Derive a non-reversible connection scope ID from effective URL and credential."""
    api_url, api_key = config if config is not None else get_effective_config()
    raw = f"{api_url.rstrip('/')}\0{api_key or ''}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def get_existing_spaces(ttl: float = 60.0) -> list[dict] | None:
    """Fetch known spaces from the Nowledge Mem backend with connection-scoped memory & disk caching."""
    global _SPACES_CACHE
    now = time.time()
    plugin_dir = get_plugin_storage_dir()
    initial_config = get_effective_config()
    read_scope_id = _spaces_cache_scope_id(initial_config)
    read_mem_key = f"{read_scope_id}:{plugin_dir}"
    mem_entries = _SPACES_CACHE.get("entries")
    if isinstance(mem_entries, dict):
        cached = mem_entries.get(read_mem_key)
        if (
            isinstance(cached, dict)
            and isinstance(cached.get("spaces"), list)
            and (now - cached.get("timestamp", 0.0)) < ttl
        ):
            return cached["spaces"]

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

    # 1. Try connection-scoped entry in ~/.nowledge-mem/plugins/antigravity/cache/spaces_cache.json
    cache_file = cache_dir / "spaces_cache.json"
    if cache_file.is_file():
        try:
            raw_data = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(raw_data, dict) and isinstance(raw_data.get("entries"), dict):
                scoped_entry = raw_data["entries"].get(read_scope_id)
                if (
                    isinstance(scoped_entry, dict)
                    and isinstance(scoped_entry.get("spaces"), list)
                    and (now - float(scoped_entry.get("timestamp", 0.0))) < ttl
                ):
                    if not isinstance(_SPACES_CACHE.get("entries"), dict):
                        _SPACES_CACHE["entries"] = {}
                    _SPACES_CACHE["entries"][read_mem_key] = {
                        "timestamp": float(scoped_entry["timestamp"]),
                        "spaces": scoped_entry["spaces"],
                    }
                    return scoped_entry["spaces"]
            else:
                # Unscoped legacy list/object whose connection origin cannot be verified: discard it.
                cache_file.unlink(missing_ok=True)
        except Exception:
            pass

    spaces_data = None
    actual_config: tuple[str, str | None] = initial_config

    # 2. Try HTTP GET /spaces
    http_conn: list[tuple[str, str | None]] = []
    res = http_request("/spaces", method="GET", timeout=1.5, connection_out=http_conn)
    if isinstance(res, dict):
        if "spaces" in res and isinstance(res["spaces"], list):
            spaces_data = res["spaces"]
        elif "items" in res and isinstance(res["items"], list):
            spaces_data = res["items"]
    elif isinstance(res, list):
        spaces_data = res
    if spaces_data is not None:
        actual_config = http_conn[0] if http_conn else initial_config

    # 3. Fallback to CLI nmem --json spaces list if backend is not marked unreachable
    if spaces_data is None and not is_backend_unreachable():
        try:
            cli_before_config = get_effective_config()
            cli_env: dict[str, str] = {"NMEM_API_URL": cli_before_config[0]}
            if cli_before_config[1] is not None:
                cli_env["NMEM_API_KEY"] = cli_before_config[1]
            else:
                cli_env["NMEM_API_KEY"] = ""
                cli_env["NMEM_CLI_CONFIG_DIR"] = os.devnull
                cli_env["NMEM_CONFIG_PATH"] = os.devnull
            cli_conn: list[tuple[str, str | None]] = []
            cmd_res = run_nmem_command(
                ["--json", "spaces", "list"],
                env=cli_env,
                timeout=2.5,
                connection_out=cli_conn,
            )
            if cmd_res.returncode == 0 and cmd_res.stdout:
                parsed = json.loads(cmd_res.stdout)
                if isinstance(parsed, dict) and "spaces" in parsed and isinstance(parsed["spaces"], list):
                    spaces_data = parsed["spaces"]
                elif isinstance(parsed, list):
                    spaces_data = parsed
                if spaces_data is not None:
                    actual_config = cli_conn[0] if cli_conn else cli_before_config
        except Exception:
            pass

    if spaces_data is not None:
        write_now = time.time()
        write_plugin_dir = get_plugin_storage_dir()
        write_cache_dir = write_plugin_dir / "cache"
        write_cache_file = write_cache_dir / "spaces_cache.json"
        write_scope_id = _spaces_cache_scope_id(actual_config)
        write_mem_key = f"{write_scope_id}:{write_plugin_dir}"
        if not isinstance(_SPACES_CACHE.get("entries"), dict):
            _SPACES_CACHE["entries"] = {}
        _SPACES_CACHE["entries"][write_mem_key] = {"timestamp": write_now, "spaces": spaces_data}
        try:
            write_cache_dir.mkdir(parents=True, exist_ok=True)
            latest_disk_entries: dict = {}
            if write_cache_file.is_file():
                try:
                    latest_raw = json.loads(write_cache_file.read_text(encoding="utf-8"))
                    if isinstance(latest_raw, dict) and isinstance(latest_raw.get("entries"), dict):
                        latest_disk_entries = latest_raw["entries"]
                except Exception:
                    pass
            merged_entries = {
                k: v
                for k, v in latest_disk_entries.items()
                if isinstance(v, dict)
                and isinstance(v.get("spaces"), list)
                and (write_now - float(v.get("timestamp", 0.0))) < max(ttl, 300.0)
            }
            merged_entries[write_scope_id] = {"timestamp": write_now, "spaces": spaces_data}
            write_cache_file.write_text(json.dumps({"entries": merged_entries}), encoding="utf-8")
        except Exception:
            pass
        return spaces_data

    return None


def _read_explicit_workspace_space(target_dir: Path) -> str | None:
    """Read the first documented workspace-level Space declaration."""
    config_paths = (
        target_dir / ".config.json",
        target_dir / ".nmemspace",
        target_dir / ".nowledge" / "config.json",
    )
    for cfg_path in config_paths:
        if not cfg_path.is_file():
            continue
        try:
            if cfg_path.name == ".nmemspace":
                value = cfg_path.read_text(encoding="utf-8").strip()
            else:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("expected a JSON object")
                raw_value = data.get("space") or data.get("space_id")
                value = raw_value.strip() if isinstance(raw_value, str) else ""
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise SpaceResolutionError(f"cannot read {cfg_path}: {error}") from error

        if value:
            return value
        if cfg_path.name != ".config.json":
            raise SpaceResolutionError(f"{cfg_path} does not name a Space")
    return None


def _matching_space_ids(space: str, spaces: list[dict]) -> set[str]:
    expected = space.lower()
    matches = set()
    for item in spaces:
        if not isinstance(item, dict):
            continue
        space_id = item.get("id")
        if not isinstance(space_id, str) or not space_id.strip():
            continue
        aliases = item.get("aliases")
        identifiers = [space_id, item.get("key"), item.get("name")]
        if isinstance(aliases, list):
            identifiers.extend(aliases)
        if any(isinstance(value, str) and value.strip().lower() == expected for value in identifiers):
            matches.add(space_id.strip())
    return matches


def _validate_explicit_workspace_space(space: str, target_dir: Path) -> None:
    existing_spaces = get_existing_spaces()
    if existing_spaces is None:
        # Preserve the explicit destination while offline so session-end can
        # queue it and retry when the backend is reachable.
        return

    expected = space.lower()
    for space_obj in existing_spaces:
        if not isinstance(space_obj, dict):
            continue
        identifiers = [space_obj.get("id"), space_obj.get("key"), space_obj.get("name")]
        aliases = space_obj.get("aliases")
        if isinstance(aliases, list):
            identifiers.extend(aliases)
        if any(isinstance(value, str) and value.strip().lower() == expected for value in identifiers):
            return
    raise SpaceResolutionError(f"Space '{space}' from {target_dir} is not available on the connected server")


def validate_workspace_space_configuration(cwd: str | Path | None) -> None:
    """Validate a workspace declaration without triggering dynamic inference."""
    env_space = os.environ.get("NMEM_SPACE", "").strip() or os.environ.get("NMEM_SPACE_ID", "").strip()
    if env_space:
        return
    if cwd is None:
        return
    target_dir = Path(cwd).resolve()
    workspace_space = _read_explicit_workspace_space(target_dir)
    if workspace_space:
        _validate_explicit_workspace_space(workspace_space, target_dir)


def resolve_space(cwd: str | Path | None = None, *, validate_explicit: bool = False) -> str:
    return _resolve_space_selection(cwd, validate_explicit=validate_explicit) or "default"


def _resolve_space_selection(cwd: str | Path | None, *, validate_explicit: bool = False, mcp_spaces=None) -> str | None:
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
    cwd = _effective_cwd(cwd)

    # 1. Check explicit environment override
    env_space = os.environ.get("NMEM_SPACE", "").strip() or os.environ.get("NMEM_SPACE_ID", "").strip()
    if env_space:
        return env_space

    target_dir = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    plugin_root = Path(__file__).parent.parent.resolve()

    # 2-3. Check documented explicit workspace config files.
    workspace_space = _read_explicit_workspace_space(target_dir)
    if workspace_space:
        if validate_explicit:
            _validate_explicit_workspace_space(workspace_space, target_dir)
        return workspace_space

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

    if mcp_spaces is None:
        # 7. Dynamic space auto-detection from workspace directory
        candidate = target_dir.name.strip()
        if not candidate or candidate.lower() == "default":
            return None

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
        return None

    # 7. Infer a Space only when the workspace name matches the catalog.
    candidate = target_dir.name.strip()
    if not candidate or candidate.lower() == "default":
        return None
    existing_spaces = mcp_spaces()
    if existing_spaces is None:
        raise SpaceResolutionError("Cannot verify the workspace's inferred MCP Space on the connected server")
    if existing_spaces:
        matches = _matching_space_ids(candidate, existing_spaces)
        if len(matches) == 1:
            return next(iter(matches))
        if matches:
            raise SpaceResolutionError(f"MCP Space '{candidate}' matches more than one ID")
    return None


def _mcp_space_id(cwd: str | Path | None, client_config: tuple[str, str | None]) -> str | None:
    # Look up IDs on the same endpoint and credentials written into this file.
    # The hook catalog cache is shared and can belong to a different workspace.
    catalog = None
    fetched = False

    def fetch_spaces():
        nonlocal catalog, fetched
        if not fetched:
            fetched = True
            result = http_request("/spaces", client_config=client_config)
            if isinstance(result, dict):
                result = result.get("spaces", result.get("items"))
            if isinstance(result, list):
                catalog = result
        return catalog

    selected = _resolve_space_selection(cwd, mcp_spaces=fetch_spaces)
    if selected is None or selected == "default":
        return selected

    spaces = fetch_spaces()
    if spaces is not None:
        exact_ids = {item.get("id") for item in spaces if isinstance(item, dict) and isinstance(item.get("id"), str)}
        if selected in exact_ids:
            return selected
        matches = _matching_space_ids(selected, spaces)
        if len(matches) == 1:
            return next(iter(matches))
    raise SpaceResolutionError(f"Cannot resolve MCP Space '{selected}' to a unique ID on the connected server")


def _write_json_atomic(path: Path, data: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as staged:
        temporary = Path(staged.name)
        try:
            staged.write(json.dumps(data, indent=2) + "\n")
            staged.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def sync_mcp_config_file(mcp_config_path: str = None, cwd: str | Path | None = None) -> bool:
    """Synchronize plugin mcp_config.json with effective client configuration
    (~/.nowledge-mem/config.json or NMEM_API_URL/NMEM_API_KEY env vars).
    Returns True if mcp_config.json was updated, False if already up to date.
    """
    if mcp_config_path is None:
        mcp_config_path = str(Path(__file__).parent.parent / "mcp_config.json")

    api_url, api_key = get_effective_config(cwd)
    clean_url = api_url.rstrip("/")
    server_url = f"{clean_url}/mcp"

    p = Path(mcp_config_path)
    try:
        current_data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current_data = None

    headers = {"APP": "Google Antigravity"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-NMEM-API-Key"] = api_key

    try:
        space_id = _mcp_space_id(cwd, (api_url, api_key))
    except SpaceResolutionError:
        servers = current_data.get("mcpServers") if isinstance(current_data, dict) else None
        if isinstance(servers, dict) and "nowledge-mem" in servers:
            del servers["nowledge-mem"]
            try:
                _write_json_atomic(p, current_data)
            except OSError as exc:
                raise SpaceResolutionError(
                    "Could not disable Mem: the previous MCP connection may still be active. Check config file permissions."
                ) from exc
        raise
    if space_id is not None:
        headers["X-Nmem-Space-Id"] = space_id
    target_data = dict(current_data) if isinstance(current_data, dict) else {}
    servers = target_data.get("mcpServers")
    target_data["mcpServers"] = {
        **(servers if isinstance(servers, dict) else {}),
        "nowledge-mem": {"url": server_url, "headers": headers},
    }

    if current_data == target_data:
        return False

    try:
        _write_json_atomic(p, target_data)
        return True
    except OSError as exc:
        raise SpaceResolutionError(
            "Could not update Mem: the previous MCP connection may still be active. Check config file permissions."
        ) from exc


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


def _format_http_error_response(status_code: int, raw_body: bytes) -> dict:
    parsed = None
    if raw_body:
        try:
            candidate = json.loads(raw_body.decode("utf-8"))
            if isinstance(candidate, dict):
                parsed = candidate
        except Exception:
            pass
    result: dict = {"status": status_code, "error": f"http_{status_code}", "_http_error": True}
    if parsed is not None:
        for k, v in parsed.items():
            if k not in ("status", "error", "_http_error", "_is_json"):
                result[k] = v
        if isinstance(parsed.get("error"), str) and parsed["error"].strip():
            result["error"] = parsed["error"].strip()
        result["_is_json"] = True
    else:
        result["_is_json"] = False
    return result


def http_request(
    endpoint: str,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = 1.5,
    skip_circuit_breaker: bool = False,
    include_error_response: bool = False,
    connection_out: list[tuple[str, str | None]] | None = None,
    client_config: tuple[str, str | None] | None = None,
) -> dict | None:
    """Make a direct HTTP request to the Nowledge Mem backend using httpx prior to CLI fallback."""
    import urllib.error
    import urllib.request

    try:
        import httpx
    except ImportError:
        httpx = None

    if connection_out is not None:
        connection_out.clear()

    if not skip_circuit_breaker and is_backend_unreachable():
        return None

    api_url, api_key = client_config if client_config is not None else get_effective_config()
    url = f"{api_url.rstrip('/')}{endpoint}"

    headers = {"Content-Type": "application/json", "APP": "Google Antigravity"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-NMEM-API-Key"] = api_key
        headers["X-MEM-API-Key"] = api_key

    if httpx is not None:
        try:
            resp = httpx.request(method, url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code in (200, 201):
                parsed_body = resp.json() if resp.text else {}
                if connection_out is not None:
                    connection_out[:] = [(api_url, api_key)]
                reset_backend_unreachable()
                return parsed_body
            if resp.status_code in (401, 403) and client_config is None:
                api_url, api_key = get_effective_config()
                url = f"{api_url}{endpoint}"
                headers = {"Content-Type": "application/json", "APP": "Google Antigravity"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                    headers["X-NMEM-API-Key"] = api_key
                    headers["X-MEM-API-Key"] = api_key
                resp = httpx.request(method, url, json=payload, headers=headers, timeout=timeout)
                if resp.status_code in (200, 201):
                    parsed_body = resp.json() if resp.text else {}
                    if connection_out is not None:
                        connection_out[:] = [(api_url, api_key)]
                    reset_backend_unreachable()
                    return parsed_body
            if resp.status_code >= 500:
                mark_backend_unreachable()
            else:
                reset_backend_unreachable()
            if include_error_response:
                if connection_out is not None:
                    connection_out[:] = [(api_url, api_key)]
                raw_bytes = (
                    resp.content
                    if isinstance(getattr(resp, "content", None), bytes)
                    else (resp.text or "").encode("utf-8")
                )
                return _format_http_error_response(resp.status_code, raw_bytes)
            return None
        except Exception:
            pass

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status in (200, 201):
                body = resp.read().decode("utf-8")
                parsed_body = json.loads(body) if body else {}
                if connection_out is not None:
                    connection_out[:] = [(api_url, api_key)]
                reset_backend_unreachable()
                return parsed_body
    except urllib.error.HTTPError as e:
        err_status = e.code
        err_bytes = b""
        try:
            err_bytes = e.read()
        except Exception:
            pass
        e.close()
        if err_status in (401, 403) and client_config is None:
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
                        body = resp.read().decode("utf-8")
                        parsed_body = json.loads(body) if body else {}
                        if connection_out is not None:
                            connection_out[:] = [(api_url, api_key)]
                        reset_backend_unreachable()
                        return parsed_body
            except urllib.error.HTTPError as retry_http_err:
                err_status = retry_http_err.code
                try:
                    err_bytes = retry_http_err.read()
                except Exception:
                    err_bytes = b""
                retry_http_err.close()
            except Exception as retry_err:
                mark_backend_unreachable()
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"Retry HTTP request to {url} failed: {retry_err}\n")
                return None
        if err_status >= 500:
            mark_backend_unreachable()
        else:
            reset_backend_unreachable()
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"HTTP request to {url} failed with status {err_status}\n")
        if include_error_response:
            if connection_out is not None:
                connection_out[:] = [(api_url, api_key)]
            return _format_http_error_response(err_status, err_bytes)
        return None
    except Exception as e:
        mark_backend_unreachable()
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"HTTP request to {url} failed: {e}\n")

    return None


def _nmem_command() -> str | None:
    candidate = shutil.which("nmem") or shutil.which("nmem.cmd")
    if os.name == "nt" and (not candidate or candidate.lower().endswith((".cmd", ".bat"))):
        native = shutil.which("nmem.exe")
        if not native and os.environ.get("LOCALAPPDATA"):
            app = Path(os.environ["LOCALAPPDATA"]) / "Nowledge Mem"
            for path in (
                app / "_up_" / "rust-backend" / "nmem.exe",
                app / "rust-backend" / "nmem.exe",
                app / "nmem.exe",
            ):
                if path.is_file():
                    native = str(path)
                    break
        candidate = native or candidate
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
                    encoding="utf-8",
                    errors="strict",
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


def _build_nmem_command(nmem: str, *args: str) -> list[str] | str:
    if nmem.lower().endswith((".cmd", ".bat")):
        launcher = _cmd_exe_path(nmem)
        # A batch wrapper reparses its arguments when it forwards %*. Avoid
        # passing shell syntax or environment substitutions through either parse.
        if re.search(r'["%!\r\n]', launcher) or any(re.search(r"[&|<>^%!()\r\n]", arg) for arg in args):
            raise ValueError("This argument requires native nmem.exe")
        line = '""' + launcher + '" ' + subprocess.list2cmdline(args) + '"'
        if len(line.encode("utf-16-le")) // 2 + 1 > 8191:
            raise ValueError("Capture exceeds the Windows batch command limit")
        if os.name == "nt":
            # Pass /c its original quotation marks; Python would escape them a
            # second time if it converted this final command from a list.
            interpreter = str(Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe")
            return subprocess.list2cmdline([interpreter]) + " /d /v:off /s /c " + line
        return ["cmd.exe", "/d", "/v:off", "/s", "/c", line]
    command = [nmem, *args]
    if os.name == "nt" and len(subprocess.list2cmdline(command).encode("utf-16-le")) // 2 >= 32767:
        raise ValueError("Capture exceeds the Windows native command limit")
    return command


def run_nmem_command(
    args: list[str],
    env: dict | None = None,
    cwd: str | None = None,
    timeout: float | None = 5.0,
    input_str: str | None = None,
    connection_out: list[tuple[str, str | None]] | None = None,
) -> subprocess.CompletedProcess:
    """Run an nmem command, finding the binary, translating path arguments if needed, and executing safely."""
    if connection_out is not None:
        connection_out.clear()

    nmem = _nmem_command()
    if not nmem:
        raise FileNotFoundError("nowledge-mem: nmem command not found in PATH")

    is_cmd = nmem.lower().endswith((".cmd", ".bat"))

    processed_args = []
    for arg in args:
        if is_cmd and isinstance(arg, str) and (arg.startswith("/") or arg.startswith("./") or arg.startswith("../")):
            processed_args.append(_cmd_exe_path(arg))
        else:
            processed_args.append(arg)

    cmd = _build_nmem_command(nmem, *processed_args)

    run_env = os.environ.copy()
    effective_url = ""
    effective_key: str | None = None
    try:
        effective_url, effective_key = get_effective_config(cwd)
        if effective_url and not str(run_env.get("NMEM_API_URL") or "").strip():
            run_env["NMEM_API_URL"] = effective_url
        if effective_key and not str(run_env.get("NMEM_API_KEY") or "").strip():
            run_env["NMEM_API_KEY"] = effective_key
    except Exception:
        pass
    if env:
        run_env.update(env)

    if _capture_connection.get() is not None:
        run_env["NMEM_API_URL"], key = _capture_connection.get()
        run_env["NMEM_API_KEY"] = key or ""
        run_env["NMEM_CONFIG_PATH"] = os.devnull
        run_env["NMEM_CLI_CONFIG_DIR"] = os.devnull

    raw_url = str(run_env.get("NMEM_API_URL") or "").strip()
    final_url = (raw_url or effective_url or "http://127.0.0.1:14242").rstrip("/")
    run_env["NMEM_API_URL"] = final_url
    final_key = str(run_env.get("NMEM_API_KEY") or "").strip() or None

    # Safeguard spawned nmem CLI subprocess from accidentally picking up host ~/.nowledge-mem/config.json
    if run_env.get("NMEM_IGNORE_HOST_CONFIG", "").strip().lower() in ("1", "true", "yes"):
        if not str(run_env.get("NMEM_CONFIG_PATH") or "").strip():
            run_env["NMEM_CONFIG_PATH"] = os.devnull
        if not str(run_env.get("NMEM_CLI_CONFIG_DIR") or "").strip():
            run_env["NMEM_CLI_CONFIG_DIR"] = os.devnull

    proc = subprocess.run(
        cmd,
        input=input_str,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=run_env,
        cwd=cwd,
        timeout=timeout,
        **_windows_no_window_kwargs(),
    )
    if connection_out is not None:
        connection_out[:] = [(final_url, final_key)]
    return proc


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


def _load_capture_queue(path: Path) -> dict:
    """Read under the queue lock. Preserve undecodable records for manual recovery."""
    try:
        raw = path.read_text(encoding="utf-8").strip()
        queue = json.loads(raw) if raw else {}
        if not isinstance(queue, dict):
            raise ValueError("invalid capture queue")
        return queue
    except FileNotFoundError:
        return {}
    except ValueError:
        path.replace(path.with_name(f"{path.stem}.corrupt-{time.time_ns()}{path.suffix}"))
        return {}


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
                        old_data = _load_capture_queue(old_path)
                        merged = _load_capture_queue(new_path)
                        conflicts = False
                        changed = False
                        for key, value in old_data.items():
                            if key not in merged:
                                merged[key] = value
                                changed = True
                            elif merged[key] != value:
                                conflicts = True
                        if changed:
                            _write_json_atomic(new_path, merged)
                        if not conflicts:
                            old_path.unlink(missing_ok=True)
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
    if not messages:
        return
    if not space:
        space = resolve_space()
    queue_path = get_unsynced_queue_path()
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = queue_path.with_suffix(".lock")

    try:
        with FileLock(lock_path):
            queue = _load_capture_queue(queue_path)

            # Add/Update session
            scope = _spaces_cache_scope_id()
            key = conv_id
            if f"{scope}:{conv_id}" in queue or (key in queue and queue[key].get("connection_scope") != scope):
                key = f"{scope}:{conv_id}"
            queue[key] = {
                "connection_scope": scope,
                "conversation_id": conv_id,
                "messages": messages,
                "title": title,
                "space": space,
                "host_agent_id": host_agent_id,
            }

            _write_json_atomic(queue_path, queue)
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


def build_cli_thread_append_args(thread_id: str, messages: list[dict]) -> list[str]:
    """Build supported `nmem t append` CLI arguments (no `--space` flag on append)."""
    return ["t", "append", thread_id, "-m", json.dumps(messages, ensure_ascii=False, separators=(",", ":"))]


def build_cli_thread_create_args(
    thread_id: str, title: str, messages: list[dict], space: str | None = None
) -> list[str]:
    """Build create-only `nmem t create` CLI arguments (never relocates an existing thread)."""
    args = [
        "t",
        "create",
        "--id",
        thread_id,
        "-t",
        title,
        "-m",
        json.dumps(messages, ensure_ascii=False, separators=(",", ":")),
        "-s",
        "google-antigravity",
    ]
    if space:
        args.extend(["--space", space])
    return args


def is_cli_thread_not_found(result) -> bool:
    """Return True only when `nmem t show` explicitly reports the thread was not found."""
    if result is None or getattr(result, "returncode", 0) == 0:
        return False
    combined = f"{getattr(result, 'stderr', '') or ''}\n{getattr(result, 'stdout', '') or ''}".lower()
    if "thread_not_found" in combined:
        return True
    return "thread" in combined and any(marker in combined for marker in ("not found", "not_found", "does not exist"))


def build_http_thread_lookup_endpoint(thread_id: str, space: str | None = None) -> str:
    """Build URL-encoded `GET /threads/{thread_id}` endpoint with optional `space_id` guard."""
    import urllib.parse

    encoded_id = urllib.parse.quote(thread_id, safe="")
    if space:
        return f"/threads/{encoded_id}?space_id={urllib.parse.quote(space, safe='')}"
    return f"/threads/{encoded_id}"


def build_http_thread_create_payload(
    thread_id: str, title: str, messages: list[dict], space: str | None = None
) -> dict:
    """Build `POST /threads` (`ThreadCreateRequest`) payload using `thread_id` and optional `space_id`."""
    payload: dict = {
        "thread_id": thread_id,
        "title": title,
        "source": "google-antigravity",
        "messages": messages,
    }
    if space:
        payload["space_id"] = space
    return payload


def _is_http_ok(res: dict | None) -> bool:
    return isinstance(res, dict) and not res.get("_http_error") and not res.get("error")


def is_http_thread_not_found(res: dict | None) -> bool:
    """Return True only for an explicit thread-not-found error code or thread-specific 404 detail."""
    if not isinstance(res, dict):
        return False
    status = res.get("status")
    if status not in (None, 404):
        return False
    if res.get("error") == "thread_not_found" or res.get("error_code") == "thread_not_found":
        return True
    if status != 404 or res.get("_is_json") is False:
        return False
    detail = res.get("detail")
    return isinstance(detail, str) and detail.strip().lower() == "thread not found"


def extract_thread_space_id(payload: dict) -> str | None:
    """Extract the thread's `space_id` from a `GET /threads/{id}` response."""
    nested = payload.get("thread")
    candidates = (
        payload.get("space_id"),
        nested.get("space_id") if isinstance(nested, dict) else None,
        payload.get("space"),
        nested.get("space") if isinstance(nested, dict) else None,
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def spaces_match(expected_space: str, actual_space_id: str | None) -> bool:
    """Check whether `actual_space_id` matches `expected_space` (by exact ID, `default` casing, or Space name/alias)."""
    if not isinstance(actual_space_id, str) or not actual_space_id:
        return False
    if actual_space_id == expected_space:
        return True
    norm_expected = expected_space.strip().lower()
    if norm_expected == "default" and actual_space_id == "default":
        return True
    for ttl in (60.0, 0.0):
        entries = get_existing_spaces(ttl=ttl) or []
        found_id = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id") or entry.get("key")
            if entry_id != actual_space_id:
                continue
            found_id = True
            names = [entry.get("id"), entry.get("key"), entry.get("name")]
            aliases = entry.get("aliases")
            if isinstance(aliases, list):
                names.extend(aliases)
            if any(isinstance(n, str) and n.strip().lower() == norm_expected for n in names):
                return True
        if found_id:
            break
    return False


def _capture_prefix(detail, conv_id, messages, space):
    """Count verified persisted messages, or return None on identity/scope/content conflict."""
    if not _is_http_ok(detail):
        return None
    thread = detail.get("thread", detail)
    if not isinstance(thread, dict) or (thread.get("thread_id") or thread.get("id")) != conv_id:
        return None
    if space and not spaces_match(space, extract_thread_space_id(detail)):
        return None
    rows = detail.get("messages", thread.get("messages"))
    if not isinstance(rows, list):
        return None
    total = detail.get("total_messages", len(rows))
    if not isinstance(total, int) or total < len(rows) or len(rows) < min(total, len(messages)):
        return None
    for old, new in zip(rows, messages):
        if not isinstance(old, dict) or (old.get("role"), old.get("content")) != (new["role"], new["content"]):
            return None
    return min(len(messages), len(rows))


def sync_thread_capture(
    conv_id: str,
    title: str,
    messages: list[dict],
    space: str | None = None,
    host_agent_id: str | None = None,
) -> tuple[bool, bool]:
    """Synchronize a single thread via HTTP (with CLI fallback).

    Returns `(synced, stop_retrying_in_current_pass)`.
    """
    import urllib.parse

    if is_backend_unreachable():
        return False, True

    matched_count = 0
    http_handled = False
    encoded_id = urllib.parse.quote(conv_id, safe="")

    try:
        lookup_endpoint = build_http_thread_lookup_endpoint(conv_id, space)
        check_res = http_request(lookup_endpoint, method="GET", timeout=1.5, include_error_response=True)
        if _is_http_ok(check_res) and (
            check_res.get("id") or check_res.get("thread_id") or "messages" in check_res or "thread" in check_res
        ):
            http_handled = True
            thread = check_res.get("thread", check_res)
            if isinstance(thread, dict):
                returned_id = thread.get("thread_id") or thread.get("id")
                if returned_id is not None and returned_id != conv_id:
                    return False, True
            res_space = extract_thread_space_id(check_res)
            if space and res_space is not None and not spaces_match(space, res_space):
                return False, True

            existing_msgs = check_res.get("messages")
            if existing_msgs is None and isinstance(check_res.get("thread"), dict):
                existing_msgs = check_res["thread"].get("messages")
            existing_msgs = existing_msgs or []
            total = check_res.get("total_messages", len(existing_msgs))
            if not isinstance(total, int) or not isinstance(existing_msgs, list):
                return False, True
            if len(existing_msgs) < min(total, len(messages)):
                separator = "&" if "?" in lookup_endpoint else "?"
                full = http_request(
                    f"{lookup_endpoint}{separator}limit={len(messages)}",
                    method="GET",
                    timeout=2.5,
                    include_error_response=True,
                )
                if not _is_http_ok(full) or full.get("total_messages") != total:
                    return False, True
                if space and not spaces_match(space, extract_thread_space_id(full)):
                    return False, True
                existing_msgs = full.get("messages")
                if not isinstance(existing_msgs, list) or len(existing_msgs) < min(total, len(messages)):
                    return False, True
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
                if not space or spaces_match(space, res_space):
                    return True, True
                return False, True

            if matched_count > 0:
                rec_payload = {"matched_count": matched_count, "messages": messages[matched_count:]}
                if space:
                    rec_payload["space_id"] = space
                rec_res = http_request(
                    f"/threads/{encoded_id}/reconcile-tail",
                    method="POST",
                    payload=rec_payload,
                    timeout=2.5,
                    include_error_response=True,
                )
                return _is_http_ok(rec_res), True

            app_payload = {"messages": messages[matched_count:] if matched_count > 0 else messages}
            if space:
                app_payload["space_id"] = space
            app_res = http_request(
                f"/threads/{encoded_id}/append",
                method="POST",
                payload=app_payload,
                timeout=2.5,
                include_error_response=True,
            )
            if _is_http_ok(app_res):
                return True, True
            return False, True
        elif is_http_thread_not_found(check_res):
            http_handled = True
            create_payload = build_http_thread_create_payload(conv_id, title, messages, space)
            create_res = http_request(
                "/threads",
                method="POST",
                payload=create_payload,
                timeout=2.5,
                include_error_response=True,
            )
            if _is_http_ok(create_res):
                separator = "&" if "?" in lookup_endpoint else "?"
                persisted = http_request(
                    f"{lookup_endpoint}{separator}limit={len(messages)}", method="GET", timeout=2.5
                )
                return _capture_prefix(persisted, conv_id, messages, space) == len(messages), True
            return False, True
        elif isinstance(check_res, dict):
            return False, True
    except Exception:
        pass

    if is_backend_unreachable() or http_handled:
        return False, True

    # CLI Fallback (only when HTTP transport yielded None / could not complete)
    check_args = ["--json", "t", "show", conv_id, "--limit", str(len(messages))]
    if space:
        check_args.extend(["--space", space])

    thread_exists = False
    thread_not_found = False
    try:
        result = run_nmem_command(check_args, timeout=2.0)
        if result.returncode == 0:
            prefix = _capture_prefix(json.loads(result.stdout), conv_id, messages, space)
            if prefix is None:
                return False, True
            if prefix == len(messages):
                return True, True
            matched_count = prefix
            thread_exists = True
        elif is_cli_thread_not_found(result):
            thread_not_found = True
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"nmem t show execution failed: {e}\n")

    env = {"NMEM_HOST_AGENT_ID": host_agent_id} if host_agent_id else None

    if thread_exists:
        cli_append_msgs = messages[matched_count:] if matched_count > 0 else messages
        if cli_append_msgs:
            if space:
                return False, True
            append_args = build_cli_thread_append_args(conv_id, cli_append_msgs)
            try:
                result = (
                    run_nmem_command(append_args, env=env, timeout=3.0)
                    if env
                    else run_nmem_command(append_args, timeout=3.0)
                )
                if result.returncode == 0:
                    return True, True
            except Exception as e:
                if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                    sys.stderr.write(f"nmem t append execution failed: {e}\n")
        else:
            if not space:
                return True, True
            return False, True
    elif thread_not_found:
        create_args = build_cli_thread_create_args(conv_id, title, messages, space)
        try:
            result = (
                run_nmem_command(create_args, env=env, timeout=3.0)
                if env
                else run_nmem_command(create_args, timeout=3.0)
            )
            if result.returncode == 0:
                verified = run_nmem_command(check_args, timeout=2.0)
                return (
                    verified.returncode == 0
                    and _capture_prefix(json.loads(verified.stdout), conv_id, messages, space) == len(messages)
                ), True
        except Exception as e:
            if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
                sys.stderr.write(f"nmem t create execution failed: {e}\n")

    return False, False


def retry_unsynced_sessions() -> None:
    """Attempt to sync any unsynced sessions in the queue."""
    queue_path = get_unsynced_queue_path()
    lock_path = queue_path.with_suffix(".lock")

    if not queue_path.exists():
        return

    try:
        with FileLock(lock_path):
            queue = _load_capture_queue(queue_path)
            if not queue:
                try:
                    queue_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return

            updated_queue = dict(queue)
            for queue_key, data in queue.items():
                conv_id = data.get("conversation_id", queue_key)
                if data.get("messages") == []:
                    del updated_queue[queue_key]
                    continue
                if is_backend_unreachable():
                    continue

                messages = data.get("messages", [])
                title = data.get("title", f"Antigravity Session {conv_id[:8]}")
                space = data.get("space")
                host_agent_id = data.get("host_agent_id")

                with pin_capture_connection() as connection:
                    if data.get("connection_scope") != _spaces_cache_scope_id(connection):
                        continue
                    success, _ = sync_thread_capture(conv_id, title, messages, space, host_agent_id)
                if success:
                    del updated_queue[queue_key]

            if updated_queue == queue:
                return
            if updated_queue:
                _write_json_atomic(queue_path, updated_queue)
            else:
                queue_path.unlink(missing_ok=True)
    except Exception as e:
        if os.environ.get("DEBUG") or os.environ.get("NMEM_DEBUG"):
            sys.stderr.write(f"Warning: Failed to lock unsynced sessions file for retry: {e}\n")


def sync_learnings_if_any(
    conversation_id: str,
    transcript_path: str,
    artifact_directory_path: str,
    space: str | None = None,
    workspace_root: str | Path | None = None,
) -> None:
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

    if not space:
        space = resolve_space(workspace_root, validate_explicit=True)

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
                    # A live worker owns the retry lane. Bound this trust window
                    # so an OS-reused PID cannot suppress retries forever after
                    # a worker crashes without cleaning up its marker.
                    if pid > 0 and _is_pid_alive(pid) and (now - spawn_time) < 900.0:
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
