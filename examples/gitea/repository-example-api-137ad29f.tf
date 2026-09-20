# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  config_66337ff32fe9fb4a = {
    "example/api" = {
      "name" = "api"
      "username" = module.gitea_org.objects["example"].name
      "private" = true
    }
  }
}

locals {
  config_a32d5ebf08b9d686 = {
    "example/api/7" = {
      "username" = "example"
      "name" = module.gitea_repository.objects["example/api"].name
      "events" = [
        "push",
      ]
      "type" = "gitea"
      "url" = var.input_gitea_repository_webhook_6254da9a86bb
    }
  }
}
