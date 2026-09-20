# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitlab_project_hook" "this" {
  for_each = var.keys

  project = var.items[each.key].project
  url = var.items[each.key].url

  lifecycle {
    prevent_destroy = true
    ignore_changes = [token]
  }
}

output "objects" {
  value = { for key, object in gitlab_project_hook.this : key => {
  } }
}
