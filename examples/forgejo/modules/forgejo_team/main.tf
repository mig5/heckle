# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "forgejo_team" "this" {
  for_each = var.keys

  organization_id = var.items[each.key].organization_id
  name = var.items[each.key].name
  units_map = var.items[each.key].units_map

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in forgejo_team.this : key => {
  } }
}
