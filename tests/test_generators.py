from collections import OrderedDict

from heckle.providers.github.renderers.common import (
    provider_pins_literal_owner,
    unique_render_names,
)
from heckle.providers.github.renderers.project import emit_webhook_variables
from heckle.providers.github.model.types import Model, WebhookVariable


def test_provider_owner_is_literal():
    assert provider_pins_literal_owner('provider "github" {\n  owner = "acme"\n}\n', "acme")
    assert not provider_pins_literal_owner(
        'provider "github" {\n  owner = var.github_org\n}\n', "acme"
    )


def test_render_name_collisions_are_disambiguated():
    names = unique_render_names(["a/b", "a-b"], local_prefix="repo", file_prefix="repository-")
    assert len({value[0] for value in names.values()}) == 2
    assert len({value[1] for value in names.values()}) == 2


def test_webhook_variable_has_no_empty_default():
    model = Model(org="acme")
    model.webhook_variables["webhook_org_example_1"] = WebhookVariable(
        name="webhook_org_example_1",
        description="Webhook URL",
        scope="organization",
        hook_id="1",
        value="https://example.test/hook",
    )
    text = emit_webhook_variables(model)
    assert "sensitive   = true" in text
    assert "default" not in text


def test_empty_model_modules_still_export_required_outputs():
    from heckle.providers.github.renderers.members import emit_members_module
    from heckle.providers.github.renderers.repositories import emit_repository_module
    from heckle.providers.github.renderers.teams import emit_teams_module

    model = Model(org="empty")
    assert 'output "usernames"' in emit_members_module(model)
    teams = emit_teams_module(model)
    assert "team_ids      = {}" in teams
    assert 'output "ids"' in teams
    repositories = emit_repository_module(model, prevent_destroy=True)
    assert "value = {}" in repositories


def test_github_provider_enables_parallel_requests():
    from heckle.compatibility import provider_spec
    from heckle.core.model import Source

    text = provider_spec("github").configuration_hcl(
        Source(forge="github", url="https://api.github.com", scope="acme")
    )
    assert "parallel_requests = true" in text
    assert "parallel_requests = false" not in text
