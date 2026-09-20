from dataclasses import asdict, replace
import pytest

from heckle.core.inventory import save_inventory
from heckle.core.model import FORGES, ForgeModel, Source
from heckle.errors import GenerationError
from heckle.pipeline import Pipeline, Options
from fixtures import model_for, connection, FakeRunner


@pytest.mark.parametrize("forge", FORGES)
def test_old_inventories_have_unambiguous_org_or_group_identity(forge):
    value = model_for(forge).to_dict()
    value["schema_version"] = 1
    value["source"].pop("namespace_type")
    loaded = ForgeModel.from_dict(value)
    assert loaded.source.namespace_type == ("group" if forge == "gitlab" else "organization")
    assert loaded.to_dict()["schema_version"] == 2


@pytest.mark.parametrize("forge", FORGES)
def test_same_named_personal_and_org_snapshots_are_not_interchangeable(forge, tmp_path):
    source = connection(forge)
    save_inventory(tmp_path / "snapshot", model_for(forge))
    conn = replace(source, source=replace(source.source, namespace_type="user"))
    with pytest.raises(GenerationError, match="namespace type"):
        Pipeline(conn, Options(tmp_path / "out", from_inventory=tmp_path / "snapshot"), runner=FakeRunner()).run()


def test_unresolved_me_cannot_be_saved():
    model = ForgeModel(Source("github", "https://api.github.com", "@me", "user"))
    with pytest.raises(GenerationError, match="Resolve"):
        model.to_dict()


def test_namespace_type_is_required_in_new_snapshot_format():
    value = model_for("gitlab").to_dict()
    value["source"].pop("namespace_type")
    with pytest.raises(GenerationError, match="namespace_type"):
        ForgeModel.from_dict(value)


@pytest.mark.parametrize("scope", ["a/b", " a", "alice user"])
def test_user_selector_does_not_accept_a_group_path(scope):
    with pytest.raises(GenerationError):
        Source("gitlab", "https://gitlab.example.test", scope, "user")
