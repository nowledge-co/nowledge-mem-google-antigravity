"""Integration test suite for live MCP tool call workflows against nowledgelabs/mem container."""

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


def test_mcp_memory_add_and_search(mem_server: MemServerContext) -> None:
    """Verify storing a memory via memory_add and searching it back via memory_search."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    # Add memory
    add_args = {
        "title": "Pytest Integration Test Memory",
        "content": "Durable lesson: verified container memory store via pytest integration workflow.",
        "labels": ["pytest", "integration-test"],
    }
    status, body = mem_server.mcp_call_tool("memory_add", add_args, req_id=101)
    assert status == 200, f"Expected HTTP 200 from memory_add, got {status}: {body}"
    assert body.get("jsonrpc") == "2.0", f"Invalid jsonrpc field: {body}"
    assert body.get("id") == 101, f"Expected JSON-RPC id 101, got {body.get('id')}"
    assert "result" in body, f"Expected result in memory_add response: {body}"
    result = body["result"]
    assert "content" in result, f"Expected content array in result: {result}"
    assert len(result["content"]) > 0, "Result content list must not be empty"

    # Search for stored memory
    search_args = {"query": "Pytest Integration Test Memory"}
    search_status, search_body = mem_server.mcp_call_tool("memory_search", search_args, req_id=102)
    assert search_status == 200, f"Expected HTTP 200 from memory_search, got {search_status}: {search_body}"
    assert search_body.get("jsonrpc") == "2.0"
    assert search_body.get("id") == 102
    assert "result" in search_body, f"Expected result in memory_search response: {search_body}"
    search_result = search_body["result"]
    assert "content" in search_result, f"Expected content in search result: {search_result}"
    search_text = str(search_result["content"])
    assert "Pytest Integration Test Memory" in search_text, (
        f"Memory search did not retrieve added memory payload in results: {search_text}"
    )


def test_mcp_mem_fs_ls_and_cat(mem_server: MemServerContext) -> None:
    """Verify browsing virtual filesystem via mem_fs tool (ls and cat operations)."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    # Execute mem_fs ls on root
    ls_args = {"command": "ls /"}
    status, body = mem_server.mcp_call_tool("mem_fs", ls_args, req_id=201)
    assert status == 200, f"Expected HTTP 200 from mem_fs ls, got {status}: {body}"
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == 201
    assert "result" in body, f"Expected result in mem_fs ls response: {body}"
    ls_result = body["result"]
    assert "content" in ls_result, f"Expected content in mem_fs ls result: {ls_result}"
    assert len(ls_result["content"]) > 0, "mem_fs ls returned empty content"

    # Execute mem_fs cat on virtual path
    cat_args = {"command": "cat /working_memory"}
    cat_status, cat_body = mem_server.mcp_call_tool("mem_fs", cat_args, req_id=202)
    assert cat_status == 200, f"Expected HTTP 200 from mem_fs cat, got {cat_status}: {cat_body}"
    assert cat_body.get("jsonrpc") == "2.0"
    assert cat_body.get("id") == 202
    assert "result" in cat_body, f"Expected result in mem_fs cat response: {cat_body}"
    cat_result = cat_body["result"]
    assert "content" in cat_result, f"Expected content in mem_fs cat result: {cat_result}"


def test_mcp_read_working_memory(mem_server: MemServerContext) -> None:
    """Verify retrieving working memory briefing via read_working_memory tool."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    status, body = mem_server.mcp_call_tool("read_working_memory", {}, req_id=301)
    assert status == 200, f"Expected HTTP 200 from read_working_memory, got {status}: {body}"
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == 301
    assert "result" in body, f"Expected result in read_working_memory response: {body}"
    result = body["result"]
    assert "content" in result, f"Expected content in read_working_memory result: {result}"
    assert isinstance(result["content"], list), "Result content must be a list"


def test_mcp_unknown_tool_call(mem_server: MemServerContext) -> None:
    """Negative Test: Verify calling an unknown/unregistered tool returns error response."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    status, body = mem_server.mcp_call_tool("non_existent_tool_xyz", {"arg": "val"}, req_id=401)
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == 401
    has_error_dict = "error" in body
    is_error_result = body.get("result", {}).get("isError") is True
    assert has_error_dict or is_error_result, (
        f"Expected error payload or isError=True for unknown tool call, got: {body}"
    )


def test_mcp_mem_fs_non_existent_path(mem_server: MemServerContext) -> None:
    """Negative Test: Verify mem_fs handling of non-existent file or directory paths."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    args = {"command": "cat /non_existent_file_path_99999.md"}
    status, body = mem_server.mcp_call_tool("mem_fs", args, req_id=402)
    assert status == 200
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == 402
    result = body.get("result", {})
    result_str = str(result)
    assert (
        result.get("isError") is True
        or "not found" in result_str.lower()
        or "error" in result_str.lower()
        or "no such" in result_str.lower()
        or "does not exist" in result_str.lower()
    ), f"Expected error indication for non-existent mem_fs path, got: {body}"


def test_mcp_malformed_jsonrpc_payload(mem_server: MemServerContext) -> None:
    """Negative Test: Verify JSON-RPC error response when sending malformed payload missing method."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    malformed_payload = {"jsonrpc": "2.0", "id": 403}
    status, body, _ = mem_server.post_json("/mcp/", malformed_payload)
    has_error_dict = "error" in body
    has_raw_error = "fail to deserialize" in str(body.get("raw", "")).lower()
    assert has_error_dict or has_raw_error, f"Expected JSON-RPC error or deserialization failure payload, got: {body}"
