import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Add hooks directory to path to import nmem_shared and others
HOOKS_DIR = Path(__file__).parent.parent / "hooks"
PLUGIN_ROOT = HOOKS_DIR.parent
sys.path.insert(0, str(HOOKS_DIR))

import nmem_shared  # noqa: E402


def import_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


session_start = import_module_from_path("session_start", str(HOOKS_DIR / "session-start.py"))
session_end = import_module_from_path("session_end", str(HOOKS_DIR / "session-end.py"))
post_invocation = import_module_from_path("post_invocation", str(HOOKS_DIR / "post-invocation.py"))
nmem_gate = import_module_from_path("nmem_gate", str(HOOKS_DIR / "nmem-gate.py"))
nmem_status = import_module_from_path("nmem_status", str(HOOKS_DIR / "nmem_status.py"))
nmem_entrypoint = import_module_from_path("nmem_entrypoint", str(HOOKS_DIR / "nmem_entrypoint.py"))
load_skill = import_module_from_path(
    "load_skill", str(HOOKS_DIR.parent / "skills" / "nmem-skill-load" / "scripts" / "load_skill.py")
)
manage_skills = import_module_from_path(
    "manage_skills", str(HOOKS_DIR.parent / "skills" / "nmem-skill-manage" / "scripts" / "manage_skills.py")
)
update_artifact = import_module_from_path("update_artifact", str(HOOKS_DIR.parent / "scripts" / "update_artifact.py"))
update_working_memory = import_module_from_path(
    "update_working_memory", str(HOOKS_DIR.parent / "scripts" / "update_working_memory.py")
)
manage_rules = import_module_from_path("manage_rules", str(HOOKS_DIR.parent / "scripts" / "manage_rules.py"))
propose_skill = import_module_from_path(
    "propose_skill", str(HOOKS_DIR.parent / "skills" / "nmem-skill-propose" / "scripts" / "propose_skill.py")
)


@pytest.fixture(autouse=True)
def reset_active_workspace_root():
    nmem_shared.activate_workspace_root(None)
    yield
    nmem_shared.activate_workspace_root(None)


@patch("os.access", return_value=True)
@patch("pathlib.Path.is_file", return_value=True)
@patch("pathlib.Path.resolve")
@patch("shutil.which")
def test_nmem_command_resolution(mock_which, mock_resolve, mock_is_file, mock_access):
    # Case 1: nmem exists
    mock_which.side_effect = lambda x: "/usr/bin/nmem" if x == "nmem" else None
    mock_resolve.side_effect = lambda: Path("/usr/bin/nmem")
    assert nmem_shared._nmem_command() == "/usr/bin/nmem"

    # Case 2: nmem.cmd exists
    mock_which.side_effect = lambda x: "/usr/bin/nmem.cmd" if x == "nmem.cmd" else None
    mock_resolve.side_effect = lambda: Path("/usr/bin/nmem.cmd")
    assert nmem_shared._nmem_command() == "/usr/bin/nmem.cmd"


def test_cmd_exe_path_conversion():
    # WSL mount to Windows path
    assert nmem_shared._cmd_exe_path("/mnt/c/Users/test") == "C:\\Users\\test"
    # Already Windows path
    assert nmem_shared._cmd_exe_path("C:\\Users\\test") == "C:\\Users\\test"
    assert nmem_shared._cmd_exe_path("C:/Users/test") == "C:\\Users\\test"
    # Fallback to name
    assert nmem_shared._cmd_exe_path("nmem.cmd") == "nmem.cmd"


@patch("nmem_shared._cmd_exe_path")
def test_build_nmem_command(mock_cmd_path):
    mock_cmd_path.side_effect = lambda x: "C:\\bin\\nmem.cmd" if "nmem.cmd" in x else x

    # Non-Windows cmd path
    cmd = nmem_shared._build_nmem_command("/usr/bin/nmem", "t", "show")
    assert cmd == ["/usr/bin/nmem", "t", "show"]

    # Windows cmd path
    cmd = nmem_shared._build_nmem_command("C:\\bin\\nmem.cmd", "t", "show")
    assert cmd[0] == "cmd.exe"
    assert cmd[1] == "/s"
    assert cmd[2] == "/c"


@patch("nmem_shared._nmem_command")
@patch("subprocess.run")
def test_run_nmem_command(mock_run, mock_nmem_command):
    mock_nmem_command.return_value = "/usr/bin/nmem"
    mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")

    res = nmem_shared.run_nmem_command(["t", "show"])
    assert res.stdout == "success"
    mock_run.assert_called_once()


@patch("uuid.getnode")
@patch("socket.gethostname")
def test_host_agent_fingerprint(mock_gethostname, mock_getnode):
    mock_getnode.return_value = 123456789
    mock_gethostname.return_value = "my-host"

    fp = nmem_shared.get_host_agent_fingerprint()
    assert fp.startswith("antigravity-")


@patch("nmem_shared.get_effective_config")
def test_sync_mcp_config_file(mock_get_effective_config):
    mock_get_effective_config.return_value = ("https://mem.example.com", "nmem_sec_123")
    with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
        tf.write('{"mcpServers":{"nowledge-mem":{"serverUrl":"http://127.0.0.1:14242/mcp/"}}}')
        tf_path = tf.name
    try:
        updated = nmem_shared.sync_mcp_config_file(tf_path)
        assert updated is True
        data = json.loads(Path(tf_path).read_text(encoding="utf-8"))
        assert data["mcpServers"]["nowledge-mem"]["serverUrl"] == "https://mem.example.com/mcp/"
        assert data["mcpServers"]["nowledge-mem"]["headers"]["Authorization"] == "Bearer nmem_sec_123"
        assert data["mcpServers"]["nowledge-mem"]["headers"]["X-NMEM-API-Key"] == "nmem_sec_123"
        assert "X-MEM-API-Key" not in data["mcpServers"]["nowledge-mem"]["headers"]

        # Second call should return False (already up to date)
        updated_again = nmem_shared.sync_mcp_config_file(tf_path)
        assert updated_again is False
    finally:
        if os.path.exists(tf_path):
            os.unlink(tf_path)


@patch("threading.Thread")
@patch("nmem_shared._nmem_command")
@patch("nmem_shared.run_nmem_command")
def test_sync_host_skills_async(mock_run_cmd, mock_nmem_cmd, mock_thread):
    mock_nmem_cmd.return_value = "/usr/bin/nmem"

    def mock_start():
        target = mock_thread.call_args[1].get("target")
        if target:
            target()

    mock_thread.return_value.start.side_effect = mock_start

    nmem_shared.sync_host_skills_async()
    assert mock_run_cmd.called
    calls = [c[0][0] for c in mock_run_cmd.call_args_list]
    assert ["skills", "connect", "antigravity"] in calls
    assert ["skills", "sync"] in calls


def test_unsynced_queue_migration_from_legacy_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".nowledge-mem"
        config_dir.mkdir(parents=True, exist_ok=True)
        old_path1 = config_dir / "antigravity_unsynced.json"
        old_path2 = config_dir / "cache" / "antigravity_unsynced.json"
        old_path2.parent.mkdir(parents=True, exist_ok=True)
        new_path = config_dir / "plugins" / "antigravity" / "unsynced.json"

        # Write legacy queue files
        old_path1.write_text(json.dumps({"conv-legacy-1": {"title": "Legacy 1"}}), encoding="utf-8")
        old_path2.write_text(json.dumps({"conv-legacy-2": {"title": "Legacy 2"}}), encoding="utf-8")

        with patch.dict(os.environ, {"HOME": tmpdir}):
            assert not new_path.exists()
            q_path = nmem_shared.get_unsynced_queue_path()
            assert q_path == new_path
            assert new_path.exists()
            assert not old_path1.exists()
            assert not old_path2.exists()

            loaded = json.loads(new_path.read_text(encoding="utf-8"))
            assert "conv-legacy-1" in loaded
            assert "conv-legacy-2" in loaded


def test_unsynced_queue_migration_merges_with_existing_new_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".nowledge-mem"
        plugin_dir = config_dir / "plugins" / "antigravity"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        old_path = config_dir / "antigravity_unsynced.json"
        new_path = plugin_dir / "unsynced.json"

        # Both legacy and new queue files exist
        old_path.write_text(json.dumps({"conv-old": {"title": "Old Session"}}), encoding="utf-8")
        new_path.write_text(json.dumps({"conv-new": {"title": "New Session"}}), encoding="utf-8")

        with patch.dict(os.environ, {"HOME": tmpdir}):
            q_path = nmem_shared.get_unsynced_queue_path()
            assert q_path == new_path
            assert not old_path.exists()

            loaded = json.loads(new_path.read_text(encoding="utf-8"))
            assert "conv-old" in loaded
            assert "conv-new" in loaded


def test_spaces_cache_legacy_discard():
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".nowledge-mem"
        config_dir.mkdir(parents=True, exist_ok=True)
        old_cache1 = config_dir / "spaces_cache.json"
        old_cache2 = config_dir / "cache" / "spaces_cache.json"
        old_cache2.parent.mkdir(parents=True, exist_ok=True)
        new_cache = config_dir / "plugins" / "antigravity" / "cache" / "spaces_cache.json"

        old_cache1.write_text(json.dumps([{"id": "old_space_1"}]), encoding="utf-8")
        old_cache2.write_text(json.dumps([{"id": "old_space_2"}]), encoding="utf-8")

        with (
            patch.dict(os.environ, {"HOME": tmpdir}),
            patch("nmem_shared.http_request", return_value=[{"id": "new_space"}]),
        ):
            saved_cache = nmem_shared._SPACES_CACHE
            nmem_shared._SPACES_CACHE = {"timestamp": 0.0, "spaces": None}
            try:
                spaces = nmem_shared.get_existing_spaces()
                assert spaces == [{"id": "new_space"}]
                assert not old_cache1.exists()
                assert not old_cache2.exists()
                assert new_cache.exists()
            finally:
                nmem_shared._SPACES_CACHE = saved_cache


@patch("nmem_shared.FileLock")
@patch("pathlib.Path.exists")
@patch("pathlib.Path.mkdir")
@patch("pathlib.Path.write_text")
@patch("pathlib.Path.read_text")
def test_save_unsynced_session(mock_read, mock_write, mock_mkdir, mock_exists, mock_lock):
    mock_exists.return_value = False
    nmem_shared.save_unsynced_session("conv-1", [{"role": "user", "content": "hi"}], "title", "space", "host")
    mock_mkdir.assert_called_once()
    mock_write.assert_called_once()


@patch("subprocess.Popen")
def test_retry_unsynced_sessions_async(mock_popen):
    with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"HOME": tmpdir}):
        nmem_shared.retry_unsynced_sessions_async()
        mock_popen.assert_called_once()


def test_retry_unsynced_sessions_cleans_empty_file():
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch.dict(os.environ, {"HOME": tmpdir}),
    ):
        queue_file = nmem_shared.get_unsynced_queue_path()
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        queue_file.write_text("{}", encoding="utf-8")
        assert queue_file.exists()

        nmem_shared.retry_unsynced_sessions()
        assert not queue_file.exists()


def test_retry_unsynced_sessions_quarantines_corrupt_file():
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch.dict(os.environ, {"HOME": tmpdir}),
    ):
        queue_file = nmem_shared.get_unsynced_queue_path()
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        queue_file.write_text("NOT VALID JSON", encoding="utf-8")
        assert queue_file.exists()

        nmem_shared.retry_unsynced_sessions()
        assert not queue_file.exists()
        quarantine_files = list(queue_file.parent.glob("unsynced.corrupt-*.json"))
        assert len(quarantine_files) == 1
        assert quarantine_files[0].read_text(encoding="utf-8") == "NOT VALID JSON"


@patch("nmem_shared.http_request")
def test_retry_unsynced_sessions_success_unlinks_queue(mock_http):
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch.dict(os.environ, {"HOME": tmpdir}),
    ):
        queue_file = nmem_shared.get_unsynced_queue_path()
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        queue_file.write_text(
            json.dumps(
                {
                    "conv-1": {
                        "conversation_id": "conv-1",
                        "messages": [{"role": "user", "content": "hi"}],
                        "title": "Title",
                        "space": "default",
                        "host_agent_id": "host",
                    }
                }
            ),
            encoding="utf-8",
        )
        mock_http.side_effect = [
            {"error": "not_found"},
            {"id": "conv-1", "title": "Title"},
        ]
        assert queue_file.exists()
        nmem_shared.retry_unsynced_sessions()
        assert not queue_file.exists()


@patch("nmem_shared.is_backend_unreachable", return_value=False)
@patch("nmem_shared.run_nmem_command")
@patch("nmem_shared.http_request")
def test_retry_unsynced_sessions_reconciles_existing_thread_prefix(mock_http, mock_run, mock_unreachable):
    # Only the unsent tail goes to an existing thread; the whole transcript must not be appended again.
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch.dict(os.environ, {"HOME": tmpdir, "USERPROFILE": tmpdir}),
    ):
        queue_file = nmem_shared.get_unsynced_queue_path()
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        queue_file.write_text(
            json.dumps(
                {
                    "conv-1": {
                        "conversation_id": "conv-1",
                        "messages": [
                            {"role": "user", "content": "Save this"},
                            {"role": "assistant", "content": "Saved."},
                        ],
                        "title": "Title",
                        "space": "default",
                        "host_agent_id": "host",
                    }
                }
            ),
            encoding="utf-8",
        )
        mock_http.side_effect = [
            {"id": "conv-1", "messages": [{"role": "user", "content": "Save this"}]},
            {"status": "ok"},
        ]
        nmem_shared.retry_unsynced_sessions()

        assert mock_http.call_count == 2
        assert mock_http.call_args_list[1].args[0] == "/threads/conv-1/reconcile-tail"
        payload = mock_http.call_args_list[1].kwargs["payload"]
        assert payload["matched_count"] == 1
        assert payload["messages"] == [{"role": "assistant", "content": "Saved."}]
        mock_run.assert_not_called()
        assert not queue_file.exists()


def test_catchup_debounce():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir) / "session_1"
        session_dir.mkdir(parents=True, exist_ok=True)

        with patch.dict(os.environ, {"HOME": tmpdir}):
            allowed, reason = nmem_shared.should_allow_catchup("today", session_dir=session_dir)
            assert allowed is True
            assert reason == ""

            nmem_shared.record_catchup_execution("today", session_dir=session_dir)
            assert (session_dir / ".nmem" / "catchup_history.json").exists()

            allowed, reason = nmem_shared.should_allow_catchup("today", session_dir=session_dir)
            assert allowed is False
            assert "already been executed in this session" in reason

            other_session_dir = Path(tmpdir) / "session_2"
            allowed, reason = nmem_shared.should_allow_catchup("today", session_dir=other_session_dir)
            assert allowed is False
            assert "already executed" in reason


def test_unsynced_warning_throttle():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir) / "session_1"
        session_dir.mkdir(parents=True, exist_ok=True)

        with patch.dict(os.environ, {"HOME": tmpdir}):
            assert nmem_shared.should_emit_unsynced_warning("conv-1", session_dir=session_dir) is True
            nmem_shared.record_unsynced_warning_emitted("conv-1", session_dir=session_dir)
            assert (session_dir / ".nmem" / "warning_history.json").exists()
            assert nmem_shared.should_emit_unsynced_warning("conv-1", session_dir=session_dir) is False


@patch.dict(os.environ, {"NMEM_API_URL": "https://remote.example.com", "NMEM_API_KEY": "secret_key"})
def test_get_effective_config_env():
    url, key = nmem_shared.get_effective_config()
    assert url == "https://remote.example.com"
    assert key == "secret_key"


def test_get_effective_config_precedence():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_dir = Path(tmpdir) / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)
        local_cfg = ws_dir / ".config.json"
        local_cfg.write_text(json.dumps({"apiUrl": "https://local-mem.example.com", "apiKey": "local_key"}))

        plugin_cfg_dir = Path(tmpdir) / ".nowledge-mem" / "plugins" / "antigravity"
        plugin_cfg_dir.mkdir(parents=True, exist_ok=True)
        plugin_cfg = plugin_cfg_dir / "config.json"
        plugin_cfg.write_text(json.dumps({"apiUrl": "https://plugin-mem.example.com", "apiKey": "plugin_key"}))

        global_cfg_dir = Path(tmpdir) / ".nowledge-mem"
        global_cfg_dir.mkdir(parents=True, exist_ok=True)
        global_cfg = global_cfg_dir / "config.json"
        global_cfg.write_text(json.dumps({"apiUrl": "https://global-mem.example.com", "apiKey": "global_key"}))

        with patch.dict(os.environ, {"HOME": tmpdir}, clear=True):
            # 1. Global config when no overrides
            url, key = nmem_shared.get_effective_config(cwd=Path(tmpdir) / "empty")
            # Plugin config overrides global
            assert url == "https://plugin-mem.example.com"
            assert key == "plugin_key"

            # 2. Local workspace overrides plugin config
            url, key = nmem_shared.get_effective_config(cwd=ws_dir)
            assert url == "https://local-mem.example.com"
            assert key == "local_key"

            # 3. Env var overrides local
            with patch.dict(os.environ, {"NMEM_API_URL": "https://env-mem.example.com", "NMEM_API_KEY": "env_key"}):
                url, key = nmem_shared.get_effective_config(cwd=ws_dir)
                assert url == "https://env-mem.example.com"
                assert key == "env_key"


def test_resolve_space_precedence():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_dir = Path(tmpdir) / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)
        local_cfg = ws_dir / ".config.json"
        local_cfg.write_text(json.dumps({"space": "local-space"}))

        plugin_cfg_dir = Path(tmpdir) / ".nowledge-mem" / "plugins" / "antigravity"
        plugin_cfg_dir.mkdir(parents=True, exist_ok=True)
        plugin_cfg = plugin_cfg_dir / "config.json"
        plugin_cfg.write_text(json.dumps({"space": "plugin-space"}))

        global_cfg_dir = Path(tmpdir) / ".nowledge-mem"
        global_cfg_dir.mkdir(parents=True, exist_ok=True)
        global_cfg = global_cfg_dir / "config.json"
        global_cfg.write_text(json.dumps({"space": "global-space"}))

        with patch.dict(os.environ, {"HOME": tmpdir}, clear=True):
            # 1. Plugin config overrides global
            space = nmem_shared.resolve_space(cwd=Path(tmpdir) / "empty")
            assert space == "plugin-space"

            # 2. Local workspace overrides plugin config
            space = nmem_shared.resolve_space(cwd=ws_dir)
            assert space == "local-space"

            # 3. Env var overrides local
            with patch.dict(os.environ, {"NMEM_SPACE": "env-space"}):
                space = nmem_shared.resolve_space(cwd=ws_dir)
                assert space == "env-space"


@patch("pathlib.Path.is_file", return_value=True)
@patch("pathlib.Path.read_text")
def test_get_effective_config_ignore_host_config(mock_read_text, mock_is_file):
    mock_read_text.return_value = '{"apiUrl": "http://127.0.0.1:14242", "apiKey": "host_secret_key"}'
    env_dict = {"NMEM_IGNORE_HOST_CONFIG": "1", "NMEM_API_URL": "http://127.0.0.1:9999"}
    with patch.dict(os.environ, env_dict, clear=True):
        url, key = nmem_shared.get_effective_config()
        assert url == "http://127.0.0.1:9999"
        assert key is None


def test_get_effective_config_ignore_host_preserves_workspace_config():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_dir = Path(tmpdir) / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)
        local_cfg = ws_dir / ".config.json"
        local_cfg.write_text(json.dumps({"apiUrl": "https://ws-mem.example.com", "apiKey": "ws_key"}))

        global_cfg_dir = Path(tmpdir) / ".nowledge-mem"
        global_cfg_dir.mkdir(parents=True, exist_ok=True)
        global_cfg = global_cfg_dir / "config.json"
        global_cfg.write_text(json.dumps({"apiUrl": "https://global-mem.example.com", "apiKey": "global_key"}))

        env_dict = {"HOME": tmpdir, "NMEM_IGNORE_HOST_CONFIG": "1"}
        with patch.dict(os.environ, env_dict, clear=True):
            url, key = nmem_shared.get_effective_config(cwd=ws_dir)
            assert url == "https://ws-mem.example.com"
            assert key == "ws_key"


def test_resolve_space_detailed_hierarchy():
    with tempfile.TemporaryDirectory() as tmpdir:
        ws_dir = Path(tmpdir) / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)
        ws_cfg = ws_dir / ".config.json"
        ws_cfg.write_text(json.dumps({"space": "ws-config-space"}))

        nmemspace_file = ws_dir / ".nmemspace"
        nmemspace_file.write_text("ws-nmemspace-space")

        plugin_cfg_dir = Path(tmpdir) / ".nowledge-mem" / "plugins" / "antigravity"
        plugin_cfg_dir.mkdir(parents=True, exist_ok=True)
        plugin_cfg = plugin_cfg_dir / "config.json"
        plugin_cfg.write_text(json.dumps({"space": "plugin-storage-space"}))

        global_cfg_dir = Path(tmpdir) / ".nowledge-mem"
        global_cfg_dir.mkdir(parents=True, exist_ok=True)
        global_cfg = global_cfg_dir / "config.json"
        global_cfg.write_text(json.dumps({"space": "global-space"}))

        with patch.dict(os.environ, {"HOME": tmpdir}, clear=True):
            # 1. Workspace .config.json outranks .nmemspace
            assert nmem_shared.resolve_space(cwd=ws_dir) == "ws-config-space"

            # 2. .nmemspace outranks plugin storage
            ws_cfg.unlink()
            assert nmem_shared.resolve_space(cwd=ws_dir) == "ws-nmemspace-space"

            # 3. Plugin storage outranks global
            nmemspace_file.unlink()
            assert nmem_shared.resolve_space(cwd=ws_dir) == "plugin-storage-space"

            # 4. Global fallback
            plugin_cfg.unlink()
            assert nmem_shared.resolve_space(cwd=ws_dir) == "global-space"


def test_retry_unsynced_sessions_async_spawn_guard():
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / ".nowledge-mem" / "plugins" / "antigravity"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        with (
            patch.dict(os.environ, {"HOME": tmpdir}),
            patch("subprocess.Popen") as mock_popen,
            patch("nmem_shared._is_pid_alive", return_value=True),
        ):
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            # First spawn succeeds
            nmem_shared.retry_unsynced_sessions_async(cooldown_seconds=10.0)
            assert mock_popen.call_count == 1

            # Second spawn is suppressed because PID is alive
            nmem_shared.retry_unsynced_sessions_async(cooldown_seconds=10.0)
            assert mock_popen.call_count == 1


def test_retry_unsynced_sessions_async_does_not_trust_reused_pid_forever():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir) / ".nowledge-mem" / "plugins" / "antigravity" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "retry_worker.pid").write_text(f"12345:{time.time() - 901}", encoding="utf-8")
        with (
            patch.dict(os.environ, {"HOME": tmpdir}),
            patch("subprocess.Popen") as mock_popen,
            patch("nmem_shared._is_pid_alive", return_value=True),
        ):
            mock_popen.return_value = MagicMock(pid=54321)
            nmem_shared.retry_unsynced_sessions_async(cooldown_seconds=10.0)

        mock_popen.assert_called_once()


@patch("pathlib.Path.is_file", return_value=True)
@patch("pathlib.Path.read_text")
def test_get_effective_config_prevents_host_key_leak_on_url_mismatch(mock_read_text, mock_is_file):
    mock_read_text.return_value = '{"apiUrl": "http://127.0.0.1:14242", "apiKey": "host_prod_key"}'
    env_dict = {"NMEM_API_URL": "http://127.0.0.1:8888"}
    with patch.dict(os.environ, env_dict, clear=True):
        url, key = nmem_shared.get_effective_config()
        assert url == "http://127.0.0.1:8888"
        assert key is None


@patch("httpx.request")
@patch("urllib.request.urlopen")
def test_http_request_success(mock_urlopen, mock_httpx):
    nmem_shared.reset_backend_unreachable()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"status": "ok"}'
    mock_resp.json.return_value = {"status": "ok"}
    mock_httpx.return_value = mock_resp

    res = nmem_shared.http_request("/health")
    assert res == {"status": "ok"}
    assert nmem_shared.is_backend_unreachable() is False


@patch("httpx.request")
@patch("urllib.request.urlopen")
def test_circuit_breaker_unreachable_fast_fail(mock_urlopen, mock_httpx):
    nmem_shared.reset_backend_unreachable()
    mock_httpx.side_effect = Exception("Connection timed out")
    mock_urlopen.side_effect = Exception("Connection timed out")

    res1 = nmem_shared.http_request("/health")
    assert res1 is None
    assert nmem_shared.is_backend_unreachable() is True

    mock_httpx.reset_mock()
    mock_urlopen.reset_mock()
    res2 = nmem_shared.http_request("/health")
    assert res2 is None
    mock_httpx.assert_not_called()
    mock_urlopen.assert_not_called()

    nmem_shared.reset_backend_unreachable()


@patch("nmem_shared._is_pid_alive", return_value=False)
def test_file_lock_stale_pid_recovery(mock_pid_alive):
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_path = Path(tmpdir) / "test.lock"
        lock_path.write_text("999999", encoding="utf-8")

        with nmem_shared.FileLock(lock_path) as lock:
            assert lock.acquired is True
            assert lock_path.exists()
            pid_in_file = lock_path.read_text(encoding="utf-8").strip()
            assert pid_in_file == str(os.getpid())


@patch.dict(os.environ, {"NMEM_SPACE": "custom-explicit-space"}, clear=False)
def test_resolve_space_explicit_env():
    assert nmem_shared.resolve_space("/path/to/random") == "custom-explicit-space"


@patch("nmem_shared.get_existing_spaces")
def test_resolve_space_dynamic_existing(mock_get_spaces):
    mock_get_spaces.return_value = [
        {"id": "default", "key": "default", "name": "Default"},
        {"id": "agync", "key": "agync", "name": "agync"},
    ]
    with patch.dict(os.environ, {}, clear=True):
        assert nmem_shared.resolve_space("/home/user/workspace/agync") == "agync"


@patch("nmem_shared.get_existing_spaces")
def test_resolve_space_dynamic_non_existing_falls_back_to_default(mock_get_spaces):
    mock_get_spaces.return_value = [
        {"id": "default", "key": "default", "name": "Default"},
        {"id": "agync", "key": "agync", "name": "agync"},
    ]
    with patch.dict(os.environ, {}, clear=True):
        assert (
            nmem_shared.resolve_space("/home/user/workspace/nowledge-co/nowledge-mem-google-antigravity") == "default"
        )


@patch("nmem_shared.get_existing_spaces")
def test_resolve_space_dynamic_unreachable_falls_back_to_default(mock_get_spaces):
    mock_get_spaces.return_value = None
    with patch.dict(os.environ, {}, clear=True):
        assert nmem_shared.resolve_space("/home/user/workspace/random-project") == "default"


def _write_workspace_space_config(workspace: Path, config_kind: str, space: str) -> None:
    if config_kind == "config_json":
        (workspace / ".config.json").write_text(json.dumps({"space": space}), encoding="utf-8")
    elif config_kind == "nmemspace":
        (workspace / ".nmemspace").write_text(space, encoding="utf-8")
    else:
        (workspace / ".nowledge").mkdir()
        (workspace / ".nowledge" / "config.json").write_text(json.dumps({"space_id": space}), encoding="utf-8")


def test_workspace_root_from_hook_input_prefers_transcript_owner(tmp_path):
    outer = tmp_path / "outer"
    nested = outer / "nested"
    nested.mkdir(parents=True)

    root = nmem_shared.workspace_root_from_hook_input(
        {
            "workspacePaths": [str(outer), str(nested)],
            "transcriptPath": str(nested / ".gemini" / "antigravity" / "transcript.jsonl"),
        }
    )

    assert root == nested.resolve()


@patch("httpx.request")
def test_hook_workspace_binds_http_transport_config(mock_request, tmp_path):
    workspace = tmp_path / "customer-project"
    workspace.mkdir()
    (workspace / ".config.json").write_text(
        json.dumps({"apiUrl": "https://workspace-mem.example.com", "apiKey": "workspace-key"}),
        encoding="utf-8",
    )
    response = MagicMock(status_code=200, text='{"ok": true}')
    response.json.return_value = {"ok": True}
    mock_request.return_value = response

    nmem_shared.activate_hook_workspace({"workspacePaths": [str(workspace)]})
    assert nmem_shared.http_request("/status") == {"ok": True}

    assert mock_request.call_args.args[1] == "https://workspace-mem.example.com/status"
    assert mock_request.call_args.kwargs["headers"]["Authorization"] == "Bearer workspace-key"


@pytest.mark.parametrize("config_kind", ["config_json", "nmemspace", "nowledge_config"])
def test_session_start_uses_payload_workspace_when_process_cwd_differs(tmp_path, config_kind):
    workspace = tmp_path / "customer-project"
    workspace.mkdir()
    _write_workspace_space_config(workspace, config_kind, "project-space")
    hook_input = {
        "conversationId": "conv-workspace",
        "invocationNum": 0,
        "workspacePaths": [str(workspace)],
        "transcriptPath": str(workspace / ".gemini" / "antigravity" / "transcript.jsonl"),
    }

    def read_context(workspace_root):
        space = nmem_shared.resolve_space(workspace_root, validate_explicit=True)
        return {"tag": "test", "label": "test", "content": space}

    with (
        patch("nmem_shared.read_hook_input", return_value=hook_input),
        patch("nmem_shared.get_existing_spaces", return_value=[{"id": "project-space"}]),
        patch("nmem_shared.sync_mcp_config_file"),
        patch("nmem_shared.sync_host_skills_async"),
        patch("session_start.read_startup_context", side_effect=read_context),
        patch("subprocess.Popen"),
        patch("pathlib.Path.cwd", return_value=HOOKS_DIR),
        patch("nmem_shared.emit") as mock_emit,
        patch.object(sys, "argv", ["session-start.py"]),
    ):
        session_start.main()

    payload = mock_emit.call_args.args[0]
    assert "project-space" in payload["injectSteps"][0]["ephemeralMessage"]


def test_post_invocation_surfaces_invalid_workspace_space(tmp_path):
    workspace = tmp_path / "customer-project"
    workspace.mkdir()
    (workspace / ".nmemspace").write_text("missing-space", encoding="utf-8")

    with (
        patch(
            "nmem_shared.read_hook_input",
            return_value={"conversationId": "conv-workspace", "workspacePaths": [str(workspace)]},
        ),
        patch("nmem_shared.get_existing_spaces", return_value=[{"id": "default"}]),
        patch("nmem_shared.emit") as mock_emit,
        patch.object(sys, "argv", ["post-invocation.py"]),
    ):
        post_invocation.main()

    message = mock_emit.call_args.args[0]["injectSteps"][0]["ephemeralMessage"]
    assert "missing-space" in message
    assert "not available" in message


def test_session_end_writes_to_payload_workspace_space(tmp_path):
    workspace = tmp_path / "customer-project"
    workspace.mkdir()
    _write_workspace_space_config(workspace, "nowledge_config", "project-space")
    transcript = workspace / ".gemini" / "antigravity" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        '{"source":"USER_EXPLICIT","content":"Save this"}\n'
        '{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Saved"}\n',
        encoding="utf-8",
    )
    hook_input = {
        "conversationId": "conv-workspace",
        "workspacePaths": [str(workspace)],
        "transcriptPath": str(transcript),
        "fullyIdle": True,
    }

    with (
        patch("nmem_shared.read_hook_input", return_value=hook_input),
        patch("nmem_shared.get_existing_spaces", return_value=[{"id": "project-space"}]),
        patch("nmem_shared.http_request") as mock_http,
        patch("nmem_shared.emit"),
        patch.object(sys, "argv", ["session-end.py"]),
    ):
        mock_http.side_effect = [{"status": 404, "error": "not_found"}, {"status": "ok"}]
        session_end.main()

    import_call = next(call for call in mock_http.call_args_list if call.args[0] == "/threads/import")
    assert import_call.kwargs["payload"]["space"] == "project-space"


def test_status_workspace_argument_surfaces_malformed_project_config(tmp_path, capsys):
    workspace = tmp_path / "customer-project"
    workspace.mkdir()
    (workspace / ".config.json").write_text("{not-json", encoding="utf-8")

    with (
        patch("nmem_shared.run_nmem_command", return_value=MagicMock(returncode=1, stdout="", stderr="offline")),
        patch("nmem_shared.http_request", return_value={"error": "offline"}),
        patch.object(sys, "argv", ["nmem_status.py", "--workspace-root", str(workspace)]),
    ):
        nmem_status.main()

    output = capsys.readouterr().out
    assert "Active Space (Workspace)** | `unresolved`" in output
    assert "Project Space configuration error" in output
    assert str(workspace / ".config.json") in output


@patch("nmem_shared.http_request")
@patch("nmem_shared.sync_mcp_config_file")
@patch("nmem_shared.get_effective_config")
@patch("nmem_shared.read_hook_input")
@patch("nmem_shared.resolve_space", return_value="default")
@patch("sys.stdout.write")
def test_session_start_hook(mock_write, mock_space, mock_read, mock_config, mock_sync, mock_http):
    mock_config.return_value = ("http://127.0.0.1:14242", "key123")
    mock_read.return_value = {"conversationId": "conv-123"}
    mock_http.side_effect = [
        {"rendered_markdown": "# Startup Context Bundle"},
        {"content": "Daily Working Memory Briefing"},
    ]

    with (
        patch("nmem_shared.sync_host_skills_async") as mock_skills_sync,
        patch.object(sys, "argv", ["session-start.py"]),
    ):
        session_start.main()
        mock_skills_sync.assert_called_once()
        assert mock_http.call_count >= 1
        mock_write.assert_called_once()
        payload = json.loads(mock_write.call_args[0][0])
        assert "injectSteps" in payload
        assert len(payload["injectSteps"]) > 0
        emitted_text = payload["injectSteps"][0]["ephemeralMessage"]
        assert "Startup Context Bundle" in emitted_text


@patch("nmem_shared.http_request")
@patch("nmem_shared.sync_mcp_config_file")
@patch("nmem_shared.get_effective_config")
@patch("nmem_shared.read_hook_input")
@patch("nmem_shared.resolve_space", return_value="default")
@patch("sys.stdout.write")
def test_session_start_hook_working_memory_fallback(
    mock_write, mock_space, mock_read, mock_config, mock_sync, mock_http
):
    mock_config.return_value = ("http://127.0.0.1:14242", "key123")
    mock_read.return_value = {"conversationId": "conv-123"}
    mock_http.side_effect = [
        {},  # Context bundle returns empty -> falls back to working memory
        {"content": "Daily Working Memory Briefing"},
    ]

    with (
        patch("nmem_shared.sync_host_skills_async") as mock_skills_sync,
        patch.object(sys, "argv", ["session-start.py"]),
    ):
        session_start.main()
        mock_skills_sync.assert_called_once()
        assert mock_http.call_count == 2
        mock_write.assert_called_once()
        payload = json.loads(mock_write.call_args[0][0])
        assert "injectSteps" in payload
        assert len(payload["injectSteps"]) > 0
        emitted_text = payload["injectSteps"][0]["ephemeralMessage"]
        assert "Daily Working Memory Briefing" in emitted_text


@patch("nmem_shared.http_request")
@patch("nmem_shared.read_hook_input")
@patch("nmem_shared.save_unsynced_session")
@patch("nmem_shared.resolve_space")
@patch("sys.stdout.write")
@patch("os.path.exists")
@patch(
    "builtins.open",
    new_callable=mock_open,
    read_data='{"source":"USER_EXPLICIT","type":"USER_INPUT","content":"<USER_REQUEST>Test request</USER_REQUEST>"}\n{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Hello world!"}\n',
)
def test_session_end_captures_transcript(
    mock_file, mock_exists, mock_stdout, mock_space, mock_save, mock_input, mock_http
):
    mock_input.return_value = {
        "conversationId": "conv-12345",
        "transcriptPath": "/path/to/transcript.jsonl",
        "fullyIdle": True,
    }
    mock_exists.return_value = True
    mock_space.return_value = "default"
    mock_http.side_effect = [{"id": "conv-12345", "messages": []}, {"status": "ok"}]

    with patch.object(sys, "argv", ["session-end.py"]):
        session_end.main()
        assert mock_http.call_count >= 2


@patch("nmem_shared.http_request")
@patch("nmem_shared.read_hook_input")
@patch("nmem_shared.save_unsynced_session")
@patch("nmem_shared.resolve_space")
@patch("sys.stdout.write")
@patch("os.path.exists")
@patch(
    "builtins.open",
    new_callable=mock_open,
    read_data='{"source":"USER_EXPLICIT","type":"USER_INPUT","content":"<USER_REQUEST>Test request</USER_REQUEST>"}\n{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Hello world!"}\n',
)
def test_session_end_offline_fallback(
    mock_file, mock_exists, mock_stdout, mock_space, mock_save, mock_input, mock_http
):
    mock_input.return_value = {
        "conversationId": "conv-12345",
        "transcriptPath": "/path/to/transcript.jsonl",
        "fullyIdle": True,
    }
    mock_exists.return_value = True
    mock_space.return_value = "default"
    mock_http.return_value = None

    with patch("nmem_shared.run_nmem_command") as mock_run, patch.object(sys, "argv", ["session-end.py"]):
        mock_run.side_effect = Exception("CLI unavailable")
        session_end.main()
        mock_save.assert_called_once()


@patch("nmem_shared.save_unsynced_session")
@patch("nmem_shared.sync_learnings_if_any")
@patch("nmem_shared.is_backend_unreachable", return_value=False)
@patch("nmem_shared.run_nmem_command")
@patch("nmem_shared.http_request")
@patch("nmem_shared.resolve_space", return_value="default")
def test_session_end_reconciles_existing_thread_prefix(
    mock_space, mock_http, mock_run, mock_unreachable, mock_learnings, mock_save, tmp_path
):
    # Only the unsent tail goes to an existing thread; the whole transcript must not be appended again.
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"source":"USER_EXPLICIT","type":"USER_INPUT","content":"Save this"}\n'
        '{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Saved."}\n',
        encoding="utf-8",
    )
    mock_http.side_effect = [
        {"id": "conv-prefix", "messages": [{"role": "user", "content": "Save this"}]},
        {"status": "ok"},
    ]
    hook_input = {"conversationId": "conv-prefix", "transcriptPath": str(transcript), "fullyIdle": True}
    with (
        patch("nmem_shared.read_hook_input", return_value=hook_input),
        patch("sys.stdout.write"),
        patch.object(sys, "argv", ["session-end.py"]),
    ):
        session_end.main()

    assert mock_http.call_count == 2
    assert mock_http.call_args_list[1].args[0] == "/threads/conv-prefix/reconcile-tail"
    payload = mock_http.call_args_list[1].kwargs["payload"]
    assert payload["matched_count"] == 1
    assert payload["messages"] == [{"role": "assistant", "content": "Saved."}]
    mock_run.assert_not_called()
    mock_save.assert_not_called()


@pytest.mark.parametrize(
    "raw",
    [b"", b"{", b"\xff{}", b"[]"],
    ids=["empty", "invalid-json", "not-utf8", "not-an-object"],
)
def test_read_hook_input_gives_lifecycle_hooks_an_empty_payload_it_cannot_read(raw):
    with patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")):
        assert nmem_shared.read_hook_input() == {}


def test_read_hook_input_decodes_utf8_whatever_the_stdin_encoding():
    # On Windows a piped stdin decodes with the locale code page, such as GBK, but the payload is UTF-8.
    payload = {"conversationId": "conv-utf8", "transcriptPath": "C:/Users/\u5f20/t.jsonl"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="gbk")):
        assert nmem_shared.read_hook_input() == payload


@patch("nmem_shared.save_unsynced_session")
@patch("nmem_shared.sync_learnings_if_any")
@patch("nmem_shared.is_backend_unreachable", return_value=False)
@patch("nmem_shared.run_nmem_command")
@patch("nmem_shared.http_request")
@patch("nmem_shared.resolve_space", return_value="default")
def test_session_end_captures_a_utf8_payload_whatever_the_stdin_encoding(
    mock_space, mock_http, mock_run, mock_unreachable, mock_learnings, mock_save, tmp_path
):
    # Stop reads a GBK-decoded stdin through the real shared reader and must still capture the session.
    transcript = tmp_path / "\u5f20-project" / "transcript.jsonl"
    transcript.parent.mkdir()
    transcript.write_text(
        '{"source":"USER_EXPLICIT","type":"USER_INPUT","content":"Save this"}\n'
        '{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Saved."}\n',
        encoding="utf-8",
    )
    hook_input = {"conversationId": "conv-utf8", "transcriptPath": str(transcript), "fullyIdle": True}
    raw = json.dumps(hook_input, ensure_ascii=False).encode("utf-8")
    mock_http.side_effect = [{"status": 404, "error": "not_found"}, {"status": "ok"}]
    with (
        patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="gbk")),
        patch("sys.stdout.write"),
        patch.object(sys, "argv", ["session-end.py"]),
    ):
        session_end.main()

    imports = [call for call in mock_http.call_args_list if call.args[0] == "/threads/import"]
    assert len(imports) == 1
    assert imports[0].kwargs["payload"]["id"] == "conv-utf8"
    assert imports[0].kwargs["payload"]["messages"] == [
        {"role": "user", "content": "Save this"},
        {"role": "assistant", "content": "Saved."},
    ]
    mock_save.assert_not_called()


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
def test_nmem_gate_auto_allow_nmem_status(mock_flush, mock_write, mock_input):
    safe_commands = [
        "python3 hooks/nmem_status.py",
        "python hooks/nmem_status.py",
        "python3 hooks/nmem_entrypoint.py status",
        "nmem status",
        "nmem status --json",
        "nmem tasks",
        "nmem tasks --json",
        "nmem_status.py --conv-id test-conv",
    ]
    for cmd in safe_commands:
        mock_write.reset_mock()
        mock_input.return_value = {
            "toolCall": {"name": "run_command", "args": {"CommandLine": cmd}},
            "conversationId": "conv-123",
        }

        with patch.object(sys, "argv", ["nmem-gate.py"]):
            nmem_gate.main()
            mock_write.assert_called_once()
            payload = json.loads(mock_write.call_args[0][0])
            assert payload["decision"] == "allow"
            assert payload["reason"] == "Auto-allowing validated plugin status and diagnostic command"


def test_is_safe_status_command_rejection():
    dangerous_or_unrelated = [
        "nmem status && rm -rf /",
        "nmem tasks; whoami",
        "python3 hooks/nmem_status.py | sh",
        "nmem status > /tmp/out",
        "nmem memories list",
        "nmem tasks --unknown-flag",
        "python3 hooks/nmem_entrypoint.py unknown",
        "",
        None,
    ]
    for cmd in dangerous_or_unrelated:
        assert not nmem_gate.is_safe_status_command(cmd)


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
def test_nmem_gate_read_tool(mock_flush, mock_write, mock_input):
    for tool in ["memory_search", "ontology_read", "entity_search"]:
        mock_write.reset_mock()
        mock_input.return_value = {
            "toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "nowledge-mem", "ToolName": tool}},
            "conversationId": "conv-123",
        }

        with patch.object(sys, "argv", ["nmem-gate.py"]):
            nmem_gate.main()
            mock_write.assert_called_once()
            payload = json.loads(mock_write.call_args[0][0])
            assert payload["decision"] == "allow"


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
def test_nmem_gate_delete_destructive(mock_flush, mock_write, mock_input):
    mock_input.return_value = {
        "toolCall": {"name": "mcp_nowledge-mem_memory_delete", "args": {"id": "mem-1"}},
        "conversationId": "conv-123",
    }

    with patch.object(sys, "argv", ["nmem-gate.py"]):
        nmem_gate.main()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        assert payload["decision"] == "force_ask"


def _gate_reply_from_stdin(raw, capsys, encoding="utf-8"):
    stdin = io.TextIOWrapper(io.BytesIO(raw), encoding=encoding)
    with patch.object(sys, "stdin", stdin), patch.object(sys, "argv", ["nmem-gate.py"]):
        nmem_gate.main()
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    "raw",
    [b"", b"{", b"[]", b'{"conversationId": "conv-123"}', b'{"toolCall": {"args": {}}}', b"\xff{}"],
    ids=["empty", "invalid-json", "not-an-object", "no-tool-call", "no-tool-name", "not-utf8"],
)
def test_nmem_gate_confirms_a_tool_call_it_cannot_read(raw, capsys):
    assert _gate_reply_from_stdin(raw, capsys)["decision"] == "force_ask"


def test_nmem_gate_reads_a_utf8_payload_whatever_the_stdin_encoding(capsys):
    # On Windows a piped stdin decodes with the locale code page, such as GBK, but the payload is UTF-8.
    payload = {
        "toolCall": {"name": "mcp_nowledge-mem_memory_delete", "args": {"memory_id": "x"}},
        "transcriptPath": "C:/Users/\u5f20/t.jsonl",
    }
    reply = _gate_reply_from_stdin(json.dumps(payload, ensure_ascii=False).encode("utf-8"), capsys, encoding="gbk")

    assert reply["decision"] == "force_ask"
    assert "memory_delete" in reply["reason"]


MEM_FS_ENVELOPES = ["direct", "call_mcp_tool"]


def _mem_fs_call(envelope, arguments):
    if envelope == "direct":
        return {"toolCall": {"name": "mcp_nowledge-mem_mem_fs", "args": arguments}}
    return {
        "toolCall": {
            "name": "call_mcp_tool",
            "args": {"ServerName": "nowledge-mem", "ToolName": "mem_fs", "Arguments": arguments},
        }
    }


def _gate_reply(payload, capsys):
    with patch("nmem_gate.read_hook_input", return_value=payload), patch.object(sys, "argv", ["nmem-gate.py"]):
        nmem_gate.main()
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("envelope", MEM_FS_ENVELOPES)
@pytest.mark.parametrize(
    "command",
    [
        "ls /memories",
        "cat /memories/by-id/x.memory.md --line 1 --lines 20",
        "stat /memories/by-id/x.memory.md",
        "find /memories --label work",
        "grep deploy /memories",
        'recall "release plan"',
        "capabilities",
        "caps",
    ],
)
def test_nmem_gate_allows_mem_fs_reads(envelope, command, capsys):
    reply = _gate_reply(_mem_fs_call(envelope, {"command": command}), capsys)

    assert reply["decision"] == "allow"
    # A tool-wide override from a read would pre-approve later mem_fs writes.
    assert "permissionOverrides" not in reply


@pytest.mark.parametrize("envelope", MEM_FS_ENVELOPES)
@pytest.mark.parametrize(
    "command",
    [
        "rm /memories/by-id/x.memory.md",
        "delete /memories/by-id/x.memory.md",
        "RM /memories/by-id/x.memory.md",
        "Delete /memories/by-id/x.memory.md",
        '"rm" /memories/by-id/x.memory.md',
        "  rm /memories/by-id/x.memory.md",
        "rm	/memories/by-id/x.memory.md",
    ],
)
def test_nmem_gate_confirms_mem_fs_deletes(envelope, command, capsys):
    assert _gate_reply(_mem_fs_call(envelope, {"command": command}), capsys)["decision"] == "force_ask"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "toolCall": {
                "name": "mcp_nowledge-mem_mem_fs",
                "args": {"command": "rm /memories/by-id/x.memory.md", "Arguments": {"command": "ls"}},
            }
        },
        {
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ServerName": "nowledge-mem",
                    "ToolName": "mem_fs",
                    "command": "ls",
                    "Arguments": '{"command": "rm /memories/by-id/x.memory.md"}',
                },
            }
        },
    ],
    ids=["direct-with-nested-arguments", "call_mcp_tool-with-string-arguments"],
)
def test_nmem_gate_reads_the_mem_fs_command_the_server_runs(payload, capsys):
    # The server runs a direct call's own command, and call_mcp_tool forwards only Arguments.
    assert _gate_reply(payload, capsys)["decision"] == "force_ask"


@pytest.mark.parametrize("envelope", MEM_FS_ENVELOPES)
@pytest.mark.parametrize(
    "arguments",
    [{}, {"command": None}, {"command": ""}, {"command": 'rm "/memories/by-id/x.memory.md'}, {"command": "mount /x"}],
    ids=["missing", "not-a-string", "empty", "unbalanced-quote", "unknown-verb"],
)
def test_nmem_gate_confirms_mem_fs_commands_it_cannot_classify(envelope, arguments, capsys):
    # The server parses commands itself and may still read one of these as a delete.
    assert _gate_reply(_mem_fs_call(envelope, arguments), capsys)["decision"] == "force_ask"


@pytest.mark.parametrize("envelope", MEM_FS_ENVELOPES)
@pytest.mark.parametrize("verb", ["write", "WRITE"])
def test_nmem_gate_asks_before_mem_fs_write_without_intent(envelope, verb, capsys):
    reply = _gate_reply(_mem_fs_call(envelope, {"command": f"{verb} /memories/by-id/x.memory.md --body hi"}), capsys)

    assert reply["decision"] == "ask"


def test_nmem_gate_allows_mem_fs_write_with_intent(tmp_path, capsys):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"source":"USER_EXPLICIT","content":"Please save this"}\n', encoding="utf-8")
    payload = _mem_fs_call("direct", {"command": "write /memories/by-id/x.memory.md --body hi"})
    payload["transcriptPath"] = str(transcript)

    reply = _gate_reply(payload, capsys)
    assert reply["decision"] == "allow"
    # Like other memory writes, an approved write pre-approves later ones; deletes still get force_ask.
    assert reply["permissionOverrides"] == ["mcp(nowledge-mem/mem_fs)"]


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
@patch("os.path.exists")
@patch(
    "builtins.open",
    new_callable=mock_open,
    read_data='{"source":"USER_EXPLICIT","content":"Please run memory maintenance catch up"}\n',
)
def test_nmem_gate_trigger_memory_catchup_with_intent(mock_file, mock_exists, mock_flush, mock_write, mock_input):
    mock_input.return_value = {
        "toolCall": {
            "name": "call_mcp_tool",
            "args": {
                "ServerName": "nowledge-mem",
                "ToolName": "trigger_memory_catchup",
                "Arguments": {"horizon": "today"},
            },
        },
        "conversationId": "conv-test-catchup",
        "transcriptPath": "/fake/transcript.jsonl",
    }
    mock_exists.return_value = True

    with (
        patch("nmem_shared.should_allow_catchup", return_value=(True, "")),
        patch("nmem_shared.record_catchup_execution") as mock_record,
        patch.object(sys, "argv", ["nmem-gate.py"]),
    ):
        nmem_gate.main()
        mock_record.assert_called_once()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        assert payload["decision"] == "allow"
        assert "Explicit user intent detected" in payload["reason"]


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
def test_nmem_gate_trigger_memory_catchup_without_intent(mock_flush, mock_write, mock_input):
    mock_input.return_value = {
        "toolCall": {
            "name": "call_mcp_tool",
            "args": {
                "ServerName": "nowledge-mem",
                "ToolName": "trigger_memory_catchup",
                "Arguments": {"horizon": 3},
            },
        },
        "conversationId": "conv-test-catchup",
    }
    with (
        patch("nmem_shared.should_allow_catchup", return_value=(True, "")) as mock_should,
        patch("nmem_shared.record_catchup_execution") as mock_record,
        patch.object(sys, "argv", ["nmem-gate.py"]),
    ):
        nmem_gate.main()
        mock_should.assert_called_once_with("3", "conv-test-catchup", session_dir=None, transcript_path=None)
        mock_record.assert_not_called()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        assert payload["decision"] == "ask"
        assert "Confirmation required to run server-side memory maintenance" in payload["reason"]


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
def test_nmem_gate_trigger_memory_catchup_debounced(mock_flush, mock_write, mock_input):
    mock_input.return_value = {
        "toolCall": {
            "name": "call_mcp_tool",
            "args": {
                "ServerName": "nowledge-mem",
                "ToolName": "trigger_memory_catchup",
                "Arguments": {"horizon": "today"},
            },
        },
        "conversationId": "conv-test-catchup",
    }
    with (
        patch("nmem_shared.should_allow_catchup", return_value=(False, "Already executed in this session.")),
        patch.object(sys, "argv", ["nmem-gate.py"]),
    ):
        nmem_gate.main()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        assert payload["decision"] == "deny"
        assert "Auto-suppressing redundant memory catchup" in payload["reason"]


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
@patch("os.path.exists")
@patch(
    "builtins.open",
    new_callable=mock_open,
    read_data='{"source":"USER_EXPLICIT","content":"Please save the session"}\n',
)
def test_nmem_gate_write_intent(mock_file, mock_exists, mock_flush, mock_write, mock_input):
    mock_input.return_value = {
        "toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "nowledge-mem", "ToolName": "memory_add"}},
        "transcriptPath": "/path/to/transcript.jsonl",
        "conversationId": "conv-123",
    }
    mock_exists.return_value = True

    with patch.object(sys, "argv", ["nmem-gate.py"]):
        nmem_gate.main()
        mock_write.assert_called_once()
        payload = json.loads(mock_write.call_args[0][0])
        assert payload["decision"] == "allow"


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
def test_nmem_gate_write_no_intent(mock_flush, mock_write, mock_input):
    mock_input.return_value = {
        "toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "nowledge-mem", "ToolName": "memory_add"}},
        "conversationId": "conv-123",
    }

    with patch.object(sys, "argv", ["nmem-gate.py"]):
        nmem_gate.main()
        mock_write.assert_called_once()
        payload = json.loads(mock_write.call_args[0][0])
        assert payload["decision"] in ("ask", "force_ask")


@patch("nmem_shared.read_hook_input")
@patch("sys.stdout.write")
def test_post_invocation_runs(mock_write, mock_input):
    mock_input.return_value = {"conversationId": "conv-123"}
    with patch("nmem_shared.get_unsynced_sessions") as mock_unsynced, patch.object(sys, "argv", ["post-invocation.py"]):
        mock_unsynced.return_value = {}
        post_invocation.main()
        mock_unsynced.assert_called_once()
        mock_write.assert_called_once()


@patch("nmem_shared.retry_unsynced_sessions_async")
@patch("nmem_shared.should_emit_unsynced_warning", return_value=True)
@patch("nmem_shared.record_unsynced_warning_emitted")
@patch("nmem_shared.get_unsynced_sessions")
@patch("nmem_shared.read_hook_input")
@patch("nmem_shared.emit")
def test_post_invocation_injects_warning_when_unsynced_exists(
    mock_emit, mock_input, mock_unsynced, mock_record, mock_should_emit, mock_retry
):
    mock_input.return_value = {"invocationNum": 1, "conversationId": "conv-test-1"}
    mock_unsynced.return_value = {"conv-1": {"title": "Test"}}

    with patch.object(sys, "argv", ["post-invocation.py"]):
        post_invocation.main()

    mock_retry.assert_called_once()
    mock_record.assert_called_once()
    mock_emit.assert_called_once()
    payload = mock_emit.call_args[0][0]
    assert "injectSteps" in payload
    assert "pending offline session(s)" in payload["injectSteps"][0]["ephemeralMessage"]
    assert "Do NOT call trigger_memory_catchup" in payload["injectSteps"][0]["ephemeralMessage"]


@patch("nmem_shared.retry_unsynced_sessions_async")
@patch("nmem_shared.should_emit_unsynced_warning", return_value=False)
@patch("nmem_shared.get_unsynced_sessions")
@patch("nmem_shared.read_hook_input")
@patch("nmem_shared.emit")
def test_post_invocation_throttles_warning_when_already_emitted(
    mock_emit, mock_input, mock_unsynced, mock_should_emit, mock_retry
):
    mock_input.return_value = {"invocationNum": 2, "conversationId": "conv-test-1"}
    mock_unsynced.return_value = {"conv-1": {"title": "Test"}}

    with patch.object(sys, "argv", ["post-invocation.py"]):
        post_invocation.main()

    mock_retry.assert_called_once()
    mock_emit.assert_called_once_with({})


@patch("nmem_shared.run_nmem_command")
@patch("sys.stdout.write")
def test_nmem_status_connected(mock_write, mock_run_cmd):
    mock_run_cmd.return_value = MagicMock(returncode=0, stdout="Connected to Nowledge Mem server", stderr="")

    with (
        patch("nmem_shared.resolve_space", return_value="default"),
        patch(
            "nmem_shared.http_request",
            return_value={"agent_running": True, "running": [], "queued": [], "scheduled_once": [], "recurring": []},
        ),
        patch.object(sys, "argv", ["nmem_status.py"]),
    ):
        nmem_status.main()
        assert mock_write.call_count >= 1
        written = "".join([c[0][0] for c in mock_write.call_args_list])
        assert "🟢 Connected" in written
        assert "| **Active Space (Workspace)** | `default` |" in written
        assert "#### ⏱️ In-Flight Background Tasks" in written
        assert "*Nothing in flight.*" in written


@patch("nmem_shared.run_nmem_command")
@patch("sys.stdout.write")
def test_nmem_status_with_in_flight_tasks(mock_write, mock_run_cmd):
    mock_run_cmd.return_value = MagicMock(returncode=0, stdout="Connected to Nowledge Mem server", stderr="")
    tasks_data = {
        "agent_running": True,
        "running": [
            {
                "kind": "scheduler",
                "id": "t-1",
                "title": "Community detection",
                "lane": "community_detection",
                "started_at": "2026-08-20T18:00:00Z",
            }
        ],
        "queued": [{"kind": "scheduler", "id": "t-2", "title": "Graph compaction", "lane": "compaction"}],
        "scheduled_once": [
            {
                "kind": "reminder",
                "id": "r-1",
                "title": "Staging check",
                "lane": "scheduled_follow_up",
                "next_run_at": "2026-08-21T09:00:00Z",
            }
        ],
        "recurring": [
            {
                "kind": "recurring",
                "id": "rec-1",
                "title": "Daily briefing",
                "lane": "recurring_task",
                "status": "active",
                "next_run_at": "2026-08-21T07:00:00Z",
            }
        ],
    }

    with (
        patch("nmem_shared.resolve_space", return_value="default"),
        patch("nmem_shared.http_request", return_value=tasks_data),
        patch.object(sys, "argv", ["nmem_status.py"]),
    ):
        nmem_status.main()
        assert mock_write.call_count >= 1
        written = "".join([c[0][0] for c in mock_write.call_args_list])
        assert "#### ⏱️ In-Flight Background Tasks" in written
        assert "🏃 `[community_detection]` Community detection (started 2026-08-20T18:00:00Z)" in written
        assert "⏳ `[compaction]` Graph compaction" in written
        assert "📅 `[scheduled_follow_up]` Staging check (next: 2026-08-21T09:00:00Z)" in written
        assert "🔄 `[recurring_task]` Daily briefing `[active]` (next: 2026-08-21T07:00:00Z)" in written


@patch("nmem_shared.run_nmem_command")
@patch("sys.stdout.write")
def test_nmem_status_malformed_lanes(mock_write, mock_run_cmd):
    mock_run_cmd.return_value = MagicMock(returncode=0, stdout="Nothing in flight.\n", stderr="")
    malformed_tasks_data = {
        "agent_running": True,
        "running": "not-a-list",
        "queued": [],
        "scheduled_once": [],
        "recurring": [],
    }

    with (
        patch("nmem_shared.resolve_space", return_value="default"),
        patch("nmem_shared.http_request", return_value=malformed_tasks_data),
        patch.object(sys, "argv", ["nmem_status.py"]),
    ):
        nmem_status.main()
        assert mock_write.call_count >= 1
        written = "".join([c[0][0] for c in mock_write.call_args_list])
        assert "#### ⏱️ In-Flight Background Tasks" in written
        assert "*Nothing in flight.*" in written


@patch("nmem_shared.run_nmem_command")
@patch("sys.stdout.write")
def test_nmem_status_fallback_unavailable(mock_write, mock_run_cmd):
    mock_run_cmd.return_value = MagicMock(returncode=1, stdout="", stderr="daemon offline")

    with (
        patch("nmem_shared.resolve_space", return_value="default"),
        patch("nmem_shared.http_request", return_value={"error": "offline"}),
        patch.object(sys, "argv", ["nmem_status.py"]),
    ):
        nmem_status.main()
        assert mock_write.call_count >= 1
        written = "".join([c[0][0] for c in mock_write.call_args_list])
        assert "#### ⏱️ In-Flight Background Tasks" in written
        assert "*Background task status unavailable.*" in written


def test_sync_learnings_rules():
    proposal_content = """# Learning Proposal - Coding Guidelines
## Active Space
`default`

## Proposed Additions
- Preferred language: English
- Prefers concise code snippets
"""
    transcript_content = '{"source": "USER_EXPLICIT", "content": "learning_proposal.md - approved this document"}\n'

    def fake_exists(path):
        return True

    def fake_open(path, encoding="utf-8", **kwargs):
        if "transcript" in str(path):
            return io.StringIO(transcript_content)
        return io.StringIO(proposal_content)

    with (
        patch("os.path.exists", side_effect=fake_exists),
        patch("builtins.open", side_effect=fake_open),
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.read_text", return_value=proposal_content),
        patch("pathlib.Path.unlink"),
        patch("nmem_shared.run_nmem_command") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")
        nmem_shared.sync_learnings_if_any("conv-123", "/path/to/transcript.jsonl", "/path/to/artifacts", "default")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "memories"
        assert args[1] == "add"


@patch("nmem_shared.resolve_space", return_value="default")
def test_session_end_missing_transcript_file(mock_space):
    with (
        patch("nmem_shared.http_request") as mock_http,
        patch("sys.stdout.write") as mock_write,
        patch.object(sys, "argv", ["session-end.py"]),
    ):
        input_data = {
            "conversationId": "pytest-conv-missing",
            "transcriptPath": "/non/existent/path/transcript.jsonl",
            "fullyIdle": True,
        }
        with patch("nmem_shared.read_hook_input", return_value=input_data):
            session_end.main()
            mock_write.assert_called_once()
            mock_http.assert_not_called()


def test_extract_frontmatter_field():
    content = "---\nid: src_12345\ntitle: Sample Artifact\n---\n# Header\nContent line"
    assert update_artifact.extract_frontmatter_field(content, ("id", "source_id")) == "src_12345"
    assert update_artifact.extract_frontmatter_field(content, ("title", "name")) == "Sample Artifact"


@patch("urllib.request.urlopen")
def test_update_artifact_content_put(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"status": "success", "id": "src_12345"}'
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    config = {"apiUrl": "http://localhost:14242", "apiKey": "test_key"}
    res = update_artifact.update_artifact_content(config, "src_12345", "Updated content body", space_id="space_abc")

    assert res == {"status": "success", "id": "src_12345"}
    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert req.get_full_url() == "http://localhost:14242/sources/src_12345/content?space_id=space_abc"
    assert req.get_method() == "PUT"

    payload = json.loads(req.data.decode("utf-8"))
    assert payload == {"content": "Updated content body"}
    assert req.headers.get("Authorization") == "Bearer test_key"


@patch("urllib.request.urlopen")
def test_update_artifact_reparse_patch(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"status": "reparsed"}'
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    config = {"apiUrl": "http://localhost:14242", "apiKey": "test_key"}
    res = update_artifact.update_artifact_reparse(config, "src_12345", space_id="space_abc")

    assert res == {"status": "reparsed"}
    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert req.get_full_url() == "http://localhost:14242/sources/src_12345?space_id=space_abc"
    assert req.get_method() == "PATCH"
    assert req.headers.get("Authorization") == "Bearer test_key"

    payload = json.loads(req.data.decode("utf-8"))
    assert payload == {"action": "reparse"}


@patch("nmem_status.main")
def test_nmem_entrypoint_status(mock_status_main):
    with patch.object(sys, "argv", ["nmem_entrypoint.py", "status"]):
        nmem_entrypoint.main()
        mock_status_main.assert_called_once()


HOOK_FALLBACKS = [
    ("session-start", {}),
    ("session-end", {}),
    ("gate", {"decision": "force_ask"}),
    ("post-invocation", {}),
    ("post-tool-use", {}),
]


@pytest.mark.parametrize(("subcommand", "fallback"), HOOK_FALLBACKS)
def test_entrypoint_prints_safe_fallback_when_running_hook_fails(subcommand, fallback, capsys):
    # Antigravity blocks the tool call on a missing or malformed PreToolUse decision,
    # and a running hook may already have consumed stdin, so it must still answer.
    hook = MagicMock()
    hook.main.side_effect = TypeError("unsupported operand type(s) for |")
    with (
        patch.object(sys, "argv", ["nmem_entrypoint.py", subcommand]),
        patch("importlib.import_module", return_value=hook),
    ):
        nmem_entrypoint.main()

    captured = capsys.readouterr()
    reply = json.loads(captured.out)
    if "decision" in fallback:
        assert reply["decision"] == fallback["decision"]
        assert "unsupported operand" in reply["reason"]
    else:
        assert reply == fallback
    assert "unsupported operand" in captured.err


def test_entrypoint_confirms_a_delete_when_the_running_gate_fails(capsys):
    # The real gate reads a delete and fails before its answer is written; the fallback must still confirm it.
    with (
        patch.object(sys, "argv", ["nmem_entrypoint.py", "gate"]),
        patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(DELETE_MEMORY_PAYLOAD.encode()), encoding="utf-8")),
        patch("importlib.import_module", return_value=nmem_gate),
        patch.object(nmem_gate, "emit", side_effect=RuntimeError("emit failed")) as emit,
    ):
        nmem_entrypoint.main()

    # The gate classified the delete itself, not an unreadable payload.
    assert emit.call_args.args[0]["decision"] == "force_ask"
    assert "memory_delete" in emit.call_args.args[0]["reason"]
    reply = json.loads(capsys.readouterr().out)
    assert reply["decision"] == "force_ask"
    assert "emit failed" in reply["reason"]


@pytest.mark.parametrize(("subcommand", "fallback"), HOOK_FALLBACKS)
def test_entrypoint_exits_nonzero_when_hook_cannot_load(subcommand, fallback, capsys):
    # Nothing has read stdin yet, so the hooks.json chain must be free to try `python` next.
    with (
        patch.object(sys, "argv", ["nmem_entrypoint.py", subcommand]),
        patch("importlib.import_module", side_effect=TypeError("unsupported operand type(s) for |")),
        pytest.raises(TypeError),
    ):
        nmem_entrypoint.main()

    assert capsys.readouterr().out == ""


def test_entrypoint_keeps_hook_output_when_hook_fails_after_writing(capsys):
    def gate_main():
        sys.stdout.write(json.dumps({"decision": "force_ask"}))
        raise RuntimeError("late failure")

    gate = MagicMock()
    gate.main.side_effect = gate_main
    with (
        patch.object(sys, "argv", ["nmem_entrypoint.py", "gate"]),
        patch("importlib.import_module", return_value=gate),
    ):
        nmem_entrypoint.main()

    assert json.loads(capsys.readouterr().out) == {"decision": "force_ask"}


@pytest.mark.parametrize(("subcommand", "fallback"), HOOK_FALLBACKS)
def test_entrypoint_exits_nonzero_on_unsupported_python(subcommand, fallback, capsys):
    with (
        patch.object(sys, "argv", ["nmem_entrypoint.py", subcommand]),
        patch.object(sys, "version_info", (3, 8, 18, "final", 0)),
        patch("importlib.import_module") as mock_import,
        pytest.raises(SystemExit) as exit_info,
    ):
        nmem_entrypoint.main()

    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Python 3.9" in captured.err
    mock_import.assert_not_called()


@patch("nmem_status.main")
def test_entrypoint_status_reports_unsupported_python(mock_status_main, capsys):
    with (
        patch.object(sys, "argv", ["nmem_entrypoint.py", "status"]),
        patch.object(sys, "version_info", (3, 8, 18, "final", 0)),
        pytest.raises(SystemExit),
    ):
        nmem_entrypoint.main()

    assert "Python 3.9" in capsys.readouterr().err
    mock_status_main.assert_not_called()


def _hook_command(event):
    entry = json.loads((PLUGIN_ROOT / "hooks.json").read_text(encoding="utf-8"))["nowledge-mem-hooks"][event][0]
    return entry["hooks"][0]["command"] if "hooks" in entry else entry["command"]


FAILING_INTERPRETER = "exit 1"
# This interpreter, or the same one reporting Python 3.8 before it runs the entrypoint.
CURRENT_INTERPRETER = f'exec "{Path(sys.executable).as_posix()}" "$@"'
REPORT_PYTHON_38 = (
    "import runpy, sys; sys.version_info = (3, 8, 18, 'final', 0); "
    "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
)
TOO_OLD_INTERPRETER = f'exec "{Path(sys.executable).as_posix()}" -c "{REPORT_PYTHON_38}" "$@"'
RUN_COMMAND_PAYLOAD = json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": "ls -la"}}})
DELETE_MEMORY_PAYLOAD = json.dumps({"toolCall": {"name": "mcp_nowledge-mem_memory_delete", "args": {"memory_id": "x"}}})


def _run_hook_command(event, tmp_path, python3, python, payload="{}"):
    # Run the exact hooks.json command the way a POSIX host does, with these two interpreters on PATH.
    for name, body in (("python3", python3), ("python", python)):
        interpreter = tmp_path / name
        interpreter.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        interpreter.chmod(0o755)

    return subprocess.run(
        [shutil.which("sh"), "-c", _hook_command(event)],
        cwd=PLUGIN_ROOT,
        env={**os.environ, "PATH": str(tmp_path)},
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
@pytest.mark.parametrize("event", ["PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop"])
def test_hooks_json_shell_fallback_prints_valid_json(event, tmp_path):
    # With neither python3 nor python runnable, the echo at the end of the chain answers alone.
    result = _run_hook_command(event, tmp_path, FAILING_INTERPRETER, FAILING_INTERPRETER)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    if event == "PreToolUse":
        assert payload == {"decision": "force_ask"}
    else:
        assert payload == {}


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_hooks_json_shell_fallback_confirms_a_delete(tmp_path):
    # The echo cannot see the payload, so a delete must still get the confirmation the gate gives it.
    result = _run_hook_command("PreToolUse", tmp_path, FAILING_INTERPRETER, FAILING_INTERPRETER, DELETE_MEMORY_PAYLOAD)

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"decision": "force_ask"}


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_hooks_json_gate_decides_on_this_python(tmp_path):
    # On the Python 3.9 CI job this is the reported host: the real gate answers, not a fallback.
    result = _run_hook_command("PreToolUse", tmp_path, CURRENT_INTERPRETER, FAILING_INTERPRETER, RUN_COMMAND_PAYLOAD)

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"decision": "allow"}


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_hooks_json_falls_through_to_python_when_python3_is_too_old(tmp_path):
    # A delete must reach python intact, not as an empty stdin left by python3, which the gate cannot read.
    result = _run_hook_command("PreToolUse", tmp_path, TOO_OLD_INTERPRETER, CURRENT_INTERPRETER, DELETE_MEMORY_PAYLOAD)

    assert result.returncode == 0
    reply = json.loads(result.stdout)
    assert reply["decision"] == "force_ask"
    assert "memory_delete" in reply["reason"]
    assert "Python 3.9" in result.stderr


@patch("load_skill.make_request")
def test_search_and_fetch_skill(mock_make_request):
    mock_make_request.side_effect = [
        {"skills": [{"id": "makefile-pattern", "name": "Makefile Pattern", "description": "Makefile guidelines"}]},
        {"id": "makefile-pattern", "body": "# Makefile Pattern"},
    ]
    res = load_skill.search_skills("make")
    assert len(res) == 1
    assert res[0]["id"] == "makefile-pattern"

    body = load_skill.fetch_skill("makefile-pattern")
    assert body["body"] == "# Makefile Pattern"


def test_manage_skills_badges():
    assert manage_skills.compute_trust_badge({"trust_badge": "proven"}) == "Proven"
    assert manage_skills.compute_trust_badge({"passed_tests_count": 2}) == "Proven"
    assert manage_skills.compute_trust_badge({"passed_tests_count": 1}) == "Checked"
    assert manage_skills.compute_trust_badge({"stage": "active"}) == "Checked"
    assert manage_skills.compute_trust_badge({"stage": "candidate"}) == "Draft"


def test_unified_script_config_resolution():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        local_cfg = tmp_path / ".config.json"
        local_cfg.write_text(
            json.dumps({"apiUrl": "http://127.0.0.1:19999", "apiKey": "local_key", "space": "local_space"}),
            encoding="utf-8",
        )

        test_doc = tmp_path / "doc.md"
        test_doc.write_text("---\nid: src_test\ntitle: Sample Doc\n---\nHello", encoding="utf-8")

        with (
            patch.dict(os.environ, {"HOME": tmpdir}, clear=True),
            patch("pathlib.Path.cwd", return_value=tmp_path),
            patch("os.getcwd", return_value=str(tmp_path)),
            patch("urllib.request.urlopen") as mock_urlopen,
        ):
            modules = [
                update_artifact,
                update_working_memory,
                manage_rules,
                load_skill,
                manage_skills,
                propose_skill,
            ]
            for mod in modules:
                cfg = mod.load_config()
                assert cfg["apiUrl"] == "http://127.0.0.1:19999"
                assert cfg["apiKey"] == "local_key"
                assert cfg["space"] == "local_space"

            # Verify update_artifact passes effective_space when no --space-id is provided on CLI
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"status": "success", "id": "src_test"}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            with patch.object(sys, "argv", ["update_artifact.py", str(test_doc)]):
                update_artifact.main()
                assert mock_urlopen.call_count >= 1
                req = mock_urlopen.call_args[0][0]
                assert "space_id=local_space" in req.get_full_url()

            # Verify update_working_memory passes effective_space when no --space-id is provided on CLI
            mock_urlopen.reset_mock()
            mock_resp.read.return_value = b'{"status": "ok", "size_bytes": 5}'
            with patch.object(sys, "argv", ["update_working_memory.py", "--content", "Working memory test"]):
                update_working_memory.main()
                assert mock_urlopen.call_count >= 1
                req = mock_urlopen.call_args[0][0]
                assert "space_id=local_space" in req.get_full_url()
                payload = json.loads(req.data.decode("utf-8"))
                assert payload.get("space_id") == "local_space"


def load_tests(loader, tests, pattern):
    import inspect
    import unittest

    suite = unittest.TestSuite()
    current_module = sys.modules[__name__]
    for name, obj in inspect.getmembers(current_module):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite
