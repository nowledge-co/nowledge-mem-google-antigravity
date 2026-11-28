import importlib.util
import io
import json
import os
import shlex
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
def reset_active_workspace_root(tmp_path, monkeypatch):
    nmem_shared.activate_workspace_root(None)
    # Keep unit tests isolated from any real ~/.gemini/config/mcp_config.json on the developer host
    # unless a test explicitly creates one under its own HOME/USERPROFILE.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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
    if os.name == "nt":
        assert "cmd.exe /d /v:off /s /c " in cmd
    else:
        assert cmd[:5] == ["cmd.exe", "/d", "/v:off", "/s", "/c"]


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


@patch("nmem_shared._mcp_space_id", return_value="personal")
@patch("nmem_shared.get_effective_config")
def test_sync_mcp_config_file(mock_get_effective_config, mock_resolve_space):
    mock_get_effective_config.return_value = ("https://mem.example.com", "nmem_sec_123")
    with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
        tf.write('{"mcpServers":{"nowledge-mem":{"serverUrl":"http://127.0.0.1:14242/mcp/"}}}')
        tf_path = tf.name
    try:
        updated = nmem_shared.sync_mcp_config_file(tf_path)
        assert updated is True
        data = json.loads(Path(tf_path).read_text(encoding="utf-8"))
        assert data["mcpServers"]["nowledge-mem"]["url"] == "https://mem.example.com/mcp"
        assert "serverUrl" not in data["mcpServers"]["nowledge-mem"]
        assert data["mcpServers"]["nowledge-mem"]["headers"]["Authorization"] == "Bearer nmem_sec_123"
        assert data["mcpServers"]["nowledge-mem"]["headers"]["X-NMEM-API-Key"] == "nmem_sec_123"
        assert "X-MEM-API-Key" not in data["mcpServers"]["nowledge-mem"]["headers"]

        headers = data["mcpServers"]["nowledge-mem"]["headers"]
        assert headers["X-Nmem-Space-Id"] == "personal"
        assert "x-nmem-space-protocol" not in headers
        mock_resolve_space.assert_called_once_with(None, ("https://mem.example.com", "nmem_sec_123"))

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


def test_save_unsynced_session(tmp_path, monkeypatch):
    path = tmp_path / "data" / "unsynced.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: path)
    nmem_shared.save_unsynced_session("conv-1", [{"role": "user", "content": "hi"}], "title", "space", "host")
    assert json.loads(path.read_text())["conv-1"]["messages"] == [{"role": "user", "content": "hi"}]


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
                        "connection_scope": nmem_shared._spaces_cache_scope_id(),
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
            {"status": 404, "detail": "Thread not found"},
            {"id": "conv-1", "title": "Title"},
            {"id": "conv-1", "space_id": "default", "messages": [{"role": "user", "content": "hi"}]},
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
                        "connection_scope": nmem_shared._spaces_cache_scope_id(),
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
            {
                "id": "conv-1",
                "space_id": "default",
                "messages": [{"role": "user", "content": "Save this"}, {"role": "assistant", "content": "Saved."}],
            },
        ]
        nmem_shared.retry_unsynced_sessions()

        assert mock_http.call_count == 3
        assert mock_http.call_args_list[1].args[0] == "/threads/conv-1/append"
        payload = mock_http.call_args_list[1].kwargs["payload"]
        assert payload["expected_message_count"] == 1
        assert payload["idempotency_key"] == nmem_shared.build_append_idempotency_key(
            "conv-1", 1, [{"role": "assistant", "content": "Saved."}]
        )
        assert payload["space_id"] == "default"
        assert "space" not in payload
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
    assert "project-space" in payload["injectSteps"][0]["systemMessage"]["systemMessage"]


def test_session_start_reports_invalid_workspace_space_once(tmp_path):
    # SessionStart reports a Space error once; PreInvocation skips Space lookup when no proposal exists.
    workspace = tmp_path / "customer-project"
    workspace.mkdir()
    (workspace / ".nmemspace").write_text("missing-space", encoding="utf-8")
    emitted = {}

    for argv in (["session-start.py"], ["session-start.py", "--pre-invocation"]):
        with (
            patch(
                "nmem_shared.read_hook_input",
                return_value={"conversationId": "conv-workspace", "workspacePaths": [str(workspace)]},
            ),
            patch("nmem_shared.get_existing_spaces", return_value=[{"id": "default"}]),
            patch("nmem_shared.sync_mcp_config_file"),
            patch("nmem_shared.sync_host_skills_async"),
            patch("subprocess.Popen"),
            patch("nmem_shared.emit") as mock_emit,
            patch.object(sys, "argv", argv),
        ):
            session_start.main()
        mock_emit.assert_called_once()
        emitted[len(argv)] = mock_emit.call_args.args[0]

    assert "missing-space" in emitted[1]["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert emitted[2] == {}


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
        mock_http.side_effect = [{"status": 404, "detail": "Thread not found"}, {"status": "ok"}]
        session_end.main()

    create_call = next(call for call in mock_http.call_args_list if call.args[0] == "/threads")
    assert create_call.kwargs["payload"]["space_id"] == "project-space"


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
        patch("subprocess.Popen") as mock_popen,
        patch.object(sys, "argv", ["session-start.py"]),
    ):
        session_start.main()
        mock_skills_sync.assert_called_once()
        assert mock_popen.call_args.args[0][-1] == "--retry-only"
        assert mock_http.call_count >= 1
        mock_write.assert_called_once()
        payload = json.loads(mock_write.call_args[0][0])
        assert "injectSteps" in payload
        assert len(payload["injectSteps"]) > 0
        emitted_text = payload["injectSteps"][0]["systemMessage"]["systemMessage"]
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
        patch("subprocess.Popen"),
        patch.object(sys, "argv", ["session-start.py"]),
    ):
        session_start.main()
        mock_skills_sync.assert_called_once()
        assert mock_http.call_count == 2
        mock_write.assert_called_once()
        payload = json.loads(mock_write.call_args[0][0])
        assert "injectSteps" in payload
        assert len(payload["injectSteps"]) > 0
        emitted_text = payload["injectSteps"][0]["systemMessage"]["systemMessage"]
        assert "Daily Working Memory Briefing" in emitted_text


@pytest.mark.parametrize(
    ("invocation_num", "initial_num_steps"),
    [(0, 1), (1, 4), (0, 6)],
    ids=["first-turn", "after-tool-call", "later-turn"],
)
def test_session_start_pre_invocation_never_injects_startup_context(invocation_num, initial_num_steps):
    # Antigravity restarts invocationNum at 0 on every user turn, so PreInvocation cannot tell a new conversation.
    hook_input = {
        "conversationId": "conv-123",
        "invocationNum": invocation_num,
        "initialNumSteps": initial_num_steps,
    }

    with (
        patch("nmem_shared.read_hook_input", return_value=hook_input),
        patch("nmem_shared.resolve_space", return_value="default"),
        patch("nmem_shared.sync_mcp_config_file") as mock_sync_mcp,
        patch("nmem_shared.sync_host_skills_async") as mock_sync_skills,
        patch("nmem_shared.sync_learnings_if_any") as mock_sync_learnings,
        patch("session_start.read_startup_context") as mock_read_context,
        patch("subprocess.Popen") as mock_popen,
        patch("nmem_shared.emit") as mock_emit,
        patch.object(sys, "argv", ["session-start.py", "--pre-invocation"]),
    ):
        session_start.main()

    mock_emit.assert_called_once_with({})
    mock_read_context.assert_not_called()
    mock_popen.assert_not_called()
    mock_sync_mcp.assert_not_called()
    mock_sync_skills.assert_not_called()
    mock_sync_learnings.assert_called_once()


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            "<USER_REQUEST><t3_code_instructions>host instructions</t3_code_instructions>\n"
            "<user_request>Fix\n the parser</user_request><runtime_info>metadata</runtime_info>"
            "</USER_REQUEST><ADDITIONAL_METADATA>ignored</ADDITIONAL_METADATA>",
            "Fix the parser",
        ),
        ("<user_request>Inner only</user_request>", "Inner only"),
        ("Explain <user_request>x</user_request> in prose", "Explain <user_request>x</user_request> in prose"),
        ("<USER_REQUEST>Plain request</USER_REQUEST>", "Plain request"),
        ("<runtime_info>metadata</runtime_info>", "Antigravity Session 12345678"),
        ("a" * 60, "a" * 60),
        ("a" * 61, "a" * 60 + "..."),
    ],
)
def test_session_end_thread_title(content, expected):
    assert session_end.thread_title(content, "123456789") == expected


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
        {
            "id": "conv-prefix",
            "space_id": "default",
            "messages": [{"role": "user", "content": "Save this"}, {"role": "assistant", "content": "Saved."}],
        },
    ]
    hook_input = {"conversationId": "conv-prefix", "transcriptPath": str(transcript), "fullyIdle": True}
    with (
        patch("nmem_shared.read_hook_input", return_value=hook_input),
        patch("sys.stdout.write"),
        patch.object(sys, "argv", ["session-end.py"]),
    ):
        session_end.main()

    assert mock_http.call_count == 3
    assert mock_http.call_args_list[1].args[0] == "/threads/conv-prefix/append"
    payload = mock_http.call_args_list[1].kwargs["payload"]
    assert payload["expected_message_count"] == 1
    assert payload["idempotency_key"] == nmem_shared.build_append_idempotency_key(
        "conv-prefix", 1, [{"role": "assistant", "content": "Saved."}]
    )
    assert payload["space_id"] == "default"
    assert "space" not in payload
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
    mock_http.side_effect = [
        {"status": 404, "detail": "Thread not found"},
        {"status": "ok"},
        {
            "thread_id": "conv-utf8",
            "space_id": "default",
            "messages": [{"role": "user", "content": "Save this"}, {"role": "assistant", "content": "Saved."}],
        },
    ]
    with (
        patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="gbk")),
        patch("sys.stdout.write"),
        patch.object(sys, "argv", ["session-end.py"]),
    ):
        session_end.main()

    creates = [call for call in mock_http.call_args_list if call.args[0] == "/threads"]
    assert len(creates) == 1
    assert creates[0].kwargs["payload"]["thread_id"] == "conv-utf8"
    assert "id" not in creates[0].kwargs["payload"]
    assert creates[0].kwargs["payload"]["messages"] == [
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
    ("bridge-start", {}),
    ("session-start", {}),
    ("session-end", {}),
    ("gate", {"decision": "force_ask"}),
    ("post-invocation", {}),
    ("post-tool-use", {}),
]


@pytest.mark.parametrize(
    ("enabled", "context", "expects_hint"),
    [("1", "approved-role", True), ("1", " ", False), ("0", "approved-role", False), ("", "", False)],
)
def test_bridge_start_requires_opt_in_and_configured_context(monkeypatch, capsys, enabled, context, expects_hint):
    monkeypatch.setenv("NMEM_BRIDGE_INBOX_ON_START", enabled)
    monkeypatch.setenv("NMEM_AGENT_CONTEXT", context)
    monkeypatch.setattr(sys, "argv", ["nmem_entrypoint.py", "bridge-start"])
    nmem_entrypoint.main()

    output = json.loads(capsys.readouterr().out)
    if expects_hint:
        hint = output["injectSteps"][0]["systemMessage"]
        assert "nmem-bridge-chat" in hint
        assert "NMEM_AGENT_CONTEXT" in hint
        assert "check one page" in hint
        assert "CLI-only" in hint
        assert context not in hint
    else:
        assert output == {}


def test_bridge_skill_commands_keep_the_selected_inbox_scope():
    guide = (PLUGIN_ROOT / "skills/nmem-bridge-chat/SKILL.md").read_text(encoding="utf-8")
    examples = [shlex.split(line) for line in guide.splitlines() if line.startswith('"${NMEM_CLI_PATH:-nmem}"')]
    prefix = ["${NMEM_CLI_PATH:-nmem}", "--json", "--agent-context=$NMEM_AGENT_CONTEXT", "bridge"]
    scoped = [*prefix, "--resolved-space-id=$bridge_space_id"]
    assert [*scoped, "inbox"] in examples
    assert [*scoped, "open", "$message_id"] in examples
    assert [*scoped, "reply", "$message_id", "--body=$reply_body"] in examples
    interactive = [prefix[0], "--json", "--agent-context=$bridge_context", "bridge"]
    assert [*interactive, "setup"] in examples
    assert [*interactive, "inbox"] in examples
    connect = [prefix[0], "--json", "bridge", "connections", "connect"]
    assert [*connect, "--space-id=$bridge_space_id", "--agent-id=$agent_id"] in examples
    for response_field in (
        "messages[].message",
        "open_args",
        "reply_args_prefix",
        "incomplete",
        "agent_context",
        "next_args",
        "spaces[].next_args",
        "spaces_next_args",
    ):
        assert f"`{response_field}`" in guide


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
@pytest.mark.parametrize(
    "event", ["SessionStart", "PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop"]
)
def test_hooks_json_shell_fallback_prints_valid_json(event, tmp_path):
    # With neither python3 nor python runnable, the echo at the end of the chain answers alone.
    result = _run_hook_command(event, tmp_path, FAILING_INTERPRETER, FAILING_INTERPRETER)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    if event == "PreToolUse":
        assert payload == {"decision": "force_ask"}
    else:
        assert payload == {}


def test_hooks_json_injects_startup_context_from_session_start_only():
    # SessionStart runs once per conversation; both PreInvocation interpreters must carry the sync-only flag.
    assert "session-start" in _hook_command("SessionStart")
    assert "--pre-invocation" not in _hook_command("SessionStart")
    assert _hook_command("PreInvocation").count("session-start --pre-invocation") == 2


def test_entrypoint_passes_pre_invocation_flag_to_session_start():
    hook = MagicMock()
    hook.main.side_effect = lambda: hook.seen_args(sys.argv[1:])
    with (
        patch.object(sys, "argv", ["nmem_entrypoint.py", "session-start", "--pre-invocation"]),
        patch("importlib.import_module", return_value=hook),
    ):
        nmem_entrypoint.main()

    hook.seen_args.assert_called_once_with(["--pre-invocation"])


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


def test_nmem_gate_allows_new_readonly_tools_and_gates_entity_aliases():
    import nmem_gate

    for tool in ("visibility_reason", "memory_identity_read", "read_skill"):
        with (
            patch.object(
                nmem_gate,
                "read_hook_input",
                return_value={"toolCall": {"name": f"mcp_nowledge-mem_{tool}", "args": {}}},
            ),
            patch.object(nmem_gate, "emit") as mock_emit,
        ):
            nmem_gate.main()
            assert mock_emit.call_args.args[0]["decision"] == "allow"

    for tool_call in (
        {
            "name": "call_mcp_tool",
            "args": {"ServerName": "nowledge-mem", "ToolName": "entity_aliases", "Arguments": {"entity_id": "ent_1"}},
        },
        {"name": "mcp_nowledge-mem_entity_aliases", "args": {"entity_id": "ent_1"}},
    ):
        with (
            patch.object(nmem_gate, "read_hook_input", return_value={"toolCall": tool_call}),
            patch.object(nmem_gate, "emit") as mock_emit,
        ):
            nmem_gate.main()
            reply = mock_emit.call_args.args[0]
            assert reply["decision"] == "allow"
            assert "permissionOverrides" not in reply

    for tool_call in (
        {
            "name": "call_mcp_tool",
            "args": {
                "ServerName": "nowledge-mem",
                "ToolName": "entity_aliases",
                "Arguments": {"entity_id": "ent_1", "dry_run": False, "remove": ["old"]},
            },
        },
        {
            "name": "mcp_nowledge-mem_entity_aliases",
            "args": {"entity_id": "ent_1", "dry_run": False, "remove": ["old"]},
        },
        {
            "name": "call_mcp_tool",
            "args": {"ServerName": "nowledge-mem", "ToolName": "entity_aliases", "Arguments": "invalid"},
        },
        {"name": "mcp_nowledge-mem_entity_aliases", "args": "invalid"},
        {"name": "mcp_nowledge-mem_entity_aliases", "args": None},
        {"name": "mcp_nowledge-mem_entity_aliases", "args": {"entity_id": "ent_1", "dry_run": False}},
    ):
        with (
            patch.object(nmem_gate, "read_hook_input", return_value={"toolCall": tool_call}),
            patch.object(nmem_gate, "emit") as mock_emit,
        ):
            nmem_gate.main()
            assert mock_emit.call_args.args[0]["decision"] == "force_ask"

    for tool_call in (
        {
            "name": "call_mcp_tool",
            "args": {
                "ServerName": "nowledge-mem",
                "ToolName": "entity_aliases",
                "Arguments": {"entity_id": "ent_1", "dry_run": False, "add": ["new"]},
            },
        },
        {
            "name": "mcp_nowledge-mem_entity_aliases",
            "args": {"entity_id": "ent_1", "dry_run": False, "add": ["new"]},
        },
    ):
        with (
            patch.object(nmem_gate, "read_hook_input", return_value={"toolCall": tool_call}),
            patch.object(nmem_gate, "emit") as mock_emit,
        ):
            nmem_gate.main()
            reply = mock_emit.call_args.args[0]
            assert reply["decision"] == "ask"
            assert "permissionOverrides" not in reply


def test_session_end_and_retry_cli_append_omit_space_flag(tmp_path):
    session_end = importlib.import_module("session-end")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"source": "USER_EXPLICIT", "content": "hello"}) + "\n", encoding="utf-8")

    # 1. HTTP append sends space_id (not space) as its mutation guard.
    with (
        patch.object(
            nmem_shared,
            "read_hook_input",
            return_value={"conversationId": "conv-append-http", "transcriptPath": str(transcript)},
        ),
        patch.object(nmem_shared, "activate_hook_workspace", return_value=tmp_path),
        patch.object(nmem_shared, "resolve_space", return_value="work-space"),
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(
            nmem_shared,
            "http_request",
            side_effect=[{"id": "conv-append-http", "messages": []}, {"status": "ok"}],
        ) as mock_http,
        patch.object(nmem_shared, "sync_learnings_if_any"),
        patch.object(nmem_shared, "emit"),
    ):
        session_end.main()
        append_payload = mock_http.call_args_list[1].kwargs["payload"]
        assert append_payload.get("space_id") == "work-space"
        assert "space" not in append_payload

    # 2. Scoped CLI append is skipped (kept pending) because CLI t append cannot carry space_id.
    with (
        patch.object(
            nmem_shared,
            "read_hook_input",
            return_value={"conversationId": "conv-append-scoped", "transcriptPath": str(transcript)},
        ),
        patch.object(nmem_shared, "activate_hook_workspace", return_value=tmp_path),
        patch.object(nmem_shared, "resolve_space", return_value="work-space"),
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", return_value=None),
        patch.object(nmem_shared, "run_nmem_command") as mock_run,
        patch.object(nmem_shared, "save_unsynced_session") as mock_save,
        patch.object(nmem_shared, "sync_learnings_if_any"),
        patch.object(nmem_shared, "emit"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="{}")
        session_end.main()
        assert mock_run.call_count == 1  # only t show, no unscoped t append
        mock_save.assert_called_once()

    # 3. Unscoped CLI append uses shared args without --space.
    with (
        patch.object(
            nmem_shared,
            "read_hook_input",
            return_value={"conversationId": "conv-append-1", "transcriptPath": str(transcript)},
        ),
        patch.object(nmem_shared, "activate_hook_workspace", return_value=tmp_path),
        patch.object(nmem_shared, "resolve_space", return_value=""),
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", return_value=None),
        patch.object(nmem_shared, "run_nmem_command") as mock_run,
        patch.object(nmem_shared, "sync_learnings_if_any"),
        patch.object(nmem_shared, "emit"),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"id":"conv-append-1","messages":[]}')
        session_end.main()
        show_call = mock_run.call_args_list[0].args[0]
        append_call = mock_run.call_args_list[1].args[0]
        assert show_call == ["--json", "t", "show", "conv-append-1", "--limit", "1"]
        assert append_call[:3] == ["t", "append", "conv-append-1"]
        assert "--space" not in append_call

    nmem_bin = shutil.which("nmem")
    if nmem_bin:
        help_res = subprocess.run(
            [nmem_bin, *nmem_shared.build_cli_thread_append_args("fixture-id", []), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert help_res.returncode == 0


def test_hooks_json_and_pre_invocation_sync_only(tmp_path):
    assert "session-start" in _hook_command("SessionStart")
    assert "--pre-invocation" not in _hook_command("SessionStart")
    assert _hook_command("PreInvocation").count("session-start --pre-invocation") == 2

    with (
        patch.object(
            nmem_shared,
            "read_hook_input",
            return_value={"conversationId": "conv-1", "artifactDirectoryPath": str(tmp_path)},
        ),
        patch.object(nmem_shared, "activate_hook_workspace", return_value=tmp_path),
        patch.object(nmem_shared, "resolve_space") as mock_resolve,
        patch.object(nmem_shared, "http_request") as mock_http,
        patch.object(nmem_shared, "run_nmem_command") as mock_run,
        patch.object(session_start, "read_startup_context") as mock_read,
        patch("subprocess.Popen") as mock_popen,
        patch.object(nmem_shared, "emit") as mock_emit,
        patch.object(sys, "argv", ["session-start.py", "--pre-invocation"]),
    ):
        session_start.main()

    mock_resolve.assert_not_called()
    mock_http.assert_not_called()
    mock_run.assert_not_called()
    mock_read.assert_not_called()
    mock_popen.assert_not_called()
    mock_emit.assert_called_once_with({})


def test_scoped_capture_retains_queue_and_never_relocates_via_import(tmp_path):
    session_end = importlib.import_module("session-end")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"source": "USER_EXPLICIT", "content": "hello"}) + "\n", encoding="utf-8")

    # 1. session-end: guarded HTTP append error must keep item queued without falling back to CLI/import.
    with (
        patch.object(
            nmem_shared,
            "read_hook_input",
            return_value={"conversationId": "conv-mismatch", "transcriptPath": str(transcript)},
        ),
        patch.object(nmem_shared, "activate_hook_workspace", return_value=tmp_path),
        patch.object(nmem_shared, "resolve_space", return_value="work-space"),
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(
            nmem_shared,
            "http_request",
            side_effect=[{"id": "conv-mismatch", "messages": []}, {"error": "space_mismatch"}],
        ) as mock_http,
        patch.object(nmem_shared, "run_nmem_command") as mock_run,
        patch.object(nmem_shared, "save_unsynced_session") as mock_save,
        patch.object(nmem_shared, "sync_learnings_if_any"),
        patch.object(nmem_shared, "emit"),
    ):
        session_end.main()
        assert all(call.args[0] != "/threads/import" for call in mock_http.call_args_list)
        mock_run.assert_not_called()
        mock_save.assert_called_once()

    # 2. session-end: indeterminate CLI `t show` failure must keep item queued and never run `t import` or `t create`.
    with (
        patch.object(
            nmem_shared,
            "read_hook_input",
            return_value={"conversationId": "conv-indeterminate", "transcriptPath": str(transcript)},
        ),
        patch.object(nmem_shared, "activate_hook_workspace", return_value=tmp_path),
        patch.object(nmem_shared, "resolve_space", return_value="work-space"),
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", return_value=None),
        patch.object(
            nmem_shared,
            "run_nmem_command",
            return_value=MagicMock(returncode=1, stdout="", stderr="connection timed out"),
        ) as mock_run,
        patch.object(nmem_shared, "save_unsynced_session") as mock_save,
        patch.object(nmem_shared, "sync_learnings_if_any"),
        patch.object(nmem_shared, "emit"),
    ):
        session_end.main()
        assert all("import" not in c.args[0] and "create" not in c.args[0] for c in mock_run.call_args_list)
        mock_save.assert_called_once()

    # 3. retry_unsynced_sessions: failed guarded write or collision must retain queue item and never call /threads/import.
    with (
        tempfile.TemporaryDirectory() as home_dir,
        patch.dict(os.environ, {"HOME": home_dir, "USERPROFILE": home_dir}),
    ):
        queue_file = nmem_shared.get_unsynced_queue_path()
        queue_file.parent.mkdir(parents=True, exist_ok=True)
        queue_file.write_text(
            json.dumps(
                {
                    "conv-replay-1": {
                        "connection_scope": nmem_shared._spaces_cache_scope_id(),
                        "conversation_id": "conv-replay-1",
                        "messages": [{"role": "user", "content": "hi"}],
                        "title": "Title",
                        "space": "work-space",
                        "host_agent_id": "host",
                    }
                }
            ),
            encoding="utf-8",
        )
        with (
            patch("nmem_shared.is_backend_unreachable", return_value=False),
            patch(
                "nmem_shared.http_request",
                side_effect=[{"status": 404, "detail": "Thread not found"}, {"error": "thread_exists_in_other_space"}],
            ) as mock_http,
            patch("nmem_shared.run_nmem_command") as mock_run,
        ):
            nmem_shared.retry_unsynced_sessions()
            assert [c.args[0] for c in mock_http.call_args_list] == [
                "/threads/conv-replay-1?space_id=work-space",
                "/threads",
            ]
            mock_run.assert_not_called()
        assert queue_file.exists()
        assert "conv-replay-1" in json.loads(queue_file.read_text(encoding="utf-8"))

        # 4. retry_unsynced_sessions: indeterminate CLI `t show` failure retains queue item without `t import` or `t create`.
        with (
            patch("nmem_shared.is_backend_unreachable", return_value=False),
            patch("nmem_shared.http_request", return_value=None),
            patch(
                "nmem_shared.run_nmem_command",
                return_value=MagicMock(returncode=1, stdout="", stderr="503 service unavailable"),
            ) as mock_run,
        ):
            nmem_shared.retry_unsynced_sessions(ignore_backoff=True)
            assert all("import" not in c.args[0] and "create" not in c.args[0] for c in mock_run.call_args_list)
        assert queue_file.exists()
        assert "conv-replay-1" in json.loads(queue_file.read_text(encoding="utf-8"))


@pytest.mark.parametrize("disable_httpx", [False, True], ids=["httpx", "urllib"])
def test_loopback_http_capture_404_create_and_identical_scope_guard(tmp_path, disable_httpx):
    import http.server
    import threading

    session_end = importlib.import_module("session-end")
    recorded_requests = []
    mode = {"state": "missing"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            if self.path == "/spaces":
                body = json.dumps({"spaces": [{"id": "work-space", "name": "Work Space", "aliases": ["ws"]}]}).encode(
                    "utf-8"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            recorded_requests.append(("GET", self.path, None))
            state = mode["state"]
            if state == "missing":
                body = json.dumps({"detail": "Thread not found"}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "unauthorized":
                body = json.dumps({"detail": "Unauthorized"}).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "forbidden":
                body = json.dumps({"detail": "Forbidden", "error": None}).encode("utf-8")
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "route_404":
                body = json.dumps({"detail": "Not Found"}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "gateway_json_404":
                body = json.dumps({"message": "Not Found"}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "generic_error_not_found":
                body = json.dumps({"error": "not_found"}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "space_not_found":
                body = json.dumps({"error": "not_found", "detail": "Space not found"}).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "server_503":
                body = json.dumps({"detail": "Service Unavailable", "error": ""}).encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "proxy_html_404":
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html>404 Not Found</html>")
            elif state == "wrong_space_identical":
                body = json.dumps(
                    {
                        "thread_id": "conv/1",
                        "space_id": "other-space",
                        "messages": [{"role": "user", "content": "hello"}],
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "canonical_space_id_identical":
                body = json.dumps(
                    {
                        "thread": {
                            "thread_id": "conv/1",
                            "space_id": "work-space",
                            "messages": [{"role": "user", "content": "hello"}],
                        }
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif state == "matching_space_identical":
                body = json.dumps(
                    {
                        "thread_id": "conv/1",
                        "space_id": "work space",
                        "messages": [{"role": "user", "content": "hello"}],
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            recorded_requests.append(("POST", self.path, payload))
            # Enforce ThreadCreateRequest contract: require thread_id, reject id.
            if self.path == "/threads":
                if "thread_id" not in payload or "id" in payload:
                    self.send_response(422)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"detail": "thread_id required"}).encode("utf-8"))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                mode["state"] = "matching_space_identical"
                self.wfile.write(json.dumps({"thread": {"thread_id": payload["thread_id"]}}).encode("utf-8"))
                return
            self.send_response(404)
            self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"source": "USER_EXPLICIT", "content": "hello"}) + "\n", encoding="utf-8")

    sys_mods = {"httpx": None} if disable_httpx else {}
    try:
        with (
            tempfile.TemporaryDirectory() as home_dir,
            patch.dict(
                os.environ,
                {
                    "HOME": home_dir,
                    "USERPROFILE": home_dir,
                    "NMEM_API_URL": base_url,
                    "NMEM_IGNORE_HOST_CONFIG": "1",
                },
            ),
            patch.dict(sys.modules, sys_mods),
            patch.object(nmem_shared, "run_nmem_command", side_effect=AssertionError("CLI must not be called")),
            patch.object(nmem_shared, "resolve_space", return_value="work space"),
            patch.object(nmem_shared, "sync_learnings_if_any"),
            patch.object(nmem_shared, "emit"),
        ):
            nmem_shared.reset_backend_unreachable()

            # 1. Real 404 reaches POST /threads with thread_id + URL-encoded space_id lookup, no CLI needed.
            mode["state"] = "missing"
            recorded_requests.clear()
            with patch.object(
                nmem_shared,
                "read_hook_input",
                return_value={"conversationId": "conv/1", "transcriptPath": str(transcript)},
            ):
                session_end.main()

            assert [r[:2] for r in recorded_requests] == [
                ("GET", "/threads/conv%2F1?space_id=work%20space"),
                ("POST", "/threads"),
                ("GET", "/threads/conv%2F1?space_id=work%20space&limit=1"),
            ]
            post_payload = recorded_requests[1][2]
            assert post_payload is not None
            assert post_payload["thread_id"] == "conv/1"
            assert "id" not in post_payload
            assert post_payload["space_id"] == "work space"
            assert not nmem_shared.get_unsynced_queue_path().exists()

            # 2. Unauthorized (401) and HTML proxy 404 do not dispatch POST /threads and leave capture queued.
            for bad_state in (
                "unauthorized",
                "forbidden",
                "route_404",
                "gateway_json_404",
                "generic_error_not_found",
                "space_not_found",
                "proxy_html_404",
                "server_503",
            ):
                mode["state"] = bad_state
                recorded_requests.clear()
                nmem_shared.reset_backend_unreachable()
                with patch.object(
                    nmem_shared,
                    "read_hook_input",
                    return_value={"conversationId": "conv/1", "transcriptPath": str(transcript)},
                ):
                    session_end.main()
                assert all(r[0] != "POST" for r in recorded_requests)
                assert nmem_shared.get_unsynced_queue_path().exists()

            # 3. Identical-transcript thread in wrong Space stays queued in replay; matching Space unlinks queue.
            mode["state"] = "wrong_space_identical"
            recorded_requests.clear()
            nmem_shared.reset_backend_unreachable()
            nmem_shared.retry_unsynced_sessions()
            assert [r[:2] for r in recorded_requests] == [("GET", "/threads/conv%2F1?space_id=work%20space")]
            assert nmem_shared.get_unsynced_queue_path().exists()

            mode["state"] = "canonical_space_id_identical"
            recorded_requests.clear()
            nmem_shared.reset_backend_unreachable()
            nmem_shared.retry_unsynced_sessions(ignore_backoff=True)
            assert [r[:2] for r in recorded_requests] == [("GET", "/threads/conv%2F1?space_id=work%20space")]
            assert not nmem_shared.get_unsynced_queue_path().exists()

            nmem_shared.save_unsynced_session(
                "conv/1", [{"role": "user", "content": "hello"}], "hello", "work space", "host"
            )
            mode["state"] = "matching_space_identical"
            recorded_requests.clear()
            nmem_shared.retry_unsynced_sessions()
            assert [r[:2] for r in recorded_requests] == [("GET", "/threads/conv%2F1?space_id=work%20space")]
            assert not nmem_shared.get_unsynced_queue_path().exists()
    finally:
        server.shutdown()
        server.server_close()


def test_spaces_cache_binds_to_connection_across_memory_and_fresh_process(tmp_path):
    import http.server
    import threading

    def _start_spaces_server(space_id: str):
        hits = []

        class SpacesHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                hits.append(self.headers.get("Authorization"))
                body = json.dumps({"spaces": [{"id": space_id, "name": "project"}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.HTTPServer(("127.0.0.1", 0), SpacesHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, hits

    srv_a, hits_a = _start_spaces_server("first-id")
    srv_b, hits_b = _start_spaces_server("second-id")
    url_a = f"http://127.0.0.1:{srv_a.server_port}"
    url_b = f"http://127.0.0.1:{srv_b.server_port}"
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()

    try:
        saved_cache = nmem_shared._SPACES_CACHE
        nmem_shared._SPACES_CACHE = {"timestamp": 0.0, "spaces": None}
        try:
            # Seed an unscoped legacy list at the current cache path; it must be discarded.
            cache_file = home_dir / ".nowledge-mem" / "plugins" / "antigravity" / "cache" / "spaces_cache.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps([{"id": "stale-unscoped-id"}]), encoding="utf-8")

            # 1. Warm in-memory switch: resolve A ("first-id"), then switch directly to B ("second-id").
            with patch.dict(
                os.environ,
                {
                    "HOME": str(home_dir),
                    "USERPROFILE": str(home_dir),
                    "NMEM_API_URL": url_a,
                    "NMEM_API_KEY": "secret-key-a",
                    "NMEM_IGNORE_HOST_CONFIG": "1",
                },
            ):
                nmem_shared.reset_backend_unreachable()
                spaces_a = nmem_shared.get_existing_spaces()
                assert spaces_a == [{"id": "first-id", "name": "project"}]
                assert len(hits_a) == 1

            with patch.dict(
                os.environ,
                {
                    "HOME": str(home_dir),
                    "USERPROFILE": str(home_dir),
                    "NMEM_API_URL": url_b,
                    "NMEM_API_KEY": "secret-key-b",
                    "NMEM_IGNORE_HOST_CONFIG": "1",
                },
            ):
                nmem_shared.reset_backend_unreachable()
                assert nmem_shared.get_existing_spaces() == [{"id": "second-id", "name": "project"}]
                assert len(hits_b) == 1

            # Simulate a memory-cache miss (as in a new process) and verify disk hit without new HTTP requests.
            nmem_shared._SPACES_CACHE = {"timestamp": 0.0, "spaces": None}
            with patch.dict(
                os.environ,
                {
                    "HOME": str(home_dir),
                    "USERPROFILE": str(home_dir),
                    "NMEM_API_URL": url_b,
                    "NMEM_API_KEY": "secret-key-b",
                    "NMEM_IGNORE_HOST_CONFIG": "1",
                },
            ):
                nmem_shared.reset_backend_unreachable()
                spaces_b = nmem_shared.get_existing_spaces()
                assert spaces_b == [{"id": "second-id", "name": "project"}]
                assert len(hits_b) == 1

            # 2. Disk cache must contain zero raw secrets and serve each connection in a fresh Python process.
            raw_cache = cache_file.read_text(encoding="utf-8")
            assert "secret-key-a" not in raw_cache
            assert "secret-key-b" not in raw_cache
            assert url_a not in raw_cache
            assert url_b not in raw_cache

            check_code = (
                f"import sys; sys.path.insert(0, {str(HOOKS_DIR)!r}); import nmem_shared, json; "
                "print(json.dumps(nmem_shared.get_existing_spaces()))"
            )
            for url, key, expected_id in ((url_a, "secret-key-a", "first-id"), (url_b, "secret-key-b", "second-id")):
                proc = subprocess.run(
                    [sys.executable, "-c", check_code],
                    env={
                        **os.environ,
                        "HOME": str(home_dir),
                        "USERPROFILE": str(home_dir),
                        "NMEM_API_URL": url,
                        "NMEM_API_KEY": key,
                        "NMEM_IGNORE_HOST_CONFIG": "1",
                    },
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert proc.returncode == 0
                assert json.loads(proc.stdout.strip()) == [{"id": expected_id, "name": "project"}]
            # Neither A nor B received a second HTTP /spaces request during the fresh-process cache hits.
            assert len(hits_a) == 1
            assert len(hits_b) == 1
        finally:
            nmem_shared._SPACES_CACHE = saved_cache
    finally:
        srv_a.shutdown()
        srv_a.server_close()
        srv_b.shutdown()
        srv_b.server_close()


@pytest.mark.parametrize("disable_httpx", [False, True], ids=["httpx", "urllib"])
def test_spaces_cache_401_reload_and_cli_switch_bind_to_answering_connection(tmp_path, disable_httpx, monkeypatch):
    import http.server
    import threading

    if disable_httpx:
        monkeypatch.setitem(sys.modules, "httpx", None)

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    local_cfg_file = proj_dir / ".config.json"

    hits_a = []
    hits_b = []
    rotate_on_401 = {"armed": True, "url_b": ""}

    class HandlerA(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            hits_a.append(self.headers.get("Authorization"))
            if rotate_on_401["armed"]:
                rotate_on_401["armed"] = False
                local_cfg_file.write_text(
                    json.dumps({"apiUrl": rotate_on_401["url_b"], "apiKey": "key-b"}),
                    encoding="utf-8",
                )
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail":"Unauthorized"}')
                return
            body = json.dumps({"spaces": [{"id": "first-id", "name": "project"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    class HandlerB(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            hits_b.append(self.headers.get("Authorization"))
            # Rotate config AGAIN (B -> C) while B's response is in flight so neither pre-call (A)
            # nor post-call (C) get_effective_config() matches B; only connection_out knows B answered.
            local_cfg_file.write_text(
                json.dumps({"apiUrl": rotate_on_401["url_a"], "apiKey": "key-c"}),
                encoding="utf-8",
            )
            body = json.dumps({"spaces": [{"id": "second-id", "name": "project"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv_a = http.server.HTTPServer(("127.0.0.1", 0), HandlerA)
    srv_b = http.server.HTTPServer(("127.0.0.1", 0), HandlerB)
    threading.Thread(target=srv_a.serve_forever, daemon=True).start()
    threading.Thread(target=srv_b.serve_forever, daemon=True).start()
    url_a = f"http://127.0.0.1:{srv_a.server_port}"
    url_b = f"http://127.0.0.1:{srv_b.server_port}"
    rotate_on_401["url_a"] = url_a
    rotate_on_401["url_b"] = url_b

    monkeypatch.delenv("NMEM_API_URL", raising=False)
    monkeypatch.delenv("NMEM_API_KEY", raising=False)
    monkeypatch.delenv("NMEM_CONFIG_PATH", raising=False)
    monkeypatch.delenv("NMEM_CLI_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("NMEM_IGNORE_HOST_CONFIG", "1")

    saved_cache = nmem_shared._SPACES_CACHE
    saved_ws = nmem_shared._ACTIVE_WORKSPACE_ROOT
    nmem_shared._SPACES_CACHE = {"timestamp": 0.0, "spaces": None}
    try:
        nmem_shared.activate_hook_workspace({"workspacePaths": [str(proj_dir)]})
        nmem_shared.reset_backend_unreachable()
        cache_file = home_dir / ".nowledge-mem" / "plugins" / "antigravity" / "cache" / "spaces_cache.json"

        # Start configured for A; A returns 401 while rotating A -> B, and B rotates B -> C before replying 200.
        local_cfg_file.write_text(json.dumps({"apiUrl": url_a, "apiKey": "key-a"}), encoding="utf-8")
        scope_a = nmem_shared._spaces_cache_scope_id((url_a, "key-a"))
        scope_b = nmem_shared._spaces_cache_scope_id((url_b, "key-b"))
        scope_c = nmem_shared._spaces_cache_scope_id((url_a, "key-c"))

        spaces = nmem_shared.get_existing_spaces()
        assert spaces == [{"id": "second-id", "name": "project"}]
        assert len(hits_a) == 1
        assert len(hits_b) == 1

        # Persisted and in-memory caches must record B's catalog under scope_b ONLY (never scope_a or scope_c).
        persisted = json.loads(cache_file.read_text(encoding="utf-8"))
        assert scope_b in persisted["entries"]
        assert scope_a not in persisted["entries"]
        assert scope_c not in persisted["entries"]
        mem_entries = nmem_shared._SPACES_CACHE.get("entries")
        assert isinstance(mem_entries, dict)
        assert any(k.startswith(f"{scope_b}:") for k in mem_entries)
        assert all(not k.startswith(f"{scope_a}:") and not k.startswith(f"{scope_c}:") for k in mem_entries)

        # Connection B hits both memory and disk caches with zero new requests to B.
        local_cfg_file.write_text(json.dumps({"apiUrl": url_b, "apiKey": "key-b"}), encoding="utf-8")
        assert nmem_shared.get_existing_spaces() == [{"id": "second-id", "name": "project"}]
        nmem_shared._SPACES_CACHE = {"timestamp": 0.0, "spaces": None}
        assert nmem_shared.get_existing_spaces() == [{"id": "second-id", "name": "project"}]
        assert len(hits_b) == 1

        # Returning to Connection A (warm memory + populated disk cache) must NOT read B's catalog; it queries A.
        local_cfg_file.write_text(json.dumps({"apiUrl": url_a, "apiKey": "key-a"}), encoding="utf-8")
        assert nmem_shared.get_existing_spaces() == [{"id": "first-id", "name": "project"}]
        assert len(hits_a) == 2

        # CLI fallback: config rotates A -> B during HTTP failure, and B -> C during subprocess execution;
        # CLI also isolates NMEM_CLI_CONFIG_DIR when target connection has no apiKey.
        nmem_shared._SPACES_CACHE = {"timestamp": 0.0, "spaces": None}
        cache_file.unlink(missing_ok=True)
        host_cfg = home_dir / ".nowledge-mem" / "config.json"
        host_cfg.write_text(
            json.dumps({"apiUrl": "https://other.example.com", "apiKey": "foreign-key"}), encoding="utf-8"
        )
        monkeypatch.setenv("NMEM_IGNORE_HOST_CONFIG", "0")
        local_cfg_file.write_text(json.dumps({"apiUrl": url_a, "apiKey": "cli-key-a"}), encoding="utf-8")
        scope_cli_a = nmem_shared._spaces_cache_scope_id((url_a, "cli-key-a"))
        scope_cli_b_nokey = nmem_shared._spaces_cache_scope_id((url_b, None))
        scope_cli_c = nmem_shared._spaces_cache_scope_id((url_a, "cli-key-c"))

        def _fail_http_and_rotate_to_keyless_b(*args, **kwargs):
            local_cfg_file.write_text(json.dumps({"apiUrl": url_b}), encoding="utf-8")
            return None

        captured_envs = []

        def _fake_cli_run(*args, **kwargs):
            captured_envs.append(dict(kwargs.get("env") or {}))
            local_cfg_file.write_text(json.dumps({"apiUrl": url_a, "apiKey": "cli-key-c"}), encoding="utf-8")
            return subprocess.CompletedProcess(
                args=["nmem", "--json", "spaces", "list"],
                returncode=0,
                stdout=json.dumps({"spaces": [{"id": "cli-second-id", "name": "project"}]}),
                stderr="",
            )

        with (
            patch.object(nmem_shared, "http_request", side_effect=_fail_http_and_rotate_to_keyless_b),
            patch.object(nmem_shared, "_nmem_command", return_value="/usr/bin/nmem"),
            patch("subprocess.run", side_effect=_fake_cli_run),
        ):
            nmem_shared.reset_backend_unreachable()
            assert nmem_shared.get_existing_spaces() == [{"id": "cli-second-id", "name": "project"}]

        assert len(captured_envs) == 1
        assert captured_envs[0].get("NMEM_API_URL") == url_b
        assert captured_envs[0].get("NMEM_API_KEY") == ""
        assert captured_envs[0].get("NMEM_CLI_CONFIG_DIR") == os.devnull
        assert captured_envs[0].get("NMEM_CONFIG_PATH") == os.devnull

        persisted_cli = json.loads(cache_file.read_text(encoding="utf-8"))
        assert scope_cli_b_nokey in persisted_cli["entries"]
        assert scope_cli_a not in persisted_cli["entries"]
        assert scope_cli_c not in persisted_cli["entries"]
        cli_mem_entries = nmem_shared._SPACES_CACHE.get("entries")
        assert isinstance(cli_mem_entries, dict)
        assert any(k.startswith(f"{scope_cli_b_nokey}:") for k in cli_mem_entries)
    finally:
        nmem_shared._SPACES_CACHE = saved_cache
        nmem_shared._ACTIVE_WORKSPACE_ROOT = saved_ws
        nmem_shared.reset_backend_unreachable()
        srv_a.shutdown()
        srv_a.server_close()
        srv_b.shutdown()
        srv_b.server_close()


def load_tests(loader, tests, pattern):
    import inspect
    import unittest

    suite = unittest.TestSuite()
    current_module = sys.modules[__name__]
    for name, obj in inspect.getmembers(current_module):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite


def test_pending_capture_replays_only_for_original_account(tmp_path, monkeypatch):
    path = tmp_path / "pending.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: path)
    monkeypatch.setattr(nmem_shared, "is_backend_unreachable", lambda: False)
    monkeypatch.setenv("NMEM_API_URL", "https://test.invalid")
    path.write_text(
        json.dumps({"legacy": {"messages": [{"role": "user", "content": "preserve"}]}, "empty": {"messages": []}})
    )
    for key in ("first", "second"):
        monkeypatch.setenv("NMEM_API_KEY", key)
        nmem_shared.save_unsynced_session("same", [{"role": "user", "content": key}], "Title", "default", None)
    with patch.object(nmem_shared, "sync_thread_capture", return_value=(True, True)) as sync:
        for key in ("first", "second"):
            monkeypatch.setenv("NMEM_API_KEY", key)
            nmem_shared.retry_unsynced_sessions()
            assert sync.call_args.args[0:3] == ("same", "Title", [{"role": "user", "content": key}])
        assert sync.call_count == 2
    assert list(json.loads(path.read_text())) == ["legacy"]


@pytest.mark.parametrize("changed", [False, True])
def test_paginated_capture_requires_complete_prefix_and_honors_reconcile_refusal(changed):
    rows = [{"role": "user", "content": str(i)} for i in range(501)]
    local = [*rows[:-1], {"role": "user", "content": "changed"}] if changed else rows

    def fake_http(endpoint, method="GET", payload=None, **kwargs):
        if method == "GET":
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(endpoint).query)
            offset = int(qs.get("offset", ["0"])[0])
            limit = int(qs.get("limit", ["500"])[0])
            assert limit <= nmem_shared.THREAD_SYNC_TAIL_WINDOW
            return {
                "id": "c",
                "space_id": "project",
                "total_messages": len(rows),
                "messages": rows[offset : offset + limit],
            }
        if endpoint == "/threads/c/reconcile-tail":
            return {"status": 409, "error": "linked"}
        raise AssertionError(f"Unexpected call: {method} {endpoint}")

    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", side_effect=fake_http) as request,
    ):
        assert nmem_shared.sync_thread_capture("c", "Title", local, "project") == (not changed, True)
    assert request.call_args_list[0].args == ("/threads/c?space_id=project&limit=0",)
    assert request.call_args_list[1].args == ("/threads/c?space_id=project&limit=32",)
    # Full unknown prefix (501 rows) must be verified across bounded <=32 pages (16 pages + 1 limit=0 metadata GET).
    get_calls = [c for c in request.call_args_list if c.kwargs.get("method") == "GET"]
    assert len(get_calls) == 17
    if changed:
        assert request.call_args.args == ("/threads/c/reconcile-tail",)
        assert request.call_args.kwargs["payload"]["matched_count"] == 500
        assert request.call_args.kwargs["payload"]["messages"] == [{"role": "user", "content": "changed"}]


def test_sync_mcp_space_change_and_validation(tmp_path, monkeypatch):
    config = tmp_path / "mcp.json"
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd: ("https://mem.example.com", None))
    monkeypatch.setattr(nmem_shared, "http_request", lambda *args, **kwargs: [{"id": "personal"}, {"id": "work"}])
    monkeypatch.setenv("NMEM_SPACE", "personal")
    assert nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    monkeypatch.setenv("NMEM_SPACE", "work")
    assert nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    data = json.loads(config.read_text())
    assert data["mcpServers"]["nowledge-mem"]["headers"]["X-Nmem-Space-Id"] == "work"
    assert "Authorization" not in data["mcpServers"]["nowledge-mem"]["headers"]
    monkeypatch.delenv("NMEM_SPACE")
    monkeypatch.delenv("NMEM_SPACE_ID", raising=False)
    monkeypatch.setattr(nmem_shared, "get_existing_spaces", lambda: [{"id": "personal"}])
    (tmp_path / ".nmemspace").write_text("personal")
    assert nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    data = json.loads(config.read_text())
    assert data["mcpServers"]["nowledge-mem"]["headers"]["X-Nmem-Space-Id"] == "personal"
    (tmp_path / ".nmemspace").write_text("nonexistent-space")
    with pytest.raises(nmem_shared.SpaceResolutionError):
        nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    assert json.loads(config.read_text()) == {"mcpServers": {}}


def test_mcp_space_header_uses_canonical_id_and_preserves_unbound_policy(tmp_path, monkeypatch):
    config = tmp_path / "mcp.json"
    workspace = tmp_path / "unbound-project"
    workspace.mkdir()
    monkeypatch.delenv("NMEM_SPACE", raising=False)
    monkeypatch.delenv("NMEM_SPACE_ID", raising=False)
    monkeypatch.setattr(nmem_shared, "get_plugin_config", lambda: {})
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd: ("https://mem.example.com", None))
    monkeypatch.setattr(
        nmem_shared,
        "http_request",
        lambda *args, **kwargs: [
            {"id": "space-42", "name": "Research", "aliases": ["work"]},
            {"id": "default"},
        ],
    )

    def headers():
        nmem_shared.sync_mcp_config_file(str(config), cwd=workspace)
        return json.loads(config.read_text())["mcpServers"]["nowledge-mem"]["headers"]

    assert "X-Nmem-Space-Id" not in headers()
    monkeypatch.setattr(nmem_shared, "get_existing_spaces", lambda: [])
    assert nmem_shared.resolve_space(workspace) == "default"

    binding = workspace / ".nmemspace"
    binding.write_text("work")
    assert headers()["X-Nmem-Space-Id"] == "space-42"
    binding.write_text("default")
    assert headers()["X-Nmem-Space-Id"] == "default"
    binding.unlink()
    assert "X-Nmem-Space-Id" not in headers()


def test_mcp_catalog_failure_disables_previous_inferred_header(tmp_path, monkeypatch):
    config = tmp_path / "mcp.json"
    workspace = tmp_path / "research"
    workspace.mkdir()
    monkeypatch.delenv("NMEM_SPACE", raising=False)
    monkeypatch.delenv("NMEM_SPACE_ID", raising=False)
    monkeypatch.setattr(nmem_shared, "get_plugin_config", lambda: {})
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd: ("https://mem.example.com", "fixture-key"))

    def catalog(endpoint, *, client_config):
        assert endpoint == "/spaces"
        assert client_config == ("https://mem.example.com", "fixture-key")
        return {"spaces": [{"id": "space-42", "name": "Research"}]}

    monkeypatch.setattr(nmem_shared, "http_request", catalog)
    assert nmem_shared.sync_mcp_config_file(str(config), cwd=workspace)
    before = config.read_bytes()
    assert json.loads(before)["mcpServers"]["nowledge-mem"]["headers"]["X-Nmem-Space-Id"] == "space-42"

    monkeypatch.setattr(nmem_shared, "http_request", lambda *args, **kwargs: None)
    with pytest.raises(nmem_shared.SpaceResolutionError):
        nmem_shared.sync_mcp_config_file(str(config), cwd=workspace)
    assert json.loads(config.read_text()) == {"mcpServers": {}}


@pytest.mark.parametrize("initial", [False, True])
def test_hook_mcp_sync_refusal_keeps_startup_context_and_skips_tool_rounds(initial):
    hook_input = {"conversationId": "fixture", "invocationNum": 0 if initial else 2, "initialNumSteps": 5}
    context = {"tag": "nowledge_working_memory", "label": "legacy Working Memory file", "content": "local context"}
    with (
        patch("nmem_shared.read_hook_input", return_value=hook_input),
        patch("nmem_shared.activate_hook_workspace", return_value=None),
        patch("nmem_shared.sync_host_skills_async"),
        patch(
            "nmem_shared.sync_mcp_config_file", side_effect=nmem_shared.SpaceResolutionError("catalog unavailable")
        ) as sync,
        patch("nmem_shared.resolve_space", return_value="default"),
        patch("nmem_shared.sync_learnings_if_any"),
        patch("session_start.read_startup_context", return_value=context) as read_context,
        patch("subprocess.Popen") as retry,
        patch("nmem_shared.emit") as emit,
        patch.object(sys, "argv", ["session-start.py"] if initial else ["session-start.py", "--pre-invocation"]),
    ):
        session_start.main()
    if initial:
        sync.assert_called_once()
        read_context.assert_called_once()
        retry.assert_called_once()
        steps = emit.call_args.args[0]["injectSteps"]
        assert "catalog unavailable" in steps[0]["ephemeralMessage"]
        assert "local context" in steps[1]["systemMessage"]["systemMessage"]
    else:
        sync.assert_not_called()
        read_context.assert_not_called()
        retry.assert_not_called()
        emit.assert_called_once_with({})


@pytest.mark.parametrize(
    "next_connection", [("https://b.invalid", "key-a"), ("https://a.invalid", "key-b"), ("https://a.invalid", None)]
)
def test_offline_switch_disables_previous_mcp_transport(tmp_path, monkeypatch, next_connection):
    config = tmp_path / "mcp.json"
    monkeypatch.setenv("NMEM_SPACE", "Research")
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd: ("https://a.invalid", "key-a"))
    monkeypatch.setattr(nmem_shared, "http_request", lambda *a, **kw: [{"id": "old", "name": "Research"}])
    nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    existing = json.loads(config.read_text())
    existing["mcpServers"]["other"] = {"command": "other"}
    config.write_text(json.dumps(existing))
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd: next_connection)
    monkeypatch.setattr(nmem_shared, "http_request", lambda *a, **kw: None)
    with pytest.raises(nmem_shared.SpaceResolutionError):
        nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    assert json.loads(config.read_text()) == {"mcpServers": {"other": {"command": "other"}}}
    monkeypatch.setattr(nmem_shared, "http_request", lambda *a, **kw: [{"id": "new", "name": "Research"}])
    assert nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    assert json.loads(config.read_text())["mcpServers"]["nowledge-mem"]["headers"]["X-Nmem-Space-Id"] == "new"
    assert json.loads(config.read_text())["mcpServers"]["other"] == {"command": "other"}


def test_mcp_write_failure_reports_old_connection_without_credentials(tmp_path, monkeypatch, capsys):
    config = tmp_path / "mcp.json"
    monkeypatch.setenv("NMEM_SPACE", "default")
    monkeypatch.setenv("NMEM_DEBUG", "1")
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd: ("https://a.invalid", "first-key"))
    assert nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    before = config.read_bytes()
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd: ("https://b.invalid", "second-key"))
    with (
        patch("nmem_shared._write_json_atomic", side_effect=PermissionError("private-error-marker")),
        pytest.raises(nmem_shared.SpaceResolutionError, match="previous MCP connection may still be active") as err,
    ):
        nmem_shared.sync_mcp_config_file(str(config), cwd=tmp_path)
    assert config.read_bytes() == before
    diagnostic = str(err.value) + capsys.readouterr().err
    for private in ("first-key", "second-key", "private-error-marker"):
        assert private not in diagnostic


def test_queue_save_preserves_evidence_and_existing_capture_on_failures(tmp_path, monkeypatch):
    path = tmp_path / "unsynced.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: path)
    path.write_text("partial-json")

    def save(messages):
        nmem_shared.save_unsynced_session("conv", messages, "title", "default", None)

    save([{"content": "original"}])
    assert next(tmp_path.glob("unsynced.corrupt-*.json")).read_text() == "partial-json"
    snapshot = path.read_bytes()
    save([])
    assert path.read_bytes() == snapshot
    with patch.object(os, "replace", side_effect=OSError("disk error")):
        save([{"content": "new"}])
    assert path.read_bytes() == snapshot


def test_failed_replay_does_not_replace_pending_queue(tmp_path, monkeypatch):
    path = tmp_path / "unsynced.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: path)
    path.write_text('{"legacy": {"messages":[{"role":"user","content":"preserve"}]}}')
    previous = path.read_bytes()
    nmem_shared.retry_unsynced_sessions()
    assert path.read_bytes() == previous


def test_queue_migration_preserves_corrupt_or_unwritable_backlogs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    target = nmem_shared.get_unsynced_queue_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    old = tmp_path / ".nowledge-mem" / "antigravity_unsynced.json"
    old.write_text("corrupt")
    nmem_shared.get_unsynced_queue_path()
    assert next(old.parent.glob("antigravity_unsynced.corrupt-*.json")).read_text() == "corrupt"
    old.write_text('{"old":{}}')
    target.write_text('{"new":{}}')
    with patch.object(nmem_shared, "_write_json_atomic", side_effect=OSError("full")):
        nmem_shared.get_unsynced_queue_path()
    assert old.read_text() == '{"old":{}}'
    assert target.read_text() == '{"new":{}}'


@pytest.mark.parametrize(
    "response",
    [
        {"id": "other", "space_id": "project", "messages": [{"role": "user", "content": "hi"}]},
        {"id": "conversation", "space_id": "other", "messages": [{"role": "user", "content": "hi"}]},
        {"id": "conversation", "space_id": "project", "messages": []},
    ],
)
def test_capture_never_acknowledges_unverified_creation(response):
    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "get_existing_spaces", return_value=[]),
        patch.object(
            nmem_shared,
            "http_request",
            side_effect=[
                {"status": 404, "detail": "Thread not found"},
                {"status": "ok"},
                response,
            ],
        ),
        patch.object(nmem_shared, "run_nmem_command") as cli,
    ):
        assert nmem_shared.sync_thread_capture(
            "conversation", "title", [{"role": "user", "content": "hi"}], "project"
        ) == (False, True)
        cli.assert_not_called()


def test_cli_replay_of_persisted_transcript_never_appends_twice():
    rows = [{"role": "user", "content": "你好"}]
    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", return_value=None),
        patch.object(
            nmem_shared,
            "run_nmem_command",
            return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"id": "conversation", "space_id": "project", "messages": rows}), ""
            ),
        ) as cli,
    ):
        assert nmem_shared.sync_thread_capture("conversation", "title", rows, "project") == (True, True)
        assert cli.call_count == 1
        assert cli.call_args.args[0] == ["--json", "t", "show", "conversation", "--limit", "1", "--space", "project"]


@pytest.mark.parametrize("existing", [[], [{"role": "user", "content": "first"}]])
def test_successful_mutation_without_persisted_tail_stays_pending(existing):
    rows = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "second"}]
    detail = {"id": "c", "space_id": "work", "messages": existing}
    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", side_effect=[detail, {"status": "ok"}, detail]) as http,
        patch.object(nmem_shared, "run_nmem_command") as cli,
    ):
        assert nmem_shared.sync_thread_capture("c", "Title", rows, "work") == (False, True)
        assert http.call_count == 3
        cli.assert_not_called()


def test_cli_successful_append_without_persisted_tail_stays_pending():
    rows = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "second"}]
    detail = subprocess.CompletedProcess([], 0, json.dumps({"id": "c", "messages": rows[:1]}), "")
    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", return_value=None),
        patch.object(nmem_shared, "run_nmem_command", side_effect=[detail, subprocess.CompletedProcess([], 0), detail]),
    ):
        assert nmem_shared.sync_thread_capture("c", "Title", rows) == (False, True)


@pytest.mark.parametrize("invocation,expected", [(0, True), (1, False), (None, False)])
def test_bridge_notice_uses_actual_session_without_network(monkeypatch, capsys, invocation, expected):
    hook = import_module_from_path("bridge_notice", str(HOOKS_DIR / "bridge-notice.py"))
    monkeypatch.setattr(
        nmem_shared, "read_hook_input", lambda: {"conversationId": "actual-conversation", "invocationNum": invocation}
    )
    notice = MagicMock(return_value={"hookSpecificOutput": {"additionalContext": "Fresh local notice"}})
    monkeypatch.setattr(hook, "response", notice)
    monkeypatch.setattr(
        nmem_shared, "run_nmem_command", MagicMock(side_effect=AssertionError("No CLI or network in notice hook"))
    )
    hook.main()
    result = json.loads(capsys.readouterr().out)
    if expected:
        assert result == {"injectSteps": [{"systemMessage": "Fresh local notice"}]}
        notice.assert_called_once_with(
            {"hook_event_name": "UserPromptSubmit", "session_id": "actual-conversation"}, host="antigravity"
        )
    else:
        assert result == {}
        notice.assert_not_called()


def test_bridge_start_correlates_actual_conversation(monkeypatch, capsys):
    hook = import_module_from_path("bridge_start", str(HOOKS_DIR / "bridge-start.py"))
    monkeypatch.setenv("NMEM_BRIDGE_INBOX_ON_START", "1")
    monkeypatch.setenv("NMEM_AGENT_CONTEXT", "approved")
    monkeypatch.setattr(nmem_shared, "read_hook_input", lambda: {"conversationId": "actual-conversation"})
    hook.main()
    hint = json.loads(capsys.readouterr().out)["injectSteps"][0]["systemMessage"]
    assert '"session_id": "actual-conversation"' in hint
    assert "notices subscribe" in hint
    assert "not native sessions" in hint


def test_long_thread_incremental_append_uses_verified_checkpoint_and_idempotency_key():
    stored = [
        {"id": f"msgocc_{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"} for i in range(1351)
    ]
    delta = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"msg-{i}"} for i in range(1351, 1359)]
    local_initial = [{"role": m["role"], "content": m["content"]} for m in stored]
    local_grown = local_initial + delta

    def fake_http(endpoint, method="GET", payload=None, **kwargs):
        if method == "GET":
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(endpoint).query)
            offset = int(qs.get("offset", ["0"])[0])
            limit = int(qs.get("limit", ["500"])[0])
            assert limit <= nmem_shared.THREAD_SYNC_TAIL_WINDOW
            return {
                "id": "long-conv",
                "space_id": "work",
                "total_messages": len(stored),
                "messages": stored[offset : offset + limit],
            }
        if endpoint == "/threads/long-conv/append":
            assert payload["expected_message_count"] == len(stored)
            start = len(stored)
            for idx, msg in enumerate(payload["messages"]):
                stored.append({"id": f"msgocc_{start + idx}", **msg})
            return {"success": True, "messages_added": len(payload["messages"]), "total_messages": len(stored)}
        raise AssertionError(f"Unexpected call: {method} {endpoint}")

    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", side_effect=fake_http) as mock_http,
        patch.object(nmem_shared, "run_nmem_command") as mock_cli,
    ):
        # 1. Unknown history verifies the full prefix across bounded <=32 pages and saves a checkpoint.
        assert nmem_shared.sync_thread_capture("long-conv", "Long Thread", local_initial, "work") == (True, True)
        mock_cli.assert_not_called()

        # 2. Subsequent turn with 8 new messages uses the verified checkpoint in O(1) without rescanning 1,351 rows.
        mock_http.reset_mock()
        assert nmem_shared.sync_thread_capture("long-conv", "Long Thread", local_grown, "work") == (True, True)
        mock_cli.assert_not_called()

    endpoints = [c.args[0] for c in mock_http.call_args_list]
    assert endpoints == [
        "/threads/long-conv?space_id=work&limit=0",
        "/threads/long-conv?space_id=work&offset=1319&limit=32",
        "/threads/long-conv/append",
        "/threads/long-conv?space_id=work&offset=1327&limit=32",
    ]
    assert all("limit=1359" not in ep for ep in endpoints)
    append_call = mock_http.call_args_list[2]
    assert append_call.kwargs["timeout"] >= nmem_shared.THREAD_SYNC_WRITE_TIMEOUT
    assert mock_http.call_args_list[0].kwargs["timeout"] >= nmem_shared.THREAD_SYNC_READ_TIMEOUT
    assert mock_http.call_args_list[3].kwargs["timeout"] >= nmem_shared.THREAD_SYNC_READ_TIMEOUT
    payload = append_call.kwargs["payload"]
    assert payload["messages"] == delta
    assert payload["expected_message_count"] == 1351
    assert payload["space_id"] == "work"
    assert payload["idempotency_key"] == nmem_shared.build_append_idempotency_key("long-conv", 1351, delta)


def test_long_thread_divergent_history_fails_without_full_fetch():
    stored = [{"role": "user", "content": f"remote-{i}"} for i in range(1351)]
    local = [{"role": "user", "content": f"local-{i}"} for i in range(1359)]

    responses = [
        {"id": "div-conv", "space_id": "work", "total_messages": 1351, "messages": []},
        {"id": "div-conv", "space_id": "work", "total_messages": 1351, "messages": stored[0:32]},
    ]
    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", side_effect=responses) as mock_http,
    ):
        assert nmem_shared.sync_thread_capture("div-conv", "Divergent", local, "work") == (False, True)
        assert nmem_shared.get_last_sync_failure_reason() == "divergent_history"

    endpoints = [c.args[0] for c in mock_http.call_args_list]
    assert endpoints == [
        "/threads/div-conv?space_id=work&limit=0",
        "/threads/div-conv?space_id=work&limit=32",
    ]


@pytest.mark.parametrize("single_edit", [False, True], ids=["rewritten-suffix", "unchanged-tail"])
def test_long_thread_mid_history_divergence_uses_first_differing_row(single_edit):
    stored = [{"role": "user", "content": f"m-{i}"} for i in range(150)]
    local = [*stored[:60], *[{"role": "user", "content": f"edited-{i}"} for i in range(60, 150)]]
    if single_edit:
        local[61:] = stored[61:]

    def fake_http(endpoint, method="GET", payload=None, **kwargs):
        if method == "GET":
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(endpoint).query)
            offset = int(qs.get("offset", ["0"])[0])
            limit = int(qs.get("limit", ["500"])[0])
            assert limit <= nmem_shared.THREAD_SYNC_TAIL_WINDOW
            return {
                "id": "mid-conv",
                "space_id": "work",
                "total_messages": len(stored),
                "messages": stored[offset : offset + limit],
            }
        if endpoint == "/threads/mid-conv/reconcile-tail":
            assert payload["matched_count"] == 60
            assert payload["messages"] == local[60:]
            stored[payload["matched_count"] :] = payload["messages"]
            return {"success": True}
        raise AssertionError(f"Unexpected call: {method} {endpoint}")

    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", side_effect=fake_http) as mock_http,
    ):
        assert nmem_shared.sync_thread_capture("mid-conv", "Mid Edit", local, "work") == (True, True)
    assert any(c.args[0] == "/threads/mid-conv/reconcile-tail" for c in mock_http.call_args_list)
    assert stored == local


def test_verified_checkpoint_invalidates_on_local_edit_remote_tail_id_change_and_unapplied_reconcile():
    stored = [{"id": f"v1_{i}", "role": "user", "content": f"m-{i}"} for i in range(150)]
    base_local = [{"role": "user", "content": f"m-{i}"} for i in range(150)]
    apply_reconcile = {"enabled": True}

    def fake_http(endpoint, method="GET", payload=None, **kwargs):
        if method == "GET":
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(endpoint).query)
            offset = int(qs.get("offset", ["0"])[0])
            limit = int(qs.get("limit", ["500"])[0])
            assert limit <= nmem_shared.THREAD_SYNC_TAIL_WINDOW
            return {
                "id": "cp-conv",
                "space_id": "work",
                "total_messages": len(stored),
                "messages": stored[offset : offset + limit],
            }
        if endpoint == "/threads/cp-conv/reconcile-tail":
            if apply_reconcile["enabled"]:
                start = payload["matched_count"]
                stored[start:] = [{"id": f"v2_{start + i}", **msg} for i, msg in enumerate(payload["messages"])]
            return {"success": True}
        raise AssertionError(f"Unexpected call: {method} {endpoint}")

    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", side_effect=fake_http) as mock_http,
    ):
        # Establish verified checkpoint for 150 rows.
        assert nmem_shared.sync_thread_capture("cp-conv", "CP", base_local, "work") == (True, True)

        # 1. Local single interior edit with unchanged tail invalidates local prefix_hash and reconciles row 60.
        edited_local = [*base_local[:60], {"role": "user", "content": "local-edit-60"}, *base_local[61:]]
        assert nmem_shared.sync_thread_capture("cp-conv", "CP", edited_local, "work") == (True, True)
        assert [{"role": m["role"], "content": m["content"]} for m in stored] == edited_local

        # 2. Out-of-band server reconcile-tail changes row 60 and replaces tail message IDs while keeping tail text.
        stored[60]["content"] = "remote-edit-60"
        for idx in range(60, 150):
            stored[idx]["id"] = f"oob_{idx}"
        mock_http.reset_mock()
        assert nmem_shared.sync_thread_capture("cp-conv", "CP", edited_local, "work") == (True, True)
        assert any(c.args[0] == "/threads/cp-conv/reconcile-tail" for c in mock_http.call_args_list)
        assert [{"role": m["role"], "content": m["content"]} for m in stored] == edited_local

        # 3. Unapplied reconcile-tail on an unchanged-tail edit fails write-boundary readback at offset 60.
        apply_reconcile["enabled"] = False
        unapplied_local = [*edited_local[:60], {"role": "user", "content": "unapplied-60"}, *edited_local[61:]]
        assert nmem_shared.sync_thread_capture("cp-conv", "CP", unapplied_local, "work") == (False, True)
        assert nmem_shared.get_last_sync_failure_reason() == "readback_failed"


def test_cli_long_thread_unknown_history_checkpoint_and_interior_edit():
    stored = [{"role": "user", "content": f"m-{i}"} for i in range(95)]
    delta_1 = [{"role": "user", "content": f"m-{i}"} for i in range(95, 100)]
    delta_2 = [{"role": "user", "content": f"m-{i}"} for i in range(100, 105)]
    local_100 = stored + delta_1
    local_105 = local_100 + delta_2

    def fake_cli(args, **kwargs):
        if args[:3] == ["--json", "t", "show"]:
            limit = int(args[args.index("--limit") + 1]) if "--limit" in args else len(stored)
            offset = int(args[args.index("--offset") + 1]) if "--offset" in args else 0
            assert limit <= nmem_shared.THREAD_SYNC_TAIL_WINDOW
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "id": "cli-long",
                        "created_at": "2026-11-28T00:00:00Z",
                        "total_messages": len(stored),
                        "messages": stored[offset : offset + limit],
                    }
                ),
                "",
            )
        if args[:3] == ["t", "append", "cli-long"]:
            assert "--idempotency-key" in args
            appended = json.loads(args[args.index("-m") + 1])
            stored.extend(appended)
            return subprocess.CompletedProcess(args, 0, '{"success": true}', "")
        raise AssertionError(f"Unexpected CLI call: {args}")

    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", return_value=None),
        patch.object(nmem_shared, "run_nmem_command", side_effect=fake_cli) as mock_cli,
    ):
        # 1. Unknown history scans all 95 rows in bounded <=32 pages, appends 5 with idempotency key, and checkpoints.
        assert nmem_shared.sync_thread_capture("cli-long", "Long CLI", local_100) == (True, True)
        assert stored == local_100
        calls_1 = [c.args[0] for c in mock_cli.call_args_list]
        assert calls_1[0] == ["--json", "t", "show", "cli-long", "--limit", "0"]
        assert calls_1[1] == ["--json", "t", "show", "cli-long", "--limit", "32"]
        assert calls_1[2] == ["--json", "t", "show", "cli-long", "--limit", "32", "--offset", "32"]
        assert calls_1[3] == ["--json", "t", "show", "cli-long", "--limit", "31", "--offset", "64"]
        assert calls_1[4][:3] == ["t", "append", "cli-long"]
        key_idx = calls_1[4].index("--idempotency-key")
        assert calls_1[4][key_idx + 1] == nmem_shared.build_append_idempotency_key("cli-long", 95, delta_1)
        assert calls_1[5] == ["--json", "t", "show", "cli-long", "--limit", "32", "--offset", "68"]

        # 2. Subsequent append uses verified checkpoint in O(1).
        mock_cli.reset_mock()
        assert nmem_shared.sync_thread_capture("cli-long", "Long CLI", local_105) == (True, True)
        assert stored == local_105
        calls_2 = [c.args[0] for c in mock_cli.call_args_list]
        assert calls_2[0] == ["--json", "t", "show", "cli-long", "--limit", "0"]
        assert calls_2[1] == ["--json", "t", "show", "cli-long", "--limit", "32", "--offset", "68"]
        assert calls_2[2][:3] == ["t", "append", "cli-long"]
        assert calls_2[3] == ["--json", "t", "show", "cli-long", "--limit", "32", "--offset", "73"]

        # 3. Single interior edit with unchanged tail must fail closed on CLI (both with and without checkpoint).
        edited_interior = [*local_105[:40], {"role": "user", "content": "edited-40"}, *local_105[41:]]
        mock_cli.reset_mock()
        assert nmem_shared.sync_thread_capture("cli-long", "Long CLI", edited_interior) == (False, True)
        assert nmem_shared.get_last_sync_failure_reason() == "divergent_history"
        assert all(c.args[0][:2] != ["t", "append"] for c in mock_cli.call_args_list)

        # Also verify unknown history (no checkpoint) with interior edit and unchanged tail fails closed on CLI.
        nmem_shared.get_thread_sync_checkpoints_path().unlink(missing_ok=True)
        mock_cli.reset_mock()
        assert nmem_shared.sync_thread_capture("cli-long", "Long CLI", edited_interior) == (False, True)
        assert nmem_shared.get_last_sync_failure_reason() == "divergent_history"
        assert all(c.args[0][:2] != ["t", "append"] for c in mock_cli.call_args_list)


def test_unsynced_retry_exponential_backoff_and_dead_letter_isolation(tmp_path, monkeypatch):
    queue_path = tmp_path / "unsynced.json"
    dlq_path = tmp_path / "dead_letter.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: queue_path)
    monkeypatch.setattr(nmem_shared, "is_backend_unreachable", lambda: False)

    nmem_shared.save_unsynced_session("conv-backoff", [{"role": "user", "content": "hi"}], "Title", "default", "host")
    base_now = 1_700_000_000.0
    expected_delays = [30.0, 60.0, 120.0, 240.0]

    with patch.object(nmem_shared, "sync_thread_capture", return_value=(False, True)) as mock_sync:
        current = base_now
        for attempt, expected_delay in enumerate(expected_delays, start=1):
            nmem_shared.retry_unsynced_sessions(now=current)
            assert mock_sync.call_count == attempt
            entry = json.loads(queue_path.read_text(encoding="utf-8"))["conv-backoff"]
            assert entry["retry_count"] == attempt
            assert entry["next_retry_at"] == current + expected_delay
            assert not dlq_path.exists()

            # Retrying before next_retry_at must skip without calling sync_thread_capture.
            nmem_shared.retry_unsynced_sessions(now=current + expected_delay - 1.0)
            assert mock_sync.call_count == attempt
            current += expected_delay

        # Final failure hits UNSYNCED_MAX_RETRIES -> moves to dead_letter.json and clears unsynced.json.
        nmem_shared.retry_unsynced_sessions(now=current)
        assert mock_sync.call_count == nmem_shared.UNSYNCED_MAX_RETRIES

    assert not queue_path.exists()
    dlq = json.loads(dlq_path.read_text(encoding="utf-8"))
    assert "conv-backoff" in dlq
    assert dlq["conv-backoff"]["retry_count"] == nmem_shared.UNSYNCED_MAX_RETRIES
    assert dlq["conv-backoff"]["messages"] == [{"role": "user", "content": "hi"}]
    assert dlq["conv-backoff"]["dead_letter_reason"] == "sync_failed"
    assert dlq["conv-backoff"]["dead_lettered_at"] == current


def test_unsynced_divergent_history_immediately_isolates_to_dead_letter(tmp_path, monkeypatch):
    queue_path = tmp_path / "unsynced.json"
    dlq_path = tmp_path / "dead_letter.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: queue_path)
    monkeypatch.setattr(nmem_shared, "is_backend_unreachable", lambda: False)

    nmem_shared.save_unsynced_session("conv-div", [{"role": "user", "content": "local"}], "Title", "default", "host")
    with patch.object(
        nmem_shared,
        "http_request",
        return_value={"id": "conv-div", "space_id": "default", "messages": [{"role": "user", "content": "remote"}]},
    ):
        nmem_shared.retry_unsynced_sessions(now=1_700_000_000.0)

    assert not queue_path.exists()
    dlq = json.loads(dlq_path.read_text(encoding="utf-8"))
    assert dlq["conv-div"]["dead_letter_reason"] == "divergent_history"
    assert dlq["conv-div"]["retry_count"] == 1


def test_dead_letter_write_failure_preserves_unsynced_entry(tmp_path, monkeypatch):
    queue_path = tmp_path / "unsynced.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: queue_path)
    monkeypatch.setattr(nmem_shared, "is_backend_unreachable", lambda: False)

    nmem_shared.save_unsynced_session("conv-keep", [{"role": "user", "content": "local"}], "Title", "default", "host")
    with (
        patch.object(
            nmem_shared,
            "http_request",
            return_value={"id": "conv-keep", "space_id": "default", "messages": [{"role": "user", "content": "other"}]},
        ),
        patch.object(nmem_shared, "_append_dead_letter_entries", return_value=False),
    ):
        nmem_shared.retry_unsynced_sessions(now=1_700_000_000.0)

    assert queue_path.exists()
    preserved = json.loads(queue_path.read_text(encoding="utf-8"))["conv-keep"]
    assert preserved["messages"] == [{"role": "user", "content": "local"}]
    assert preserved["next_retry_at"] == 1_700_000_000.0 + nmem_shared.UNSYNCED_MAX_BACKOFF_SECONDS


def test_save_unsynced_preserves_backoff_for_same_snapshot_and_resets_on_new_messages(tmp_path, monkeypatch):
    queue_path = tmp_path / "unsynced.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: queue_path)
    monkeypatch.setattr(nmem_shared, "is_backend_unreachable", lambda: False)

    msgs = [{"role": "user", "content": "turn 1"}]
    nmem_shared.save_unsynced_session("conv-fp", msgs, "Title", "default", "host")
    with patch.object(nmem_shared, "sync_thread_capture", return_value=(False, True)):
        nmem_shared.retry_unsynced_sessions(now=1_700_000_000.0)

    # Re-saving identical snapshot keeps retry_count and next_retry_at intact.
    nmem_shared.save_unsynced_session("conv-fp", msgs, "Title", "default", "host")
    same_entry = json.loads(queue_path.read_text(encoding="utf-8"))["conv-fp"]
    assert same_entry["retry_count"] == 1
    assert same_entry["next_retry_at"] == 1_700_000_030.0

    # Saving updated snapshot with new messages resets retry backoff.
    nmem_shared.save_unsynced_session(
        "conv-fp", [*msgs, {"role": "assistant", "content": "turn 2"}], "Title", "default", "host"
    )
    fresh_entry = json.loads(queue_path.read_text(encoding="utf-8"))["conv-fp"]
    assert "retry_count" not in fresh_entry
    assert "next_retry_at" not in fresh_entry


@patch("nmem_shared.retry_unsynced_sessions_async")
@patch("nmem_shared.should_emit_unsynced_warning", return_value=False)
@patch("nmem_shared.get_unsynced_sessions")
@patch("nmem_shared.read_hook_input")
@patch("nmem_shared.emit")
def test_post_invocation_skips_background_retry_when_all_sessions_in_backoff(
    mock_emit, mock_input, mock_unsynced, mock_should_emit, mock_retry
):
    mock_input.return_value = {"invocationNum": 2, "conversationId": "conv-backoff-ui"}
    mock_unsynced.return_value = {
        "conv-1": {
            "title": "Test",
            "messages": [{"role": "user", "content": "hi"}],
            "retry_count": 2,
            "next_retry_at": time.time() + 300.0,
        }
    }

    with patch.object(sys, "argv", ["post-invocation.py"]):
        post_invocation.main()

    mock_retry.assert_not_called()
    mock_emit.assert_called_once_with({})


@patch("nmem_shared.run_nmem_command")
def test_nmem_status_reports_dead_letter_sessions(mock_run_cmd, tmp_path, monkeypatch, capsys):
    queue_path = tmp_path / "unsynced.json"
    dlq_path = tmp_path / "dead_letter.json"
    monkeypatch.setattr(nmem_shared, "get_unsynced_queue_path", lambda: queue_path)
    dlq_path.write_text(
        json.dumps({"conv-dead": {"conversation_id": "conv-dead", "dead_letter_reason": "divergent_history"}}),
        encoding="utf-8",
    )
    mock_run_cmd.return_value = MagicMock(returncode=0, stdout="Connected", stderr="")
    with (
        patch("nmem_shared.resolve_space", return_value="default"),
        patch("nmem_shared.is_backend_unreachable", return_value=False),
        patch(
            "nmem_shared.http_request",
            return_value={"agent_running": True, "running": [], "queued": [], "scheduled_once": [], "recurring": []},
        ),
        patch.object(sys, "argv", ["nmem_status.py", "--conv-id", "conv-dead"]),
    ):
        nmem_status.main()
    output = capsys.readouterr().out
    assert "dead_letter.json" in output
    assert "conv-dead" in output
    assert "divergent_history" in output


def test_sync_mcp_config_defers_to_host_global_config(tmp_path, monkeypatch):
    host_cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    host_cfg.parent.mkdir(parents=True)
    host_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "nowledge-mem": {
                        "url": "http://127.0.0.1:14242/mcp",
                        "disabledTools": ["memory_delete"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    plugin_cfg = tmp_path / "plugin" / "mcp_config.json"
    plugin_cfg.parent.mkdir(parents=True)

    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd=None: ("https://mem.example.com", "key"))
    monkeypatch.setattr(nmem_shared, "_mcp_space_id", lambda cwd, cfg: "personal")

    # 1. Missing plugin mcp_config.json must not be recreated when host already defines nowledge-mem.
    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=tmp_path) is False
    assert not plugin_cfg.exists()

    # 2. Existing plugin mcp_config.json removes only nowledge-mem and preserves other servers.
    plugin_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "nowledge-mem": {"url": "http://127.0.0.1:14242/mcp"},
                    "other-server": {"command": "other"},
                }
            }
        ),
        encoding="utf-8",
    )
    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=tmp_path) is True
    assert json.loads(plugin_cfg.read_text(encoding="utf-8")) == {"mcpServers": {"other-server": {"command": "other"}}}
    # Second call is a no-op.
    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=tmp_path) is False

    # 3. Managed mode explicitly overrides deferral and syncs plugin mcp_config.json.
    monkeypatch.setenv("NMEM_MCP_SYNC_MODE", "managed")
    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=tmp_path) is True
    managed_data = json.loads(plugin_cfg.read_text(encoding="utf-8"))
    assert managed_data["mcpServers"]["nowledge-mem"]["url"] == "https://mem.example.com/mcp"
    assert managed_data["mcpServers"]["other-server"] == {"command": "other"}


def test_sync_mcp_config_defers_to_workspace_agents_config(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace_mcp = workspace / ".agents" / "mcp_config.json"
    workspace_mcp.parent.mkdir(parents=True)
    workspace_mcp.write_text(
        json.dumps({"mcpServers": {"nowledge-mem": {"url": "http://127.0.0.1:14242/mcp"}}}),
        encoding="utf-8",
    )
    plugin_cfg = tmp_path / "plugin_mcp_config.json"
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd=None: ("http://127.0.0.1:14242", None))
    monkeypatch.setattr(nmem_shared, "_mcp_space_id", lambda cwd, cfg: None)

    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=workspace) is False
    assert not plugin_cfg.exists()


def test_sync_mcp_config_mode_off_and_ignore_host_config(tmp_path, monkeypatch):
    host_cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    host_cfg.parent.mkdir(parents=True)
    host_cfg.write_text(
        json.dumps({"mcpServers": {"nowledge-mem": {"url": "http://127.0.0.1:14242/mcp"}}}),
        encoding="utf-8",
    )
    plugin_cfg = tmp_path / "plugin_mcp_config.json"
    plugin_cfg.write_text('{"mcpServers":{"nowledge-mem":{"url":"http://custom/mcp"}}}', encoding="utf-8")
    before = plugin_cfg.read_text(encoding="utf-8")

    # Mode 'off' via plugin storage config leaves plugin_cfg completely untouched.
    storage_cfg = tmp_path / ".nowledge-mem" / "plugins" / "antigravity" / "config.json"
    storage_cfg.parent.mkdir(parents=True)
    storage_cfg.write_text(json.dumps({"mcpSyncMode": "off"}), encoding="utf-8")
    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=tmp_path) is False
    assert plugin_cfg.read_text(encoding="utf-8") == before

    # NMEM_IGNORE_HOST_CONFIG ignores host global ~/.gemini/config/mcp_config.json and storage config.
    monkeypatch.setenv("NMEM_IGNORE_HOST_CONFIG", "1")
    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd=None: ("http://127.0.0.1:14242", None))
    monkeypatch.setattr(nmem_shared, "_mcp_space_id", lambda cwd, cfg: None)
    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=tmp_path) is True
    assert json.loads(plugin_cfg.read_text(encoding="utf-8"))["mcpServers"]["nowledge-mem"]["url"] == (
        "http://127.0.0.1:14242/mcp"
    )


def test_sync_mcp_config_ignores_malformed_host_config_and_self_path(tmp_path, monkeypatch):
    host_cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    host_cfg.parent.mkdir(parents=True)
    host_cfg.write_text("{not valid json", encoding="utf-8")
    plugin_cfg = tmp_path / "plugin_mcp_config.json"

    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd=None: ("http://127.0.0.1:14242", None))
    monkeypatch.setattr(nmem_shared, "_mcp_space_id", lambda cwd, cfg: None)

    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=tmp_path) is True
    assert json.loads(plugin_cfg.read_text(encoding="utf-8"))["mcpServers"]["nowledge-mem"]["url"] == (
        "http://127.0.0.1:14242/mcp"
    )

    # Syncing the host config file path directly must never treat itself as an external host override.
    host_cfg.write_text(
        json.dumps({"mcpServers": {"nowledge-mem": {"url": "http://old/mcp"}}}),
        encoding="utf-8",
    )
    assert nmem_shared.sync_mcp_config_file(str(host_cfg), cwd=tmp_path) is True
    assert json.loads(host_cfg.read_text(encoding="utf-8"))["mcpServers"]["nowledge-mem"]["url"] == (
        "http://127.0.0.1:14242/mcp"
    )


def test_stop_hook_timeout_covers_thread_sync_budget():
    hooks_cfg = json.loads((HOOKS_DIR.parent / "hooks.json").read_text(encoding="utf-8"))
    stop_timeout = hooks_cfg["nowledge-mem-hooks"]["Stop"][0]["timeout"]
    worst_case_sync = (
        nmem_shared.THREAD_SYNC_READ_TIMEOUT
        + nmem_shared.THREAD_SYNC_MAX_WRITE_TIMEOUT
        + nmem_shared.THREAD_SYNC_READ_TIMEOUT
    )
    assert stop_timeout > worst_case_sync


def test_sync_mcp_config_defers_to_jsonc_bom_and_disabled_host_config(tmp_path, monkeypatch):
    host_cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    host_cfg.parent.mkdir(parents=True)
    host_cfg.write_bytes(
        b"\xef\xbb\xbf{\n"
        b"  // Host config written by `agy mcp add` + user JSONC comments\n"
        b'  "mcpServers": {\n'
        b"    /* Keep serverUrl http:// intact inside quoted string */\n"
        b'    "nowledge-mem": {\n'
        b'      "disabled": true,\n'
        b'      "serverUrl": "http://127.0.0.1:14242/mcp",\n'
        b'      "disabledTools": ["memory_delete",],\n'
        b"    },\n"
        b"  },\n"
        b"}\n"
    )
    plugin_cfg = tmp_path / "plugin" / "mcp_config.json"
    plugin_cfg.parent.mkdir(parents=True)
    plugin_cfg.write_bytes(b'\xef\xbb\xbf{"mcpServers": {"nowledge-mem": {"url": "http://127.0.0.1:14242/mcp"}}}\n')

    monkeypatch.setattr(nmem_shared, "get_effective_config", lambda cwd=None: ("http://127.0.0.1:14242", None))
    monkeypatch.setattr(nmem_shared, "_mcp_space_id", lambda cwd, cfg: None)

    assert nmem_shared.sync_mcp_config_file(str(plugin_cfg), cwd=tmp_path) is True
    assert json.loads(plugin_cfg.read_text(encoding="utf-8")) == {"mcpServers": {}}
