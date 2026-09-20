# SYNTHETIC EXAMPLE: not a live-validated import configuration.
module "gitea_org" {
  source = "./modules/gitea_org"
  providers = { gitea = gitea }
  items = local.gitea_org
  keys = toset(try(nonsensitive(keys(local.gitea_org)), keys(local.gitea_org)))
}

module "gitea_repository" {
  source = "./modules/gitea_repository"
  providers = { gitea = gitea }
  items = local.gitea_repository
  keys = toset(try(nonsensitive(keys(local.gitea_repository)), keys(local.gitea_repository)))
}

module "gitea_repository_webhook" {
  source = "./modules/gitea_repository_webhook"
  providers = { gitea = gitea }
  items = local.gitea_repository_webhook
  keys = toset(try(nonsensitive(keys(local.gitea_repository_webhook)), keys(local.gitea_repository_webhook)))
}

module "gitea_team" {
  source = "./modules/gitea_team"
  providers = { gitea = gitea }
  items = local.gitea_team
  keys = toset(try(nonsensitive(keys(local.gitea_team)), keys(local.gitea_team)))
}

module "gitea_team_members" {
  source = "./modules/gitea_team_members"
  providers = { gitea = gitea }
  items = local.gitea_team_members
  keys = toset(try(nonsensitive(keys(local.gitea_team_members)), keys(local.gitea_team_members)))
}
