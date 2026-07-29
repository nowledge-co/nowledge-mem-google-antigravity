import sys
import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock, mock_open
from pathlib import Path

# Add hooks directory to path to import nmem_shared and others
HOOKS_DIR = Path(__file__).parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import nmem_shared

import importlib.util
def import_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

session_start = import_module_from_path("session_start", str(HOOKS_DIR / "session-start.py"))
session_end = import_module_from_path("session_end", str(HOOKS_DIR / "session-end.py"))
nmem_gate = import_module_from_path("nmem_gate", str(HOOKS_DIR / "nmem-gate.py"))
nmem_status = import_module_from_path("nmem_status", str(HOOKS_DIR / "nmem_status.py"))

class TestNmemShared(unittest.TestCase):
    
    @patch("os.access", return_value=True)
    @patch("pathlib.Path.is_file", return_value=True)
    @patch("shutil.which")
    def test_nmem_command_resolution(self, mock_which, mock_is_file, mock_access):
        # Case 1: nmem exists
        mock_which.side_effect = lambda x: "/usr/bin/nmem" if x == "nmem" else None
        self.assertEqual(nmem_shared._nmem_command(), "/usr/bin/nmem")

        # Case 2: nmem.cmd exists
        mock_which.side_effect = lambda x: "/usr/bin/nmem.cmd" if x == "nmem.cmd" else None
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
            self.assertEqual(data["mcpServers"]["nowledge-mem"]["headers"]["X-MEM-API-Key"], "nmem_sec_123")
            
            # Second call should return False (already up to date)
            updated_again = nmem_shared.sync_mcp_config_file(tf_path)
            self.assertFalse(updated_again)
        finally:
            if os.path.exists(tf_path):
                os.unlink(tf_path)

    @patch("nmem_shared._nmem_command")
    @patch("nmem_shared.run_nmem_command")
    def test_sync_host_skills_async(self, mock_run_cmd, mock_nmem_cmd):
        mock_nmem_cmd.return_value = "/usr/bin/nmem"
        import time
        nmem_shared.sync_host_skills_async()
        time.sleep(0.1)
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
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = nmem_shared.http_request("/health")
        self.assertEqual(res, {"status": "ok"})


class TestSessionStart(unittest.TestCase):
    
    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.emit")
    @patch("nmem_shared.run_nmem_command")
    @patch("subprocess.Popen")
    def test_session_start_ephemeral_injection(self, mock_popen, mock_run, mock_emit, mock_input):
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


class TestSessionEnd(unittest.TestCase):
    
    @patch("nmem_shared.read_hook_input")
    @patch("nmem_shared.emit")
    @patch("nmem_shared.run_nmem_command")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data='{"source":"USER_EXPLICIT","type":"USER_INPUT","content":"<USER_REQUEST>Test request</USER_REQUEST>"}\n{"source":"MODEL","type":"PLANNER_RESPONSE","content":"Hello world!"}\n')
    def test_session_end_captures_transcript(self, mock_file, mock_exists, mock_run, mock_emit, mock_input):
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
    
    @patch("nmem_shared.run_nmem_command")
    @patch("nmem_shared.get_host_agent_fingerprint")
    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_nmem_status_script(self, mock_read_text, mock_exists, mock_fingerprint, mock_run):
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
        with redirect_stdout(f):
            with patch("sys.argv", ["nmem_status.py", "--conv-id", "conv-123"]):
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


if __name__ == "__main__":
    unittest.main()
