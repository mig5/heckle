# SYNTHETIC EXAMPLE: not a live-validated import configuration.
# Keys are public resource identities; configuration values remain sensitive.

locals {
  gitlab_branch_protection = merge({}, local.config_df7a21be20ebac05)
}

locals {
  gitlab_group_level_0 = merge({}, local.config_b3b503d013cfc47a)
}

locals {
  gitlab_group_level_1 = merge({}, local.config_e3fd57d439913a1d)
}

locals {
  gitlab_project = merge({}, local.config_9be6ff5e9bc9eda0)
}

locals {
  gitlab_project_hook = merge({}, local.config_235141ad587b209b)
}

locals {
  gitlab_project_membership = merge({}, local.config_590cbbefc257713d)
}
