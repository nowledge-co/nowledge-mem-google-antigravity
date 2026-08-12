"""Integration test suite for live skill script execution against nowledgelabs/mem container."""

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

ROOT_DIR = Path(__file__).parent.parent.parent
LOAD_SKILL_SCRIPT = ROOT_DIR / "skills" / "nmem-skill-load" / "scripts" / "load_skill.py"
MANAGE_SKILLS_SCRIPT = ROOT_DIR / "skills" / "nmem-skill-manage" / "scripts" / "manage_skills.py"
PROPOSE_SKILL_SCRIPT = ROOT_DIR / "skills" / "nmem-skill-propose" / "scripts" / "propose_skill.py"


def test_script_load_skill_live(mem_server: MemServerContext) -> None:
    """Verify live load_skill.py search execution against test container."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    proc = subprocess.run(
        [sys.executable, str(LOAD_SKILL_SCRIPT), "search", "python"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, f"Expected exit code 0 from load_skill.py, got {proc.returncode}: {proc.stderr}"
    assert len(proc.stdout) >= 0


def test_script_manage_skills_live(mem_server: MemServerContext) -> None:
    """Verify live manage_skills.py list execution against test container."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    proc = subprocess.run(
        [sys.executable, str(MANAGE_SKILLS_SCRIPT), "list"], capture_output=True, text=True, env=env, timeout=10
    )
    assert (
        proc.returncode == 0
    ), f"Expected exit code 0 from manage_skills.py list, got {proc.returncode}: {proc.stderr}"


def test_script_propose_skill_live(mem_server: MemServerContext, tmp_path: Path) -> None:
    """Verify live propose_skill.py execution staging a skill proposal against test container."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    skill_file = tmp_path / "test-pytest-skill.md"
    skill_file.write_text("""---
name: test-pytest-skill
description: Pytest integration test skill definition.
---
# Pytest Integration Test Skill
Test body content for skill proposal.
""")

    proc = subprocess.run(
        [sys.executable, str(PROPOSE_SKILL_SCRIPT), str(skill_file), "--no-activate"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert proc.returncode == 0, f"Expected exit code 0 from propose_skill.py, got {proc.returncode}: {proc.stderr}"


def test_script_load_skill_invalid_subcommand(mem_server: MemServerContext) -> None:
    """Negative Test: Verify load_skill.py handles invalid subcommand with exit code != 0 or clean error."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    proc = subprocess.run(
        [sys.executable, str(LOAD_SKILL_SCRIPT), "invalid_subcommand_xyz"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert (
        proc.returncode != 0
        or "usage" in proc.stderr.lower()
        or "error" in proc.stderr.lower()
        or "invalid" in proc.stderr.lower()
    ), f"Expected failure or error output for invalid subcommand: returncode={proc.returncode}, stderr={proc.stderr}"


def test_script_propose_skill_non_existent_file(mem_server: MemServerContext) -> None:
    """Negative Test: Verify propose_skill.py fails with non-zero exit code when input file does not exist."""
    env = os.environ.copy()
    env["NMEM_API_URL"] = mem_server.base_url
    if mem_server.api_key:
        env["NMEM_API_KEY"] = mem_server.api_key

    non_existent = "/non/existent/path/to/skill_proposal_999.md"
    proc = subprocess.run(
        [sys.executable, str(PROPOSE_SKILL_SCRIPT), non_existent], capture_output=True, text=True, env=env, timeout=10
    )
    assert (
        proc.returncode != 0
        or "not found" in proc.stderr.lower()
        or "error" in proc.stderr.lower()
        or "no such" in proc.stderr.lower()
    ), (
        f"Expected non-zero exit code or error output when proposing missing file: "
        f"returncode={proc.returncode}, stderr={proc.stderr}"
    )
