# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "gitlab_branch_protection" "this" {
  for_each = var.keys

  project = var.items[each.key].project
  branch = var.items[each.key].branch
  push_access_level = var.items[each.key].push_access_level

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in gitlab_branch_protection.this : key => {
  } }
}
