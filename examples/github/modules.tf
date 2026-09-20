# SYNTHETIC EXAMPLE: not a live-validated import configuration.
module "members" {
  source = "./modules/members"

  providers = {
    github = github
  }

  members = local.github.members
}

module "teams" {
  source = "./modules/teams"

  providers = {
    github = github
  }

  teams            = local.github.teams
  member_usernames = module.members.usernames
}

module "repositories" {
  source = "./modules/repositories"

  providers = {
    github = github
  }

  repositories = local.github.repositories
  team_ids      = module.teams.ids
}

module "access" {
  source = "./modules/access"

  providers = {
    github = github
  }

  teams            = local.github.teams
  team_ids         = module.teams.ids
  repository_names = module.repositories.names
}

module "organization" {
  source = "./modules/organization"

  providers = {
    github = github
  }

  organization   = local.github.organization
  repository_ids = module.repositories.ids
  team_ids       = module.teams.ids
}
