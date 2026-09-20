# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "members" {
  type = any
}

locals {
  instances_github_membership_this = var.members
}

resource "github_membership" "this" {
  for_each = toset(try(nonsensitive(keys(local.instances_github_membership_this)), keys(local.instances_github_membership_this)))

  username = each.key
  role = local.instances_github_membership_this[each.key].role
}

output "usernames" {
  value = { for key, membership in github_membership.this : key => membership.username }
}
