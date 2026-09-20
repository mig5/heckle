# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "items" {
  type = any
}

variable "keys" {
  type = set(string)
}

resource "forgejo_branch_protection" "this" {
  for_each = var.keys

  repository_id = var.items[each.key].repository_id
  branch_name = var.items[each.key].branch_name
  enable_push = var.items[each.key].enable_push

  lifecycle {
    prevent_destroy = true
  }
}

output "objects" {
  value = { for key, object in forgejo_branch_protection.this : key => {
  } }
}
