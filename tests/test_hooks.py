import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

# Add hooks directory to path to import nmem_shared and others
HOOKS_DIR = Path(__file__).parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import importlib.util

import nmem_shared


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

class TestNmemShared(unittest.TestCase):

    @patch("os.access", return_value=True)
    @patch("pathlib.Path.is_file", return_value=True)
    @patch("pathlib.Path.resolve")
    @patch("shutil.which")
    def test_nmem_command_resolution(self, mock_which, mock_resolve, mock_is_file, mock_access):
        # Case 1: nmem exists
        mock_which.side_effect = lambda x: "/usr/bin/nmem" if x == "nmem" else None
        mock_resolve.side_effect = lambda: Path("/usr/bin/nmem")
        self.assertEqual(nmem_shared._nmem_command(), "/usr/bin/nmem")
        mock_resolve.side_effect = lambda: Path("/usr/bin/nmem")
        self.assertEqual(nmem_shared._nmem_command(), "/usr/bin/nmem")

        # Case 2: nmem.cmd exists
        mock_which.side_effect = lambda x: "/usr/bin/nmem.cmd" if x == "nmem.cmd" else None
        mock_resolve.side_effect = lambda: Path("/usr/bin/nmem.cmd")
        self.assertEqual(nmem_shared._nmem_command(), "/usr/bin/nmem.cmd")

    def test_cmd_exe_path_conversion(self):
        # WSL mount to Windows path
        self.assertEqual(nmem_shared._cmd_exe_path("/mnt/c/Users/test"), "C:\\Users\\test")

        # Already Windows path
        self.assertEqual(nmem_shared._cmd_exe_path("C:\\Users\\test"), "C:\\Users\\test")
        self.assertEqual(nmem_shared._cmd_exe_path("C:/Users/test"), "C:\\Users\\test")

        # Fallback to name
        self.assertEqual(nmem_shared._cmd_exe_path("nmem.cmd"), "nmem.cmd")

    @patch("nmem_shared._cmd_exe_path")
    def test_build_nmem_command(self, mock_cmd_path):
        mock_cmd_path.side_effect = lambda x: "C:\\bin\\nmem.cmd" if "nmem.cmd" in x else x

        # Non-Windows cmd path
        cmd = nmem_shared._build_nmem_command("/usr/bin/nmem", "t", "show")
        self.assertEqual(cmd, ["/usr/bin/nmem", "t", "show"])

        # Windows cmd path
        cmd = nmem_shared._build_nmem_command("C:\\bin\\nmem.cmd", "t", "show")
        self.assertEqual(cmd[0], "cmd.exe")
        self.assertEqual(cmd[1], "/s")
        self.assertEqual(cmd[2], "/c")

    @patch("nmem_shared._nmem_command")
    @patch("subprocess.run")
    def test_run_nmem_command(self, mock_run, mock_nmem_command):
        mock_nmem_command.return_value = "/usr/bin/nmem"
        mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")

        res = nmem_shared.run_nmem_command(["t", "show"])
        self.assertEqual(res.stdout, "success")
        mock_run.assert_called_once()

    @patch("uuid.getnode")
    @patch("socket.gethostname")
    def test_host_agent_fingerprint(self, mock_gethostname, mock_getnode):
        mock_getnode.return_value = 123456789
        mock_gethostname.return_value = "my-host"

        fp = nmem_shared.get_host_agent_fingerprint()
        self.assertTrue(fp.startswith("antigravity-"))

    @patch("nmem_shared.get_effective_config")
    def test_sync_mcp_config_file(self, mock_get_effective_config):
        mock_get_effective_config.return_value = ("https://mem.example.com", "nmem_sec_123")
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write('{"mcpServers":{"nowledge-mem":{"serverUrl":"http://127.0.0.1:14242/mcp/"}}}')
            tf_path = tf.name
        try:
            updated = nmem_shared.sync_mcp_config_file(tf_path)
            self.assertTrue(updated)
            data = json.loads(Path(tf_path).read_text(encoding="utf-8"))
            self.assertEqual(data["mcpServers"]["nowledge-mem"]["serverUrl"], "https://mem.example.com/mcp/")
            self.assertEqual(data["mcpServers"]["nowledge-mem"]["headers"]["Authorization"], "Bearer nmem_sec_123")
            self.assertEqual(data["mcpServers"]["nowledge-mem"]["headers"]["X-NMEM-API-Key"], "nmem_sec_123")
            self.assertNotIn("X-MEM-API-Key", data["mcpServers"]["nowledge-mem"]["headers"])

            # Second call should return False (already up to date)
            updated_again = nmem_shared.sync_mcp_config_file(tf_path)
            self.assertFalse(updated_again)
        finally:
            if os.path.exists(tf_path):
                os.unlink(tf_path)

    @patch("threading.Thread")
    @patch("nmem_shared._nmem_command")
    @patch("nmem_shared.run_nmem_command")
    def test_sync_host_skills_async(self, mock_run_cmd, mock_nmem_cmd, mock_thread):
        mock_nmem_cmd.return_value = "/usr/bin/nmem"

        # Make the mocked Thread's start call target immediately
        def mock_start():
            target = mock_thread.call_args[1].get('target')
            if target:
                target()
        mock_thread.return_value.start.side_effect = mock_start

        nmem_shared.sync_host_skills_async()
        self.assertTrue(mock_run_cmd.called)
        calls = [c[0][0] for c in mock_run_cmd.call_args_list]
        self.assertIn(["skills", "connect", "antigravity"], calls)
        self.assertIn(["skills", "sync"], calls)

    @patch("nmem_shared.FileLock")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.write_text")
    @patch("pathlib.Path.read_text")
    def test_save_unsynced_session(self, mock_read, mock_write, mock_mkdir, mock_exists, mock_lock):
        mock_exists.return_value = False
        nmem_shared.save_unsynced_session("conv-1", [{"role": "user", "content": "hi"}], "title", "space", "host")
        mock_mkdir.assert_called_once()
        mock_write.assert_called_once()

    @patch.dict(os.environ, {"NMEM_API_URL": "https://remote.example.com", "NMEM_API_KEY": "secret_key"})
    def test_get_effective_config_env(self):
        url, key = nmem_shared.get_effective_config()
        self.assertEqual(url, "https://remote.example.com")
        self.assertEqual(key, "secret_key")

    @patch("urllib.request.urlopen")
    def test_http_request_success(self, mock_urlopen):
        nmem_shared.reset_backend_unreachable()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = nmem_shared.http_request("/health")
        self.assertEqual(res, {"status": "ok"})
        self.assertFalse(nmem_shared.is_backend_unreachable())

    @patch("urllib.request.urlopen")
    def test_circuit_breaker_unreachable_fast_fail(self, mock_urlopen):
        nmem_shared.reset_backend_unreachable()
        mock_urlopen.side_effect = TimeoutError("Connection timed out")

        # 1st call fails and marks backend unreachable
        res1 = nmem_shared.http_request("/health")
        self.assertIsNone(res1)
        self.assertTrue(nmem_shared.is_backend_unreachable())

        # 2nd call fails fast without invoking urlopen again
        mock_urlopen.reset_mock()
        res2 = nmem_shared.http_request("/health")
        self.assertIsNone(res2)
        mock_urlopen.assert_not_called()

        # Clean up
        nmem_shared.reset_backend_unreachable()

    @patch("nmem_shared._is_pid_alive", return_value=False)
    def test_file_lock_stale_pid_recovery(self, mock_pid_alive):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "test.lock"
            # Write a fake dead PID (999999) to lock file
            lock_path.write_text("999999", encoding="utf-8")

            # Acquiring FileLock should detect dead PID owner, unlink stale lock, and acquire cleanly
            with nmem_shared.FileLock(lock_path) as lock:
                self.assertTrue(lock.acquired)
                self.assertTrue(lock_path.exists())
                pid_in_file = lock_path.read_text(encoding="utf-8").strip()
                self.assertEqual(pid_in_file, str(os.getpid()))

    @patch.dict(os.environ, {"NMEM_SPACE": "custom-explicit-space"}, clear=False)
    def test_resolve_space_explicit_env(self):
        # Explicit env override returns explicitly requested space even if it doesn't exist on server
        self.assertEqual(nmem_shared.resolve_space("/path/to/random"), "custom-explicit-space")

    @patch("pathlib.Path.is_file", return_value=True)
    @patch("pathlib.Path.read_text")
    def test_get_effective_config_ignore_host_config(self, mock_read_text, mock_is_file):
        mock_read_text.return_value = '{"apiUrl": "http://127.0.0.1:14242", "apiKey": "host_secret_key"}'
        env_dict = {"NMEM_IGNORE_HOST_CONFIG": "1", "NMEM_API_URL": "http://127.0.0.1:9999"}
        with patch.dict(os.environ, env_dict, clear=True):
            url, key = nmem_shared.get_effective_config()
            self.assertEqual(url, "http://127.0.0.1:9999")
            self.assertIsNone(key)

    @patch("pathlib.Path.is_file", return_value=True)
    @patch("pathlib.Path.read_text")
    def test_get_effective_config_prevents_host_key_leak_on_url_mismatch(self, mock_read_text, mock_is_file):
        mock_read_text.return_value = '{"apiUrl": "http://127.0.0.1:14242", "apiKey": "host_prod_key"}'
        # NMEM_API_URL set to test server 8888 without explicit API key -> should NOT leak host_prod_key from config.json
        env_dict = {"NMEM_API_URL": "http://127.0.0.1:8888"}
        with patch.dict(os.environ, env_dict, clear=True):
            url, key = nmem_shared.get_effective_config()
            self.assertEqual(url, "http://127.0.0.1:8888")
            self.assertIsNone(key)


    @patch("nmem_shared.get_existing_spaces")
    def test_resolve_space_dynamic_existing(self, mock_get_spaces):
        mock_get_spaces.return_value = [
            {"id": "default", "key": "default", "name": "Default"},
            {"id": "agync", "key": "agync", "name": "agync"}
        ]
        with patch.dict(os.environ, {}, clear=True):
            # Dynamic space 'agync' exists on backend -> returns 'agync'
            self.assertEqual(nmem_shared.resolve_space("/home/user/workspace/agync"), "agync")

    @patch("nmem_shared.get_existing_spaces")
    def test_resolve_space_dynamic_non_existing_falls_back_to_default(self, mock_get_spaces):
        mock_get_spaces.return_value = [
            {"id": "default", "key": "default", "name": "Default"},
            {"id": "agync", "key": "agync", "name": "agync"}
        ]
        with patch.dict(os.environ, {}, clear=True):
            # Dynamic space 'nowledge-mem-google-antigravity' does NOT exist on backend -> falls back to 'default'
            self.assertEqual(
                nmem_shared.resolve_space("/home/user/workspace/nowledge-co/nowledge-mem-google-antigravity"),
                "default"
            )

    @patch("nmem_shared.get_existing_spaces")
    def test_resolve_space_dynamic_unreachable_falls_back_to_default(self, mock_get_spaces):
        mock_get_spaces.return_value = None
        with patch.dict(os.environ, {}, clear=True):
            # Backend unreachable/unverified -> falls back to 'default'
            self.assertEqual(nmem_shared.resolve_space("/home/user/workspace/unknown-project"), "default")


class TestSessionStart(unittest.TestCase):

    def setUp(self):
        nmem_shared.reset_backend_unreachable()

    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.http_request")
    @patch("nmem_shared.emit")
    @patch("nmem_shared.run_nmem_command")
    @patch("subprocess.Popen")
    @patch("nmem_shared.sync_host_skills_async")
    @patch("nmem_shared.sync_mcp_config_file")
    def test_session_start_ephemeral_injection(self, mock_sync_mcp, mock_sync, mock_popen, mock_run, mock_emit, mock_http, mock_input):
        nmem_shared.reset_backend_unreachable()
        mock_input.return_value = {"invocationNum": 0}

        # Mock Context Bundle output
        context_json = json.dumps({
            "rendered_markdown": "Startup context bundle content"
        })
        mock_run.return_value = MagicMock(returncode=0, stdout=context_json)

        session_start.main()

        mock_emit.assert_called_once()
        args, _ = mock_emit.call_args
        payload = args[0]
        self.assertIn("injectSteps", payload)
        self.assertIn("nowledge_context_bundle", payload["injectSteps"][0]["ephemeralMessage"])


class TestPostInvocation(unittest.TestCase):

    @patch("nmem_shared.get_unsynced_sessions")
    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.emit")
    def test_post_invocation_injects_warning_when_unsynced_exists(self, mock_emit, mock_input, mock_unsynced):
        mock_input.return_value = {"invocationNum": 1}
        mock_unsynced.return_value = {"conv-1": {"title": "Test"}}

        post_invocation.main()

        mock_emit.assert_called_once()
        args, _ = mock_emit.call_args
        payload = args[0]
        self.assertIn("injectSteps", payload)
        self.assertIn("pending offline session(s)", payload["injectSteps"][0]["ephemeralMessage"])

    @patch("nmem_shared.get_unsynced_sessions")
    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.emit")
    def test_post_invocation_emits_empty_when_no_unsynced(self, mock_emit, mock_input, mock_unsynced):
        mock_input.return_value = {"invocationNum": 1}
        mock_unsynced.return_value = {}

        post_invocation.main()

        mock_emit.assert_called_once_with({})


class TestSessionEnd(unittest.TestCase):

    def setUp(self):
        nmem_shared.reset_backend_unreachable()

    @patch("nmem_shared.get_existing_spaces", return_value=[{"id": "default"}])
    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.emit")
    @patch("nmem_shared.run_nmem_command")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data='{"source":"USER_EXPLICIT","type":"USER_INPUT","content":"<USER_REQUEST>Test request</USER_REQUEST>"}\n{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Hello world!"}\n')
    def test_session_end_captures_transcript(self, mock_file, mock_exists, mock_run, mock_emit, mock_input, mock_spaces):
        mock_input.return_value = {
            "conversationId": "conv-12345",
            "transcriptPath": "/fake/transcript.jsonl"
        }
        mock_exists.return_value = True

        # Thread show returns non-zero (does not exist)
        # Thread import returns 0 (success)
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr=""), # show
            MagicMock(returncode=0, stdout="imported", stderr="") # import
        ]

        session_end.main()

        mock_emit.assert_called_once_with({})
        # Verify show and import commands were executed
        self.assertEqual(mock_run.call_count, 2)

    @patch("nmem_shared.save_unsynced_session")
    @patch("nmem_shared.get_existing_spaces", return_value=[{"id": "default"}])
    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.emit")
    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data='{"source":"USER_EXPLICIT","type":"USER_INPUT","content":"<USER_REQUEST>Test request</USER_REQUEST>"}\n{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Hello world!"}\n')
    def test_session_end_unreachable_preserves_transcript(self, mock_file, mock_exists, mock_emit, mock_input, mock_spaces, mock_save):
        mock_input.return_value = {
            "conversationId": "conv-12345",
            "transcriptPath": "/fake/transcript.jsonl"
        }
        # Mark backend unreachable prior to session-end
        nmem_shared.mark_backend_unreachable()

        session_end.main()

        # Verify save_unsynced_session was called with non-empty populated messages & clean title
        mock_save.assert_called_once()
        call_args = mock_save.call_args[0]
        conv_id, msgs, title, space, host_agent_id = call_args
        self.assertEqual(conv_id, "conv-12345")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["content"], "<USER_REQUEST>Test request</USER_REQUEST>")
        self.assertEqual(msgs[1]["content"], "Hello world!")
        self.assertEqual(title, "Test request")

    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.emit")
    @patch("nmem_shared.run_nmem_command")
    def test_session_end_skips_when_not_fully_idle(self, mock_run, mock_emit, mock_input):
        mock_input.return_value = {
            "conversationId": "conv-12345",
            "transcriptPath": "/fake/transcript.jsonl",
            "fullyIdle": False
        }
        session_end.main()
        mock_emit.assert_called_once_with({})
        mock_run.assert_not_called()

    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.emit")
    @patch("os.path.exists", return_value=False)
    def test_session_end_missing_transcript_file(self, mock_exists, mock_emit, mock_input):
        mock_input.return_value = {
            "conversationId": "conv-12345",
            "transcriptPath": "/fake/non_existent_transcript.jsonl"
        }
        session_end.main()
        mock_emit.assert_called_once_with({})



class TestNmemGate(unittest.TestCase):

    @patch("nmem_gate.read_hook_input")
    @patch("sys.stdout.write")
    @patch("sys.stdout.flush")
    def test_nmem_gate_read_only(self, mock_flush, mock_write, mock_input):
        mock_input.return_value = {
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ServerName": "nowledge-mem",
                    "ToolName": "memory_search"
                }
            }
        }

        nmem_gate.main()

        # Gather stdout write calls
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        self.assertEqual(payload["decision"], "allow")

    @patch("nmem_gate.read_hook_input")
    @patch("sys.stdout.write")
    @patch("sys.stdout.flush")
    def test_nmem_gate_delete_destructive(self, mock_flush, mock_write, mock_input):
        mock_input.return_value = {
            "toolCall": {
                "name": "mcp_nowledge-mem_memory_delete",
                "args": {
                    "id": "mem-1"
                }
            }
        }

        nmem_gate.main()

        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        self.assertEqual(payload["decision"], "force_ask")

    @patch("nmem_gate.read_hook_input")
    @patch("sys.stdout.write")
    @patch("sys.stdout.flush")
    def test_nmem_gate_trigger_memory_catchup(self, mock_flush, mock_write, mock_input):
        mock_input.return_value = {
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ServerName": "nowledge-mem",
                    "ToolName": "trigger_memory_catchup"
                }
            }
        }
        nmem_gate.main()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        self.assertEqual(payload["decision"], "allow")
        self.assertIn("Auto-allowing read-only tool trigger_memory_catchup", payload["reason"])

    @patch("nmem_gate.read_hook_input")
    @patch("sys.stdout.write")
    @patch("sys.stdout.flush")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data='{"source":"USER_EXPLICIT","content":"Please save the session"}\n')
    def test_nmem_gate_write_intent(self, mock_file, mock_exists, mock_flush, mock_write, mock_input):
        mock_input.return_value = {
            "toolCall": {
                "name": "mcp_nowledge-mem_memory_add",
                "args": {"content": "durable decision"}
            },
            "transcriptPath": "/fake/transcript.jsonl"
        }
        mock_exists.return_value = True

        nmem_gate.main()

        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        self.assertEqual(payload["decision"], "allow")

    @patch("nmem_gate.read_hook_input")
    @patch("sys.stdout.write")
    @patch("sys.stdout.flush")
    def test_nmem_gate_run_command_status(self, mock_flush, mock_write, mock_input):
        mock_input.return_value = {
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": "python3 hooks/nmem_status.py --conv-id 123"
                }
            }
        }
        nmem_gate.main()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        self.assertEqual(payload["decision"], "allow")
        self.assertEqual(payload["reason"], "Auto-allowing plugin status command")

    @patch("nmem_gate.read_hook_input")
    @patch("sys.stdout.write")
    @patch("sys.stdout.flush")
    def test_nmem_gate_run_command_other(self, mock_flush, mock_write, mock_input):
        mock_input.return_value = {
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": "echo 'hello'"
                }
            }
        }
        nmem_gate.main()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        # Should fall back to "allow" since it is not an nmem tool
        self.assertEqual(payload["decision"], "allow")

    @patch("nmem_gate.read_hook_input")
    @patch("sys.stdout.write")
    @patch("sys.stdout.flush")
    def test_nmem_gate_new_tools_classification(self, mock_flush, mock_write, mock_input):
        # 1. New read-only tool
        mock_input.return_value = {
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ServerName": "nowledge-mem",
                    "ToolName": "list_timeline_reviews"
                }
            }
        }
        nmem_gate.main()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        self.assertEqual(payload["decision"], "allow")
        mock_write.reset_mock()

        # 2. New destructive tool
        mock_input.return_value = {
            "toolCall": {
                "name": "mcp_nowledge-mem_entity_delete",
                "args": {"id": "ent-1"}
            }
        }
        nmem_gate.main()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        self.assertEqual(payload["decision"], "force_ask")
        mock_write.reset_mock()

        # 3. New write/mutation tool without intent
        mock_input.return_value = {
            "toolCall": {
                "name": "mcp_nowledge-mem_resolve_timeline_review",
                "args": {"event_id": "evt-1", "action": "dismiss"}
            }
        }
        nmem_gate.main()
        written = "".join(call.args[0] for call in mock_write.call_args_list)
        payload = json.loads(written)
        self.assertEqual(payload["decision"], "ask")


class TestNmemStatus(unittest.TestCase):

    @patch("nmem_shared.get_existing_spaces", return_value=[{"id": "default"}])
    @patch("nmem_shared.run_nmem_command")
    @patch("nmem_shared.get_host_agent_fingerprint")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_nmem_status_script(self, mock_read_text, mock_exists, mock_fingerprint, mock_run, mock_spaces):
        mock_fingerprint.return_value = "antigravity-test"
        mock_exists.return_value = True
        mock_read_text.return_value = json.dumps({
            "conv-123": {"title": "Test thread"},
            "conv-2": {"title": "Another thread"}
        })

        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="Connected to backend", stderr=""),
            MagicMock(returncode=0, stdout="Thread: conv-123\nMessages: 5", stderr="")
        ]

        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f), patch("sys.argv", ["nmem_status.py", "--conv-id", "conv-123"]):
            nmem_status.main()

        output = f.getvalue()
        self.assertIn("🟢 Connected", output)
        self.assertIn("🟢 Synced", output)
        self.assertIn("conv-123", output)
        self.assertIn("antigravity-test", output)


class TestSyncLearnings(unittest.TestCase):

    @patch("nmem_shared.run_nmem_command")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("pathlib.Path.write_text")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data='{"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Comments on artifact URI: file:///path/to/artifacts/learning_proposal.md\\n\\nThe user has approved this document."}\n')
    def test_sync_learnings_rules(self, mock_file, mock_os_exists, mock_write_text, mock_read_text, mock_exists, mock_run):
        mock_os_exists.side_effect = lambda x: True
        mock_exists.return_value = True

        proposal_content = """# Learning Proposal - My Rule

## Classification
- **Type**: Project-Scoped Rule

## Proposed Additions to [AGENTS.md](file:///path/to/AGENTS.md)
```markdown
* Rule content
```
"""
        # First read_text for proposal, second for checking synced state file (doesn't exist)
        mock_read_text.side_effect = [proposal_content, "[]"]

        mock_run.return_value = MagicMock(returncode=0, stdout="success", stderr="")

        with patch("pathlib.Path.unlink") as mock_unlink:
            nmem_shared.sync_learnings_if_any("conv-123", "/path/to/transcript.jsonl", "/path/to/artifacts", "default")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "rules")
        self.assertEqual(args[1], "upsert")


nmem_entrypoint = import_module_from_path("nmem_entrypoint", str(HOOKS_DIR / "nmem_entrypoint.py"))
load_skill = import_module_from_path("load_skill", str(HOOKS_DIR.parent / "skills" / "nmem-skill-load" / "scripts" / "load_skill.py"))
manage_skills = import_module_from_path("manage_skills", str(HOOKS_DIR.parent / "skills" / "nmem-skill-manage" / "scripts" / "manage_skills.py"))


class TestNmemEntrypoint(unittest.TestCase):
    @patch("nmem_status.main")
    def test_subcommand_status(self, mock_status_main):
        with patch.object(sys, "argv", ["nmem_entrypoint.py", "status"]):
            nmem_entrypoint.main()
            mock_status_main.assert_called_once()

    @patch("importlib.import_module")
    def test_subcommand_session_start(self, mock_import_module):
        mock_module = MagicMock()
        mock_import_module.return_value = mock_module
        with patch.object(sys, "argv", ["nmem_entrypoint.py", "session-start"]):
            nmem_entrypoint.main()
            mock_import_module.assert_called_once_with("session-start")
            mock_module.main.assert_called_once()

    @patch("importlib.import_module")
    def test_subcommand_session_end(self, mock_import_module):
        mock_module = MagicMock()
        mock_import_module.return_value = mock_module
        with patch.object(sys, "argv", ["nmem_entrypoint.py", "session-end"]):
            nmem_entrypoint.main()
            mock_import_module.assert_called_once_with("session-end")
            mock_module.main.assert_called_once()

    @patch("importlib.import_module")
    def test_subcommand_gate(self, mock_import_module):
        mock_module = MagicMock()
        mock_import_module.return_value = mock_module
        with patch.object(sys, "argv", ["nmem_entrypoint.py", "gate"]):
            nmem_entrypoint.main()
            mock_import_module.assert_called_once_with("nmem-gate")
            mock_module.main.assert_called_once()


class TestLoadSkill(unittest.TestCase):

    @patch("load_skill.make_request")
    def test_search_skills(self, mock_make_request):
        mock_make_request.return_value = {
            "skills": [
                {"id": "makefile-pattern", "name": "Makefile Pattern", "description": "Makefile guidelines"},
                {"id": "docker-build", "name": "Docker Build", "description": "Docker container setup"}
            ]
        }
        res = load_skill.search_skills("make")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "makefile-pattern")

    @patch("load_skill.make_request")
    def test_fetch_skill(self, mock_make_request):
        mock_make_request.return_value = {
            "id": "makefile-pattern",
            "body": "# Makefile Pattern"
        }
        res = load_skill.fetch_skill("makefile-pattern")
        self.assertEqual(res["body"], "# Makefile Pattern")


class TestManageSkills(unittest.TestCase):

    def test_compute_trust_badge(self):
        self.assertEqual(manage_skills.compute_trust_badge({"trust_badge": "proven"}), "Proven")
        self.assertEqual(manage_skills.compute_trust_badge({"passed_tests_count": 2}), "Proven")
        self.assertEqual(manage_skills.compute_trust_badge({"passed_tests_count": 1}), "Checked")
        self.assertEqual(manage_skills.compute_trust_badge({"stage": "active"}), "Checked")
        self.assertEqual(manage_skills.compute_trust_badge({"stage": "candidate"}), "Draft")

    @patch("manage_skills.make_request")
    def test_restore_merge(self, mock_make_request):
        mock_make_request.return_value = {"status": "success", "skill_id": "makefile-pattern"}

        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            manage_skills.restore_merge_command({"apiUrl": "http://localhost", "apiKey": ""}, "makefile-pattern")
        output = f.getvalue()
        self.assertIn("Success: Merge undone", output)
        mock_make_request.assert_called_once_with(
            {"apiUrl": "http://localhost", "apiKey": ""},
            "/skills/makefile-pattern/restore-merge",
            method='POST'
        )

    @patch("urllib.request.urlopen")
    @patch("manage_skills.load_config")
    def test_make_request_token_rotation_retry(self, mock_load_config, mock_urlopen):
        import io
        import urllib.error

        # 1st call raises 401
        err = urllib.error.HTTPError("http://localhost/skills", 401, "Unauthorized", {}, None)
        err.fp = io.BytesIO(b'{"detail": "Token expired"}')

        # 2nd call returns success
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__.return_value = mock_resp

        mock_urlopen.side_effect = [err, mock_resp]
        mock_load_config.return_value = {"apiUrl": "http://localhost", "apiKey": "new_key"}

        config = {"apiUrl": "http://localhost", "apiKey": "old_key"}
        res = manage_skills.make_request(config, "/skills")

        self.assertEqual(res, {"status": "ok"})
        self.assertEqual(config["apiKey"], "new_key")
        self.assertEqual(mock_urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()

