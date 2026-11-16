"""Progress display and rate-limit pacing for discovery and hydration."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from heckle.activity import Activity, format_duration
from heckle.errors import GenerationError
from heckle.forges.github.api import GitHubClient
from heckle.forges.shared import http as http_module
from heckle.forges.shared.http import Quota
from heckle import opentofu
from heckle.opentofu import HydrationProgress, TfRunner
from fixtures import connection


# --- the live status line -------------------------------------------------------------


def test_status_line_redraws_in_place_on_a_terminal():
    stream = io.StringIO()
    activity = Activity(stream, live=True, interval=0.01)
    with activity.running("Hydrating", lambda: "12/40 imports read"):
        time.sleep(0.1)
        activity.note("A permanent message")
    output = stream.getvalue()
    assert "\r\x1b[2K" in output  # redrawn in place
    assert "Hydrating: 12/40 imports read" in output
    assert "A permanent message\n" in output
    assert output.endswith("\r\x1b[2K")  # cleared when the step ends


def test_heartbeat_lines_when_not_a_terminal():
    stream = io.StringIO()
    activity = Activity(stream, live=False, heartbeat=0.05)
    with activity.running("Hydrating", lambda: "3/9 imports read"):
        time.sleep(0.3)
    output = stream.getvalue()
    assert "... still working. Hydrating: 3/9 imports read" in output
    assert "\r" not in output and "\x1b" not in output  # nothing a log can't hold


def test_disabled_activity_routes_notes_to_progress():
    seen = []
    activity = Activity(note=seen.append)
    with activity.running("Discovering", lambda: "never drawn"):
        activity.note("Pausing for the rate limit")
    assert seen == ["Pausing for the rate limit"]


def test_a_failing_description_never_breaks_the_work():
    stream = io.StringIO()
    activity = Activity(stream, live=True, interval=0.01)

    def broken():
        raise RuntimeError("boom")

    with activity.running("Discovering", broken):
        time.sleep(0.05)
    assert "Discovering" in stream.getvalue()


def test_durations():
    assert format_duration(5) == "5s"
    assert format_duration(125) == "2m05s"
    assert format_duration(3 * 3600 + 60) == "3h01m"


# --- discovery: the HTTP client ---------------------------------------------------------


class Clock:
    """A virtual clock for time.time/monotonic/sleep inside the HTTP client."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Response:
    def __init__(self, body, headers):
        self._raw = json.dumps(body).encode()
        self.headers = headers

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def client_with(monkeypatch, responses):
    """A GitHubClient whose transport answers from `responses` (callables of the clock)."""
    clock = Clock()
    monkeypatch.setattr(http_module, "time", clock)
    client = GitHubClient(connection("github"))
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, clock.now))
            return responses[len(calls) - 1](clock, request)

    client._opener = Opener()
    notes = []
    client.notify = notes.append
    return client, clock, calls, notes


def quota_headers(remaining, limit=5000, reset=None, clock=None):
    return {
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Reset": str(int(reset if reset is not None else clock.now + 600)),
        "X-RateLimit-Resource": "core",
    }


def test_discovery_pauses_before_the_quota_runs_out(monkeypatch):
    reset_at = {}

    def low(clock, request):
        reset_at["value"] = clock.now + 900
        return Response({}, quota_headers(40, reset=reset_at["value"], clock=clock))

    def fresh(clock, request):
        return Response({}, quota_headers(4999, clock=clock))

    client, clock, calls, notes = client_with(monkeypatch, [low, fresh, fresh, fresh])
    client.get("/orgs/example")
    client.get("/orgs/example/repos")  # 40 left is under the reserve: wait for the reset
    client.get("/orgs/example/teams")
    client.get("/orgs/example/members")

    assert calls[1][1] >= reset_at["value"]  # the second request waited for the reset
    assert len(notes) == 1  # announced once, not per request
    assert "quota is nearly used up (40 of 5000 requests left)" in notes[0]
    assert "please be patient" in notes[0]
    assert len(calls) == 4


def test_plenty_of_quota_means_no_pause(monkeypatch):
    def ok(clock, request):
        return Response({}, quota_headers(4000, clock=clock))

    client, clock, calls, notes = client_with(monkeypatch, [ok, ok, ok])
    for path in ("/a", "/b", "/c"):
        client.get(path)
    assert clock.slept == [] and notes == []
    assert client.requests == 3
    assert "3 API requests" in client.describe()
    assert "quota 4,000/5,000" in client.describe()


def test_exhausted_quota_waits_for_the_reset_and_says_so(monkeypatch):
    import urllib.error

    def limited(clock, request):
        headers = quota_headers(0, reset=clock.now + 1200, clock=clock)
        raise urllib.error.HTTPError(
            request.full_url, 403, "rate limited", headers, io.BytesIO(b"API rate limit exceeded")
        )

    def ok(clock, request):
        return Response({"ok": True}, quota_headers(4999, clock=clock))

    client, clock, calls, notes = client_with(monkeypatch, [limited, ok])
    body, _ = client.get("/orgs/example")
    assert body == {"ok": True}
    assert calls[1][1] - calls[0][1] >= 1200  # waited out the window before retrying
    assert len(notes) == 1
    assert "rate limit reached; waiting until" in notes[0]
    assert "please be patient" in notes[0]


def test_quota_probe_is_uncached_unpaced_and_tolerates_absence(monkeypatch):
    import urllib.error

    def low(clock, request):
        return Response({}, quota_headers(10, clock=clock))

    def rate_limit(clock, request):
        assert request.full_url.endswith("/rate_limit")
        body = {"resources": {"core": {"remaining": 10, "limit": 5000, "reset": clock.now + 60}}}
        return Response(body, quota_headers(10, clock=clock))

    def disabled(clock, request):  # GitHub Enterprise Server without rate limiting
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    client, clock, calls, notes = client_with(monkeypatch, [low, rate_limit, rate_limit, disabled])
    client.get("/orgs/example")  # leaves the client under its reserve
    first = client.quota()
    second = client.quota()  # not served from the cache
    assert first == second == Quota(10, 5000, clock.now + 60)
    assert clock.slept == [] and notes == []  # the probe never waits for the reset
    assert client.quota() is None
    assert len(calls) == 4


# --- hydration: batches sized to the quota ----------------------------------------------

FAKE_TOFU = textwrap.dedent(
    """\
    #!{python}
    # A stand-in for tofu that charges each import read against a simulated quota.
    import json, re, sys
    from pathlib import Path

    state_file = Path({state!r})
    state = json.loads(state_file.read_text())
    args = sys.argv[1:]
    if args[0] == "init":
        sys.exit(0)
    if args[:2] == ["providers", "schema"]:
        schema = {{"block": {{"attributes": {{"name": {{"type": "string", "required": True}}}}}}}}
        print(json.dumps({{"format_version": "1.0", "provider_schemas": {{
            "registry.opentofu.org/integrations/github": {{
                "resource_schemas": {{"github_repository": schema}}}}}}}}))
        sys.exit(0)
    assert args[0] == "plan" and "-generate-config-out=generated.tf" in args
    assert not Path("generated.tf").exists()
    assert not list(Path(".").glob("hydrated/*.tf")) or True
    targets = re.findall(r'to = (\\S+)\\n  id = "([^"]*)"', Path("imports.tf").read_text())
    configured = set()
    for tf in Path(".").glob("*.tf"):
        if tf.name != "imports.tf":
            configured.update(
                "github_repository." + name
                for name in re.findall(r'resource "github_repository" "([^"]+)"', tf.read_text())
            )
    state["plans"].append(len(targets))
    generated, failed = [], 0
    burst = state["burst_plans"] > 0
    for index, (address, import_id) in enumerate(targets):
        print(f"{{address}}: Preparing import... [id={{import_id}}]")
        if import_id in state["broken"]:
            reason = "404 Not Found []"
        elif state["used"] + state["cost"] > state["limit"]:
            reason = "403 API rate limit of 5000 still exceeded"
        elif burst and index >= state["burst_after"]:
            reason = "403 You have exceeded a secondary rate limit. Please wait a few\\n    minutes before you try again. []"
        else:
            reason = None
        if reason:
            failed += 1
            print("\\nError: GET https://api.github.com/repos/o/SECRET-repo: " + reason)
            print(f"\\n  with {{address}},\\n  on imports.tf line 1:\\n\\nSECRET-detail\\n")
            continue
        state["used"] += state["cost"]
        print(f"{{address}}: Refreshing state... [id={{import_id}}]")
        if address in configured:
            continue  # already configured (e.g. a hand-written webhook): nothing generated
        name = address.split(".", 1)[1]
        generated.append(f'resource "github_repository" "{{name}}" {{{{\\n  name = "{{import_id}}"\\n}}}}\\n')
    if generated:
        Path("generated.tf").write_text("\\n".join(generated))
    if burst:
        state["burst_plans"] -= 1
    state_file.write_text(json.dumps(state))
    sys.exit(1 if failed else 0)
    """
)


class Simulation:
    """The fake tofu's quota, as both the provider (via the file) and the probe see it."""

    def __init__(
        self, tmp_path: Path, *, limit=5000, used=0, cost=3, broken=(), burst=(0, 0)
    ):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.path = tmp_path / "quota.json"
        self.reset = 1_800_000_000.0
        burst_plans, burst_after = burst  # that many plans fail after burst_after reads
        self.path.write_text(
            json.dumps(
                {
                    "limit": limit,
                    "used": used,
                    "cost": cost,
                    "broken": list(broken),
                    "burst_plans": burst_plans,
                    "burst_after": burst_after,
                    "plans": [],
                }
            )
        )
        self.sleeps: list[float] = []

    @property
    def state(self):
        return json.loads(self.path.read_text())

    def quota(self) -> Quota:
        state = self.state
        return Quota(state["limit"] - state["used"], state["limit"], self.reset)

    def sleep(self, seconds: float) -> None:  # waiting rolls the window over
        self.sleeps.append(seconds)
        state = self.state
        state["used"] = 0
        self.path.write_text(json.dumps(state))
        self.reset += 3600


def hydrate(
    tmp_path, monkeypatch, simulation, count, *, quota=True, stream=None, hand_written=()
):
    binary = tmp_path / "bin" / "tofu"
    binary.parent.mkdir()
    binary.write_text(FAKE_TOFU.format(python=sys.executable, state=str(simulation.path)))
    binary.chmod(0o755)
    monkeypatch.setattr(opentofu.time, "time", lambda: simulation.reset - 600)
    runner = TfRunner(str(binary), connection("github"))
    notes: list[str] = []
    runner.activity = Activity(stream, live=False, heartbeat=0.05) if stream else Activity(
        note=notes.append
    )
    if stream:
        runner.activity._note = notes.append
        original = runner.activity.note

        def note(message):
            notes.append(message)
            original(message)

        runner.activity.note = note
    runner.quota = simulation.quota if quota else None
    runner.sleep = simulation.sleep
    targets = [(f"github_repository.r{i}", f"repo-{i}") for i in range(count)]
    provider = SimpleNamespace(
        spec=SimpleNamespace(
            source="integrations/github",
            versions_hcl=lambda: "",
            configuration_hcl=lambda source: "",
        ),
        imports=lambda plan: targets,
        # Like webhooks and secrets: imported, but configured by hand.
        bootstrap_extra=lambda plan: "".join(
            f'resource "github_repository" "r{i}" {{\n  name = "repo-{i}"\n}}\n'
            for i in hand_written
        ),
    )
    plan = SimpleNamespace(
        candidates=[SimpleNamespace(resource_type="github_repository")] * count,
        model=SimpleNamespace(source=None),
    )
    resources, _ = runner.hydrate(provider, plan, tmp_path / "workspace")
    hydrate.last_runner = runner
    return resources, notes


def test_ample_quota_reads_everything_in_capped_batches(tmp_path, monkeypatch):
    simulation = Simulation(tmp_path)
    resources, notes = hydrate(tmp_path, monkeypatch, simulation, 600)
    assert len(resources) == 600
    # Every import really cost 3 requests; heckle-sync plans its own reads from this.
    assert hydrate.last_runner.hydration_requests_per_object == 3.0
    assert simulation.state["plans"] == [250, 250, 100]
    assert simulation.sleeps == []
    assert "Reading 600 objects in batches sized to the GitHub API quota" in notes[0]


def test_low_quota_waits_for_the_reset_instead_of_failing(tmp_path, monkeypatch):
    # 300 left: one small batch fits above the reserve, then heckle has to wait.
    simulation = Simulation(tmp_path, used=4700)
    resources, notes = hydrate(tmp_path, monkeypatch, simulation, 400)
    assert len(resources) == 400
    assert len(simulation.sleeps) == 1
    plans = simulation.state["plans"]
    assert plans[0] == (300 - opentofu.HYDRATION_RESERVE) // 3
    assert sum(plans) == 400
    waiting = [note for note in notes if "Waiting until" in note]
    assert len(waiting) == 1 and "please be patient" in waiting[0]
    left = 300 - plans[0] * 3
    assert f"GitHub API quota is low: {left} of 5,000 requests left" in waiting[0]


def test_imports_lost_to_an_exhausted_quota_are_retried(tmp_path, monkeypatch):
    # Each import really costs 15 requests, five times the initial estimate, so the
    # first batch runs the quota dry part-way through.
    simulation = Simulation(tmp_path, used=2000, cost=15)
    resources, notes = hydrate(tmp_path, monkeypatch, simulation, 300)
    assert len(resources) == 300
    assert any("could not be read while the GitHub API quota was exhausted" in n for n in notes)
    plans = simulation.state["plans"]
    assert plans[0] == 250
    # Measured after the first batch, so later batches fit inside a window.
    assert max(plans[1:]) <= (5000 - opentofu.HYDRATION_RESERVE) // 15 + 1


def test_a_genuine_failure_is_not_retried(tmp_path, monkeypatch):
    simulation = Simulation(tmp_path, broken=["repo-7"])
    with pytest.raises(
        GenerationError, match=r"\(1 missing; 1 failed: 1 not found\); first: github_repository.r7"
    ):
        hydrate(tmp_path, monkeypatch, simulation, 50)
    assert simulation.state["plans"] == [50]
    assert simulation.sleeps == []


def test_a_burst_rate_limit_cools_down_and_retries(tmp_path, monkeypatch):
    """The reported run: plenty of hourly quota, then a burst limit in batch 4."""
    simulation = Simulation(tmp_path, used=1481, cost=1, burst=(0, 0))
    state = simulation.state
    state["burst_plans"], state["burst_after"] = 0, 88
    simulation.path.write_text(json.dumps(state))

    # Fail only the fourth plan, as in the report.
    original = simulation.quota
    calls = {"plans": 0}

    def quota():
        current = simulation.state
        if len(current["plans"]) == 3 and calls["plans"] == 0:
            calls["plans"] = 1
            current["burst_plans"] = 1
            simulation.path.write_text(json.dumps(current))
        return original()

    simulation.quota = quota
    resources, notes = hydrate(tmp_path, monkeypatch, simulation, 1539)
    assert len(resources) == 1539
    plans = simulation.state["plans"]
    assert plans[:4] == [250, 250, 250, 250]
    retry = [note for note in notes if "could not be read" in note]
    assert len(retry) == 1
    assert "162 import(s) could not be read (162 GitHub's secondary (burst) rate limit)" in retry[0]
    assert "Waiting 1m00s before retrying them, in batches of at most 125" in retry[0]
    assert "please be patient" in retry[0]
    assert simulation.sleeps == [60.0]
    assert max(plans[4:]) <= 125


def test_a_persistent_burst_limit_gives_up_and_says_why(tmp_path, monkeypatch):
    simulation = Simulation(tmp_path, burst=(99, 10))
    with pytest.raises(GenerationError) as error:
        hydrate(tmp_path, monkeypatch, simulation, 600)
    message = str(error.value)
    assert "GitHub's secondary (burst) rate limit" in message
    assert "not attempted after that" in message
    assert "SECRET" not in message
    # Four cool-downs, each twice as long as the last.
    assert simulation.sleeps == [60.0, 120.0, 240.0, 480.0]


def test_without_a_quota_hydration_is_one_plan_as_before(tmp_path, monkeypatch):
    simulation = Simulation(tmp_path)
    resources, notes = hydrate(tmp_path, monkeypatch, simulation, 600, quota=False)
    assert len(resources) == 600
    assert simulation.state["plans"] == [600]
    imports_tf = (tmp_path / "workspace" / "imports.tf").read_text()
    assert imports_tf.count("import {") == 600


def test_provider_output_is_counted_but_never_shown(tmp_path, monkeypatch):
    stream = io.StringIO()
    # Without a quota probe, rate-limited reads are retried after a cool-down.
    simulation = Simulation(tmp_path, used=4990, cost=1)
    resources, notes = hydrate(tmp_path, monkeypatch, simulation, 40, quota=False, stream=stream)
    assert len(resources) == 40
    output = stream.getvalue()
    assert "30 import(s) could not be read (30 API rate limit)" in output
    assert "SECRET" not in output

    simulation = Simulation(tmp_path / "again", broken=["repo-3"])
    with pytest.raises(GenerationError) as error:
        hydrate(tmp_path / "again", monkeypatch, simulation, 5, quota=False, stream=stream)
    assert "SECRET" not in str(error.value) + stream.getvalue()


def test_failures_are_classified_without_keeping_their_text():
    progress = HydrationProgress(4, "GitHub")
    progress.start_batch()
    for line in (
        'a.b["x"]: Preparing import... [id=x]\n',
        "│ Error: GET https://api.github.com/repos/o/SECRET: 403 You have exceeded a\n",
        "│ secondary rate limit. Please wait.\n",
        "│\n",
        'a.b["y"]: Preparing import... [id=y]\n',
        "Error: Cannot import non-existent remote object\n",
        "Error: GET https://api.github.com/repos/o/r: 502 Bad Gateway []\n",
        "Warning: something unrelated\n",
        "Error: GET https://api.github.com/user: 401 Bad credentials []\n",
    ):
        progress.on_line(line)
    progress.finish_batch(0)
    assert dict(progress.failures) == {
        "GitHub's secondary (burst) rate limit": 1,
        "not found": 1,
        "server or network error": 1,
        "permission denied": 1,
    }
    assert "SECRET" not in repr(vars(progress))


def test_progress_counts_import_lines():
    progress = HydrationProgress(10, "GitHub")
    progress.start_batch()
    for line in (
        'module.repositories.github_repository.this["a b"]: Preparing import... [id=a b]\n',
        'module.repositories.github_repository.this["a b"]: Refreshing state... [id=a b]\n',
        "module.access.github_team.this[\"x\"]: Refreshing state... [id=1]\n",
        "Planning failed. SECRET provider detail\n",
    ):
        progress.on_line(line)
    assert progress.describe().startswith("2/10 imports read · batch 1")
    progress.finish_batch(2)
    progress.quota = Quota(1234, 5000, 1_800_000_000)
    assert "2/10 imports read" in progress.describe()
    assert "GitHub API quota 1,234/5,000" in progress.describe()
    progress.waiting_until = 1_800_000_000
    assert "waiting for the GitHub API quota to reset" in progress.describe()


def test_hand_written_targets_are_not_mistaken_for_failures(tmp_path, monkeypatch):
    """The reported "-88 failed": webhooks and secrets get no generated configuration."""
    hand_written = range(5, 600, 6)  # 100 of them, spread through every batch
    simulation = Simulation(tmp_path)
    resources, notes = hydrate(tmp_path, monkeypatch, simulation, 600, hand_written=hand_written)
    assert len(resources) == 600
    assert simulation.state["plans"] == [250, 250, 100]
    assert simulation.sleeps == []
    assert not any("could not be read" in note for note in notes)


def test_failure_counts_never_go_negative(tmp_path, monkeypatch):
    simulation = Simulation(tmp_path, broken=["repo-300"])
    with pytest.raises(GenerationError) as error:
        hydrate(tmp_path, monkeypatch, simulation, 600, hand_written=range(0, 600, 7))
    message = str(error.value)
    # repo-300 is in batch 2; batch 3 (500-599) is never attempted, 14 of which are
    # hand-written and so not missing.
    assert "87 missing; 1 failed: 1 not found; 86 not attempted after that" in message
