# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  github = {
    organization = local.github_organization
    members      = local.github_members
    teams        = local.github_teams
    repositories = local.github_repositories
  }
}
