# Architecture

## Data flow

```text
ForgeAdapter.discover()
    ↓
ForgeModel: identities, parent links, native settings, discovery observations
    ↓
ProviderAdapter.plan(): explicit native-kind handler registry
    ↓
ImportPlan: importable candidates + coverage decisions
    ↓
TfRunner.hydrate(): temporary init / schema / configuration-generation plan
    ↓
ProviderAdapter.compile(): schema cleanup, associations, private inputs
    ↓
CompiledProject
    ↓
ProviderAdapter.render(): static HCL, local modules, candidate imports
    ↓
configuration-driven adoption rehearsal
    ├─ import/no-op only → retain import blocks
    └─ plan differences/change → state-only import rehearsal
         ├─ no-op or recognised known provider behaviour → emit one-time adopt.sh
         └─ effective/unknown change → fail
    ↓
format + optional validate → reports → rollback-safe publication
```

Discovery and rendering never call one another. ForgeModel stores JSON-compatible
native settings, not HCL or Terraform addresses. Candidate/CompiledProject belong
to the compilation boundary and may contain HCL expressions. GitLab projects and
subgroups stay GitLab concepts; no GitHub-shaped common model discards native fields.

The existing GitHub domain model is deliberately retained as a provider-native
extension behind the same interfaces. This preserves its established final
addresses and richer model transformations without coupling other forges to it.

## Boundaries

| Package | Responsibility |
| --- | --- |
| `core/` | Source identity, neutral entities, versioned inventory, capabilities, compilation records, safe publication |
| `forges/shared/` | Read-only HTTP, pinned-origin pagination, retries, bounded cache, Gitea-family discovery helpers |
| `forges/{forge}/` | API paths, pagination details, native object discovery and normalization |
| `providers/{forge}/` | Resource/import contracts and handler registries; no API calls |
| `providers/github/handlers/` | Separate access, repository, Actions, webhook and organization transformations |
| `hcl/` | Provider-generated HCL parsing, escaping, field emission, schema cleanup |
| `generators/` | Common static project/module emission |
| `opentofu.py` | Sole generator subprocess boundary; never applies plans. It may import into disposable local state solely to prove a state-only adoption path has no effective live changes. |
| `pipeline.py` | Staging, orchestration, validation, reporting and publication |
| `cli.py` | Argument parsing and error presentation, not business logic |

Each forge/provider pair is registered explicitly in `core/registry.py`. There is
no entry-point plugin auto-loading or importing arbitrary code from API payloads.
The architecture can accommodate another provider for the same forge without
pretending all resources or import IDs have identical semantics. The current CLI
selects one built-in provider per forge; arbitrary provider selection is not exposed.

## Handlers and pure functions

Stateful components are Connection/RESTClient, forge discovery handlers, provider
adapters, GitHub's DomainModelBuilder, TfRunner and Pipeline. HCL escaping,
naming, schema inspection and rendering stay ordinary functions/dataclasses.
Native mapping handlers are small methods selected by an explicit registry, not a
large conditional that knows every platform.

## Provider schema is not an import contract

The exact installed provider schema supplies configurable/computed/sensitive
attributes and nested block shapes. It does not prove that a resource imports,
specify import-ID encoding, or provide all replacement/update semantics. Verified
contracts stay in native handlers. Unknown types are reported; no fake placeholder creates are generated. A one-time
`adopt.sh` is emitted only when the provider cannot safely perform configuration-
driven import without proposing live changes. It is removable after adoption and is
not part of ongoing management.

Provider hydration can return useful generated HCL with a nonzero plan status.
Heckle accepts this intermediate only when every candidate received configuration.
That intermediate is not an adoption plan and is never applied.

## Presence semantics and provider defaults

An absent remote/provider field is not interchangeable with `attribute = null`.
Providers can apply defaults to null Optional/Computed values, validate dependent
arguments differently, or populate values only when a parent feature is enabled.

Native compilation therefore preserves each resource body's exact configured shape.
Instances with different argument presence are emitted into separate resource profiles
rather than sharing a `for_each` block with `try(..., null)`. Provider handlers can
also add **per-profile** `ignore_changes` for values that are inapplicable or cannot
be read back reliably. Nested heterogeneous shapes fail closed when Heckle cannot
represent them without changing presence semantics.

This rule applies to the shared GitLab/Gitea/Forgejo renderer. GitHub retains its
specialized renderer, but is protected by the independent adoption-plan gate below.

## Final adoption safety gate

For a fresh generation, after final rendering/formatting/validation Heckle first
runs a real Terraform/OpenTofu plan against the import declarations and parses `tofu show
-json`. A pure import/no-op plan is accepted.

If configuration-driven import itself would update a live object, Heckle does not
publish that plan. It removes the active import blocks and rehearses the same final
HCL by importing each object into **disposable local state** with the state-only CLI
import operation. It then runs a normal plan. The default remains strict: non-no-op
managed changes fail. A provider adapter may recognise a narrowly-scoped, version-specific behaviour when
the plan satisfies explicit before/after guard conditions. Recognition is descriptive
only; it is not a claim that applying the update is safe or appropriate.

If the rehearsal is no-op or contains only recognised known provider behaviour, the
published project contains a one-time `adopt.sh`. The helper independently checks the exact
rehearsed addresses, attribute names and controlling-feature guards in `tofu show
-json`; it never accepts an arbitrary exit code 2 and never applies a plan.

Any create, delete, replacement, unrecognised update, or change outside the provider-
specific compatibility rule aborts generation. Diagnostics contain resource addresses and changed attribute
names only, never attribute values. This gate is deliberately independent of
provider-specific compilation, so it catches future round-trip mistakes across
GitHub, GitLab, Gitea and Forgejo rather than assuming schema validity means
adoption is free of unrecognised changes.

When `--state-root` is used, the detached output directory does not contain the
caller's state, so the fresh-adoption gate is skipped rather than producing false
create actions. That workflow remains an explicit existing-state operation.

## Dependency graphs and sensitive values

GitLab groups are partitioned into parent-depth resource modules. A group references
only an earlier depth, and projects reference their namespace outputs. Independent
family locals avoid cycles caused by passing an entire interconnected forge model
to every module. Gitea/Forgejo remain distinct handlers over shared API mechanics.

Native modules receive configuration separately from public identity keys. The
GitHub renderer preserves existing addresses and rewrites its generated for_each
expressions to iterate public keys only. Only keys are declassified; secret values
are not marked nonsensitive. Outputs contain required identity fields, not entire
resource objects. HCL string interpolation markers in literal API data are escaped.

## Publication and lifecycle

A run builds in a private temporary sibling directory, removes intermediate API
snapshots/provider output on exit, and publishes only after completion. An existing
output can be replaced with --force only while it remains a marked disposable
generation without backend, state, tfvars or initialization. Failure restores the
previous output. A user can always move/copy files manually; these guards are not a
filesystem sandbox or security boundary against another process running as that user.

A normal generation does not read existing state. --state-root is explicit,
identity-checked, and only lists addresses. It does not infer a root from cwd or
filter state by repository name. Regeneration is not a live reconciliation service.

## Provider compatibility boundary

Provider-specific upstream behaviour is deliberately separated from the generic
compiler. `heckle.compatibility` owns audited provider pins, version-scoped quirks
and links to upstream issues/docs. Provider adapters may supply narrowly-scoped
semantic adoption rules, but the generic pipeline remains fail-closed.

This means an upstream provider release does not automatically become a Heckle
runtime change. A provider bump is an explicit compatibility review; see
`docs/DEVELOPMENT.md#provider-compatibility`.
