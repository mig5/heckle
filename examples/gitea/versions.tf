# SYNTHETIC EXAMPLE: not a live-validated import configuration.
terraform {
  required_version = ">= 1.8.0, < 2.0.0"
  required_providers {
    gitea = {
      source = "go-gitea/gitea"
      version = "= 0.8.1"
    }
  }
}
