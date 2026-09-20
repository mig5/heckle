# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "organization" {
  type = any
}

variable "repository_ids" {
  type    = map(number)
  default = {}
}

variable "team_ids" {
  type    = map(number)
  default = {}
}

resource "github_organization_settings" "this" {
  billing_email = var.organization.settings.billing_email
}
