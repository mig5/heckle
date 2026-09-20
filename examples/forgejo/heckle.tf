# SYNTHETIC EXAMPLE: not a live-validated import configuration.
# Keys are public resource identities; configuration values remain sensitive.

locals {
  forgejo_branch_protection = merge({}, local.config_3e6f1d1cc6197a53)
}

locals {
  forgejo_repository = merge({}, local.config_098eeb2b3a4e74fe)
}

locals {
  forgejo_repository_webhook = merge({}, local.config_b4986da1d4a0a65b)
}

locals {
  forgejo_team = merge({}, local.config_b66f67d0e02bd75a)
}
