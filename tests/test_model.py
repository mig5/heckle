from collections import OrderedDict

from heckle.hcl.types import Body
from heckle.providers.github.model.builder import compute_team_levels
from heckle.hcl.types import Expr
from heckle.providers.github.model.types import Model


def test_compute_team_levels():
    model = Model(org="example")
    model.teams["parent"] = OrderedDict(settings=OrderedDict())
    model.teams["child"] = OrderedDict(settings=OrderedDict(parent_team_key=Expr('"parent"')))
    assert compute_team_levels(model) == {"parent": 0, "child": 1}


def test_domain_model_builder_handles_membership(tmp_path):
    import json
    from collections import OrderedDict

    from heckle.hcl.types import Body, Resource
    from heckle.providers.github.model.builder import DomainModelBuilder
    from heckle.providers.github.model.types import ImportRecord

    (tmp_path / "repositories.json").write_text("[]")
    (tmp_path / "teams.json").write_text("[]")
    resource = Resource(
        resource_type="github_membership",
        name="alice",
        body=Body(attributes=OrderedDict(username='"alice"', role='"member"')),
        lifecycle_raw=None,
        source=tmp_path / "generated.tf",
    )
    record = ImportRecord(
        old_address=resource.address,
        import_id="acme:alice",
    )
    model = DomainModelBuilder("acme", tmp_path).build([resource], [record])
    assert "alice" in model.members
    assert record.new_address == 'module.members.github_membership.this["alice"]'
