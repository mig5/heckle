# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitea_org" "this" {
  for_each = var.keys

  name = var.items[each.key].name

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in gitea_org.this : key => {
    name = object.name
  } }
}
