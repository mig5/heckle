# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "forgejo_repository" "this" {
  for_each = var.keys

  owner = var.items[each.key].owner
  name = var.items[each.key].name
  private = var.items[each.key].private

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in forgejo_repository.this : key => {
    id = object.id
  } }
}
