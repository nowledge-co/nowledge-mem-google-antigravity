import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

# Add hooks directory to path to import nmem_shared and others
HOOKS_DIR = Path(__file__).parent.parent / "hooks"
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
            nmem_shared._SPACES_CACHE = {"timestamp": 0.0, "spaces": None}
            spaces = nmem_shared.get_existing_spaces()
            assert spaces == [{"id": "new_space"}]
            assert not old_cache1.exists()
            assert not old_cache2.exists()
            assert new_cache.exists()


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
    nmem_shared.retry_unsynced_sessions_async()
    mock_popen.assert_called_once()


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


@patch("nmem_gate.read_hook_input")
@patch("sys.stdout.write")
@patch("sys.stdout.flush")
def test_nmem_gate_auto_allow_nmem_status(mock_flush, mock_write, mock_input):
    mock_input.return_value = {
        "toolCall": {"name": "run_command", "args": {"CommandLine": "python3 hooks/nmem_status.py"}},
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
def test_nmem_gate_read_tool(mock_flush, mock_write, mock_input):
    mock_input.return_value = {
        "toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "nowledge-mem", "ToolName": "memory_search"}},
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
                "Arguments": {"horizon": "today"},
            },
        },
        "conversationId": "conv-test-catchup",
    }
    with (
        patch("nmem_shared.should_allow_catchup", return_value=(True, "")),
        patch.object(sys, "argv", ["nmem-gate.py"]),
    ):
        nmem_gate.main()
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
        assert payload["decision"] == "allow"
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

    with patch("nmem_shared.resolve_space", return_value="default"), patch.object(sys, "argv", ["nmem_status.py"]):
        nmem_status.main()
        assert mock_write.call_count >= 1
        written = "".join([c[0][0] for c in mock_write.call_args_list])
        assert "🟢 Connected" in written
        assert "| **Active Space (Workspace)** | `default` |" in written


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


def load_tests(loader, tests, pattern):
    import inspect
    import unittest

    suite = unittest.TestSuite()
    current_module = sys.modules[__name__]
    for name, obj in inspect.getmembers(current_module):
        if name.startswith("test_") and inspect.isfunction(obj):
            suite.addTest(unittest.FunctionTestCase(obj))
    return suite
