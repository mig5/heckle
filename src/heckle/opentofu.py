"""Terraform/OpenTofu subprocess boundary. Never applies plans; state-only imports are limited to adoption safety rehearsal."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Sequence

from heckle.tf_version import TF_MIN_VERSION, TF_MAX_VERSION, TF_VERSION_CONSTRAINT
from heckle.config import Connection
from heckle.core.compilation import ImportPlan
from heckle.errors import KnownProviderBehaviour, GenerationError, UnsafeAdoptionError, UnsafeChange
from heckle.hcl.parser import extract_top_blocks, parse_resource
from heckle.hcl.schema import ProviderSchema
from heckle.hcl.types import Resource
from heckle.hcl.render import hcl_string
from heckle.providers.base import ProviderAdapter


def collect_resources(flat: Path, extra: Path) -> list[Resource]:
    output: dict[str, Resource] = {}
    for path in (flat, extra):
        if not path.is_file():
            continue
        blocks, _ = extract_top_blocks(path)
        for block in blocks:
            if block.kind == "resource":
                resource = parse_resource(block)
                output[resource.address] = resource
    return list(output.values())


class TfRunner:
    def __init__(self, executable: str | None = None, connection: Connection | None = None) -> None:
        # OpenTofu is Heckle's default, but the CLI contract is shared with Terraform.
        requested = executable or os.environ.get("HECKLE_TF_BIN") or "tofu"
        found = shutil.which(requested)
        if not found:
            raise GenerationError(
                f"Terraform/OpenTofu executable not found: {requested}; "
                "use --tf or HECKLE_TF_BIN"
            )
        self.executable = str(Path(found).resolve())
        basename = Path(found).name.lower()
        if "terraform" in basename:
            self.tool_name = "Terraform"
            self.command_name = "terraform"
        elif "tofu" in basename:
            self.tool_name = "OpenTofu"
            self.command_name = "tofu"
        else:
            self.tool_name = "Terraform/OpenTofu"
            self.command_name = Path(found).name

        # Keep a simple command name portable, but preserve an explicitly supplied
        # path as the default used by generated one-time adoption instructions.
        # HECKLE_TF_BIN can still override it later.
        self.project_command = (
            self.executable
            if os.path.sep in requested or (os.path.altsep and os.path.altsep in requested)
            else self.command_name
        )
        self.connection = connection
        self.last_arguments: tuple[str, ...] | None = None
        self.last_cwd: Path | None = None

    def check_version(self, cwd: Path) -> str:
        """Fail before discovery, without exposing subprocess output or secrets."""
        self.last_arguments = ("version", "-json")
        self.last_cwd = Path(cwd).resolve()
        hint = "Select a supported executable with --tf or HECKLE_TF_BIN."
        try:
            result = subprocess.run(
                [self.executable, "version", "-json"], cwd=cwd,
                env=self.environment(), text=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False, timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise GenerationError(f"{self.tool_name} version check timed out after 30 seconds. {hint}") from exc
        except OSError as exc:
            raise GenerationError(f"Unable to run {self.tool_name}: {exc.strerror}. {hint}") from exc
        if result.returncode:
            raise GenerationError(
                f"{self.tool_name} version -json failed (exit {result.returncode}). {hint}"
            )
        try:
            payload = json.loads(result.stdout)
            version = payload.get("terraform_version") if isinstance(payload, dict) else None
            match = re.fullmatch(
                r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?",
                version,
            ) if isinstance(version, str) else None
        except ValueError:
            match = None
        if match is None:
            raise GenerationError(f"{self.tool_name} version -json returned an invalid version response. {hint}")
        # Match the generated required_version constraint, including its exclusion
        # of prereleases. Both CLIs use terraform_version in their JSON response.
        if not (TF_MIN_VERSION <= tuple(map(int, match.group(1, 2, 3))) < TF_MAX_VERSION) or match.group(4):
            raise GenerationError(
                f"Unsupported {self.tool_name} version {version} at {self.executable}; "
                f"Heckle requires {TF_VERSION_CONSTRAINT} (stable releases). {hint}"
            )
        return version

    def environment(self, *, managed_state: bool = False) -> dict[str, str]:
        env = os.environ.copy()
        # A shell's global flags or debug logging must not redirect temporary
        # state, inject an apply option, or leak provider responses into a log.
        for key in list(env):
            if key.startswith("TF_CLI_ARGS") or key in {"TF_LOG", "TF_LOG_PATH", "TF_LOG_PROVIDER", "TF_DATA_DIR", "TF_VAR_github_org", "GITHUB_OWNER"}:
                env.pop(key, None)
        if not managed_state:
            env.pop("TF_WORKSPACE", None)
        env.update({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0"})
        if self.connection:
            env.update(self.connection.provider_environment())
        return env

    def run(self, arguments: Sequence[str], cwd: Path, *, allow_failure: bool = False, managed_state: bool = False) -> subprocess.CompletedProcess[str]:
        allowed = {"init", "plan", "show", "fmt", "validate", "providers", "state"}
        if not arguments or arguments[0] not in allowed:
            raise GenerationError("Refusing an unsupported Terraform/OpenTofu operation")
        if arguments[0] == "state" and list(arguments) != ["state", "list"]:
            raise GenerationError("Only state list is allowed")
        self.last_arguments = tuple(arguments)
        self.last_cwd = Path(cwd).resolve()
        try:
            result = subprocess.run([self.executable, *arguments], cwd=cwd, env=self.environment(managed_state=managed_state), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        except OSError as exc:
            raise GenerationError(f"Unable to run {self.tool_name}: {exc.strerror}") from exc
        if result.returncode and not allow_failure:
            # Provider output can include confidential values, so it never goes
            # directly to console or into a published generation directory.
            raise GenerationError(f"{self.tool_name} {' '.join(arguments[:2])} failed (exit {result.returncode}). Run the same command in a private workspace for detailed diagnostics.")
        return result

    def hydrate(self, provider: ProviderAdapter, plan: ImportPlan, workspace: Path) -> tuple[list[Resource], ProviderSchema]:
        workspace.mkdir(parents=True, mode=0o700)
        (workspace / "versions.tf").write_text(provider.spec.versions_hcl(), encoding="utf-8")
        (workspace / "provider.tf").write_text(provider.spec.configuration_hcl(plan.model.source), encoding="utf-8")
        self.run(["init", "-backend=false", "-input=false", "-no-color"], workspace)
        raw = self.run(["providers", "schema", "-json"], workspace)
        try:
            schema = ProviderSchema.from_json(json.loads(raw.stdout), provider.spec.source)
        except (ValueError, KeyError) as exc:
            raise GenerationError("Invalid provider schema JSON") from exc
        unavailable = [c for c in plan.candidates if not schema.supports(c.resource_type)]
        if unavailable:
            names = sorted({c.resource_type for c in unavailable})
            raise GenerationError("Pinned provider lacks configured import resource types: " + ", ".join(names))
        imports = "\n".join(f"import {{\n  to = {address}\n  id = {hcl_string(import_id)}\n}}\n" for address, import_id in provider.imports(plan))
        (workspace / "imports.tf").write_text(imports, encoding="utf-8")
        extra = workspace / "sensitive-metadata.tf"
        extra.write_text(provider.bootstrap_extra(plan), encoding="utf-8")
        extra.chmod(0o600)
        flat = workspace / "generated.tf"
        if plan.candidates:
            self.run(["plan", "-input=false", "-lock=false", "-no-color", "-generate-config-out=generated.tf"], workspace, allow_failure=True)
        resources = collect_resources(flat, extra)
        found = {resource.address for resource in resources}
        missing = [address for address, _ in provider.imports(plan) if address not in found]
        if missing:
            raise GenerationError(f"Provider hydration did not return all import targets ({len(missing)} missing); first: {missing[0]}. No output was published.")
        return resources, schema

    def format(self, root: Path) -> None:
        self.run(["fmt", "-recursive", "-no-color"], root)

    def validate(self, root: Path, *, disposable: bool = False) -> None:
        had_cache = (root / ".terraform").exists()
        try:
            self.run(["init", "-backend=false", "-input=false", "-no-color"], root)
            self.run(["validate", "-no-color"], root)
        finally:
            if disposable and not had_cache:
                shutil.rmtree(root / ".terraform", ignore_errors=True)


    @staticmethod
    def _changed_paths(before: object, after: object, unknown: object, prefix: str = "") -> list[str]:
        """Return changed attribute paths without ever exposing their values."""
        if unknown is True:
            return []
        if isinstance(before, dict) or isinstance(after, dict):
            left = before if isinstance(before, dict) else {}
            right = after if isinstance(after, dict) else {}
            unknown_map = unknown if isinstance(unknown, dict) else {}
            output: list[str] = []
            for key in sorted(set(left) | set(right)):
                child = f"{prefix}.{key}" if prefix else str(key)
                output.extend(TfRunner._changed_paths(
                    left.get(key), right.get(key), unknown_map.get(key), child
                ))
            return output
        if isinstance(before, list) or isinstance(after, list):
            left = before if isinstance(before, list) else []
            right = after if isinstance(after, list) else []
            unknown_list = unknown if isinstance(unknown, list) else []
            output: list[str] = []
            for index in range(max(len(left), len(right))):
                child = f"{prefix}[{index}]"
                output.extend(TfRunner._changed_paths(
                    left[index] if index < len(left) else None,
                    right[index] if index < len(right) else None,
                    unknown_list[index] if index < len(unknown_list) else None,
                    child,
                ))
            return output
        return [prefix or "<root>"] if before != after else []

    @classmethod
    def _plan_summary(cls, payload: dict[str, object]) -> tuple[int, int, list[UnsafeChange]]:
        if payload.get("errored"):
            raise GenerationError("Terraform/OpenTofu reported an errored adoption safety plan")
        imports = 0
        noops = 0
        unsafe: list[UnsafeChange] = []
        for change in payload.get("resource_changes", []):
            if not isinstance(change, dict) or change.get("mode") != "managed":
                continue
            detail = change.get("change", {})
            if not isinstance(detail, dict):
                continue
            actions = tuple(str(item) for item in detail.get("actions", []))
            if detail.get("importing"):
                imports += 1
            if actions == ("no-op",):
                noops += 1
                continue
            attributes = tuple(cls._changed_paths(
                detail.get("before"), detail.get("after"), detail.get("after_unknown")
            ))
            unsafe.append(UnsafeChange(
                str(change.get("address", "<unknown>")),
                actions,
                attributes,
                detail.get("before"),
                detail.get("after"),
            ))
        return imports, noops, unsafe

    @staticmethod
    def _classify_changes(
        changes: list[UnsafeChange],
        known_behaviour: Callable[[UnsafeChange], KnownProviderBehaviour | None] | None,
    ) -> tuple[list[dict[str, object]], list[UnsafeChange]]:
        recognised: list[dict[str, object]] = []
        unsafe: list[UnsafeChange] = []
        for change in changes:
            decision = known_behaviour(change) if known_behaviour is not None else None
            if decision is None:
                unsafe.append(change)
                continue
            recognised.append({
                "address": change.address,
                "actions": list(change.actions),
                "attributes": list(change.attributes),
                "reason": decision.reason,
                "guards": dict(decision.guards),
            })
        return recognised, unsafe

    def _write_validation_inputs(self, root: Path, values: dict[str, str] | None) -> Path:
        path = root / ".heckle-adoption.auto.tfvars"
        if values:
            path.write_text(
                "\n".join(f"{name} = {expression}" for name, expression in sorted(values.items())) + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
        return path

    def adoption_check(
        self,
        root: Path,
        validation_inputs: dict[str, str] | None = None,
        *,
        disposable: bool = False,
        known_behaviour: Callable[[UnsafeChange], KnownProviderBehaviour | None] | None = None,
    ) -> dict[str, object]:
        """Check configuration-driven import blocks without applying the plan."""
        had_cache = (root / ".terraform").exists()
        plan_path = root / ".heckle-adoption.tfplan"
        vars_path = self._write_validation_inputs(root, validation_inputs)
        try:
            self.run(["init", "-backend=false", "-input=false", "-no-color"], root)
            self.run([
                "plan", "-input=false", "-lock=false", "-no-color",
                f"-out={plan_path.name}",
            ], root)
            raw = self.run(["show", "-json", plan_path.name], root)
            try:
                payload = json.loads(raw.stdout)
            except ValueError as exc:
                raise GenerationError(f"{self.tool_name} returned invalid JSON for the adoption safety plan") from exc
            imports, noops, changes = self._plan_summary(payload)
            recognised, unsafe = self._classify_changes(changes, known_behaviour)
            if unsafe:
                raise UnsafeAdoptionError(unsafe)
            result: dict[str, object] = {
                "imports": imports,
                "no_op_resources": noops,
                "unsafe_changes": 0,
            }
            if recognised:
                result["known_provider_behaviour_changes"] = recognised
            return result
        finally:
            vars_path.unlink(missing_ok=True)
            plan_path.unlink(missing_ok=True)
            if disposable and not had_cache:
                shutil.rmtree(root / ".terraform", ignore_errors=True)

    def _state_import(self, root: Path, address: str, import_id: str) -> None:
        """Import one object into disposable local state; never call provider Update."""
        try:
            result = subprocess.run(
                [self.executable, "import", "-input=false", "-lock=false", address, import_id],
                cwd=root,
                env=self.environment(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        except OSError as exc:
            raise GenerationError(f"Unable to run {self.tool_name}: {exc.strerror}") from exc
        if result.returncode:
            raise GenerationError(
                f"{self.tool_name} state-only import failed for {address} (exit {result.returncode}). "
                "No apply was run and no remote update operation was requested."
            )

    def state_only_adoption_check(
        self,
        root: Path,
        imports: list[tuple[str, str]],
        validation_inputs: dict[str, str] | None = None,
        *,
        disposable: bool = False,
        known_behaviour: Callable[[UnsafeChange], KnownProviderBehaviour | None] | None = None,
    ) -> dict[str, object]:
        """Rehearse adoption in disposable state and reject effective live changes.

        Some providers cannot produce a safe configuration-driven import plan because
        schema defaults are applied before lifecycle ignore_changes can take effect.
        The CLI import operation writes state only; it does not update the remote object.
        Provider-specific known behaviour may be recognised by the caller under explicit
        guard conditions; all other non-no-op managed changes remain unsafe.
        """
        if (root / "imports.tf").exists():
            raise GenerationError("State-only adoption rehearsal requires inactive import blocks")
        had_cache = (root / ".terraform").exists()
        vars_path = self._write_validation_inputs(root, validation_inputs)
        plan_path = root / ".heckle-adoption.tfplan"
        state_paths = [
            root / "terraform.tfstate",
            root / "terraform.tfstate.backup",
            root / ".terraform.tfstate.lock.info",
        ]
        try:
            self.run(["init", "-backend=false", "-input=false", "-no-color"], root)
            for address, import_id in imports:
                self._state_import(root, address, import_id)
            self.run([
                "plan", "-input=false", "-lock=false", "-no-color",
                f"-out={plan_path.name}",
            ], root)
            raw = self.run(["show", "-json", plan_path.name], root)
            try:
                payload = json.loads(raw.stdout)
            except ValueError as exc:
                raise GenerationError(f"{self.tool_name} returned invalid JSON after state-only adoption rehearsal") from exc
            _imports, noops, changes = self._plan_summary(payload)
            recognised, unsafe = self._classify_changes(changes, known_behaviour)
            if unsafe:
                raise UnsafeAdoptionError(unsafe)
            result: dict[str, object] = {
                "imports": len(imports),
                "no_op_resources": noops,
                "unsafe_changes": 0,
            }
            if recognised:
                result["known_provider_behaviour_changes"] = recognised
            return result
        finally:
            vars_path.unlink(missing_ok=True)
            plan_path.unlink(missing_ok=True)
            for path in state_paths:
                path.unlink(missing_ok=True)
            if disposable and not had_cache:
                shutil.rmtree(root / ".terraform", ignore_errors=True)

    def state_addresses(self, root: Path) -> set[str]:
        result = self.run(["state", "list"], root, managed_state=True)
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

