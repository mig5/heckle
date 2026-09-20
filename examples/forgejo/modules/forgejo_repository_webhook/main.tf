# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "forgejo_repository_webhook" "this" {
  for_each = var.keys

  repository_id = var.items[each.key].repository_id
  type = var.items[each.key].type
  config = var.items[each.key].config
  events = var.items[each.key].events

  lifecycle {
    prevent_destroy = true
    ignore_changes = [authorization_header]
  }
}

output "objects" {
  value = { for key, object in forgejo_repository_webhook.this : key => {
  } }
}
