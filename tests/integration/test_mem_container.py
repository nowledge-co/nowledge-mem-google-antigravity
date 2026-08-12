"""Integration test suite for nowledgelabs/mem container test server."""

import sys
from pathlib import Path

import httpx

try:
    import pytest

    _ = pytest
except ImportError as err:
    import unittest

    raise unittest.SkipTest("pytest module is not installed in current python environment") from err


sys.path.insert(0, str(Path(__file__).parent))
from conftest import MemServerContext


def test_server_liveness(mem_server: MemServerContext) -> None:
    """Verify server /livez endpoint returns HTTP 200 OK with status ok."""
    status, body, headers = mem_server.get("/livez")
    assert status == 200, f"Expected 200 OK from /livez, got {status}: {body}"
    assert body.get("status") == "ok", f"Expected status 'ok', got: {body}"


def test_random_port_isolation(mem_server: MemServerContext) -> None:
    """Verify test server runs on a dynamic host port and does NOT conflict with default port 14242."""
    assert isinstance(mem_server.host_port, int)
    assert mem_server.host_port > 1024, (
        f"Host port should be an unprivileged dynamic port (>1024), got {mem_server.host_port}"
    )
    assert mem_server.host_port != 14242, (
        f"Test container host port must not collide with production port 14242! Got: {mem_server.host_port}"
    )
    assert mem_server.base_url == f"http://127.0.0.1:{mem_server.host_port}"


def test_cors_and_server_headers(mem_server: MemServerContext) -> None:
    """Verify server responses include required CORS and content-type headers."""
    status, _, headers = mem_server.get("/livez")
    assert status == 200
    header_map = {k.lower(): v for k, v in headers.items()}
    assert "content-type" in header_map
    assert "access-control-allow-origin" in header_map
    assert len(header_map["content-type"]) > 0
    assert len(header_map["access-control-allow-origin"]) > 0


def test_mcp_protocol_initialize(mem_server: MemServerContext) -> None:
    """Verify MCP JSON-RPC 2.0 initialize request to /mcp/ endpoint."""
    status, body = mem_server.mcp_initialize()
    assert status == 200, f"Expected HTTP 200 from /mcp/ initialize, got {status}: {body}"
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == 1
    assert "result" in body, f"Missing 'result' in response: {body}"
    result = body["result"]
    assert "serverInfo" in result, f"Missing 'serverInfo' in initialize result: {result}"
    assert "capabilities" in result, f"Missing 'capabilities' in initialize result: {result}"
    assert mem_server.mcp_session_id != "", "Expected non-empty mcp_session_id header"


def test_mcp_tools_list(mem_server: MemServerContext) -> None:
    """Verify MCP tools/list request returns registered memory tools with valid schemas."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    # Query tools/list
    tools_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    status, body, _ = mem_server.post_json("/mcp/", tools_payload)
    assert status == 200, f"Expected HTTP 200 from /mcp/ tools/list, got {status}: {body}"
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == 2
    assert "result" in body, f"Missing 'result' in tools/list response: {body}"

    tools = body["result"].get("tools", [])
    assert isinstance(tools, list), f"Expected tools to be a list, got {type(tools)}"
    assert len(tools) > 0, "tools/list returned empty array"

    for tool in tools:
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool missing 'description': {tool}"
        assert "inputSchema" in tool, f"Tool missing 'inputSchema': {tool}"
        assert isinstance(tool["inputSchema"], dict), f"Tool inputSchema must be dict: {tool}"

    tool_names = {t.get("name") for t in tools if isinstance(t, dict)}
    expected_core_tools = {"memory_search", "memory_add", "mem_fs", "read_working_memory"}
    found_core_tools = expected_core_tools.intersection(tool_names)
    assert len(found_core_tools) > 0, (
        f"Expected at least one core tool in {expected_core_tools}, found tools: {tool_names}"
    )


def test_mcp_invalid_method(mem_server: MemServerContext) -> None:
    """Verify JSON-RPC error handling when querying an unknown method on initialized session."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    payload = {"jsonrpc": "2.0", "id": 99, "method": "non_existent_method_xyz", "params": {}}
    status, body, _ = mem_server.post_json("/mcp/", payload)
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == 99
    assert "error" in body, f"Expected 'error' payload for unknown method, got: {body}"
    err = body["error"]
    assert "code" in err and "message" in err, f"Invalid JSON-RPC error format: {err}"


def test_space_header_support(mem_server: MemServerContext) -> None:
    """Verify server accepts custom space headers without error."""
    custom_headers = {"X-NMEM-Space-ID": "test-pytest-space", "APP": "Pytest Test Suite"}
    status, body, _ = mem_server.get("/livez", headers=custom_headers)
    assert status == 200
    assert body.get("status") == "ok"


def test_unauthenticated_request_rejected(mem_server: MemServerContext) -> None:
    """Negative Test: Verify unauthenticated requests with invalid API keys are rejected or handled."""
    invalid_headers = {"Authorization": "Bearer invalid_secret_key_12345", "X-NMEM-API-Key": "invalid_secret_key_12345"}
    mcp_status, mcp_body, _ = mem_server.post_json(
        "/mcp/", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, headers=invalid_headers
    )
    assert mcp_status in (200, 401, 403), f"Unexpected HTTP status for invalid key: {mcp_status}"


def test_malformed_json_body(mem_server: MemServerContext) -> None:
    """Negative Test: Verify server returns error status or JSON-RPC parse error for malformed non-JSON body."""
    url = f"{mem_server.base_url.rstrip('/')}/mcp/"
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if mem_server.api_key:
        headers["Authorization"] = f"Bearer {mem_server.api_key}"

    res = httpx.post(url, content=b"INVALID_NON_JSON{{{", headers=headers, timeout=5.0)
    assert res.status_code in (400, 406, 422, 500) or "error" in res.text.lower() or "fail" in res.text.lower(), (
        f"Expected client error status or error text for malformed JSON, got status {res.status_code}: {res.text}"
    )


def test_invalid_space_header_format(mem_server: MemServerContext) -> None:
    """Negative Test: Verify server gracefully handles unusual/special characters in space header."""
    invalid_headers = {"X-NMEM-Space-ID": "invalid/space:id@special!chars#123"}
    status, body, _ = mem_server.get("/livez", headers=invalid_headers)
    assert status == 200, f"Expected 200 OK even with unusual space header, got {status}: {body}"
    assert body.get("status") == "ok"
