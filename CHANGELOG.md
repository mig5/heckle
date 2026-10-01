# Changelog

## 0.1.3

* Check Terraform/OpenTofu compatibility before discovery or inventory replay, reporting the selected executable, unsupported version and required range. Inventory-only commands remain independent of the CLI.
* Explain provider bootstrap progress and why resource HCL has not yet been generated at that stage.

## 0.1.2

* Leave Gitea repository `allow_manual_merge` and `autodetect_manual_merge` settings unmanaged under `go-gitea/gitea` 0.8.1 because imported state does not reliably populate them and provider defaults otherwise produce unsafe adoption updates.
* Treat Gitea team definitions, unit permissions and team repository grants as inventory-only because provider 0.8.1 loses imported `units_map` values, produces unstable `units` representations and cannot safely update imported granular or owner teams.
* Continue managing authoritative Gitea team membership lists when discovery is complete, using the existing numeric team ID without depending on an unsafe managed `gitea_team` resource.
* Add regression coverage ensuring unsafe Gitea teams are excluded, team memberships retain literal team IDs, and the affected repository settings remain unmanaged.

## 0.1.1

- Remove `validate` subcommand which didn't add much value.

## 0.1.0

- Build RPM source archives from a staged, correctly named source directory instead of relying on `tar --transform`, which is rejected by Fedora 43's tar invocation.
- Make release tests wheel-build-aware under Debian pybuild, invoke `tests.sh` through Bash instead of relying on an executable bit preserved by wheel unpacking, and install Debian suite-name metadata in the package builder.
- Keep the alpha3 Forgejo repair utility in source distributions, but out of installed wheels and system packages; installed-wheel test runs skip its source-only regression module.
- Remove the unreleased `--tofu` and `OPENTOFU_BIN` compatibility aliases; use `--tf` and `HECKLE_TF_BIN`.
- Complete a source-level compatibility audit of all resource families Heckle manages with `integrations/github` 6.13.0 and `go-gitea/gitea` 0.8.1, plus follow-up passes over `svalabs/forgejo` 1.6.0 and `gitlabhq/gitlab` 19.3.0.
- Record the audited resource scope for all four providers and test the native Gitea/Forgejo scope against explicit adapter contracts, preventing future resource-family additions from silently bypassing compatibility review.
- Leave Gitea's create-only `repo_admin_change_team_access` and local `archive_on_destroy` controls unmanaged instead of exporting schema defaults as remote state.
- Omit Gitea team repository lists when `include_all_repositories` is enabled; the list is then derived, inapplicable state that otherwise changes as repositories are added or removed.
- Harden credential-bearing URL validation against encoded/double-encoded dot segments, backslash ambiguity, fragments, malformed ports and origin spelling differences.
- Refuse `--force` replacement when a marked output contains nested symbolic links.
- Mark deterministic SHA-1 address/filename hashes explicitly as non-security uses, preserving existing generated addresses while making the security intent machine-checkable.
- Add a release security-audit record covering the threat-model review, static analysis, dependency posture and residual trust boundaries.

## 0.1.0-alpha13

- Complete a source-level compatibility audit of every `gitlabhq/gitlab` 19.3.0 resource family Heckle currently manages: groups/projects, memberships, branch/tag protections, hooks, deploy keys, environments, approval rules, labels and badges.
- Record that audited resource scope separately from quirks and test that it remains aligned with the GitLab adapter, so newly-managed GitLab resource families require an explicit compatibility review.
- Treat empty GitLab project/group membership `expires_at` values as unset while preserving valid `YYYY-MM-DD` dates.
- Normalize empty provider-validated GitLab enum/date fields instead of emitting invalid empty strings, including project/group settings, branch protection, hooks, deploy-key expiry and environment settings.
- Exclude project/group create-, import- and destroy-only controls that cannot be reconstructed as existing remote state, plus GitLab fields whose imported values are unavailable.
- Normalize GitLab parent/child and mutually-exclusive settings for environments, hook branch filters, approval rules and protected-tag ACL selectors.
- Leave GitLab group `push_rules` unmanaged for provider 19.3.0 while its known zero/false/empty-value round-trip bug remains applicable.
- Treat non-representable approval-rule forms as inventory-only, including imported `any_approver` rules and non-canonical `report_approver` rules that provider 19.3.0 refuses to recreate.
- Improve `--keep-workdir` plan diagnostics to show the required backend-disabled init command before the exact failing plan command.
- Stop forcing `-parallelism=1` during provider hydration and adoption rehearsals; Terraform/OpenTofu now use their normal graph concurrency, allowing provider-side parallel request support to be effective.


## 0.1.0-alpha12

- Enable parallel GitHub provider requests (`parallel_requests = true`) to avoid unnecessarily serial provider API operations.
- Treat an empty GitLab `reviewer_assignment_strategy` as unset instead of rendering an invalid enum value. Valid configured strategies remain managed.
- `--keep-workdir` now reports the actual Terraform/OpenTofu command and directory that failed, including plan-stage failures.

## 0.1.0-alpha11

- Add `--keep-workdir` to retain a private temporary generation workspace after failure for diagnostics.
- Print the retained workspace path and the exact Terraform/OpenTofu validation commands to rerun privately.
- Exclude GitLab `mr_default_target_self` together with `forked_from_project_id` during adoption; the provider requires both and the setting only applies to forked projects.

## 0.1.0-alpha10

- Support both OpenTofu and Terraform CLI binaries for generation, validation, provider hydration and adoption.
- Add `--tf` as the CLI selector; OpenTofu (`tofu`) remains the default.
- Add `HECKLE_TF_BIN` as the executable environment override.
- Generated adoption/readme instructions follow the selected CLI instead of assuming `tofu`.
- Preserve an explicit `--tf /path/to/binary` in generated one-time adoption instructions while keeping simple binary names portable.


## 0.1.0-alpha9

- Standardise adoption terminology on “known provider behaviour”.
- Make recognised provider behaviour explicitly descriptive rather than a safety verdict.
- Generated adoption guidance now leaves reconciliation decisions to the operator and states that applying a plan may modify remote resources.
- Add operator-responsibility language to generated projects, adoption docs and security guidance.

## 0.1.0-alpha8

- Add a version-scoped provider compatibility registry and `heckle compatibility`.
- Refuse unaudited `--provider-version` overrides unless explicitly opted into with `--allow-untested-provider`.
- Leave GitHub organization repository-creation booleans unmanaged while provider issue #3429 remains applicable.
- Avoid enforcing an explicit empty GitHub Actions `patterns_allowed` list while the provider cannot serialize it reliably.
- Exclude GitLab create/destroy-control fields that are not imported remote state, and surface the documented default-branch protection takeover caveat.
- Add narrow GitLab known-provider-behaviour rules for skipped-pipeline and merge-train settings.
- Document a provider-upgrade compatibility review process.

## 0.1.0-alpha7

- Recognise narrowly-defined provider behaviour only while its explicit controlling-feature guard remains satisfied.
- Keep state-only adoption for drift cases so declarative import never writes provider defaults back to the forge.
- Teach generated `adopt.sh` to verify the exact rehearsed address/attribute/guard set rather than requiring an absolutely empty plan.
- Keep create/delete/replace, active-setting changes and unknown drift as hard adoption failures.
- Add an adoption-flow diagram and plain-text fallback under `docs/ADOPTION.md`.

## 0.1.0-alpha6

- Fix adoption for providers whose configuration-driven import plans apply schema defaults before `ignore_changes` can use prior state.
- Fall back to a one-time, state-only `adopt.sh` when declarative imports would mutate live resources, and require the follow-up plan to be empty.
- Report changed attribute names (never values) when an adoption safety plan is unsafe.
- Preserve the no-runtime-worker result: `adopt.sh` is removable immediately after successful adoption.
- Preserve existing alpha3/alpha4 state across native presence-profile address changes with generated `moved` blocks instead of duplicate imports.
- Fix release metadata paths for documentation stored under `docs/`.

## 0.1.0-alpha5

- Preserve provider argument presence during native multi-resource rendering; missing
  attributes are no longer rendered as `null` across heterogeneous instances.
- Split native resource instances into presence profiles where omission semantics differ.
- For Forgejo repositories with disabled PR/wiki/issue features, omit and per-profile-ignore inapplicable provider-defaulted settings so adoption cannot enable/change them.
- Add a final OpenTofu adoption safety plan and refuse generation on create/update/delete/replace.
- Move Python build/publish back to Poetry.
- Keep apt/yum repository uploads and Forgejo release publication outside `release.sh`.

## 0.1.0-alpha4

- Fix invalid Forgejo repository configuration from provider import defaults.
- Omit creation/migration fields and options the pinned provider cannot read.
- Keep disabled features disabled; make dependent settings optional per repository.
- Leave mirror settings outside generated configuration rather than invent a clone URL.
- Add a preview-first, local-only repair for existing alpha3 repository modules.
- Explain the personal-repository creation warning in plain English.
- Add pytest regression cases for import defaults, mixed repositories and repair safety.

## 0.1.0-alpha3

- Add `--user` and `--me` to generation and inventory for all four forges.
- Filter personal discovery by actual ownership; keep user and namespace IDs separate.
- Preserve resolved namespace type in inventories, with version-1 organization/group replay support.
- Do not generate personal account or artificial organization resources.
- Replace one-off verification files with `tests.sh`, pytest and terminal coverage.
- Build/sign locally and publish to PyPI through `release.sh`; remote uploads remain separate.
- Remove automatic GitHub publishing/package-upload workflows.


## 0.1.0-alpha2 — 2026-09-19

- Rename the project/package/CLI to Heckle; keep the HCL output independent of it.
- Separate neutral discovery data, forge adapters, provider/import handlers,
  compilation, schema inspection, HCL rendering and orchestration.
- Port the existing GitHub native model and stable final address layout.
- Add recursive GitLab group/project discovery and native import handlers.
- Add separate Gitea and Forgejo adapters sharing only API mechanics.
- Report inventory-only, unsupported, non-importable and permission/API gaps.
- Use explicit import contracts plus the installed provider schema; never assume
  a resource is importable solely because its schema exists.
- Add bounded transport caching, shared retry waits, origin-pinned pagination,
  CI-value redaction, private-input handling and staged output publication.
- Add regression tests, synthetic multi-forge fixtures, documented coverage and
  security limits, PyPI/DEB/RPM/AppImage packaging and release workflows.

## 0.1.0-alpha1 — predecessor

Initial modular GitHub-only package.
