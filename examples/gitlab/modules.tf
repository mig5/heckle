# SYNTHETIC EXAMPLE: not a live-validated import configuration.
module "gitlab_branch_protection" {
  source = "./modules/gitlab_branch_protection"
  providers = { gitlab = gitlab }
  items = local.gitlab_branch_protection
  keys = toset(try(nonsensitive(keys(local.gitlab_branch_protection)), keys(local.gitlab_branch_protection)))
}

module "gitlab_group_level_0" {
  source = "./modules/gitlab_group_level_0"
  providers = { gitlab = gitlab }
  items = local.gitlab_group_level_0
  keys = toset(try(nonsensitive(keys(local.gitlab_group_level_0)), keys(local.gitlab_group_level_0)))
}

module "gitlab_group_level_1" {
  source = "./modules/gitlab_group_level_1"
  providers = { gitlab = gitlab }
  items = local.gitlab_group_level_1
  keys = toset(try(nonsensitive(keys(local.gitlab_group_level_1)), keys(local.gitlab_group_level_1)))
}

module "gitlab_project" {
  source = "./modules/gitlab_project"
  providers = { gitlab = gitlab }
  items = local.gitlab_project
  keys = toset(try(nonsensitive(keys(local.gitlab_project)), keys(local.gitlab_project)))
}

module "gitlab_project_hook" {
  source = "./modules/gitlab_project_hook"
  providers = { gitlab = gitlab }
  items = local.gitlab_project_hook
  keys = toset(try(nonsensitive(keys(local.gitlab_project_hook)), keys(local.gitlab_project_hook)))
}

module "gitlab_project_membership" {
  source = "./modules/gitlab_project_membership"
  providers = { gitlab = gitlab }
  items = local.gitlab_project_membership
  keys = toset(try(nonsensitive(keys(local.gitlab_project_membership)), keys(local.gitlab_project_membership)))
}
