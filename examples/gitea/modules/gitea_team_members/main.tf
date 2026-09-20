# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitea_team_members" "this" {
  for_each = var.keys

  team_id = var.items[each.key].team_id
  members = var.items[each.key].members

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in gitea_team_members.this : key => {
  } }
}
