# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  config_e3fd57d439913a1d = {
    "example/platform" = {
      "name" = "Platform"
      "path" = "platform"
      "parent_id" = module.gitlab_group_level_0.objects["example"].id
    }
  }
}
