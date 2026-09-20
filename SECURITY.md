# Security

Heckle is an infrastructure migration assistant, not a security boundary around
Terraform/OpenTofu/provider binaries or an assurance that any generated plan is harmless.
Treat forge configuration, inventories, plans, state and webhook URLs as confidential.

## Boundaries implemented

Discovery uses authenticated GET and GitHub GraphQL query requests. Redirects and
pagination cannot forward credentials to another origin or outside the configured
API path. URL checks canonicalize default ports and decoded path segments and reject
ambiguous backslashes, fragments and encoded traversal before credentials are sent.
HTTPS certificate verification is enabled; a private CA can be supplied.
HTTP requires explicit --allow-http consent and exposes credentials on the network.
Environment credentials are excluded from provider HCL and normal error output.

Known secret fields are scrubbed recursively; GitLab/Gitea/Forgejo CI values are
redacted before snapshots are written. GitHub Actions variables are deliberately
ordinary managed variables in the carried-over adapter, not assumed to be secret.
Redaction is not a universal secret detector: descriptions, URLs and arbitrary
free-text can contain credentials. Never publish real inventories as test fixtures.

Private JSON files are 0600 under 0700 directories on POSIX. Generation workspaces
are temporary and removed on exit; they are not securely erased from physical
storage. Provider-generated intermediate HCL can contain sensitive values while
being compiled. Use an encrypted local filesystem and a trusted machine. Users
with the same account/root access can inspect memory, environment and open files.

The runner never invokes `apply` or `destroy`. For a fresh generation it first runs
a final configuration-driven `plan` and `show -json` safety check. If provider
defaults make that form of import unsafe, Heckle may use `terraform import` / `tofu import` only against
disposable local state, then checks the ordinary follow-up plan before publishing output. A provider adapter
may recognise narrowly-defined, version-scoped plan differences under explicit guard
conditions; this recognition is not a safety guarantee or recommendation to apply.
That temporary state is deleted and never becomes the user's managed state. Heckle
refuses to publish output for create/delete/replace or unrecognised updates. This is a client-side guard, not server-side read-only enforcement; an
external provider binary remains trusted code and can make network requests under
the supplied credentials. Use least-privilege credentials and rehearse against a
disposable namespace when possible.

The runner strips global TF_CLI_ARGS, provider debug logging and transient state
redirection variables before temporary commands. No Python sandbox can guarantee a
buggy/malicious provider will not mutate a forge while merely reading/planning.

Heckle attempts to detect and classify known provider behaviour, but provider bugs,
API changes, defaults and import semantics can still lead to unintended changes. It
does not guarantee that a generated or reconciled plan is safe, complete, or suitable
for a particular environment. Always review plans before applying them. The operator
decides whether to apply a plan and is responsible for resulting remote changes.

## Generated configuration

Provider schema sensitive values and webhook URLs/config maps become required
sensitive inputs rather than defaults. Optional unrecoverable credentials are
omitted/ignored. Only identity keys are declassified to support for_each. Some
schema-sensitive nested blocks may be ignored as a whole because lifecycle rules
cannot safely address a changing set element. Inspect ignored_attributes in coverage.

GitHub secret metadata retains the old ignored placeholder technique. It must not
be used to create or recreate a missing secret with that placeholder. Native
GitLab/Gitea/Forgejo secret and variable values remain outside managed configuration.
No claim is made that `prevent_destroy` prevents all harmful changes: it cannot stop
in-place updates, missing-resource creation, or removal of the entire resource block.
That is why fresh adoption also has the independent no-change plan gate. Gitea
membership snapshots are authoritative, not additive.

--force is restricted to marked disposable outputs and refuses backend, tfvars,
state, initialized directories and trees containing symbolic links. After adoption
remove `.heckle-generated`.
Reports/inventory never act as continuing runtime inputs to generated HCL.

## Reporting a vulnerability

Contact the maintainer privately at mig@mig5.net with a minimal anonymized
reproduction. Do not include access tokens, webhook URLs, private inventory,
provider logs or state in a public issue. The initial release has not undergone an
independent third-party security audit or live multi-forge acceptance testing. The
maintainer's release review is recorded in `docs/SECURITY-AUDIT.md`.
