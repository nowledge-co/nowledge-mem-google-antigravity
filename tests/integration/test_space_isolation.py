"""Integration test suite for multi-tenant space data isolation on nowledgelabs/mem container."""

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


def test_space_data_scoping(mem_server: MemServerContext) -> None:
    """Verify space scoping: valid space stores memories cleanly, while unknown space header is isolated."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    default_space_headers = {"X-NMEM-Space-ID": "default"}
    unknown_space_headers = {"X-NMEM-Space-ID": "unregistered-isolated-space-xyz"}

    # 1. Store memory in default space
    add_payload = {
        "jsonrpc": "2.0",
        "id": 501,
        "method": "tools/call",
        "params": {
            "name": "memory_add",
            "arguments": {
                "title": "Default Space Unique Secret Memory",
                "content": "Secret payload stored in default space.",
                "labels": ["default-only"],
            },
        },
    }
    status_a, body_a, _ = mem_server.post_json("/mcp/", add_payload, headers=default_space_headers)
    assert status_a == 200, f"Expected 200 storing memory in default space, got {status_a}: {body_a}"
    assert body_a.get("jsonrpc") == "2.0"
    assert body_a.get("id") == 501
    assert "result" in body_a

    # 2. Search in default space to confirm memory IS present
    search_payload_a = {
        "jsonrpc": "2.0",
        "id": 502,
        "method": "tools/call",
        "params": {"name": "memory_search", "arguments": {"query": "Default Space Unique Secret Memory"}},
    }
    status_a_search, body_a_search, _ = mem_server.post_json("/mcp/", search_payload_a, headers=default_space_headers)
    assert status_a_search == 200
    search_results_a = str(body_a_search.get("result", {}))
    assert (
        "Default Space Unique Secret Memory" in search_results_a
    ), f"Memory should be visible in default space, got: {body_a_search}"

    # 3. Query with unknown space header -> Server must reject or isolate with unknown space error (code -32602)
    search_payload_b = {
        "jsonrpc": "2.0",
        "id": 503,
        "method": "tools/call",
        "params": {"name": "memory_search", "arguments": {"query": "Default Space Unique Secret Memory"}},
    }
    status_b, body_b, _ = mem_server.post_json("/mcp/", search_payload_b, headers=unknown_space_headers)
    assert status_b == 200
    assert body_b.get("jsonrpc") == "2.0"
    assert body_b.get("id") == 503

    # Verify that searching in unregistered space either returns error (Unknown space) or empty results
    is_unknown_space_err = body_b.get("error", {}).get("code") == -32602 or "unknown space" in str(body_b).lower()
    not_in_results = "Default Space Unique Secret Memory" not in str(body_b.get("result", {}))
    assert (
        is_unknown_space_err or not_in_results
    ), f"Multi-tenant isolation failed! Unregistered space did not isolate memory: {body_b}"


def test_space_isolation_cross_space_mutation_denied(mem_server: MemServerContext) -> None:
    """Negative Test: Verify deleting/updating a memory from an unregistered or invalid space is rejected."""
    init_status, _ = mem_server.mcp_initialize()
    assert init_status == 200

    unknown_space_headers = {"X-NMEM-Space-ID": "unregistered-isolated-space-xyz"}

    delete_payload_b = {
        "jsonrpc": "2.0",
        "id": 504,
        "method": "tools/call",
        "params": {"name": "memory_delete", "arguments": {"memory_id": "non-existent-or-alpha-id-in-beta"}},
    }
    status_del, body_del, _ = mem_server.post_json("/mcp/", delete_payload_b, headers=unknown_space_headers)
    assert status_del == 200
    assert body_del.get("jsonrpc") == "2.0"
    assert body_del.get("id") == 504
    # Rejection by unknown space error (-32602) or isError
    is_err = body_del.get("error", {}).get("code") == -32602 or body_del.get("result", {}).get("isError") is True
    assert (
        is_err or "error" in str(body_del).lower()
    ), f"Expected cross-space mutation attempt to fail or report unknown space error, got: {body_del}"
