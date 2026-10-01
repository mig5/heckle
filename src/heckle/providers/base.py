from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from heckle.tf_version import TF_VERSION_CONSTRAINT
from heckle.core.compilation import CompiledProject, ImportPlan
from heckle.errors import KnownProviderBehaviour, UnsafeChange
from heckle.core.model import ForgeModel, Source
from heckle.hcl.render import hcl_string
from heckle.hcl.types import Resource
from heckle.hcl.schema import ProviderSchema


@dataclass(frozen=True)
class ProviderSpec:
    forge: str
    local_name: str
    source: str
    version: str
    url_attribute: str
    token_environment: str

    def versions_hcl(self, *, child: bool = False) -> str:
        pin = "" if child else f'      version = "= {self.version}"\n'
        required = "" if child else f'  required_version = "{TF_VERSION_CONSTRAINT}"\n'
        return (f'terraform {{\n{required}  required_providers {{\n    {self.local_name} = {{\n'
                f'      source = {hcl_string(self.source)}\n{pin}    }}\n  }}\n}}\n')

    def configuration_hcl(self, source: Source) -> str:
        url = source.url
        if source.forge == "gitlab":
            url += "/api/v4/"
        body = f"  {self.url_attribute} = {hcl_string(url)}\n"
        if source.forge == "github":
            body += f"  owner = {hcl_string(source.scope)}\n  parallel_requests = true\n"
        return f'provider "{self.local_name}" {{\n{body}}}\n'


class ProviderAdapter(ABC):
    spec: ProviderSpec

    @abstractmethod
    def plan(self, model: ForgeModel, workspace: Path) -> ImportPlan:
        raise NotImplementedError

    @abstractmethod
    def compile(self, plan: ImportPlan, resources: list[Resource], schema: ProviderSchema) -> CompiledProject:
        raise NotImplementedError

    @abstractmethod
    def render(self, project: CompiledProject, destination: Path, *, prevent_destroy: bool, split_teams: bool) -> None:
        raise NotImplementedError

    def bootstrap_extra(self, plan: ImportPlan) -> str:
        return ""

    def imports(self, plan: ImportPlan) -> list[tuple[str, str]]:
        return [(c.flat_address, c.import_id) for c in plan.candidates]

    def rendered_imports(self, project: CompiledProject) -> list[tuple[str, str]]:
        """Return final resource addresses and remote IDs for one-time adoption."""
        return [
            (item.address, item.candidate.import_id)
            for item in project.resources
            if item.address not in project.skip_imports
        ]

    def known_provider_behaviour(self, change: UnsafeChange) -> KnownProviderBehaviour | None:
        """Recognise narrowly-scoped, version-specific provider plan behaviour.

        The default is deliberately strict. Recognition is descriptive, not a safety
        recommendation; unrecognised changes remain adoption failures.
        """
        return None
