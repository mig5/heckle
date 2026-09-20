# Heckle

**Turn existing software forges into maintainable Terraform or OpenTofu.**

Heckle inventories personal accounts on GitHub, GitLab, Gitea and Forgejo, as well
as organizations and GitLab group hierarchies, maps supported objects to explicit provider
import contracts, and emits an editable project of `.tf` files and local modules.
The name is a play on HCL.

**Heckle is currently alpha software.**

Heckle never runs `apply` and never persists its temporary validation state.
When a provider cannot use configuration-driven import blocks without producing plan
differences, generation may rehearse a state-only CLI `import` in disposable local state. A provider
handler can recognise narrowly-defined, version-scoped **known provider behaviour** under
explicit guard conditions. Recognition is descriptive: it does not mean Heckle considers
a resulting plan safe or appropriate to apply. After successful adoption, **Heckle and
Python are not required for ongoing Terraform/OpenTofu use**. A generated one-time `adopt.sh` may
invoke `python3` solely to verify the selected CLI's `show -json` output against the behaviour Heckle rehearsed;
it contains no Heckle runtime dependency and is removed after adoption.
There are no ongoing inventory reads, custom providers, agents, provisioners or
external-data workers. Terraform or OpenTofu, the selected provider, credentials, your chosen
backend and any required private inputs remain the ordinary prerequisites.

Heckle assists with discovery and code generation, but provider behaviour, forge APIs,
defaults, import semantics and upstream bugs can produce unexpected plans. Heckle attempts
to detect and classify known cases, but it cannot guarantee that a generated or reconciled
plan is safe, complete, or free from unintended changes. Always review Terraform/OpenTofu plans before
applying them. The decision to apply a plan, and responsibility for resulting changes to
remote systems, remains with the operator.

## Install this source release

Requires Python 3.11 or newer. There are no runtime Python dependencies.
OpenTofu or Terraform 1.8 or newer, below 2.0, is required for `generate`. OpenTofu is the default.
`inventory` and `coverage` do not need either CLI.

```sh
pipx install ./heckle-0.1.0-py3-none-any.whl
# Or, from this source checkout:
pipx install .
heckle --version
```

This source release does not itself reserve a PyPI name or publish any packages.
Once the maintainer publishes it, the normal PyPI installation name is `heckle`.
The repository also contains DEB, RPM and AppImage build definitions. The AppImage
bundles Python, not Terraform or OpenTofu. See [release instructions](docs/DEVELOPMENT.md#releasing).

## One command to generate

Supply the appropriate token through the environment; no token option is accepted
on the command line and tokens are not rendered into provider configuration.

```sh
# Set GITHUB_TOKEN or GH_TOKEN in your shell or secret manager first.
heckle generate github --org example --out ./example-github

# Set GITLAB_TOKEN first. --group accepts a full nested group path.
heckle generate gitlab --group example/platform --out ./example-gitlab
heckle generate gitlab --group example --url https://gitlab.example.org

# Set GITEA_TOKEN first.
heckle generate gitea --org example --url https://git.example.org

# Set FORGEJO_API_TOKEN (or FORGEJO_TOKEN) first.
heckle generate forgejo --org example --url https://code.example.org
```

## Personal accounts

The same selectors work for all four forges, with both `generate` and `inventory`:

```sh
heckle generate github --me --out ./my-github
heckle generate gitlab --me --out ./my-gitlab
heckle generate gitea --me --url https://git.example.org --out ./my-gitea
heckle generate forgejo --me --url https://code.example.org --out ./my-forgejo

# Select a named personal account instead of the token's owner:
heckle inventory github --user alice --out ./alice-snapshot
heckle generate gitlab --user alice --url https://gitlab.example.org
heckle generate forgejo --user alice --url https://code.example.org
```

Choose exactly one of `--me`, `--user`, or the existing `--org` / `--group`.
`--me` resolves the token's account; `--user` selects a username, not a display
name. Personal discovery requires a user-scoped token that can read `/user`.
The default `--me` output directory is named `heckle-<forge>-me`; saved metadata
contains the resolved username, never a token-dependent alias.

Only repositories/projects **owned by the selected personal namespace** are
included. Collaborations in other accounts, organizations or groups are excluded,
even with an administrator token. No personal login account, artificial
organization, group, team or organization membership is created or managed.
Repository-level collaborators remain within the adapter's existing coverage.

For your own account, either selector uses the authenticated repository listing
so private repositories visible to the token are included. GitHub's named-user
endpoint is public-only, so other-user discovery also merges accessible private
collaborations. GitLab personal projects use the actual user **namespace ID**,
which is not the account's user ID; owned group projects are filtered out.
GitLab hides the project listing for another user's private profile: Heckle
rejects a known private-profile response rather than reporting a complete empty
snapshot. Use that account's token with `--me` instead. Token repository selection
and API visibility can still hide objects; this is never an instance-wide backup.

Personal mode does not extend provider feature coverage. In particular, the
pinned Forgejo provider can import an existing `owner/repository`, but its
creation path for an explicitly named user can require instance administration.
Import/update existing repositories; review any proposed creation or replacement
rather than assuming the exporter can restore a deleted personal repository.

Snapshots record `namespace_type`, and replay/state filtering must match it.
Old version-1 snapshots remain organization/group snapshots. Replaying `--me`
resolves the current token before comparing identities; `--user` replay skips
identity discovery but provider hydration still contacts the forge.

GitHub defaults to `https://api.github.com`; GitLab defaults to `https://gitlab.com`.
Gitea and Forgejo require a host. Host environment alternatives are
`GITHUB_BASE_URL`, `GITLAB_BASE_URL`, `GITEA_BASE_URL` and `FORGEJO_HOST`.
For GitHub Enterprise Server, supply the API base ending in `/api/v3`.
GitLab/Gitea/Forgejo accept the site root or their `/api/v4`/`api/v1` API root;
Heckle normalizes the latter. HTTPS verification stays enabled. Use `--ca-file`
for a private CA. `--allow-http` is an explicit, insecure local-test opt-in.

OpenTofu (`tofu`) is the default CLI. Pass `--tf terraform` (or a full path) to use Terraform instead. `HECKLE_TF_BIN` is the environment override.

Examples below use `tofu`; substitute `terraform` when that is the CLI you selected. There is no organization-specific wrapper or secret-management requirement.

## Inspect first, then generate

```sh
heckle inventory gitlab --group example --out ./snapshot
heckle coverage ./snapshot
heckle coverage ./snapshot --json

# Reuses discovery, but the provider still reads the live forge while hydrating.
heckle generate gitlab --group example --from-inventory ./snapshot --out ./iac
heckle coverage ./iac
```

`inventory` intentionally succeeds with a report of optional API gaps, so that
you can inspect permissions. `generate` refuses recorded permission/API failures
unless `--allow-partial` is explicitly supplied. A 404 can mean an absent feature
or a hidden endpoint; it is recorded as unavailable, not proof of completeness.
Objects outside the adapter's coverage are reported and never converted into
create operations. Archived repositories are inventoried but not managed.

Common generation options:

| Option | Meaning |
| --- | --- |
| `--workers 4` | Bounded repository discovery concurrency, 1–16 |
| `--keep-inventory` | Keep a private snapshot under `.generation/inventory/` |
| `--keep-workdir` | On generation failure, retain the private temporary build workspace and print commands for diagnostics |
| `--no-validate` | Skip final validation, not hydration or formatting |
| `--tf PATH` | Terraform or OpenTofu executable; defaults to OpenTofu (`tofu`) |
| `--provider-version X.Y.Z` | Override the exact pin; does not invent new import contracts |
| `--state-root PATH` | Read state from an explicitly identified Heckle project to omit existing import addresses |
| `--allow-destroy` | Omit generated `prevent_destroy` guards |
| `--split-teams` | GitHub only: a file per team |
| `--force` | Replace only a marked disposable generation, never an initialized/adopted project |

For state filtering, retain that project's private `.generation/source.json`
identity record. Forge, host, namespace type and scope must match. Heckle only reads
that state during generation. If a native presence-profile refactor changed one of
Heckle's own resource addresses, the generated project includes an explicit `moved`
block so Terraform/OpenTofu can migrate the address when you later run the normal workflow; the
remote object is not imported a second time.

## What is implemented

| Forge | Provider pin | Implemented import mappings |
| --- | --- | --- |
| GitHub | `integrations/github` 6.13.0 | Existing organization/member/team/repository architecture, grants, protections, rulesets, Actions and secret metadata, environments, webhooks, keys, autolinks, properties, default branches and Pages |
| GitLab | `gitlabhq/gitlab` 19.3.0 | Groups/subgroups, projects, direct memberships, protected branches/tags, group/project hooks, deploy keys, environments, approval rules, group/project labels and badges |
| Gitea | `go-gitea/gitea` 0.8.1 | Organizations, repositories, teams, complete authoritative team membership snapshots, team repository lists and repository hooks |
| Forgejo | `svalabs/forgejo` 1.6.0 | Repositories, teams, repository hooks and importable branch protections |

These are compatibility pins, not a promise to track the newest provider release.
See [provider compatibility](docs/DEVELOPMENT.md#provider-compatibility) and
`heckle coverage` for exclusions, import coverage and known provider behaviour.
In particular, having a provider resource does **not** imply it can import an
existing object. Forgejo organizations are currently inventory-only because the
chosen provider's organization resource has no import implementation.

Heckle is not a forge-to-forge data migrator or backup tool. Git history, issues,
pull requests, attachments, artifacts and repository file contents are not cloned
or recreated. GitLab's settings are not translated into GitHub equivalents.

## Adopt the output

See [Adoption safety and fallback flow](docs/ADOPTION.md) for the full decision path.

GitHub retains its domain layout and resource addresses. New adapters use one data
file per namespace/project/repository and reusable modules per native resource
family. Hashed suffixes avoid collisions; they are not runtime lookups.

```text
provider.tf
versions.tf
modules.tf
imports.tf                         # when configuration-driven import is accepted
adopt.sh                           # otherwise, one-time state-only adoption helper
group-example-<identity>.tf
project-example-platform-api-<identity>.tf
variables.tf                       # when private inputs are needed
modules/
  gitlab_group_level_0/
  gitlab_group_level_1/
  gitlab_project/
  gitlab_project_hook/
.generation/report.json
README.md
```

Supply required variables in an uncommitted tfvars file, a secret manager or
`TF_VAR_...`. Webhook URLs and provider-required configuration maps have no blank
default. Keep state, plans, inventories and webhook URLs confidential.

```sh
cd ./iac
# Add backend configuration and supply credentials/private variables first.
tofu init
tofu plan -out=import.tfplan
tofu show import.tfplan
# Review every change before running this yourself:
tofu apply import.tfplan
tofu plan
```

For a **fresh adoption**, Heckle first runs the final configuration-driven import
plan privately and parses the selected CLI's `show -json` output. If that plan is import/no-op only, the
project keeps ordinary `import` blocks.

Some providers apply schema defaults during import before `ignore_changes` can use
prior state. If declarative import produces plan differences, Heckle removes the
active import blocks and rehearses the selected CLI's state-only `import` into disposable local state. A
provider adapter may recognise narrowly-defined, version-scoped differences as
**known provider behaviour** when explicit before/after guard conditions match.
Create/delete/replace and unrecognised updates still stop generation.

When the state-only rehearsal is either no-op or contains only recognised provider
behaviour, Heckle publishes the project with a one-time `adopt.sh`. CLI import changes state only; the helper never runs `apply`. If the follow-up plan contains
recognised provider behaviour, `adopt.sh` explains what Heckle recognised and leaves
the apply decision to the operator. Recognition does not establish that the plan is
safe or appropriate for your environment. Review the plan and provider behaviour
yourself before deciding whether to apply it.

The report records changed attribute names, never their values. Credentials, remote
settings or provider versions can change after generation, so always review plans.
`prevent_destroy` does not stop in-place changes or protect a resource after removing
its entire resource/module block. `--state-root` is an existing-state workflow and
cannot use the detached fresh-adoption gate.

The carried-over GitHub secret resources use an ignored provider-required
placeholder value. **Do not use them to create or restore secrets without a real
value.** The new adapters redact CI variable values and leave them unmanaged.
Gitea team membership is authoritative: review changes/removals especially carefully.

After adoption, edit the `.tf` files and modules directly and remove the
`.heckle-generated` marker. The generator is no longer part of normal operation.
Do not regenerate over your maintained configuration.

## Develop and release

Heckle uses Poetry.

```sh
poetry install
./tests.sh                         # pytest + terminal branch coverage
./tests.sh --cov-report=html       # optional HTML coverage report

# Local Python package rehearsal
rm -rf dist && poetry build

# Full local release build/sign, then PyPI publication last
./release.sh
```

There is no custom verification JSON or Twine release path. `release.sh` uses
`poetry build`, `pyproject-appimage`, the DEB/RPM builders/signers, and finally
`poetry publish`. Remote apt/yum uploads and Forgejo release creation stay in the
maintainer's separate scripts. The application's `heckle coverage` command reports
forge support; pytest coverage measures Python code execution.

See [architecture](docs/ARCHITECTURE.md), [development](docs/DEVELOPMENT.md),
[security](SECURITY.md) and [release options](docs/DEVELOPMENT.md#releasing).

License: GPL-3.0-or-later. Copyright 2026 Miguel Jacq.


## Provider compatibility

Heckle pins exact, audited provider versions rather than following `latest`. Known
upstream quirks and the provider-upgrade checklist are documented in
[`docs/DEVELOPMENT.md#provider-compatibility`](docs/DEVELOPMENT.md#provider-compatibility). Run
`heckle compatibility` to see the audited pins and active compatibility rules in
this release.
