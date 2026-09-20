from pathlib import Path

from heckle.cli import build_parser
from heckle.opentofu import TfRunner
from heckle.pipeline import write_state_only_adopt_script


def _fake_binary(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_tf_option_selects_terraform():
    args = build_parser().parse_args([
        "generate", "forgejo", "--user", "mig5", "--tf", "terraform"
    ])
    assert args.tf == "terraform"


def test_tofu_option_is_not_accepted():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args([
            "generate", "forgejo", "--user", "mig5", "--tofu", "/opt/tofu"
        ])


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
    assert 'TF=${HECKLE_TF_BIN:-terraform}' in terraform_text
    assert "OPENTOFU_BIN" not in terraform_text
    assert 'TF_LABEL=Terraform' in terraform_text

    tofu_script = tmp_path / "adopt-tofu.sh"
    write_state_only_adopt_script(
        tofu_script,
        [("forgejo_repository.example", "owner/repo")],
    )
    tofu_text = tofu_script.read_text(encoding="utf-8")
    assert 'TF=${HECKLE_TF_BIN:-tofu}' in tofu_text
    assert "OPENTOFU_BIN" not in tofu_text
    assert 'TF_LABEL=OpenTofu' in tofu_text


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
