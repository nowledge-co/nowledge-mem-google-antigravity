"""Windows command boundary tests run against the platform's real process APIs."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import nmem_shared  # noqa: E402


@pytest.mark.skipif(os.name != "nt", reason="requires Windows CreateProcess and cmd.exe")
def test_real_spaced_batch_forwards_unicode_arguments(tmp_path):
    wrapper = tmp_path / "Nowledge Mem CLI" / "nmem.cmd"
    wrapper.parent.mkdir()
    wrapper.write_text('@echo off\r\n"%NMEM_TEST_INTERPRETER%" "%~dp0probe.py" %*\r\n', encoding="ascii")
    (wrapper.parent / "probe.py").write_text(
        'import json, sys\nsys.stdout.buffer.write(json.dumps(sys.argv[1:], ensure_ascii=False).encode("utf-8"))',
        encoding="utf-8",
    )
    args = ["t", "show", "中文 session"]
    with patch.object(nmem_shared, "_nmem_command", return_value=str(wrapper)):
        result = nmem_shared.run_nmem_command(args, env={"NMEM_TEST_INTERPRETER": sys.executable})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == args


@pytest.mark.skipif(os.name != "nt", reason="requires Windows native argv")
def test_native_process_receives_full_long_unicode_transcript(tmp_path):
    probe = tmp_path / "check.py"
    probe.write_text("import sys\nsys.stdout.buffer.write(sys.argv[-1].encode('utf-8'))", encoding="utf-8")
    messages = [{"role": "user", "content": '中文 "quoted" & ! % ' * 850}]
    args = nmem_shared.build_cli_thread_append_args("conversation", messages)
    assert len(args[-1]) > 8191
    with patch.object(nmem_shared, "_nmem_command", return_value=sys.executable):
        result = nmem_shared.run_nmem_command([str(probe), *args])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == messages


def test_batch_rejects_shell_substitution_and_oversized_arguments():
    for arg in ["%USERNAME%", "one & two", "multi\nline", "x" * 8200]:
        with pytest.raises(ValueError):
            nmem_shared._build_nmem_command(r"C:\Nowledge Mem CLI\nmem.cmd", "t", "show", arg)


@pytest.mark.skipif(os.name != "nt", reason="Windows packaged CLI layout")
def test_managed_native_cli_precedes_batch_and_rejects_oversized_native_argv(tmp_path, monkeypatch):
    binary = tmp_path / "Nowledge Mem" / "_up_" / "rust-backend" / "nmem.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(nmem_shared.shutil, "which", lambda name: None if name == "nmem.exe" else "nmem.cmd")
    assert nmem_shared._nmem_command() == str(binary)
    with pytest.raises(ValueError, match="native command limit"):
        nmem_shared._build_nmem_command(str(binary), "x" * 32768)
