# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitlab_project" "this" {
  for_each = var.keys

  name = var.items[each.key].name
  path = var.items[each.key].path
  namespace_id = var.items[each.key].namespace_id

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in gitlab_project.this : key => {
    id = object.id
  } }
}
