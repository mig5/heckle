# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  config_df7a21be20ebac05 = {
    "example/platform/api/main" = {
      "project" = module.gitlab_project.objects["example/platform/api"].id
      "branch" = "main"
      "push_access_level" = "maintainer"
    }
  }
}

locals {
  config_9be6ff5e9bc9eda0 = {
    "example/platform/api" = {
      "name" = "API"
      "path" = "api"
      "namespace_id" = module.gitlab_group_level_1.objects["example/platform"].id
    }
  }
}

locals {
  config_235141ad587b209b = {
    "example/platform/api/7" = {
      "project" = module.gitlab_project.objects["example/platform/api"].id
      "url" = var.input_gitlab_project_hook_f9b769765a99
    }
  }
}

locals {
  config_590cbbefc257713d = {
    "example/platform/api/5" = {
      "project" = module.gitlab_project.objects["example/platform/api"].id
      "user_id" = 5
      "access_level" = "maintainer"
    }
  }
}
