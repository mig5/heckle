import json
from pathlib import Path
import subprocess

import pytest

from heckle.cli import build_parser
from heckle.errors import GenerationError
from heckle.opentofu import TfRunner
from heckle.pipeline import write_state_only_adopt_script


def _fake_binary(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_tf_option_selects_terraform():
    args = build_parser().parse_args(["generate", "forgejo", "--user", "mig5", "--tf", "terraform"])
    assert args.tf == "terraform"


def test_tofu_option_is_not_accepted():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["generate", "forgejo", "--user", "mig5", "--tofu", "/opt/tofu"])


def test_runner_identifies_terraform_and_opentofu(tmp_path, monkeypatch):
    terraform = _fake_binary(tmp_path, "terraform")
    tofu = _fake_binary(tmp_path, "tofu")
    monkeypatch.setenv("PATH", str(tmp_path))

    terraform_runner = TfRunner(str(terraform))
    assert terraform_runner.tool_name == "Terraform"
    assert terraform_runner.command_name == "terraform"

    tofu_runner = TfRunner(str(tofu))
    assert tofu_runner.tool_name == "OpenTofu"
    assert tofu_runner.command_name == "tofu"


def test_only_heckle_tf_bin_selects_the_executable(tmp_path, monkeypatch):
    terraform = _fake_binary(tmp_path, "terraform")
    tofu = _fake_binary(tmp_path, "tofu")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("HECKLE_TF_BIN", str(terraform))

    runner = TfRunner()
    assert runner.tool_name == "Terraform"


def test_opentofu_bin_is_ignored(tmp_path, monkeypatch):
    tofu = _fake_binary(tmp_path, "tofu")
    legacy = _fake_binary(tmp_path, "terraform")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("OPENTOFU_BIN", str(legacy))

    runner = TfRunner()
    assert runner.executable == str(tofu.resolve())
    assert runner.tool_name == "OpenTofu"


def test_generated_adopt_script_follows_selected_cli(tmp_path):
    terraform_script = tmp_path / "adopt-terraform.sh"
    write_state_only_adopt_script(
        terraform_script,
        [("forgejo_repository.example", "owner/repo")],
        tf_command="terraform",
        tf_label="Terraform",
    )
    terraform_text = terraform_script.read_text(encoding="utf-8")
    assert "TF=${HECKLE_TF_BIN:-terraform}" in terraform_text
    assert "OPENTOFU_BIN" not in terraform_text
    assert "TF_LABEL=Terraform" in terraform_text

    tofu_script = tmp_path / "adopt-tofu.sh"
    write_state_only_adopt_script(
        tofu_script,
        [("forgejo_repository.example", "owner/repo")],
    )
    tofu_text = tofu_script.read_text(encoding="utf-8")
    assert "TF=${HECKLE_TF_BIN:-tofu}" in tofu_text
    assert "OPENTOFU_BIN" not in tofu_text
    assert "TF_LABEL=OpenTofu" in tofu_text


def test_runner_preserves_explicit_binary_path_for_generated_project(tmp_path, monkeypatch):
    tool_dir = tmp_path / "tool dir"
    tool_dir.mkdir()
    terraform = _fake_binary(tool_dir, "terraform")
    monkeypatch.setenv("PATH", str(tool_dir))

    runner = TfRunner(str(terraform))

    assert runner.command_name == "terraform"
    assert runner.project_command == str(terraform.resolve())


def test_tf_runner_does_not_force_parallelism_one():
    from pathlib import Path
    import heckle.opentofu as opentofu

    source = Path(opentofu.__file__).read_text(encoding="utf-8")
    assert '"-parallelism=1"' not in source


@pytest.mark.parametrize("name", ["tofu", "terraform"])
@pytest.mark.parametrize(
    "version,accepted",
    [
        ("1.6.0", False),
        ("1.7.9", False),
        ("1.8.0", True),
        ("1.10.2", True),
        ("2.0.0", False),
        ("1.8.0-rc1", False),
        ("1.9.0-beta1", False),
        ("1.8.0+build.1", True),
    ],
)
def test_cli_version_bounds(tmp_path, monkeypatch, name, version, accepted):
    runner = TfRunner(str(_fake_binary(tmp_path, name)))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, json.dumps({"terraform_version": version}), ""
        ),
    )
    if accepted:
        assert runner.check_version(tmp_path) == version
    else:
        with pytest.raises(GenerationError) as caught:
            runner.check_version(tmp_path)
        assert version in str(caught.value)
        assert runner.executable in str(caught.value)
        assert ">= 1.8.0, < 2.0.0" in str(caught.value)
        assert "--tf or HECKLE_TF_BIN" in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        "private-secret",
        "[]",
        "{}",
        '{"terraform_version": 18}',
        '{"terraform_version": "private-secret"}',
    ],
)
def test_invalid_version_response_is_private(tmp_path, monkeypatch, payload):
    runner = TfRunner(str(_fake_binary(tmp_path, "tofu")))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, payload, "private-secret"),
    )
    with pytest.raises(GenerationError, match="invalid version response") as caught:
        runner.check_version(tmp_path)
    assert "private-secret" not in str(caught.value)


@pytest.mark.parametrize("failure", ["exit", "timeout", "oserror"])
def test_version_execution_failure(tmp_path, monkeypatch, failure):
    runner = TfRunner(str(_fake_binary(tmp_path, "tofu")))
    monkeypatch.setenv("TF_CLI_ARGS", "private-secret")
    monkeypatch.setenv("TF_LOG", "TRACE")

    def run(args, **kwargs):
        assert args == [runner.executable, "version", "-json"]
        assert kwargs["timeout"] == 30
        assert "TF_CLI_ARGS" not in kwargs["env"]
        assert "TF_LOG" not in kwargs["env"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30, output="private-secret")
        if failure == "oserror":
            raise OSError(13, "Permission denied")
        return subprocess.CompletedProcess(args, 1, "private-secret", "private-secret")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(GenerationError) as caught:
        runner.check_version(tmp_path)
    assert "private-secret" not in str(caught.value)
