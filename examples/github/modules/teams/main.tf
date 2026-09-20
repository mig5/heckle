# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "teams" {
  type = any
}

variable "member_usernames" {
  type = map(string)
}

locals {
  teams_level_0 = {
    for key, team in var.teams : key => team
    if contains([
  "platform",
], key)
  }
}

locals {
  instances_github_team_level_0 = local.teams_level_0
}

resource "github_team" "level_0" {
  for_each = toset(try(nonsensitive(keys(local.instances_github_team_level_0)), keys(local.instances_github_team_level_0)))

  name = local.instances_github_team_level_0[each.key].settings.name
  privacy = local.instances_github_team_level_0[each.key].settings.privacy
}

locals {
  team_ids      = merge({}, { for key, team in github_team.level_0 : key => team.id })
  team_node_ids = merge({}, { for key, team in github_team.level_0 : key => team.node_id })
}

locals {
  memberships = merge({}, [
    for team_key, team in var.teams : {
      for username, config in try(team.members, {}) :
      "${team_key}/${username}" => {
        team_key = team_key
        username = username
        config   = config
      }
    }
  ]...)
}

locals {
  instances_github_team_membership_this = local.memberships
}

resource "github_team_membership" "this" {
  for_each = toset(try(nonsensitive(keys(local.instances_github_team_membership_this)), keys(local.instances_github_team_membership_this)))

  team_id  = local.team_ids[local.instances_github_team_membership_this[each.key].team_key]
  username = var.member_usernames[local.instances_github_team_membership_this[each.key].username]
  role = local.instances_github_team_membership_this[each.key].config.role
}

output "ids" {
  value = local.team_ids
}

output "node_ids" {
  value = local.team_node_ids
}
