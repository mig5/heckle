from __future__ import annotations
from collections import OrderedDict
from pathlib import Path
from heckle.errors import GenerationError
from heckle.hcl.render import hcl_string
from heckle.providers.base import ProviderSpec
from heckle.core.model import Source
from heckle.providers.github.model.types import Model
from heckle.providers.github.renderers.access import emit_access_module
from heckle.providers.github.renderers.common import emit_root_modules, render_local, render_merged_local, render_root_model, unique_render_names
from heckle.providers.github.renderers.members import emit_members_module
from heckle.providers.github.renderers.organization import emit_organization_module
from heckle.providers.github.renderers.repositories import emit_repository_module
from heckle.providers.github.renderers.teams import emit_teams_module

def emit_webhook_variables(model: Model) -> str:
    blocks: list[str] = []
    for name, variable in sorted(model.webhook_variables.items()):
        blocks.append(
            f'variable "{name}" {{\n'
            f"  description = {hcl_string(variable.description)}\n"
            "  type        = string\n"
            "  sensitive   = true\n"
            "}\n"
        )
    return "\n".join(blocks)

def emit_imports(model: Model) -> str:
    unresolved = [r.old_address for r in model.imports if not r.new_address]
    if unresolved:
        raise GenerationError(
            "no new import address was assigned for:\n  " + "\n  ".join(unresolved)
        )
    blocks = []
    for record in sorted(model.imports, key=lambda r: str(r.new_address)):
        blocks.append(
            "import {\n"
            f"  to = {record.new_address}\n"
            f"  id = {hcl_string(record.import_id)}\n"
            "}\n"
        )
    return "\n".join(blocks)

def write_domain_files(out: Path, model: Model, *, split_teams: bool, personal: bool = False) -> None:
    """Write the canonical model directly, without an intermediate split pass."""
    if personal:
        (out / "github.tf").write_text("locals {\n  github = { repositories = local.github_repositories }\n}\n", encoding="utf-8")
    else:
        (out / "github.tf").write_text(render_root_model(), encoding="utf-8")
        (out / "github-organization.tf").write_text(render_local("github_organization", model.organization), encoding="utf-8")
        (out / "github-members.tf").write_text(render_local("github_members", model.members), encoding="utf-8")

    repository_names = unique_render_names(
        model.repositories, local_prefix="github_repository", file_prefix="repository-"
    )
    repository_locals: list[str] = []
    for repo, config in sorted(model.repositories.items(), key=lambda item: item[0].casefold()):
        name, filename = repository_names[repo]
        repository_locals.append(name)
        value = OrderedDict([(repo, config)])
        (out / filename).write_text(render_local(name, value), encoding="utf-8")
    (out / "github-repositories.tf").write_text(
        render_merged_local("github_repositories", repository_locals),
        encoding="utf-8",
    )

    if personal:
        return
    if split_teams:
        team_names = unique_render_names(
            model.teams, local_prefix="github_team", file_prefix="team-"
        )
        team_locals: list[str] = []
        for team, config in sorted(model.teams.items(), key=lambda item: item[0].casefold()):
            name, filename = team_names[team]
            team_locals.append(name)
            value = OrderedDict([(team, config)])
            (out / filename).write_text(render_local(name, value), encoding="utf-8")
        (out / "github-teams.tf").write_text(
            render_merged_local("github_teams", team_locals), encoding="utf-8"
        )
    else:
        (out / "github-teams.tf").write_text(
            render_local("github_teams", model.teams), encoding="utf-8"
        )


def write_output(out: Path, model: Model, *, prevent_destroy: bool, split_teams: bool, spec: ProviderSpec, source: Source) -> None:
    out.mkdir(parents=True, exist_ok=False)
    (out / "versions.tf").write_text(spec.versions_hcl(), encoding="utf-8")
    (out / "provider.tf").write_text(spec.configuration_hcl(source), encoding="utf-8")
    personal = source.namespace_type == "user"
    modules = ('module "repositories" {\n  source = "./modules/repositories"\n'
               '  repositories = local.github.repositories\n  team_ids = {}\n}\n') if personal else emit_root_modules()
    (out / "modules.tf").write_text(modules, encoding="utf-8")
    write_domain_files(out, model, split_teams=split_teams, personal=personal)
    (out / "imports.tf").write_text(emit_imports(model), encoding="utf-8")
    variables = emit_webhook_variables(model)
    if variables:
        (out / "variables.tf").write_text(variables, encoding="utf-8")
    emitters = {
        "members": emit_members_module,
        "teams": emit_teams_module,
        "access": emit_access_module,
        "organization": emit_organization_module,
        "repositories": lambda m: emit_repository_module(m, prevent_destroy),
    }
    if personal:
        emitters = {"repositories": emitters["repositories"]}
    for name, emitter in emitters.items():
        module = out / "modules" / name
        module.mkdir(parents=True)
        (module / "versions.tf").write_text(spec.versions_hcl(child=True), encoding="utf-8")
        (module / "main.tf").write_text(emitter(model), encoding="utf-8")
