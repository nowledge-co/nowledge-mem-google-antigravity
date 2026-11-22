import json
import os
import subprocess
import sys
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from test_hooks import PLUGIN_ROOT, _hook_command, nmem_shared, session_start


def test_scoped_context_uses_supported_api_and_falls_back_on_legacy_error():
    args = ["context", "--source-app", "google-antigravity", "--space", "研发 & QA"]
    with (
        patch.object(nmem_shared, "is_backend_unreachable", return_value=False),
        patch.object(nmem_shared, "http_request", side_effect=[{"error": "not_found"}, {"markdown": "legacy"}]) as get,
    ):
        assert session_start.read_nmem(args, ["markdown"]) == "legacy"
    current, legacy = (urlsplit(call.args[0]) for call in get.call_args_list)
    assert current.path == "/context/bundle"
    assert parse_qs(current.query)["space_id"] == ["研发 & QA"]
    assert legacy.path == "/context"
    assert parse_qs(legacy.query)["space"] == ["研发 & QA"]


def test_child_session_skips_parent_context():
    with (
        patch.object(sys, "argv", ["session-start"]),
        patch.object(nmem_shared, "read_hook_input", return_value={"parentConversationId": "parent"}),
        patch.object(nmem_shared, "sync_host_skills_async"),
        patch.object(nmem_shared, "sync_mcp_config_file"),
        patch.object(session_start.subprocess, "Popen"),
        patch.object(session_start, "read_startup_context") as read_context,
        patch.object(nmem_shared, "emit") as emit,
    ):
        session_start.main()
    read_context.assert_not_called()
    emit.assert_called_once_with({})


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd.exe")
def test_cmd_gate_fallback_without_python(tmp_path):
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", _hook_command("PreToolUse")],
        cwd=PLUGIN_ROOT,
        env={**os.environ, "PATH": str(tmp_path)},
        input="{}",
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {"decision": "force_ask"}


@pytest.mark.skipif(os.name != "nt", reason="requires Windows output handling")
def test_status_redirected_from_gbk(tmp_path):
    result = subprocess.run(
        [sys.executable, "hooks/nmem_status.py"],
        cwd=PLUGIN_ROOT,
        capture_output=True,
        env={
            **os.environ,
            "PYTHONIOENCODING": "gbk:strict",
            "NMEM_API_URL": "http://127.0.0.1:1",
            "USERPROFILE": str(tmp_path),
            "PATH": str(tmp_path),
        },
        timeout=25,
    )
    assert result.returncode == 0, result.stderr
    assert "🌐" in result.stdout.decode("utf-8")
