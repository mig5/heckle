# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitea_repository_webhook" "this" {
  for_each = var.keys

  username = var.items[each.key].username
  name = var.items[each.key].name
  events = var.items[each.key].events
  type = var.items[each.key].type
  url = var.items[each.key].url

  lifecycle {
    prevent_destroy = true
    ignore_changes = [secret]
  }
}

output "objects" {
  value = { for key, object in gitea_repository_webhook.this : key => {
  } }
}
