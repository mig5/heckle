# SYNTHETIC EXAMPLE: not a live-validated import configuration.
module "forgejo_branch_protection" {
  source = "./modules/forgejo_branch_protection"
  providers = { forgejo = forgejo }
  items = local.forgejo_branch_protection
  keys = toset(try(nonsensitive(keys(local.forgejo_branch_protection)), keys(local.forgejo_branch_protection)))
}

module "forgejo_repository" {
  source = "./modules/forgejo_repository"
  providers = { forgejo = forgejo }
  items = local.forgejo_repository
  keys = toset(try(nonsensitive(keys(local.forgejo_repository)), keys(local.forgejo_repository)))
}

module "forgejo_repository_webhook" {
  source = "./modules/forgejo_repository_webhook"
  providers = { forgejo = forgejo }
  items = local.forgejo_repository_webhook
  keys = toset(try(nonsensitive(keys(local.forgejo_repository_webhook)), keys(local.forgejo_repository_webhook)))
}

module "forgejo_team" {
  source = "./modules/forgejo_team"
  providers = { forgejo = forgejo }
  items = local.forgejo_team
  keys = toset(try(nonsensitive(keys(local.forgejo_team)), keys(local.forgejo_team)))
}
