from __future__ import annotations
import concurrent.futures
import datetime as dt
import json
import sys
import threading
from pathlib import Path
from typing import Any, Callable
from heckle.errors import GitHubAPIError, GenerationError
from heckle.core.security import scrub
from heckle.core.identifiers import filesystem_name
from heckle.forges.github.api import GitHubClient, quote

class GitHubInventoryExporter:

    def __init__(self, client: GitHubClient, org: str, out_dir: Path, *, workers: int,
                 personal: bool = False, user: dict[str, Any] | None = None,
                 repositories: list[dict[str, Any]] | None = None) -> None:
        self.client = client
        self.org = org
        self.out_dir = out_dir
        self.inventory_dir = out_dir / 'inventory'
        self.workers = workers
        self.personal = personal
        self.user = user
        self.repositories = repositories
        self.coverage: dict[str, Any] = {'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'organization': org,  'skipped_archived_repositories': [], 'api_errors': [],   'not_managed': ['Secret values are intentionally not exported or managed.', 'Webhook secrets are intentionally omitted and ignored.', 'Repository file contents such as workflows, CODEOWNERS, Dependabot YAML, issue templates, and pull-request templates remain managed through Git.', 'Pending organization invitations are inventoried only when the token can read them; they are not imported as memberships.', 'Inherited repository rulesets are not duplicated; only repository-owned rulesets are imported.', 'Archived repositories and their repository-scoped configuration are skipped.', 'Provider coverage is calculated separately from API discovery.']}
        if self.personal:
            self.coverage.pop("organization", None)
            self.coverage["user"] = org
            self.coverage["not_managed"] = [message for message in self.coverage["not_managed"] if not message.startswith("Pending organization invitations")]
        self._lock = threading.RLock()

    def record_error(self, scope: str, exc: Exception) -> None:
        entry: dict[str, Any] = {'scope': scope, 'error': str(exc)}
        if isinstance(exc, GitHubAPIError):
            entry.update({'http_status': exc.status, 'accepted_github_permissions': exc.accepted_permissions})
        with self._lock:
            self.coverage['api_errors'].append(entry)
        print(f'warning: {scope}: {exc}', file=sys.stderr)

    def optional(self, scope: str, func: Callable[[], Any], default: Any, *, ignore_http_statuses: set[int] | None=None) -> Any:
        try:
            return func()
        except GitHubAPIError as exc:
            if ignore_http_statuses and exc.status in ignore_http_statuses:
                return default
            self.record_error(scope, exc)
            return default
        except Exception as exc:
            self.record_error(scope, exc)
            return default

    def save_inventory(self, relative: str, value: Any) -> None:
        path = self.inventory_dir / relative
        if self.inventory_dir.resolve() not in path.resolve().parents:
            raise GenerationError('Inventory path escapes workspace')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(scrub(value), indent=2, sort_keys=True) + '\n', encoding='utf-8')

    def note_not_managed(self, message: str) -> None:
        with self._lock:
            self.coverage['not_managed'].append(message)

    def run(self) -> None:
        self.inventory_dir.mkdir(parents=True, exist_ok=True)
        if self.personal:
            if self.user is None or self.repositories is None:
                raise GenerationError("Personal GitHub discovery requires a resolved user and repository list")
            self.save_inventory('user.json', self.user)
            repos = self.repositories
        else:
            org_data, _ = self.client.get(f'/orgs/{quote(self.org)}')
            self.save_inventory('organization.json', org_data)
            repos = self.client.get_paginated(f'/orgs/{quote(self.org)}/repos', params={'type': 'all', 'sort': 'full_name', 'direction': 'asc'})
        self.save_inventory('repositories.json', repos)
        active_repos = sorted((repo for repo in repos if not repo.get('archived', False)), key=lambda item: item['name'].lower())
        archived = sorted((repo['name'] for repo in repos if repo.get('archived', False)))
        self.coverage['skipped_archived_repositories'] = archived
        if not self.personal:
            self.export_members()
            self.export_teams()
            self.export_organization_configuration()
        if self.workers == 1:
            for repo in active_repos:
                self.export_repository(repo)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {executor.submit(self.export_repository, repo): repo['name'] for repo in active_repos}
                for future in concurrent.futures.as_completed(futures):
                    name = futures[future]
                    try:
                        future.result()
                    except Exception as exc:
                        self.record_error(f'repository:{name}:unexpected', exc)
        self.save_inventory('coverage.json', self.coverage)

    def export_members(self) -> None:
        members = self.client.get_paginated(f'/orgs/{quote(self.org)}/members', params={'filter': 'all', 'role': 'all'})
        memberships: list[dict[str, Any]] = []
        for member in sorted(members, key=lambda item: item['login'].lower()):
            username = member['login']
            membership, _ = self.client.get(f'/orgs/{quote(self.org)}/memberships/{quote(username)}')
            memberships.append({**membership, "_username": username})
        self.save_inventory('members.json', members)
        self.save_inventory('memberships.json', memberships)
        public_members = self.optional('organization public members', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/public_members'), [])
        self.save_inventory('public-members.json', public_members)
        outside_collaborators = self.optional('organization outside collaborators', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/outside_collaborators'), [])
        self.save_inventory('outside-collaborators.json', outside_collaborators)
        invitations = self.optional('organization invitations', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/invitations'), [])
        self.save_inventory('pending-invitations.json', invitations)

    def export_teams(self) -> None:
        teams = self.client.get_paginated(f'/orgs/{quote(self.org)}/teams')
        self.save_inventory('teams.json', teams)
        for team in sorted(teams, key=lambda item: item['slug'].lower()):
            slug = team['slug']
            team_id = str(team['id'])
            members = self.optional(f'team:{slug}:members', lambda slug=slug: self.client.get_paginated(f'/orgs/{quote(self.org)}/teams/{quote(slug)}/members', params={'role': 'all'}), [])
            memberships: list[dict[str, Any]] = []
            for member in sorted(members, key=lambda item: item['login'].lower()):
                username = member['login']
                membership = self.optional(f'team:{slug}:membership:{username}', lambda slug=slug, username=username: self.client.get(f'/orgs/{quote(self.org)}/teams/{quote(slug)}/memberships/{quote(username)}')[0], None)
                if membership is not None:
                    memberships.append({**membership, "_username": username})
            team_repos = self.optional(f'team:{slug}:repositories', lambda slug=slug: self.client.get_paginated(f'/orgs/{quote(self.org)}/teams/{quote(slug)}/repos'), [])
            self.save_inventory(f'teams/{slug}/team.json', team)
            self.save_inventory(f'teams/{slug}/members.json', members)
            self.save_inventory(f'teams/{slug}/memberships.json', memberships)
            self.save_inventory(f'teams/{slug}/repositories.json', team_repos)

    def export_organization_configuration(self) -> None:
        org_rulesets = self.optional('organization rulesets', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/rulesets'), [])
        self.save_inventory('organization-rulesets.json', org_rulesets)
        custom_roles = self.optional('custom organization roles', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/organization-roles', key='roles'), [])
        self.save_inventory('custom-organization-roles.json', custom_roles)
        custom_properties = self.optional('organization custom properties', lambda: self.client.get(f'/orgs/{quote(self.org)}/properties/schema')[0], [])
        self.save_inventory('custom-properties/schema.json', custom_properties)
        actions_permissions = self.optional('organization Actions permissions', lambda: self.client.get(f'/orgs/{quote(self.org)}/actions/permissions')[0], None)
        if actions_permissions is not None:
            self.save_inventory('actions/organization-permissions.json', actions_permissions)
        workflow_permissions = self.optional('organization Actions workflow permissions', lambda: self.client.get(f'/orgs/{quote(self.org)}/actions/permissions/workflow')[0], None)
        if workflow_permissions is not None:
            self.save_inventory('actions/organization-workflow-permissions.json', workflow_permissions)
        variables = self.optional('organization Actions variables', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/actions/variables', key='variables'), [])
        self.save_inventory('actions/organization-variables.json', variables)
        actions_secrets = self.optional('organization Actions secrets', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/actions/secrets', key='secrets'), [])
        self.save_inventory('actions/organization-secrets.json', actions_secrets)
        dependabot_secrets = self.optional('organization Dependabot secrets', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/dependabot/secrets', key='secrets'), [])
        self.save_inventory('dependabot/organization-secrets.json', dependabot_secrets)
        org_hooks = self.optional('organization webhooks', lambda: self.client.get_paginated(f'/orgs/{quote(self.org)}/hooks'), [])
        self.save_inventory('organization-webhooks.json', org_hooks)

    def export_repository(self, repo_summary: dict[str, Any]) -> None:
        repo = repo_summary['name']
        qorg = quote(self.org)
        qrepo = quote(repo)
        prefix = f'repositories/{repo}'
        details = self.optional(f'repository:{repo}:details', lambda: self.client.get(f'/repos/{qorg}/{qrepo}')[0], repo_summary)
        if self.personal:
            owner = (details.get("owner") or {}).get("login", "")
            if owner.casefold() != self.org.casefold() or details.get("id") != repo_summary.get("id"):
                raise GenerationError("GitHub repository moved or changed identity during discovery")
        self.save_inventory(f'{prefix}/repository.json', details)
        collaborators = self.optional(f'repository:{repo}:direct-collaborators', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/collaborators', params={'affiliation': 'direct'}), [])
        self.save_inventory(f'{prefix}/direct-collaborators.json', collaborators)
        protections = self.optional(f'repository:{repo}:branch-protection-rules', lambda: self.branch_protection_rules(repo), [])
        self.save_inventory(f'{prefix}/branch-protection-rules.json', protections)
        rulesets = self.optional(f'repository:{repo}:rulesets', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/rulesets', params={'includes_parents': 'false'}), [])
        self.save_inventory(f'{prefix}/rulesets.json', rulesets)
        environments = self.optional(f'repository:{repo}:environments', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/environments', key='environments'), [])
        self.save_inventory(f'{prefix}/environments.json', environments)
        for environment in environments:
            env_name = environment['name']
            self.export_environment(repo, env_name, environment)
        actions_permissions = self.optional(f'repository:{repo}:Actions-permissions', lambda: self.client.get(f'/repos/{qorg}/{qrepo}/actions/permissions')[0], None)
        if actions_permissions is not None:
            self.save_inventory(f'{prefix}/actions-permissions.json', actions_permissions)
        workflow_permissions = self.optional(f'repository:{repo}:Actions-workflow-permissions', lambda: self.client.get(f'/repos/{qorg}/{qrepo}/actions/permissions/workflow')[0], None)
        if workflow_permissions is not None:
            self.save_inventory(f'{prefix}/actions-workflow-permissions.json', workflow_permissions)
        variables = self.optional(f'repository:{repo}:Actions-variables', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/actions/variables', key='variables'), [])
        self.save_inventory(f'{prefix}/actions-variables.json', variables)
        actions_secrets = self.optional(f'repository:{repo}:Actions-secrets', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/actions/secrets', key='secrets'), [])
        self.save_inventory(f'{prefix}/actions-secrets.json', actions_secrets)
        dependabot_secrets = self.optional(f'repository:{repo}:Dependabot-secrets', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/dependabot/secrets', key='secrets'), [])
        self.save_inventory(f'{prefix}/dependabot-secrets.json', dependabot_secrets)
        hooks = self.optional(f'repository:{repo}:webhooks', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/hooks'), [])
        self.save_inventory(f'{prefix}/webhooks.json', hooks)
        deploy_keys = self.optional(f'repository:{repo}:deploy-keys', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/keys'), [])
        self.save_inventory(f'{prefix}/deploy-keys.json', deploy_keys)
        autolinks = self.optional(f'repository:{repo}:autolinks', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/autolinks'), [])
        self.save_inventory(f'{prefix}/autolinks.json', autolinks)
        pages = self.optional(f'repository:{repo}:pages', lambda: self.client.get(f'/repos/{qorg}/{qrepo}/pages')[0], None, ignore_http_statuses={404})
        if pages is not None:
            self.save_inventory(f'{prefix}/pages.json', pages)
        if not self.personal:
            # Custom properties belong to an organization schema, not personal repos.
            custom_properties = self.optional(f'repository:{repo}:custom-properties', lambda: self.client.get(f'/repos/{qorg}/{qrepo}/properties/values')[0], None)
            if custom_properties is not None:
                self.save_inventory(f'{prefix}/custom-properties.json', custom_properties)

    def export_environment(self, repo: str, environment: str, environment_data: dict[str, Any]) -> None:
        qorg = quote(self.org)
        qrepo = quote(repo)
        qenv = quote(environment)
        prefix = f'repositories/{repo}/environments/{filesystem_name(environment)}'
        variables = self.optional(f'repository:{repo}:environment:{environment}:variables', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/environments/{qenv}/variables', key='variables'), [])
        self.save_inventory(f'{prefix}/variables.json', variables)
        secrets = self.optional(f'repository:{repo}:environment:{environment}:secrets', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/environments/{qenv}/secrets', key='secrets'), [])
        self.save_inventory(f'{prefix}/secrets.json', secrets)
        environment_details = self.optional(f'repository:{repo}:environment:{environment}:details', lambda: self.client.get(f'/repos/{qorg}/{qrepo}/environments/{qenv}')[0], environment_data, ignore_http_statuses={404})
        self.save_inventory(f'{prefix}/environment.json', environment_details)
        deployment_policy = environment_details.get('deployment_branch_policy') or {}
        protection_rules = environment_details.get('protection_rules') or []
        has_branch_policy_rule = any((rule.get('type') == 'branch_policy' for rule in protection_rules if isinstance(rule, dict)))
        if deployment_policy.get('custom_branch_policies') is True and has_branch_policy_rule:
            branch_policies = self.optional(f'repository:{repo}:environment:{environment}:deployment-branch-policies', lambda: self.client.get_paginated(f'/repos/{qorg}/{qrepo}/environments/{qenv}/deployment-branch-policies', key='branch_policies'), [], ignore_http_statuses={404})
        else:
            branch_policies = []
        self.save_inventory(f'{prefix}/deployment-branch-policies.json', branch_policies)
        if branch_policies:
            self.note_not_managed(f'Deployment branch policies for {repo}:{environment} are inventoried; provider-generated repository_environment HCL may not represent each named policy as a separate resource.')

    def branch_protection_rules(self, repo: str) -> list[dict[str, Any]]:
        query = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    branchProtectionRules(first: 100, after: $cursor) {
      nodes {
        id
        databaseId
        pattern
        allowsDeletions
        allowsForcePushes
        dismissesStaleReviews
        isAdminEnforced
        requiresApprovingReviews
        requiredApprovingReviewCount
        requiresCodeOwnerReviews
        requiresCommitSignatures
        requiresConversationResolution
        requiresLinearHistory
        requiresStatusChecks
        requiresStrictStatusChecks
        restrictsPushes
        restrictsReviewDismissals
      }
      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""
        cursor: str | None = None
        output: list[dict[str, Any]] = []
        while True:
            data = self.client.graphql(query, {'owner': self.org, 'name': repo, 'cursor': cursor})
            repository = data.get('repository')
            if repository is None:
                return output
            connection = repository['branchProtectionRules']
            output.extend(connection['nodes'])
            page_info = connection['pageInfo']
            if not page_info['hasNextPage']:
                return output
            cursor = page_info['endCursor']
