# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  github_repository_api = {
    "api" = {
      "actions" = {
        "secrets" = {
          "DEPLOY_KEY" = {
          }
        }
      }
      "settings" = {
        "visibility" = "private"
        "description" = "A literal $${value} and %%{placeholder}"
        "allow_merge_commit" = true
        "allow_squash_merge" = false
        "archived" = false
      }
      "webhooks" = {
        "7" = {
          "active" = true
          "events" = ["push"]
          "__blocks" = {
            "configuration" = [
              {
                "url" = var.webhook_api_hook_example_test_7
                "content_type" = "json"
                "insecure_ssl" = false
              },
            ]
          }
        }
      }
    }
  }
}
