import json
import os
from pathlib import Path
import subprocess

import pytest

from heckle.core.security import check_destination, publish, write_private_json
from heckle.errors import GenerationError
from heckle.hcl.parser import extract_top_blocks, parse_body, parse_resource
from heckle.opentofu import TfRunner
from heckle.providers.github.adapter import restore_snapshot
from fixtures import connection, model_for


def test_destination_must_be_disposable(tmp_path):
    destination = tmp_path / "output"
    destination.mkdir()
    with pytest.raises(GenerationError, match="marked"):
        check_destination(destination, force=True)
    (destination / ".heckle-generated").touch()
    assert check_destination(destination, force=True) == destination
    (destination / "secret.auto.tfvars").touch()
    with pytest.raises(GenerationError, match="tfvars"):
        check_destination(destination, force=True)


@pytest.mark.parametrize(
    "contents", ['terraform { backend "s3" {} }', 'terraform {\n backend\t"s3" {\n}\n}']
)
def test_backend_refuses_forced_replacement(tmp_path, contents):
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / ".heckle-generated").touch()
    (destination / "backend.tf").write_text(contents)
    with pytest.raises(GenerationError, match="backend"):
        check_destination(destination, force=True)


def test_symlinks_and_current_directory_are_refused(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(GenerationError, match="symbolic"):
        check_destination(link, force=True)
    monkeypatch.chdir(real)
    with pytest.raises(GenerationError):
        check_destination(real, force=True)
    with pytest.raises(GenerationError):
        check_destination(tmp_path, force=True)


def test_forced_replacement_rejects_nested_symlinks(tmp_path):
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / ".heckle-generated").touch()
    (destination / "outside").symlink_to(tmp_path)
    with pytest.raises(GenerationError, match="symbolic"):
        check_destination(destination, force=True)


def test_publish_failure_restores_original(tmp_path, monkeypatch):
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / ".heckle-generated").touch()
    (destination / "existing").write_text("preserve me")
    staged = tmp_path / "staged"
    staged.mkdir()
    original = Path.rename

    def fail(source, target):
        if source == staged:
            raise OSError("injected publication failure")
        return original(source, target)

    monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError):
        publish(staged, destination, force=True)
    assert (destination / "existing").read_text() == "preserve me"


def test_private_json_permissions(tmp_path):
    path = tmp_path / "private" / "snapshot.json"
    write_private_json(path, {"secret": "example"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_github_snapshot_rejects_path_traversal(tmp_path):
    model = model_for("github")
    model.extensions["github"]["files"]["../escape.json"] = {}
    with pytest.raises(GenerationError, match="Invalid path"):
        restore_snapshot(model, tmp_path / "snapshot")
    assert not (tmp_path / "escape.json").exists()


def runner(monkeypatch):
    monkeypatch.setattr("heckle.opentofu.shutil.which", lambda executable: "/usr/bin/true")
    return TfRunner(connection=connection("github"))


def test_tofu_environment_isolated(monkeypatch):
    for key in (
        "TF_LOG",
        "TF_LOG_PATH",
        "TF_CLI_ARGS",
        "TF_CLI_ARGS_plan",
        "TF_DATA_DIR",
        "GITHUB_OWNER",
        "TF_VAR_github_org",
        "TF_WORKSPACE",
    ):
        monkeypatch.setenv(key, "untrusted")
    env = runner(monkeypatch).environment()
    assert not any(
        key in env
        for key in (
            "TF_LOG",
            "TF_LOG_PATH",
            "TF_CLI_ARGS",
            "TF_CLI_ARGS_plan",
            "TF_DATA_DIR",
            "GITHUB_OWNER",
            "TF_VAR_github_org",
            "TF_WORKSPACE",
        )
    )
    assert env["GITHUB_TOKEN"] == "test-token"
    assert runner(monkeypatch).environment(managed_state=True)["TF_WORKSPACE"] == "untrusted"


@pytest.mark.parametrize(
    "arguments", [["apply"], ["import", "x", "1"], ["state", "rm", "x"], ["destroy"]]
)
def test_tofu_cannot_mutate_state(monkeypatch, tmp_path, arguments):
    with pytest.raises(GenerationError):
        runner(monkeypatch).run(arguments, tmp_path)


def test_provider_errors_are_not_logged(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="SECRET IN PROVIDER OUTPUT"
        ),
    )
    with pytest.raises(GenerationError) as error:
        runner(monkeypatch).run(["validate"], tmp_path)
    assert "SECRET" not in str(error.value)
    assert "SECRET" not in capsys.readouterr().out


def test_empty_blocks_do_not_swallow_following_resource(tmp_path):
    path = tmp_path / "generated.tf"
    path.write_text(
        'resource "example_one" "one" {}\nresource "example_two" "two" {\n name = "two"\n}\n'
    )
    blocks, _ = extract_top_blocks(path)
    assert len(blocks) == 2
    assert parse_resource(blocks[1]).body.attributes["name"] == '"two"'


def test_unterminated_hcl_is_rejected(tmp_path):
    path = tmp_path / "broken.tf"
    path.write_text('resource "example" "one" {\nname = "one"\n')
    with pytest.raises(GenerationError, match="Unterminated"):
        extract_top_blocks(path)
    with pytest.raises(GenerationError, match="Duplicate"):
        parse_body('name = "one"\nname = "two"\n')


def test_adoption_check_rejects_live_updates(monkeypatch, tmp_path):
    tofu = runner(monkeypatch)

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            payload = {
                "errored": False,
                "resource_changes": [
                    {
                        "address": 'module.repo.example.this["disabled-pr"]',
                        "mode": "managed",
                        "change": {"actions": ["update"], "importing": {"id": "alice/disabled-pr"}},
                    }
                ],
            }
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "run", fake_run)
    with pytest.raises(GenerationError, match="would change live resources"):
        tofu.adoption_check(tmp_path, {"secret_input": '"existing"'}, disposable=True)
    assert not (tmp_path / ".heckle-adoption.auto.tfvars").exists()
    assert not (tmp_path / ".heckle-adoption.tfplan").exists()


def test_adoption_check_accepts_import_noops(monkeypatch, tmp_path):
    tofu = runner(monkeypatch)

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            payload = {
                "errored": False,
                "resource_changes": [
                    {
                        "address": 'module.repo.example.this["repo"]',
                        "mode": "managed",
                        "change": {"actions": ["no-op"], "importing": {"id": "alice/repo"}},
                    }
                ],
            }
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "run", fake_run)
    assert tofu.adoption_check(tmp_path, disposable=True) == {
        "imports": 1,
        "no_op_resources": 1,
        "unsafe_changes": 0,
    }


def test_adoption_check_rejects_the_three_real_forgejo_update_regressions(monkeypatch, tmp_path):
    """Regression for the first live Forgejo rehearsal: imports must not hide updates."""
    tofu = runner(monkeypatch)
    changed = ["mig5/mig5-devops", "mig5/pdo_sqlcipher", "mig5/php-sqlcipher"]

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            payload = {
                "errored": False,
                "resource_changes": [
                    {
                        "address": f'module.forgejo_repository.forgejo_repository.this["{repo}"]',
                        "mode": "managed",
                        "change": {"actions": ["update"], "importing": {"id": repo}},
                    }
                    for repo in changed
                ],
            }
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "run", fake_run)
    with pytest.raises(GenerationError) as error:
        tofu.adoption_check(tmp_path, disposable=True)
    message = str(error.value)
    for repo in changed:
        assert repo in message


def test_adoption_error_reports_attribute_names_without_values(monkeypatch, tmp_path):
    tofu = runner(monkeypatch)

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            payload = {
                "errored": False,
                "resource_changes": [
                    {
                        "address": 'module.repo.example.this["repo"]',
                        "mode": "managed",
                        "change": {
                            "actions": ["update"],
                            "importing": {"id": "alice/repo"},
                            "before": {"allow_merge_commits": None, "description": "PRIVATE OLD"},
                            "after": {"allow_merge_commits": True, "description": "PRIVATE NEW"},
                            "after_unknown": {},
                        },
                    }
                ],
            }
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "run", fake_run)
    with pytest.raises(GenerationError) as error:
        tofu.adoption_check(tmp_path, disposable=True)
    message = str(error.value)
    assert "allow_merge_commits" in message
    assert "description" in message
    assert "PRIVATE OLD" not in message
    assert "PRIVATE NEW" not in message


def test_state_only_adoption_rehearsal_imports_then_requires_noop(monkeypatch, tmp_path):
    tofu = runner(monkeypatch)
    calls = []

    def fake_state_import(root, address, import_id):
        calls.append((address, import_id))
        (root / "terraform.tfstate").write_text("temporary state")

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            payload = {
                "errored": False,
                "resource_changes": [
                    {
                        "address": 'module.repo.example.this["repo"]',
                        "mode": "managed",
                        "change": {"actions": ["no-op"]},
                    }
                ],
            }
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "_state_import", fake_state_import)
    monkeypatch.setattr(tofu, "run", fake_run)
    result = tofu.state_only_adoption_check(
        tmp_path,
        [('module.repo.example.this["repo"]', "alice/repo")],
        disposable=True,
    )
    assert result == {"imports": 1, "no_op_resources": 1, "unsafe_changes": 0}
    assert calls == [('module.repo.example.this["repo"]', "alice/repo")]
    assert not (tmp_path / "terraform.tfstate").exists()
    assert not (tmp_path / ".heckle-adoption.tfplan").exists()


def test_state_only_adoption_rehearsal_still_rejects_real_changes(monkeypatch, tmp_path):
    tofu = runner(monkeypatch)

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            payload = {
                "errored": False,
                "resource_changes": [
                    {
                        "address": 'module.repo.example.this["repo"]',
                        "mode": "managed",
                        "change": {
                            "actions": ["update"],
                            "before": {"private": False},
                            "after": {"private": True},
                            "after_unknown": {},
                        },
                    }
                ],
            }
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "_state_import", lambda *args: None)
    monkeypatch.setattr(tofu, "run", fake_run)
    with pytest.raises(GenerationError, match="private"):
        tofu.state_only_adoption_check(
            tmp_path,
            [('module.repo.example.this["repo"]', "alice/repo")],
            disposable=True,
        )


def _forgejo_pr_drift_payload(*, has_pull_requests=False, extra_after=None):
    before = {
        "has_pull_requests": has_pull_requests,
        "allow_merge_commits": False,
        "allow_rebase": False,
        "allow_rebase_explicit": False,
        "allow_squash_merge": False,
        "default_merge_style": "merge",
        "ignore_whitespace_conflicts": False,
        "private": False,
    }
    after = {
        **before,
        "allow_merge_commits": True,
        "allow_rebase": True,
        "allow_rebase_explicit": True,
        "allow_squash_merge": True,
    }
    if extra_after:
        after.update(extra_after)
    return {
        "errored": False,
        "resource_changes": [
            {
                "address": 'module.forgejo_repository.forgejo_repository.profile_deadbeef00["mig5/repo"]',
                "mode": "managed",
                "change": {
                    "actions": ["update"],
                    "before": before,
                    "after": after,
                    "after_unknown": {},
                },
            }
        ],
    }


def test_forgejo_pr_provider_behaviour_is_recognised(monkeypatch, tmp_path):
    from heckle.providers.forgejo.adapter import ForgejoProvider

    tofu = runner(monkeypatch)
    payload = _forgejo_pr_drift_payload()

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "_state_import", lambda *args: None)
    monkeypatch.setattr(tofu, "run", fake_run)
    result = tofu.state_only_adoption_check(
        tmp_path,
        [
            (
                'module.forgejo_repository.forgejo_repository.profile_deadbeef00["mig5/repo"]',
                "mig5/repo",
            )
        ],
        disposable=True,
        known_behaviour=ForgejoProvider().known_provider_behaviour,
    )
    recognised = result["known_provider_behaviour_changes"]
    assert len(recognised) == 1
    assert recognised[0]["guards"] == {"has_pull_requests": False}
    assert "allow_merge_commits" in recognised[0]["attributes"]
    assert result["unsafe_changes"] == 0


def test_forgejo_pr_provider_behaviour_is_not_recognised_when_prs_are_enabled(
    monkeypatch, tmp_path
):
    from heckle.providers.forgejo.adapter import ForgejoProvider

    tofu = runner(monkeypatch)
    payload = _forgejo_pr_drift_payload(has_pull_requests=True)

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "_state_import", lambda *args: None)
    monkeypatch.setattr(tofu, "run", fake_run)
    with pytest.raises(GenerationError, match="allow_merge_commits"):
        tofu.state_only_adoption_check(
            tmp_path,
            [
                (
                    'module.forgejo_repository.forgejo_repository.profile_deadbeef00["mig5/repo"]',
                    "mig5/repo",
                )
            ],
            disposable=True,
            known_behaviour=ForgejoProvider().known_provider_behaviour,
        )


def test_forgejo_known_pr_behaviour_does_not_hide_real_changes(monkeypatch, tmp_path):
    from heckle.providers.forgejo.adapter import ForgejoProvider

    tofu = runner(monkeypatch)
    payload = _forgejo_pr_drift_payload(extra_after={"private": True})

    def fake_run(arguments, cwd, **kwargs):
        if arguments[0] == "show":
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "_state_import", lambda *args: None)
    monkeypatch.setattr(tofu, "run", fake_run)
    with pytest.raises(GenerationError, match="private"):
        tofu.state_only_adoption_check(
            tmp_path,
            [
                (
                    'module.forgejo_repository.forgejo_repository.profile_deadbeef00["mig5/repo"]',
                    "mig5/repo",
                )
            ],
            disposable=True,
            known_behaviour=ForgejoProvider().known_provider_behaviour,
        )


def _fake_tofu_for_adopt_script(tmp_path, payload):
    plan_json = tmp_path / "fake-plan.json"
    plan_json.write_text(json.dumps(payload))
    tofu = tmp_path / "fake-tofu"
    tofu.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys\n"
        "cmd = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "if cmd == 'state':\n"
        "    raise SystemExit(0)\n"
        "if cmd == 'plan':\n"
        "    pathlib.Path('adoption.tfplan').write_text('fake')\n"
        "    raise SystemExit(2)\n"
        "if cmd == 'show':\n"
        "    print(pathlib.Path(os.environ['FAKE_PLAN_JSON']).read_text())\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(0)\n"
    )
    tofu.chmod(0o755)
    return tofu, plan_json


def test_generated_adopt_script_accepts_only_rehearsed_known_provider_behaviour(tmp_path):
    from heckle.pipeline import write_state_only_adopt_script

    address = 'module.forgejo_repository.forgejo_repository.profile_deadbeef00["mig5/repo"]'
    payload = _forgejo_pr_drift_payload()
    payload["resource_changes"][0]["address"] = address
    tofu, plan_json = _fake_tofu_for_adopt_script(tmp_path, payload)
    script = tmp_path / "adopt.sh"
    write_state_only_adopt_script(
        script,
        [(address, "mig5/repo")],
        [
            {
                "address": address,
                "actions": ["update"],
                "attributes": [
                    "allow_merge_commits",
                    "allow_rebase",
                    "allow_rebase_explicit",
                    "allow_squash_merge",
                ],
                "reason": "known provider behaviour while has_pull_requests remains disabled",
                "guards": {"has_pull_requests": False},
            }
        ],
    )
    env = os.environ.copy()
    env.update({"HECKLE_TF_BIN": str(tofu), "FAKE_PLAN_JSON": str(plan_json)})
    result = subprocess.run([str(script)], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Known provider behaviour" in result.stdout
    assert "Heckle cannot determine whether applying them is appropriate" in result.stdout


def test_generated_adopt_script_rejects_extra_live_change(tmp_path):
    from heckle.pipeline import write_state_only_adopt_script

    address = 'module.forgejo_repository.forgejo_repository.profile_deadbeef00["mig5/repo"]'
    payload = _forgejo_pr_drift_payload(extra_after={"private": True})
    payload["resource_changes"][0]["address"] = address
    tofu, plan_json = _fake_tofu_for_adopt_script(tmp_path, payload)
    script = tmp_path / "adopt.sh"
    write_state_only_adopt_script(
        script,
        [(address, "mig5/repo")],
        [
            {
                "address": address,
                "actions": ["update"],
                "attributes": [
                    "allow_merge_commits",
                    "allow_rebase",
                    "allow_rebase_explicit",
                    "allow_squash_merge",
                ],
                "reason": "known provider behaviour while has_pull_requests remains disabled",
                "guards": {"has_pull_requests": False},
            }
        ],
    )
    env = os.environ.copy()
    env.update({"HECKLE_TF_BIN": str(tofu), "FAKE_PLAN_JSON": str(plan_json)})
    result = subprocess.run([str(script)], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 2
    assert "unexpected attributes private" in result.stderr
    assert "DO NOT APPLY IT" in result.stderr


def test_adoption_checks_do_not_force_single_threaded_parallelism(monkeypatch, tmp_path):
    tofu = runner(monkeypatch)
    calls = []

    def fake_run(arguments, cwd, **kwargs):
        calls.append(list(arguments))
        if arguments[0] == "show":
            payload = {"errored": False, "resource_changes": []}
            return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps(payload))
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(tofu, "run", fake_run)
    tofu.adoption_check(tmp_path, disposable=True)
    plan = next(args for args in calls if args[0] == "plan")
    assert not any(arg.startswith("-parallelism=") for arg in plan)
