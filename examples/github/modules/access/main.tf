# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "teams" {
  type = any
}

variable "team_ids" {
  type = map(number)
}

variable "repository_names" {
  type = map(string)
}

locals {
  repository_grants = merge({}, [
    for team_key, team in var.teams : {
      for repository_key, config in try(team.repositories, {}) :
      "${team_key}/${repository_key}" => {
        team_key       = team_key
        repository_key = repository_key
        config         = config
      }
    }
  ]...)
}

locals {
  instances_github_team_repository_this = local.repository_grants
}

resource "github_team_repository" "this" {
  for_each = toset(try(nonsensitive(keys(local.instances_github_team_repository_this)), keys(local.instances_github_team_repository_this)))

  team_id    = var.team_ids[local.instances_github_team_repository_this[each.key].team_key]
  repository = var.repository_names[local.instances_github_team_repository_this[each.key].repository_key]
  permission = local.instances_github_team_repository_this[each.key].config.permission
}
