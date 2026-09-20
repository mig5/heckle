# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitlab_project_membership" "this" {
  for_each = var.keys

  project = var.items[each.key].project
  user_id = var.items[each.key].user_id
  access_level = var.items[each.key].access_level

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in gitlab_project_membership.this : key => {
  } }
}
