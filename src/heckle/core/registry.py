"""Explicit built-in handler registration."""
from __future__ import annotations

from dataclasses import dataclass
from heckle.forges.base import ForgeAdapter
from heckle.providers.base import ProviderAdapter
from heckle.forges.github.adapter import GitHubForge
from heckle.forges.gitlab.discovery import GitLabForge
from heckle.forges.gitea.discovery import GiteaForge
from heckle.forges.forgejo.discovery import ForgejoForge
from heckle.providers.github.adapter import GitHubProvider
from heckle.providers.gitlab.adapter import GitLabProvider
from heckle.providers.gitea.adapter import GiteaProvider
from heckle.providers.forgejo.adapter import ForgejoProvider
from heckle.errors import GenerationError


@dataclass(frozen=True)
class Backend:
    forge: type[ForgeAdapter]
    provider: type[ProviderAdapter]


BACKENDS = {
    "github": Backend(GitHubForge, GitHubProvider),
    "gitlab": Backend(GitLabForge, GitLabProvider),
    "gitea": Backend(GiteaForge, GiteaProvider),
    "forgejo": Backend(ForgejoForge, ForgejoProvider),
}


def backend(name: str) -> Backend:
    try:
        return BACKENDS[name]
    except KeyError as exc:
        raise GenerationError(f"Unknown forge: {name}") from exc
