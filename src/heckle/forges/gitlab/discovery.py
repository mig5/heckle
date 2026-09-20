"""GitLab namespaces are recursive groups, never emulated GitHub organizations."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

from heckle.core.model import Entity, ForgeModel, Observation
from heckle.errors import GenerationError
from heckle.forges.base import ForgeAdapter
from . import normalize


class GitLabForge(ForgeAdapter):
    GROUP_COLLECTIONS = (
        ("members", "group_membership", "membership"),
        ("hooks", "group_hook", "webhook"),
        ("labels", "group_label", "label"),
        ("badges", "group_badge", "badge"),
        ("variables", "group_variable", "variable"),
        ("access_requests", "group_access_request", "invitation"),
        ("runners", "group_runner", "runner"),
    )
    PROJECT_COLLECTIONS = (
        ("members", "project_membership", "membership"),
        ("protected_branches", "branch_protection", "branch_protection"),
        ("protected_tags", "tag_protection", "tag_protection"),
        ("hooks", "project_hook", "webhook"),
        ("deploy_keys", "deploy_key", "deploy_key"),
        ("environments", "project_environment", "environment"),
        ("approval_rules", "project_approval_rule", "approval_rule"),
        ("labels", "project_label", "label"),
        ("badges", "project_badge", "badge"),
        ("variables", "project_variable", "variable"),
        ("pipeline_schedules", "pipeline_schedule", "schedule"),
        ("runners", "project_runner", "runner"),
    )

    def discover(self, workspace: Path) -> ForgeModel:
        self.resolve_source()
        if self.connection.source.namespace_type == "user":
            return self._personal()
        root_data = self.detail("root group", f"/groups/{quote(self.connection.source.scope, safe='')}")
        if root_data is None:
            raise GenerationError("GitLab root group was not returned")
        root = self.model.add(normalize.group(root_data))
        # Queue the actual parent relationship; a selected subgroup may have an
        # unmanaged parent, whose remote ID remains literal in native settings.
        queue = deque([root])
        seen: set[str] = set()
        project_parents: dict[str, Entity] = {}
        while queue:
            namespace = queue.popleft()
            if namespace.remote_id in seen:
                continue
            seen.add(namespace.remote_id)
            prefix = f"/groups/{namespace.remote_id}"
            for segment, native_kind, kind in self.GROUP_COLLECTIONS:
                self._collect(namespace, prefix, segment, native_kind, kind)
            subgroups = self.collection(namespace.key + ":subgroups", prefix + "/subgroups")
            for summary in subgroups or []:
                data = self.detail(namespace.key + ":subgroup", f"/groups/{summary['id']}")
                subgroup = normalize.group(data, namespace.uid)
                if not subgroup.key.startswith(root.key + "/"):
                    raise GenerationError("GitLab subgroup enumeration escaped the requested scope")
                self.model.add(subgroup)
                queue.append(subgroup)
            projects = self.collection(namespace.key + ":projects", prefix + "/projects", params={
                "include_subgroups": "false", "with_shared": "false", "order_by": "id", "sort": "asc",
            })
            for summary in projects or []:
                if str((summary.get("namespace") or {}).get("id")) != namespace.remote_id:
                    self.model.observations.append(Observation(namespace.key + ":shared-project", "skipped", 1, "Project belongs to another namespace"))
                    continue
                if summary.get("archived"):
                    self.model.add(normalize.project(summary, namespace))
                    self.model.observations.append(Observation(summary["path_with_namespace"], "skipped", 1, "Archived project"))
                else:
                    project_parents[str(summary["id"])] = namespace
        # Bounded concurrency only across projects. Group discovery remains ordered.
        with ThreadPoolExecutor(max_workers=self.connection.workers) as executor:
            futures = [executor.submit(self._project, remote_id, parent) for remote_id, parent in sorted(project_parents.items())]
            for future in futures:
                future.result()  # Do not turn programming/network failures into empty repositories.
        self.model.extensions["gitlab"] = {"root_group_id": root.remote_id, "root_group_path": root.key}
        return self.model

    def lookup_user(self, username: str) -> dict:
        rows = self.collection("personal account", "/users", params={"username": username}, optional=False)
        matches = [row for row in rows if row.get("username", "").casefold() == username.casefold()]
        if len(matches) != 1:
            raise GenerationError("GitLab did not return exactly one matching user")
        return matches[0]

    def _personal(self) -> ForgeModel:
        owner = self.connection.source.scope
        data = self.detail("personal namespace", f"/namespaces/{quote(owner, safe='')}")
        if data.get("kind") != "user" or data.get("full_path", "").casefold() != owner.casefold():
            raise GenerationError("GitLab did not return the requested personal namespace")
        namespace = self.model.add(Entity("namespace", data["full_path"], str(data["id"]), "user_namespace", data, owner=owner))
        # The user-project endpoint returns [] for a private profile. The owned
        # endpoint works for our own account, but can include owned group projects.
        path = "/projects" if self.is_self else f"/users/{self.user['id']}/projects"
        params = {"order_by": "id", "sort": "asc"}
        if self.is_self:
            params["owned"] = "true"
        elif self.user.get("private_profile"):
            raise GenerationError("GitLab hides project enumeration for this private profile; use its owner's token with --me")
        rows = self.collection(owner + ":projects", path, params=params, optional=False)
        projects: dict[str, dict] = {}
        for row in rows:
            parent = row.get("namespace") or {}
            if parent.get("id") is None:
                raise GenerationError("Project response is missing its namespace ID")
            if str(parent["id"]) != namespace.remote_id:
                self.model.observations.append(Observation(row.get("path_with_namespace", str(row["id"])), "skipped", 1, "Project belongs to another namespace"))
                continue
            if parent.get("kind", "user") != "user":
                raise GenerationError("Personal namespace changed kind during discovery")
            projects[str(row["id"])] = row
        with ThreadPoolExecutor(max_workers=self.connection.workers) as executor:
            futures = []
            for remote_id, row in sorted(projects.items()):
                if row.get("archived"):
                    self.model.add(normalize.project(row, namespace))
                    self.model.observations.append(Observation(row["path_with_namespace"], "skipped", 1, "Archived project"))
                else:
                    futures.append(executor.submit(self._project, remote_id, namespace))
            for future in futures:
                future.result()
        self.model.observations.append(Observation(owner + ":groups", "not_applicable", detail="Personal namespace: no groups or group-level configuration"))
        self.model.extensions["gitlab"] = {"user_id": self.user["id"], "namespace_id": namespace.remote_id}
        return self.model

    def _collect(self, parent: Entity, prefix: str, segment: str, native_kind: str, kind: str) -> None:
        params = {"include_ancestor_groups": "false"} if segment == "labels" else None
        rows = self.collection(f"{parent.key}:{segment}", f"{prefix}/{segment}", params=params, variable=kind == "variable")
        for data in rows or []:
            if (native_kind == "project_badge" and data.get("kind") == "group") or (native_kind == "project_label" and data.get("is_project_label") is False):
                self.model.observations.append(Observation(f"{parent.key}:{segment}", "skipped", 1, "Inherited group object"))
                continue
            # /members deliberately excludes inherited memberships; /members/all does not.
            if kind == "variable":
                identity = f"{data['key']}@{data.get('environment_scope', '*')}"
            elif kind in {"branch_protection", "tag_protection"}:
                identity = data["name"]
            else:
                identity = data.get("id", data.get("name"))
            if identity is None:
                raise GenerationError(f"Missing identity in GitLab {native_kind}")
            self.model.add(normalize.child(parent, native_kind, kind, data, identity))

    def _project(self, remote_id: str, parent: Entity) -> None:
        data = self.detail(f"project:{remote_id}", f"/projects/{remote_id}")
        if str((data.get("namespace") or {}).get("id")) != parent.remote_id:
            raise GenerationError("GitLab project moved namespaces during discovery; rerun inventory")
        project = self.model.add(normalize.project(data, parent))
        if data.get("archived"):
            return
        for segment, native_kind, kind in self.PROJECT_COLLECTIONS:
            self._collect(project, f"/projects/{remote_id}", segment, native_kind, kind)
        # Push rules are an embedded project resource setting in the GitLab provider.
        rules = self.detail(f"{project.key}:push_rules", f"/projects/{remote_id}/push_rule", optional=True)
        if rules:
            self.model.add(normalize.child(project, "push_rules", "native", rules, "push-rules"))
