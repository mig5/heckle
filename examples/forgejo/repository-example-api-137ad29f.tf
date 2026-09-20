# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  config_3e6f1d1cc6197a53 = {
    "example/api/main" = {
      "repository_id" = module.forgejo_repository.objects["example/api"].id
      "branch_name" = "main"
      "enable_push" = false
    }
  }
}

locals {
  config_098eeb2b3a4e74fe = {
    "example/api" = {
      "owner" = "example"
      "name" = "api"
      "private" = true
    }
  }
}

locals {
  config_b4986da1d4a0a65b = {
    "example/api/7" = {
      "repository_id" = module.forgejo_repository.objects["example/api"].id
      "type" = "forgejo"
      "config" = var.input_forgejo_repository_webhook_c9d5717d07da
      "events" = [
        "push",
      ]
    }
  }
}
