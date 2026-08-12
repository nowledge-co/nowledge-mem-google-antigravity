"""pytest fixtures for launching and managing the nowledgelabs/mem integration test container on a random port."""

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass

import httpx

try:
    import pytest
except ImportError as err:
    import unittest

    raise unittest.SkipTest("pytest module is not installed in current python environment") from err


def _parse_response_body(body_bytes: bytes, content_type: str = "") -> dict:
    """Parse JSON or SSE data lines from server response."""
    if not body_bytes:
        return {}
    text = body_bytes.decode("utf-8", errors="replace").strip()
    if "data:" in text:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:") and len(line) > 5:
                json_str = line[5:].strip()
                if json_str.startswith("{") and json_str.endswith("}"):
                    try:
                        return json.loads(json_str)
                    except Exception:
                        pass
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


@dataclass
class MemServerContext:
    """Context information for a running nowledgelabs/mem container instance."""

    container_id: str
    host_port: int
    base_url: str
    api_key: str
    engine: str
    mcp_session_id: str = ""

    def _default_headers(self, custom_headers: dict | None = None) -> dict:
        headers = {"Accept": "application/json, text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-NMEM-API-Key"] = self.api_key
        if self.mcp_session_id:
            headers["Mcp-Session-Id"] = self.mcp_session_id
        if custom_headers:
            headers.update(custom_headers)
        return headers

    def _update_session_id(self, resp_headers: dict) -> None:
        for k, v in resp_headers.items():
            if k.lower() == "mcp-session-id" and v:
                self.mcp_session_id = str(v).strip()

    def get(self, path: str, headers: dict | None = None, timeout: float = 5.0) -> tuple[int, dict, dict]:
        """Perform GET request against server endpoint using httpx."""
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        req_headers = self._default_headers(headers)
        try:
            resp = httpx.get(url, headers=req_headers, timeout=timeout)
            resp_headers = dict(resp.headers)
            self._update_session_id(resp_headers)
            data = _parse_response_body(resp.content, resp_headers.get("content-type", ""))
            return resp.status_code, data, resp_headers
        except httpx.HTTPStatusError as e:
            resp_headers = dict(e.response.headers)
            self._update_session_id(resp_headers)
            data = _parse_response_body(e.response.content, resp_headers.get("content-type", ""))
            return e.response.status_code, data, resp_headers

    def post_json(
        self, path: str, payload: dict, headers: dict | None = None, timeout: float = 5.0
    ) -> tuple[int, dict, dict]:
        """Perform POST request with JSON payload using httpx. Returns (status_code, json_body, response_headers)."""
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        req_headers = {"Content-Type": "application/json"}
        req_headers.update(self._default_headers(headers))
        try:
            resp = httpx.post(url, json=payload, headers=req_headers, timeout=timeout)
            resp_headers = dict(resp.headers)
            self._update_session_id(resp_headers)
            data = _parse_response_body(resp.content, resp_headers.get("content-type", ""))
            return resp.status_code, data, resp_headers
        except httpx.HTTPStatusError as e:
            resp_headers = dict(e.response.headers)
            self._update_session_id(resp_headers)
            data = _parse_response_body(e.response.content, resp_headers.get("content-type", ""))
            return e.response.status_code, data, resp_headers

    def mcp_initialize(self) -> tuple[int, dict]:
        """Initialize MCP session and send notifications/initialized."""
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest-suite", "version": "1.0.0"},
            },
        }
        status, body, _ = self.post_json("/mcp/", init_payload)
        if status == 200:
            self.post_json("/mcp/", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return status, body

    def mcp_call_tool(self, tool_name: str, arguments: dict | None = None, req_id: int = 10) -> tuple[int, dict]:
        """Call an MCP tool via JSON-RPC tools/call."""
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        }
        status, body, _ = self.post_json("/mcp/", payload)
        return status, body


def _extract_api_key(engine: str, container_id: str, host_port: int) -> str:
    """Retrieve API key from container exec, nmem CLI, or container logs."""
    # 1. Direct container exec using nmem key
    try:
        res = subprocess.run([engine, "exec", container_id, "nmem", "key"], capture_output=True, text=True, timeout=5)
        key = res.stdout.strip()
        if res.returncode == 0 and key.startswith("nmem_"):
            return key
    except Exception:
        pass

    # 2. Local nmem / nmem-cli binary in venv or PATH
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    venv_nmem = os.path.join(root_dir, ".venv", "bin", "nmem")
    venv_cli = os.path.join(root_dir, ".venv", "bin", "nmem-cli")
    clis = []
    for candidate in (venv_nmem, venv_cli):
        if os.path.exists(candidate):
            clis.append(candidate)
    for name in ("nmem", "nmem-cli"):
        found = shutil.which(name)
        if found and found not in clis:
            clis.append(found)

    for cli in clis:
        try:
            cmd = [cli, "key", "--api-url", f"http://127.0.0.1:{host_port}"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            key = res.stdout.strip()
            if res.returncode == 0 and key.startswith("nmem_"):
                return key
        except Exception:
            pass

    # 3. Parse container logs
    try:
        logs_proc = subprocess.run([engine, "logs", container_id], capture_output=True, text=True, timeout=10)
        logs = logs_proc.stdout + logs_proc.stderr
        match = re.search(r"remote_access_api_key=(nmem_[A-Za-z0-9_-]+)", logs)
        if match:
            return match.group(1)
    except Exception:
        pass

    return ""


@pytest.fixture(scope="session")
def container_engine() -> str:
    """Detect available container engine binary (podman or docker)."""
    for engine in ("podman", "docker"):
        path = shutil.which(engine)
        if path:
            try:
                res = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    return engine
            except Exception:
                pass
    pytest.skip("Neither podman nor docker container engine is available on the host system.")


@pytest.fixture(scope="function")
def mem_server(container_engine: str) -> Generator[MemServerContext, None, None]:
    """Launch docker.io/nowledgelabs/mem:latest on a dynamic random host port and clean up after test."""
    image_name = os.environ.get("NMEM_TEST_IMAGE", "docker.io/nowledgelabs/mem:latest")
    os.environ["NMEM_IGNORE_HOST_CONFIG"] = "1"

    # Run container with dynamic host port (-p 14242 publishes port 14242 to an unused random host port)
    run_cmd = [container_engine, "run", "-d", "-p", "14242", image_name]
    try:
        proc = subprocess.run(run_cmd, capture_output=True, text=True, check=True, timeout=30)
        container_id = proc.stdout.strip()
    except subprocess.CalledProcessError as e:
        pytest.fail(f"Failed to start container {image_name} with {container_engine}: {e.stderr}")

    try:
        # Determine assigned host port
        port_cmd = [container_engine, "port", container_id, "14242/tcp"]
        port_proc = subprocess.run(port_cmd, capture_output=True, text=True, check=True, timeout=10)
        output = port_proc.stdout.strip()

        # Output format is typically '0.0.0.0:41297' or ':::41297' or '127.0.0.1:41297'
        match = re.search(r":([0-9]+)$", output.splitlines()[0])
        if not match:
            pytest.fail(f"Could not parse host port from output: '{output}'")

        host_port = int(match.group(1))
        base_url = f"http://127.0.0.1:{host_port}"

        # Poll healthcheck /livez until ready (timeout 20s)
        start_time = time.time()
        healthy = False
        while time.time() - start_time < 20:
            try:
                resp = httpx.get(f"{base_url}/livez", timeout=2)
                if resp.status_code == 200:
                    healthy = True
                    break
            except Exception:
                time.sleep(0.5)

        if not healthy:
            pytest.fail(f"Container {container_id} failed to become healthy on port {host_port} within timeout.")

        api_key = _extract_api_key(container_engine, container_id, host_port)

        ctx = MemServerContext(
            container_id=container_id, host_port=host_port, base_url=base_url, api_key=api_key, engine=container_engine
        )
        yield ctx

    finally:
        # Cleanup container after test completes
        stop_cmd = [container_engine, "stop", container_id]
        rm_cmd = [container_engine, "rm", "-f", container_id]
        subprocess.run(stop_cmd, capture_output=True, timeout=15)
        subprocess.run(rm_cmd, capture_output=True, timeout=10)
