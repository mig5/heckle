"""Import-safe repository settings for svalabs/forgejo 1.6.0.

The provider's ImportState fills write-only settings with defaults, not values
read from Forgejo. Its JSON schema does not expose that distinction or the
cross-attribute validators. Keep those rules here, not in the generic renderer.
See upstream internal/provider/repository_resource.go, Schema and ImportState.
"""
from __future__ import annotations

from heckle.hcl.types import Body
from heckle.compatibility import known_provider_behaviour_fields, provider_spec

# These describe how a repository is CREATED or migrated, not its ongoing
# configuration. In particular, even `mirror = false` requires clone_addr.
CREATION_FIELDS = frozenset({
    "auto_init", "gitignores", "license", "readme", "issue_labels", "trust_model",
    "clone_addr", "auth_token", "lfs", "lfs_endpoint", "labels", "milestones",
    "service", "mirror",
})

# These are initialized with guessed defaults during import and never refreshed
# from the API by this provider. Do not export those defaults as real settings,
# even when the corresponding feature is enabled.
UNREADABLE_FIELDS = frozenset({
    "allow_manual_merge", "autodetect_manual_merge", "default_delete_branch_after_merge",
    "allow_fast_forward_only_merge", "allow_rebase_update", "default_allow_maintainer_edit",
    "default_update_style", "globally_editable_wiki", "wiki_branch", "enable_prune",
})

# mirror_interval requires an explicitly configured mirror, which in turn
# requires clone_addr. Leave mirroring outside the import configuration rather
# than inventing a clone URL or disabling an existing mirror. archive_on_destroy
# is a provider preference, not a setting discovered on the repository.
IGNORED_FIELDS = CREATION_FIELDS | UNREADABLE_FIELDS | {"mirror_interval", "archive_on_destroy"}

# These settings ARE read back. Preserve their values for enabled features; omit
# them only for repositories where the parent feature is explicitly disabled.
CONDITIONAL_FIELDS = {
    "has_pull_requests": (
        "ignore_whitespace_conflicts", "allow_merge_commits", "allow_rebase",
        "allow_rebase_explicit", "allow_squash_merge", "default_merge_style",
    ),
    "has_issues": ("internal_tracker", "external_tracker"),
    "has_wiki": ("external_wiki",),
}

# The pinned provider can produce plan differences for these subordinate fields
# while the controlling feature is disabled. Keep this relationship explicit so
# Heckle can recognise the provider behaviour under exact guard conditions.
# Recognition does not assert that applying the resulting plan is safe.
KNOWN_PROVIDER_BEHAVIOUR_FIELDS = known_provider_behaviour_fields(provider_spec("forgejo"), "forgejo_repository")


IMPORT_SETTINGS_WARNING = (
    "Forgejo: repository creation/migration options and settings this provider cannot read "
    "are left out of the generated configuration. Provider defaults are not treated as "
    "your existing settings. See docs/FORGEJO.md in the Heckle source."
)

PERSONAL_CREATION_WARNING = (
    "**Check for unexpected repository creation.** This project imports repositories "
    "that already exist. If the plan says it will create or replace one, stop and check "
    "why before applying it. The Forgejo provider used here may require a server-admin "
    "token to create a repository under your username. That is separate from importing "
    "a repository that already exists.\n\n"
    "Heckle keeps your username in the configuration so changing credentials does not "
    "silently change which account owns the repositories."
)


def normalize_repository(body: Body) -> set[str]:
    """Remove inactive subordinate settings without changing feature switches.

    Missing/unknown switches are not silently interpreted as false. Normal
    provider hydration supplies literal booleans; an absent switch may mean
    that the provider uses its own default.
    """
    removed: set[str] = set()
    for enabled, fields in CONDITIONAL_FIELDS.items():
        if body.attributes.get(enabled, "").strip() == "false":
            removed.update(fields)
    for name in removed:
        body.attributes.pop(name, None)
    body.blocks = [block for block in body.blocks if block.name not in removed]
    # The provider gives several omitted Optional+Computed attributes defaults
    # during planning. Merely omitting their HCL is therefore not enough when
    # the parent feature is disabled: ignore the inapplicable values so an
    # import cannot turn provider defaults into a live update.
    return removed
