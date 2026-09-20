# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitea_team" "this" {
  for_each = var.keys

  name = var.items[each.key].name
  organisation = var.items[each.key].organisation
  repositories = var.items[each.key].repositories

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in gitea_team.this : key => {
    id = object.id
  } }
}
