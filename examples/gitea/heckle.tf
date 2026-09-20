# SYNTHETIC EXAMPLE: not a live-validated import configuration.
# Keys are public resource identities; configuration values remain sensitive.

locals {
  gitea_org = merge({}, local.config_5e33be80568e8b1f)
}

locals {
  gitea_repository = merge({}, local.config_66337ff32fe9fb4a)
}

locals {
  gitea_repository_webhook = merge({}, local.config_a32d5ebf08b9d686)
}

locals {
  gitea_team = merge({}, local.config_5c5258816f69a303)
}

locals {
  gitea_team_members = merge({}, local.config_0a009113fe2524be)
}
