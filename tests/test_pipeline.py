import json
from pathlib import Path
import pytest

from heckle.cli import build_parser, main
from heckle.core.inventory import load_inventory
from heckle.core.model import FORGES, Observation
from heckle.errors import GenerationError, UnsafeAdoptionError, UnsafeChange
from heckle.pipeline import Pipeline, Options
from fixtures import connection, model_for, FakeRunner, FakeForge


@pytest.mark.parametrize("forge", FORGES)
def test_all_backends_generate_without_runtime_worker(forge, tmp_path):
    runner = FakeRunner()
    output = tmp_path / forge
    Pipeline(connection(forge), Options(output), runner=runner, forge=FakeForge(model_for(forge)), progress=lambda _: None).run()
    text = "\n".join(p.read_text() for p in output.rglob("*.tf"))
    assert "import {" in text
    assert 'source = "./modules/' in text
    assert "private-value" not in text
    assert "test-token" not in text
    if forge == "github":
        assert "nonsensitive(keys(" in text
    else:
        assert "profiles = {" in text
        assert "for_each = toset([for key, profile in var.profiles" in text
    assert not any(token in text for token in ['data "external"', 'provisioner ', 'file("inventory', 'python'])
    assert not (output / ".terraform").exists()
    assert not list(output.rglob("*.tfstate"))
    assert runner.calls == ["version", "hydrate", "format", "validate", "adoption check"]
    report = json.loads((output / ".generation/report.json").read_text())
    assert report["validation"] == "passed"  # fake runner's claimed result only
    assert report["state_changes_performed"] is False
    assert report["adoption_safety"]["status"] == "passed_declarative_import"
    assert report["source"]["forge"] == forge
    assert not list(tmp_path.glob(".*-build-*"))


@pytest.mark.parametrize("forge", FORGES)
def test_inventory_does_not_need_tofu(forge, tmp_path):
    runner = FakeRunner()
    output = tmp_path / forge
    Pipeline(connection(forge), Options(output), runner=runner, forge=FakeForge(model_for(forge)), progress=lambda _: None).run(inventory_only=True)
    assert load_inventory(output).source.forge == forge
    assert not runner.calls
    assert main(["coverage", str(output), "--json"]) == 0


def test_failures_not_silently_empty(tmp_path):
    model = model_for("gitlab")
    model.observations.append(Observation("example:members", "permission_denied", http_status=403))
    runner = FakeRunner()
    with pytest.raises(GenerationError, match="permission/API"):
        Pipeline(connection("gitlab"), Options(tmp_path / "fail"), runner=runner, forge=FakeForge(model)).run()
    assert runner.calls == ["version"]
    assert not (tmp_path / "fail").exists()
    Pipeline(connection("gitlab"), Options(tmp_path / "allowed", allow_partial=True), runner=runner, forge=FakeForge(model)).run()
    assert (tmp_path / "allowed/.generation/report.json").is_file()


def test_failed_validation_never_replaces_output(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / ".heckle-generated").touch()
    (output / "preserve.txt").write_text("keep")
    runner = FakeRunner()
    def fail(*a, **kw):
        raise GenerationError("validation failed")
    runner.validate = fail
    with pytest.raises(GenerationError):
        Pipeline(connection("gitlab"), Options(output, force=True), runner=runner, forge=FakeForge(model_for("gitlab"))).run()
    assert (output / "preserve.txt").read_text() == "keep"


def test_snapshot_identity_must_match(tmp_path):
    from heckle.core.inventory import save_inventory
    saved = tmp_path / "saved"
    save_inventory(saved, model_for("forgejo"))
    with pytest.raises(GenerationError, match="exactly match"):
        Pipeline(connection("gitlab"), Options(tmp_path / "output", from_inventory=saved), runner=FakeRunner()).run()


@pytest.mark.parametrize("forge", FORGES)
def test_cli_syntax(forge):
    flag = "--group" if forge == "gitlab" else "--org"
    args = build_parser().parse_args(["generate", forge, flag, "example", "--url", "https://forge.example.test"])
    assert args.forge == forge and args.scope == "example"


@pytest.mark.parametrize('forge', ['github', 'gitlab', 'gitea', 'forgejo'])
def test_explicit_state_filter_keeps_correct_count(tmp_path, forge):
    import re
    from dataclasses import asdict
    from heckle.core.security import write_private_json
    model = model_for(forge)
    first = tmp_path / 'initial'
    Pipeline(connection(forge), Options(first, validate=False), runner=FakeRunner(), forge=FakeForge(model), progress=lambda text: None).run()
    target = re.search(r'(?m)^\s*to = (.+)$', (first / 'imports.tf').read_text()).group(1)
    second = tmp_path / 'filtered'
    test_runner = FakeRunner()
    test_runner.state_addresses = lambda root: {target}
    Pipeline(connection(forge), Options(second, state_root=first, validate=False), runner=test_runner, forge=FakeForge(model), progress=lambda text: None).run()
    report = json.loads((second / '.generation/report.json').read_text())
    assert report['imports_already_in_state'] == 1
    assert f'to = {target}\n' not in (second / 'imports.tf').read_text()


def test_unsafe_import_blocks_fall_back_to_state_only_adoption(tmp_path):
    class FallbackRunner(FakeRunner):
        def adoption_check(self, root, validation_inputs=None, *, disposable=False, known_behaviour=None):
            self.calls.append("adoption check")
            raise UnsafeAdoptionError([
                UnsafeChange(
                    'module.forgejo_repository.forgejo_repository.profile_deadbeef00["example/api"]',
                    ("update",),
                    ("allow_merge_commits", "default_merge_style"),
                )
            ])

    runner = FallbackRunner()
    output = tmp_path / "forgejo"
    Pipeline(
        connection("forgejo"),
        Options(output),
        runner=runner,
        forge=FakeForge(model_for("forgejo")),
        progress=lambda _: None,
    ).run()

    assert not (output / "imports.tf").exists()
    assert (output / "adopt.sh").is_file()
    assert (output / "adopt.sh").stat().st_mode & 0o111
    adopt = (output / "adopt.sh").read_text()
    assert '"$TF" apply' not in adopt
    assert '"$TF" import -input=false' in adopt
    assert "-detailed-exitcode" in adopt
    report = json.loads((output / ".generation/report.json").read_text())
    safety = report["adoption_safety"]
    assert safety["status"] == "passed_state_only_import"
    assert safety["mode"] == "state_only_import"
    assert safety["declarative_changes"][0]["attributes"] == [
        "allow_merge_commits", "default_merge_style"
    ]
    assert (output / ".generation/imports.json").is_file()
    assert runner.calls == [
        "version", "hydrate", "format", "validate", "adoption check", "state-only adoption check"
    ]


def test_known_provider_behaviour_uses_state_only_adoption(tmp_path):
    class KnownBehaviourRunner(FakeRunner):
        def adoption_check(self, root, validation_inputs=None, *, disposable=False, known_behaviour=None):
            self.calls.append("adoption check")
            return {
                "imports": 1,
                "no_op_resources": 0,
                "unsafe_changes": 0,
                "known_provider_behaviour_changes": [
                    {
                        "address": 'module.forgejo_repository.forgejo_repository.profile_deadbeef00["example/api"]',
                        "actions": ["update"],
                        "attributes": ["allow_merge_commits"],
                        "reason": "known provider behaviour while has_pull_requests remains disabled",
                        "guards": {"has_pull_requests": False},
                    }
                ],
            }

        def state_only_adoption_check(self, root, imports, validation_inputs=None, *, disposable=False, known_behaviour=None):
            self.calls.append("state-only adoption check")
            return {
                "imports": len(imports),
                "no_op_resources": 0,
                "unsafe_changes": 0,
                "known_provider_behaviour_changes": [
                    {
                        "address": imports[0][0],
                        "actions": ["update"],
                        "attributes": ["allow_merge_commits"],
                        "reason": "known provider behaviour while has_pull_requests remains disabled",
                        "guards": {"has_pull_requests": False},
                    }
                ],
            }

    runner = KnownBehaviourRunner()
    output = tmp_path / "forgejo-known-behaviour"
    Pipeline(
        connection("forgejo"),
        Options(output),
        runner=runner,
        forge=FakeForge(model_for("forgejo")),
        progress=lambda _: None,
    ).run()

    assert not (output / "imports.tf").exists()
    adopt = (output / "adopt.sh").read_text()
    assert "EXPECTED_PROVIDER_BEHAVIOUR=" in adopt
    assert "Known provider behaviour" in adopt
    report = json.loads((output / ".generation/report.json").read_text())
    assert report["adoption_safety"]["status"] == "passed_state_only_import_with_known_provider_behaviour"
    assert report["adoption_safety"]["known_provider_behaviour_changes"][0]["guards"] == {"has_pull_requests": False}


def test_keep_workdir_retains_private_workspace_after_failure(tmp_path):
    output = tmp_path / "gitlab-output"
    messages: list[str] = []
    runner = FakeRunner()

    def fail(root, *args, **kwargs):
        runner.last_cwd = Path(root).resolve()
        runner.last_arguments = ("plan", "-input=false", "-no-color")
        runner.project_command = "tofu"
        raise GenerationError("validation failed")

    runner.validate = fail
    with pytest.raises(GenerationError, match="validation failed"):
        Pipeline(
            connection("gitlab"),
            Options(output, keep_workdir=True),
            runner=runner,
            forge=FakeForge(model_for("gitlab")),
            progress=messages.append,
        ).run()

    workdirs = list(tmp_path.glob(".gitlab-output-build-*"))
    assert len(workdirs) == 1
    work = workdirs[0]
    assert work.stat().st_mode & 0o777 == 0o700
    assert (work / "result").is_dir()
    text = "\n".join(messages)
    assert str(work) in text
    assert "The failing command was:" in text
    assert "tofu init -backend=false -input=false -no-color" in text
    assert "tofu plan -input=false -no-color" in text


def test_cli_accepts_keep_workdir():
    args = build_parser().parse_args([
        "generate", "gitlab", "--user", "alice", "--keep-workdir"
    ])
    assert args.keep_workdir is True


@pytest.mark.parametrize("replay", [False, True])
def test_version_failure_precedes_discovery_and_inventory_replay(tmp_path, replay):
    runner = FakeRunner()
    messages = []

    def reject(cwd):
        raise GenerationError("Unsupported OpenTofu version 1.6.0")

    runner.check_version = reject
    pipeline = Pipeline(
        connection("github"),
        Options(tmp_path / "output", from_inventory=tmp_path / "missing" if replay else None),
        runner=runner, forge=FakeForge(model_for("github")), progress=messages.append,
    )

    def unexpected_discovery(work):
        pytest.fail("Discovery must not run with an unsupported CLI")

    pipeline.discover = unexpected_discovery
    with pytest.raises(GenerationError, match="Unsupported OpenTofu version 1.6.0"):
        pipeline.run()
    assert not runner.calls
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".*-build-*"))


def test_bootstrap_message_precedes_hydration(tmp_path):
    messages = []
    runner = FakeRunner()

    def fail(provider, plan, workspace):
        assert "only provider.tf and versions.tf" in messages[-2]
        raise GenerationError("init failed")

    runner.hydrate = fail
    with pytest.raises(GenerationError, match="init failed"):
        Pipeline(connection("github"), Options(tmp_path / "output"), runner=runner,
                 forge=FakeForge(model_for("github")), progress=messages.append).run()
