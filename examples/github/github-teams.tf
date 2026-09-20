# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  github_teams = {
    "platform" = {
      "settings" = {
        "name" = "Platform"
        "privacy" = "closed"
      }
      "members" = {
        "alice" = {
          "role" = "maintainer"
        }
      }
      "repositories" = {
        "api" = {
          "permission" = "push"
        }
      }
    }
  }
}
