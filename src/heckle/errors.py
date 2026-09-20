from __future__ import annotations

from dataclasses import dataclass, field


class GitHubAPIError(RuntimeError):
    def __init__(
        self,
        method: str,
        url: str,
        status: int,
        message: str,
        accepted_permissions: str | None = None,
    ) -> None:
        super().__init__(f"{method} {url}: HTTP {status}: {message}")
        self.method = method
        self.url = url
        self.status = status
        self.message = message
        self.accepted_permissions = accepted_permissions


class GenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class UnsafeChange:
    address: str
    actions: tuple[str, ...]
    attributes: tuple[str, ...] = ()
    before: object = field(default=None, repr=False, compare=False)
    after: object = field(default=None, repr=False, compare=False)

    def summary(self) -> str:
        action = ",".join(self.actions) or "unknown"
        if self.attributes:
            return f"{self.address}: {action} ({', '.join(self.attributes[:8])})"
        return f"{self.address}: {action}"


@dataclass(frozen=True)
class KnownProviderBehaviour:
    """A planned update that matches version-scoped provider behaviour under explicit guards.

    Recognition is descriptive only; it is not a claim that applying the update is safe
    or appropriate for a particular environment.
    """

    reason: str
    guards: tuple[tuple[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"reason": self.reason, "guards": dict(self.guards)}


class UnsafeAdoptionError(GenerationError):
    """A declarative adoption plan would modify live infrastructure."""

    def __init__(self, changes: list[UnsafeChange]) -> None:
        self.changes = tuple(changes)
        preview = "; ".join(change.summary() for change in changes[:8])
        more = f"; and {len(changes) - 8} more" if len(changes) > 8 else ""
        super().__init__(
            "Refusing to publish an adoption project because the final plan would change "
            f"live resources: {preview}{more}. No plan was applied."
        )


class APIError(GenerationError):
    """An API failure whose diagnostic deliberately excludes response bodies."""

    def __init__(self, status: int, url: str, detail: str = "API request failed") -> None:
        from heckle.core.security import safe_url

        self.status = status
        self.url = safe_url(url)
        super().__init__(f"{detail}: HTTP {status}: {self.url}")
