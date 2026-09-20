# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitlab_group" "this" {
  for_each = var.keys

  name = var.items[each.key].name
  path = var.items[each.key].path
  parent_id = var.items[each.key].parent_id

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in gitlab_group.this : key => {
    id = object.id
  } }
}
