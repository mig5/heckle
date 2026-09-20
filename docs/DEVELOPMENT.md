# Development

Heckle requires Python 3.11 or newer and has no runtime Python dependencies.
Development uses Poetry.

```sh
poetry install
./tests.sh
```

`tests.sh` runs the normal pytest suite with branch coverage and a terminal
missing-lines report, then exercises the installed `heckle` CLI. Ordinary pytest
arguments pass through:

```sh
./tests.sh -k personal
./tests.sh --cov-report=html       # then open htmlcov/index.html
poetry run pytest tests/test_hcl.py
```

There is no custom verification JSON or separate test-summary format. `.coverage`
and `htmlcov/` are local pytest/coverage artifacts. `heckle coverage PATH` is a
separate application command: it reports discovered/importable forge settings,
not Python test coverage.

The tests use synthetic API responses and provider doubles. They do not contact a
real forge, apply a Terraform/OpenTofu plan, publish packages or use a signing key. The
release-script tests substitute fake Poetry/Docker/signing commands so failures
before publication can be exercised safely.

## Working on handlers

Keep API discovery in `forges/`, provider/import semantics in `providers/`, and
shared HCL rendering in `hcl/` / `generators/`. Add a regression fixture/test
alongside a handler change.

A critical adoption rule is **presence is data**. Do not turn a field that is
absent for one remote object into `attribute = null` merely so multiple objects
can share one `for_each` block. Provider defaults, validators and
Optional+Computed attributes can make null/omitted configuration behave
surprisingly during import. Native resources are partitioned by configuration
shape; provider handlers must also ignore values that are inapplicable or cannot
be read back reliably.

The independent final adoption-plan gate is mandatory for fresh generation: a
plan containing a create, update, delete or replacement must fail generation.
It is a safety backstop, not a substitute for fixing provider-specific rendering.

Personal accounts are existing owner namespaces, not accounts to create. Filter
repository ownership before collecting children. Preserve resource addresses
unless deliberately providing a Terraform/OpenTofu state migration.

CI calls the same `./tests.sh` entry point.

## Provider compatibility

Heckle deliberately does **not** track provider `latest` at runtime.  It pins one
provider version per forge and treats a provider upgrade as a compatibility review.
This is intentional: a provider can change import IDs, defaults, read semantics or
update behaviour without changing the forge API itself.

Run:

```sh
heckle compatibility
heckle compatibility github
heckle compatibility --json
```

The command shows the exact provider pins audited by this Heckle release and the
known upstream quirks for those versions.  The same information is written to
`.generation/report.json` and shown by `heckle coverage`.

### Compatibility rules

Provider-specific knowledge belongs in `heckle.compatibility` or in a narrowly
scoped semantic adoption rule on the provider adapter.  Do not add issue-specific
conditionals to the generic pipeline.

Each recorded quirk should have:

- an identifier;
- the exact affected provider version(s);
- the affected resource and attributes;
- what Heckle does (`unmanaged`, conditional omission, known-provider-behaviour
  recognition, and so on);
- an upstream issue or provider-documentation URL whenever one exists.

The default policy remains strict. A provider adapter may recognise planned updates
only when they match a version-scoped compatibility rule and its explicit before/after
guards. Recognition is descriptive and does not establish that applying the plan is
safe or appropriate. Create, delete, replacement and unrecognised changes are never
accepted by a compatibility rule.

For GitLab 19.3.0, the audit covers every resource family the current adapter manages:
groups and projects, group/project memberships, branch/tag protections, group/project
hooks, deploy keys, project environments, project approval rules, labels and badges.
That scope is recorded separately from the quirk list, because an audited resource may
need no workaround. A regression test keeps it aligned with the adapter so adding a new
managed GitLab resource requires an explicit compatibility review.

### Upgrading a provider pin

Do not change a provider pin as routine dependency churn.  For each proposed bump:

1. Read the provider release notes and open import/state-related issues since the
   currently audited version.
2. Compare `tofu providers schema -json` for the old and new versions, concentrating
   on resources Heckle manages and on Optional/Computed/default/ForceNew changes.
3. Run the complete Heckle test suite, including compatibility and adoption-safety
   fixtures.
4. Rehearse discovery/import against a disposable test namespace for that forge and
   require Heckle's adoption safety gate to pass.
5. Review every existing compatibility quirk: keep it, update its affected versions,
   or remove it only when the upstream behaviour is demonstrably fixed.
6. Update both the provider pin and `AUDITED_VERSIONS`.  They are intentionally
   separate so a casual pin bump fails tests until this review is acknowledged.

An arbitrary `--provider-version` override is rejected unless
`--allow-untested-provider` is also supplied.  That opt-in is for investigation,
not a supported migration path.

### Current audited pins

Use `heckle compatibility` as the authoritative machine-readable view.  At release
For 0.1.0 the audited pins are:

| Forge | Provider | Version |
| --- | --- | --- |
| GitHub | `integrations/github` | `6.13.0` |
| GitLab | `gitlabhq/gitlab` | `19.3.0` |
| Gitea | `go-gitea/gitea` | `0.8.1` |
| Forgejo | `svalabs/forgejo` | `1.6.0` |

### Watching upstream without chasing every commit

The maintenance target is **provider releases, not upstream main branches**.  It is
enough to watch release notifications and provider issues for the four pinned
providers.  A new release is a prompt to evaluate it, not an automatic upgrade.

Heckle's final plan classifier remains the backstop for behaviour that was not known
in advance.  If a provider starts round-tripping a resource differently, a fresh
adoption should fail closed rather than silently alter the forge.

## Releasing

Heckle uses Poetry for Python packaging and PyPI publication.

### Setup

```sh
poetry install
```

A full release also requires Docker, `qubes-gpg-client` (for detached signatures)
and `rpmsign`. Terraform/OpenTofu remains an external runtime dependency and is not bundled
into the Python package or AppImage.

Configure PyPI authentication through Poetry in the usual way, for example with
a token stored by Poetry rather than committed to the repository:

```sh
poetry config pypi-token.pypi 'pypi-...'
```

### Release flow

```sh
./release.sh
```

The script intentionally stays simple:

```text
filedust (when installed)
  -> ./tests.sh
  -> poetry build
  -> poetry run pyproject-appimage
  -> detached signatures
  -> Debian/Ubuntu package builds
  -> Fedora RPM build + rpmsign
  -> poetry publish
```

PyPI publication is last. If tests, packaging or signing fails, the Python
release is not published.

The current native targets are Debian Bookworm/Trixie, Ubuntu Noble and Fedora
43. Package-repository synchronization and Forgejo release creation are
**deliberately not part of `release.sh`**. The finished `dist/` tree can be handed
to the same separate repository/upload scripts used by the maintainer's other
projects.

### Build without publishing

For a local Python-package rehearsal, run Poetry directly:

```sh
./tests.sh
rm -rf dist
poetry build
```

For an AppImage rehearsal:

```sh
poetry run pyproject-appimage --output dist/Heckle.AppImage
```

A full `release.sh` is intentionally a release action and ends with
`poetry publish`; it does not carry a second family of `--no-publish`/Twine-style
modes.

### Signing

The release script follows the existing mig5 Split-GPG workflow:

```sh
qubes-gpg-client --batch --armor --detach-sign FILE > FILE.asc
```

RPMs are also signed in place with:

```sh
rpmsign --addsign FILE.rpm
```

Configure the host's normal RPM/GPG integration before releasing. The repository
does not embed a private key, key ID, PyPI token or remote upload destination.

### Distribution metadata

The build backend is `poetry-core`. Modern project metadata lives in `[project]`;
`[tool.poetry]` remains as a compatibility shim for distro versions of
`python3-poetry-core`.

The project uses `pyproject-appimage` through `[tool.pyproject-appimage]`.
DEBs use pybuild's PEP-517 support and RPMs use `pyproject-rpm-macros`.
