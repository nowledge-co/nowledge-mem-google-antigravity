"""Integration test suite for live lifecycle hooks execution against nowledgelabs/mem container."""

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import pytest

    _ = pytest
except ImportError as err:
    import unittest

    raise unittest.SkipTest("pytest module is not installed in current python environment") from err

sys.path.insert(0, str(Path(__file__).parent))
from conftest import MemServerContext

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"


def test_hook_nmem_status_live(mem_server: MemServerContext) -> None:
    """Verify live hooks/nmem_status.py status execution against test container."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    status_script = HOOKS_DIR / "nmem_status.py"
    proc = subprocess.run([sys.executable, str(status_script)], capture_output=True, text=True, env=env, timeout=10)
    assert proc.returncode == 0, f"Expected 0 exit code from nmem_status.py, got {proc.returncode}: {proc.stderr}"
    assert (
        "Nowledge Mem Status" in proc.stdout or "🟢" in proc.stdout
    ), f"Unexpected status script output: {proc.stdout}"


def test_hook_session_start_live(mem_server: MemServerContext) -> None:
    """Verify live session-start hook execution against test container."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    entrypoint = HOOKS_DIR / "nmem_entrypoint.py"
    input_data = json.dumps({"conversationId": "pytest-conv-live-001"})
    proc = subprocess.run(
        [sys.executable, str(entrypoint), "session-start"],
        input=input_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, f"Expected 0 exit code from session-start hook, got {proc.returncode}: {proc.stderr}"
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict), f"Expected JSON dict payload, got: {payload}"


def test_hook_nmem_gate_live(mem_server: MemServerContext) -> None:
    """Verify live nmem-gate hook decision logic against test container."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    entrypoint = HOOKS_DIR / "nmem_entrypoint.py"
    input_data = json.dumps(
        {
            "toolCall": {"name": "call_mcp_tool", "args": {"ServerName": "nowledge-mem", "ToolName": "memory_search"}},
            "conversationId": "pytest-conv-gate-001",
        }
    )
    proc = subprocess.run(
        [sys.executable, str(entrypoint), "gate"], input=input_data, capture_output=True, text=True, env=env, timeout=10
    )
    assert proc.returncode == 0, f"Expected 0 exit code from gate hook, got {proc.returncode}: {proc.stderr}"
    res = json.loads(proc.stdout)
    assert "decision" in res, f"Expected 'decision' key in gate output: {res}"
    assert res["decision"] == "allow", f"Expected read-only tool memory_search to be auto-allowed: {res}"


def test_hook_session_end_missing_transcript(mem_server: MemServerContext) -> None:
    """Negative Test: Verify session-end hook handles missing transcript file gracefully without crashing."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    entrypoint = HOOKS_DIR / "nmem_entrypoint.py"
    input_data = json.dumps(
        {
            "conversationId": "pytest-conv-missing-transcript",
            "transcriptPath": "/non/existent/path/transcript_12345.jsonl",
            "fullyIdle": True,
        }
    )
    proc = subprocess.run(
        [sys.executable, str(entrypoint), "session-end"],
        input=input_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert (
        proc.returncode == 0
    ), f"Expected exit code 0 on missing transcript file, got {proc.returncode}: {proc.stderr}"
    res = json.loads(proc.stdout)
    assert res == {}, f"Expected empty response {{}} for missing transcript, got: {res}"


def test_hook_gate_malformed_input(mem_server: MemServerContext) -> None:
    """Negative Test: Verify nmem-gate handles malformed stdin gracefully without unhandled exception."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    entrypoint = HOOKS_DIR / "nmem_entrypoint.py"
    proc = subprocess.run(
        [sys.executable, str(entrypoint), "gate"],
        input="INVALID_NON_JSON_INPUT",
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, f"Expected exit code 0 on malformed input, got {proc.returncode}: {proc.stderr}"
    res = json.loads(proc.stdout)
    assert "decision" in res, f"Expected decision dictionary on malformed stdin: {res}"
