"""Terraform/OpenTofu subprocess boundary. Never applies plans; state-only imports are limited to adoption safety rehearsal."""

from __future__ import annotations

import collections
import contextlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Callable, Iterator, Sequence

from heckle.activity import Activity, format_clock, format_duration
from heckle.tf_version import TF_MIN_VERSION, TF_MAX_VERSION, TF_VERSION_CONSTRAINT
from heckle.config import Connection
from heckle.core.compilation import ImportPlan
from heckle.errors import KnownProviderBehaviour, GenerationError, UnsafeAdoptionError, UnsafeChange
from heckle.hcl.parser import extract_top_blocks, parse_resource
from heckle.hcl.schema import ProviderSchema
from heckle.hcl.types import Resource
from heckle.hcl.render import hcl_string
from heckle.forges.shared.http import FORGE_NAMES, Quota
from heckle.providers.base import ProviderAdapter

# Hydration reads every import target through the provider in `plan` runs. Where
# the forge reports a quota, the imports are split into batches sized to what is
# left of it, so a large organization waits for a reset instead of failing halfway.
HYDRATION_MAX_BATCH = 250
HYDRATION_MIN_BATCH = 25
HYDRATION_RESERVE = 100  # requests left untouched for anything else using the token
HYDRATION_INITIAL_COST = 3.0  # requests per import, until a batch has been measured
HYDRATION_RATE_RETRIES = 3
# Burst ("secondary") rate limits and server/network errors: cool down and retry.
HYDRATION_TRANSIENT_RETRIES = 4
HYDRATION_TRANSIENT_DELAY = 60.0  # doubled on each further retry
HYDRATION_QUOTA_POLL = 30.0  # seconds between quota checks while a batch runs

# Why the provider failed to read objects, classified from its error diagnostics.
# Only these labels are kept; the diagnostics themselves are never stored or shown.
# The flag says whether waiting and retrying can help.
FAILURE_KINDS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    (
        "GitHub's secondary (burst) rate limit",
        re.compile(r"secondary rate limit|abuse detection", re.I),
        True,
    ),
    ("API rate limit", re.compile(r"rate limit", re.I), True),
    (
        "server or network error",
        re.compile(
            r"\b50[0234]\b|timed? ?out|connection (?:reset|refused)|unexpected EOF|"
            r"TLS handshake|no such host|bad gateway|service unavailable",
            re.I,
        ),
        True,
    ),
    ("not found", re.compile(r"\b404\b|not found|non-existent remote object", re.I), False),
    (
        "permission denied",
        re.compile(r"\b40[13]\b|bad credentials|forbidden|not accessible|must have admin", re.I),
        False,
    ),
)
RETRYABLE_FAILURES = {label for label, _, retryable in FAILURE_KINDS if retryable}


def classify_failure(text: str) -> str:
    for label, pattern, _ in FAILURE_KINDS:
        if pattern.search(text):
            return label
    return "other error"


def describe_failures(failures: collections.Counter[str]) -> str:
    return ", ".join(f"{count:,} {label}" for label, count in failures.most_common())

# "<address>: Preparing import... [id=...]" / "<address>: Refreshing state... [id=...]".
# Only the address is used, to count progress; provider output is never displayed.
IMPORT_PROGRESS = re.compile(r"^(?P<address>\S.*?): (?:Preparing import|Refreshing state)\.\.\.")


class HydrationProgress:
    """Counters behind the live hydration status line."""

    def __init__(self, total: int, forge: str) -> None:
        self.total = total
        self.forge = forge
        self.finished = 0
        self.batch = 0
        self.quota: Quota | None = None
        self.waiting_until: float | None = None
        # Failure diagnostics in the current batch, by kind (see FAILURE_KINDS).
        self.failures: collections.Counter[str] = collections.Counter()
        self._diagnostic: list[str] | None = None
        self._reading: set[str] = set()
        self._lock = threading.Lock()

    def start_batch(self) -> None:
        with self._lock:
            self.batch += 1
            self._reading = set()
            self.failures = collections.Counter()
            self._diagnostic = None

    def finish_batch(self, hydrated: int) -> None:
        with self._lock:
            self._end_diagnostic()
            self.finished += hydrated
            self._reading = set()

    def _end_diagnostic(self) -> None:
        if self._diagnostic is not None:
            self.failures[classify_failure(" ".join(self._diagnostic))] += 1
            self._diagnostic = None

    def on_line(self, line: str) -> None:
        match = IMPORT_PROGRESS.match(line)
        # Diagnostics may be boxed (│) and wrapped; only their kind is recorded.
        text = line.strip(" \t\r\n│╷╵")
        with self._lock:
            if match:
                self._reading.add(match.group("address"))
            if text.startswith("Error: "):
                self._end_diagnostic()
                self._diagnostic = [text[len("Error: ") :]]
            elif text.startswith("Warning: ") or (match and self._diagnostic is not None):
                self._end_diagnostic()
            elif self._diagnostic is not None and len(self._diagnostic) < 40:
                self._diagnostic.append(text)

    def describe(self) -> str:
        with self._lock:
            done = min(self.total, self.finished + len(self._reading))
            parts = [f"{done:,}/{self.total:,} imports read"]
            if self.batch:
                parts.append(f"batch {self.batch}")
        if self.waiting_until is not None:
            parts.append(
                f"waiting for the {self.forge} API quota to reset at "
                f"{format_clock(self.waiting_until)}"
            )
        elif self.quota is not None:
            parts.append(
                f"{self.forge} API quota {self.quota.remaining:,}/{self.quota.limit:,}, "
                f"resets {format_clock(self.quota.reset)}"
            )
        return " · ".join(parts)


def write_imports(path: Path, targets: Sequence[tuple[str, str]]) -> None:
    path.write_text(
        "\n".join(
            f"import {{\n  to = {address}\n  id = {hcl_string(import_id)}\n}}\n"
            for address, import_id in targets
        ),
        encoding="utf-8",
    )


def collect_resources(*paths: Path) -> list[Resource]:
    output: dict[str, Resource] = {}
    for path in paths:
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
        # Set by the pipeline: where progress goes, and how to read the forge's quota.
        self.activity = Activity()
        self.quota: Callable[[], Quota | None] | None = None
        self.sleep: Callable[[float], None] = time.sleep

    def check_version(self, cwd: Path) -> str:
        """Fail before discovery, without exposing subprocess output or secrets."""
        self.last_arguments = ("version", "-json")
        self.last_cwd = Path(cwd).resolve()
        hint = "Select a supported executable with --tf or HECKLE_TF_BIN."
        try:
            result = subprocess.run(
                [self.executable, "version", "-json"],
                cwd=cwd,
                env=self.environment(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise GenerationError(
                f"{self.tool_name} version check timed out after 30 seconds. {hint}"
            ) from exc
        except OSError as exc:
            raise GenerationError(
                f"Unable to run {self.tool_name}: {exc.strerror}. {hint}"
            ) from exc
        if result.returncode:
            raise GenerationError(
                f"{self.tool_name} version -json failed (exit {result.returncode}). {hint}"
            )
        try:
            payload = json.loads(result.stdout)
            version = payload.get("terraform_version") if isinstance(payload, dict) else None
            match = (
                re.fullmatch(
                    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?",
                    version,
                )
                if isinstance(version, str)
                else None
            )
        except ValueError:
            match = None
        if match is None:
            raise GenerationError(
                f"{self.tool_name} version -json returned an invalid version response. {hint}"
            )
        # Match the generated required_version constraint, including its exclusion
        # of prereleases. Both CLIs use terraform_version in their JSON response.
        if not (
            TF_MIN_VERSION <= tuple(map(int, match.group(1, 2, 3))) < TF_MAX_VERSION
        ) or match.group(4):
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
            if key.startswith("TF_CLI_ARGS") or key in {
                "TF_LOG",
                "TF_LOG_PATH",
                "TF_LOG_PROVIDER",
                "TF_DATA_DIR",
                "TF_VAR_github_org",
                "GITHUB_OWNER",
            }:
                env.pop(key, None)
        if not managed_state:
            env.pop("TF_WORKSPACE", None)
        env.update({"TF_IN_AUTOMATION": "1", "TF_INPUT": "0"})
        if self.connection:
            env.update(self.connection.provider_environment())
        return env

    def run(
        self,
        arguments: Sequence[str],
        cwd: Path,
        *,
        allow_failure: bool = False,
        managed_state: bool = False,
        on_line: Callable[[str], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        allowed = {"init", "plan", "show", "fmt", "validate", "providers", "state"}
        if not arguments or arguments[0] not in allowed:
            raise GenerationError("Refusing an unsupported Terraform/OpenTofu operation")
        if arguments[0] == "state" and list(arguments) != ["state", "list"]:
            raise GenerationError("Only state list is allowed")
        self.last_arguments = tuple(arguments)
        self.last_cwd = Path(cwd).resolve()
        try:
            if on_line is None:
                result = subprocess.run(
                    [self.executable, *arguments],
                    cwd=cwd,
                    env=self.environment(managed_state=managed_state),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            else:
                result = self._run_streaming(
                    [self.executable, *arguments],
                    cwd,
                    self.environment(managed_state=managed_state),
                    on_line,
                )
        except OSError as exc:
            raise GenerationError(f"Unable to run {self.tool_name}: {exc.strerror}") from exc
        if result.returncode and not allow_failure:
            # Provider output can include confidential values, so it never goes
            # directly to console or into a published generation directory.
            raise GenerationError(
                f"{self.tool_name} {' '.join(arguments[:2])} failed (exit {result.returncode}). Run the same command in a private workspace for detailed diagnostics."
            )
        return result

    @staticmethod
    def _run_streaming(
        command: list[str], cwd: Path, env: dict[str, str], on_line: Callable[[str], None]
    ) -> subprocess.CompletedProcess[str]:
        """Like subprocess.run, but hands each output line to on_line as it arrives.

        The output is still only held privately, exactly as with subprocess.run.
        """
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        lines: list[str] = []
        try:
            assert process.stdout is not None
            for line in process.stdout:
                lines.append(line)
                on_line(line)
            returncode = process.wait()
        finally:
            if process.poll() is None:  # interrupted: don't leave tofu running
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        return subprocess.CompletedProcess(command, returncode, stdout="".join(lines))

    @property
    def forge_name(self) -> str:
        forge = self.connection.source.forge if self.connection else ""
        return FORGE_NAMES.get(forge, forge or "forge")

    def _probe(self, progress: HydrationProgress | None = None) -> Quota | None:
        if self.quota is None:
            return None
        try:
            quota = self.quota()
        except Exception:  # an unreadable quota just means no pacing
            return None
        if progress is not None and quota is not None:
            progress.quota = quota
        return quota

    @contextlib.contextmanager
    def _watch_quota(self, progress: HydrationProgress) -> Iterator[None]:
        """Refresh the displayed quota while a long batch runs."""
        if self.quota is None:
            yield
            return
        stop = threading.Event()

        def poll() -> None:
            while not stop.wait(HYDRATION_QUOTA_POLL):
                self._probe(progress)

        thread = threading.Thread(target=poll, name="heckle-quota", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()

    def _next_batch_size(
        self,
        pending: int,
        cost: float,
        progress: HydrationProgress,
        largest: int = HYDRATION_MAX_BATCH,
    ) -> int:
        """How many imports to read next, waiting for a quota reset if none fit."""
        if self.quota is None:
            return min(pending, largest)  # no quota to respect: one plan, as before
        wanted = min(pending, largest)
        while True:
            quota = self._probe(progress)
            if quota is None:
                return wanted
            affordable = int((quota.remaining - HYDRATION_RESERVE) / cost)
            if affordable >= min(wanted, HYDRATION_MIN_BATCH) or quota.remaining >= quota.limit:
                return max(1, min(wanted, affordable))
            now = time.time()
            resume = quota.reset + 2
            if resume <= now:
                self.sleep(2)  # the window has just ended; read the new one
                continue
            self.activity.note(
                f"{self.forge_name} API quota is low: {quota.remaining:,} of {quota.limit:,} "
                f"requests left, and the next {wanted:,} imports need about "
                f"{math.ceil(wanted * cost):,}. Waiting until {format_clock(resume)} (about "
                f"{format_duration(resume - now)}) for it to reset. "
                "Heckle is still working; please be patient."
            )
            progress.waiting_until = resume
            try:
                self.sleep(resume - now)
            finally:
                progress.waiting_until = None

    @staticmethod
    def _calibrate(cost: float, before: Quota | None, after: Quota | None, count: int) -> float:
        """Requests per import, measured from the last batch where possible."""
        if before is None or after is None or not count or after.reset != before.reset:
            return cost
        used = before.remaining - after.remaining
        return max(0.5, used / count) if used > 0 else cost

    def hydrate(
        self, provider: ProviderAdapter, plan: ImportPlan, workspace: Path
    ) -> tuple[list[Resource], ProviderSchema]:
        workspace.mkdir(parents=True, mode=0o700)
        (workspace / "versions.tf").write_text(provider.spec.versions_hcl(), encoding="utf-8")
        (workspace / "provider.tf").write_text(
            provider.spec.configuration_hcl(plan.model.source), encoding="utf-8"
        )
        self.run(["init", "-backend=false", "-input=false", "-no-color"], workspace)
        raw = self.run(["providers", "schema", "-json"], workspace)
        try:
            schema = ProviderSchema.from_json(json.loads(raw.stdout), provider.spec.source)
        except (ValueError, KeyError) as exc:
            raise GenerationError("Invalid provider schema JSON") from exc
        unavailable = [c for c in plan.candidates if not schema.supports(c.resource_type)]
        if unavailable:
            names = sorted({c.resource_type for c in unavailable})
            raise GenerationError(
                "Pinned provider lacks configured import resource types: " + ", ".join(names)
            )
        extra = workspace / "sensitive-metadata.tf"
        extra.write_text(provider.bootstrap_extra(plan), encoding="utf-8")
        extra.chmod(0o600)
        files = self._hydrate_in_batches(provider.imports(plan), workspace)
        resources = collect_resources(*files, extra)
        found = {resource.address for resource in resources}
        missing = [address for address, _ in provider.imports(plan) if address not in found]
        if missing:
            unattempted = getattr(self, "hydration_unattempted", 0)
            failures = getattr(self, "hydration_failures", collections.Counter())
            detail = f"{len(missing) - unattempted:,} failed"
            if failures:
                detail += f": {describe_failures(failures)}"
            if unattempted:
                detail += f"; {unattempted:,} not attempted after that"
            raise GenerationError(
                f"Provider hydration did not return all import targets ({len(missing):,} "
                f"missing; {detail}); first: {missing[0]}. No output was published."
            )
        return resources, schema

    def _hydrate_in_batches(
        self, targets: Sequence[tuple[str, str]], workspace: Path
    ) -> list[Path]:
        """Generate configuration for every import target; returns the generated files.

        Each batch's generated file is moved out of the workspace before the next
        plan, which would otherwise read it as configuration.
        """
        hydrated = workspace / "hydrated"
        hydrated.mkdir(mode=0o700)
        pending = list(targets)
        progress = HydrationProgress(len(pending), self.forge_name)
        files: list[Path] = []
        cost = HYDRATION_INITIAL_COST
        rate_retries = 0
        transient_retries = 0
        largest = len(pending) if self.quota is None else HYDRATION_MAX_BATCH
        self.hydration_failures: collections.Counter[str] = collections.Counter()
        self.hydration_unattempted = 0
        if pending and self.quota is not None:
            quota = self._probe(progress)
            if quota is not None:
                self.activity.note(
                    f"Reading {len(pending):,} objects in batches sized to the {self.forge_name} "
                    f"API quota ({quota.remaining:,} of {quota.limit:,} requests left, resets "
                    f"{format_clock(quota.reset)})."
                )
        with self.activity.running("Hydrating", progress.describe), self._watch_quota(progress):
            while pending:
                size = self._next_batch_size(len(pending), cost, progress, largest)
                batch, pending = pending[:size], pending[size:]
                before = progress.quota
                progress.start_batch()
                write_imports(workspace / "imports.tf", batch)
                self.run(
                    [
                        "plan",
                        "-input=false",
                        "-lock=false",
                        "-no-color",
                        "-generate-config-out=generated.tf",
                    ],
                    workspace,
                    allow_failure=True,
                    on_line=progress.on_line,
                )
                generated = workspace / "generated.tf"
                found: set[str] = set()
                if generated.exists():
                    part = hydrated / f"batch-{len(files) + 1:04d}.tf"
                    generated.replace(part)
                    files.append(part)
                    found = {resource.address for resource in collect_resources(part)}
                missing = [target for target in batch if target[0] not in found]
                after = self._probe(progress)
                cost = self._calibrate(cost, before, after, len(batch))
                progress.finish_batch(len(batch) - len(missing))
                if not missing:
                    continue
                exhausted = after is not None and (
                    after.remaining <= HYDRATION_RESERVE
                    or (before is not None and after.reset > before.reset)
                )
                if exhausted and rate_retries < HYDRATION_RATE_RETRIES:
                    rate_retries += 1
                    self.activity.note(
                        f"{len(missing):,} import(s) could not be read while the "
                        f"{self.forge_name} API quota was exhausted; retrying them once it "
                        "resets."
                    )
                    pending = missing + pending
                    continue
                failures = progress.failures
                retryable = sum(failures[kind] for kind in RETRYABLE_FAILURES)
                if retryable and transient_retries < HYDRATION_TRANSIENT_RETRIES:
                    delay = HYDRATION_TRANSIENT_DELAY * 2**transient_retries
                    transient_retries += 1
                    # Smaller batches put less load on the API in any one minute.
                    largest = max(HYDRATION_MIN_BATCH, min(largest, len(batch)) // 2)
                    self.activity.note(
                        f"{len(missing):,} import(s) could not be read "
                        f"({describe_failures(failures)}). Waiting "
                        f"{format_duration(delay)} before retrying them, in batches of at most "
                        f"{largest:,} (retry {transient_retries} of "
                        f"{HYDRATION_TRANSIENT_RETRIES}). Heckle is still working; please be "
                        "patient."
                    )
                    progress.waiting_until = time.time() + delay
                    try:
                        self.sleep(delay)
                    finally:
                        progress.waiting_until = None
                    pending = missing + pending
                    continue
                # Not retryable, or out of retries: the caller reports it.
                self.hydration_failures = failures
                self.hydration_unattempted = len(pending)
                break
        # Leave every import block in place, as a single plan would have, for a
        # workspace kept for diagnostics (--keep-workdir).
        write_imports(workspace / "imports.tf", targets)
        return files

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
    def _changed_paths(
        before: object, after: object, unknown: object, prefix: str = ""
    ) -> list[str]:
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
                output.extend(
                    TfRunner._changed_paths(
                        left.get(key), right.get(key), unknown_map.get(key), child
                    )
                )
            return output
        if isinstance(before, list) or isinstance(after, list):
            left = before if isinstance(before, list) else []
            right = after if isinstance(after, list) else []
            unknown_list = unknown if isinstance(unknown, list) else []
            output: list[str] = []
            for index in range(max(len(left), len(right))):
                child = f"{prefix}[{index}]"
                output.extend(
                    TfRunner._changed_paths(
                        left[index] if index < len(left) else None,
                        right[index] if index < len(right) else None,
                        unknown_list[index] if index < len(unknown_list) else None,
                        child,
                    )
                )
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
            attributes = tuple(
                cls._changed_paths(
                    detail.get("before"), detail.get("after"), detail.get("after_unknown")
                )
            )
            unsafe.append(
                UnsafeChange(
                    str(change.get("address", "<unknown>")),
                    actions,
                    attributes,
                    detail.get("before"),
                    detail.get("after"),
                )
            )
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
            recognised.append(
                {
                    "address": change.address,
                    "actions": list(change.actions),
                    "attributes": list(change.attributes),
                    "reason": decision.reason,
                    "guards": dict(decision.guards),
                }
            )
        return recognised, unsafe

    def _write_validation_inputs(self, root: Path, values: dict[str, str] | None) -> Path:
        path = root / ".heckle-adoption.auto.tfvars"
        if values:
            path.write_text(
                "\n".join(f"{name} = {expression}" for name, expression in sorted(values.items()))
                + "\n",
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
            self.run(
                [
                    "plan",
                    "-input=false",
                    "-lock=false",
                    "-no-color",
                    f"-out={plan_path.name}",
                ],
                root,
            )
            raw = self.run(["show", "-json", plan_path.name], root)
            try:
                payload = json.loads(raw.stdout)
            except ValueError as exc:
                raise GenerationError(
                    f"{self.tool_name} returned invalid JSON for the adoption safety plan"
                ) from exc
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
            self.run(
                [
                    "plan",
                    "-input=false",
                    "-lock=false",
                    "-no-color",
                    f"-out={plan_path.name}",
                ],
                root,
            )
            raw = self.run(["show", "-json", plan_path.name], root)
            try:
                payload = json.loads(raw.stdout)
            except ValueError as exc:
                raise GenerationError(
                    f"{self.tool_name} returned invalid JSON after state-only adoption rehearsal"
                ) from exc
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
