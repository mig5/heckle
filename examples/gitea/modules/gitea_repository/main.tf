# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitea_repository" "this" {
  for_each = var.keys

  name = var.items[each.key].name
  username = var.items[each.key].username
  private = var.items[each.key].private

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in gitea_repository.this : key => {
    name = object.name
  } }
}
