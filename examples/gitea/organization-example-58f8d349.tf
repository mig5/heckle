# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  config_5e33be80568e8b1f = {
    "example" = {
      "name" = "example"
    }
  }
}

locals {
  config_5c5258816f69a303 = {
    "example/platform" = {
      "name" = "platform"
      "organisation" = module.gitea_org.objects["example"].name
      "repositories" = ["api"]
    }
  }
}

locals {
  config_0a009113fe2524be = {
    "example/platform/members" = {
      "team_id" = module.gitea_team.objects["example/platform"].id
      "members" = ["alice"]
    }
  }
}
