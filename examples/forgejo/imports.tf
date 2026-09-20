# SYNTHETIC EXAMPLE: not a live-validated import configuration.
import {
  to = module.forgejo_branch_protection.forgejo_branch_protection.this["example/api/main"]
  id = "example/api/main"
}

import {
  to = module.forgejo_repository.forgejo_repository.this["example/api"]
  id = "example/api"
}

import {
  to = module.forgejo_repository_webhook.forgejo_repository_webhook.this["example/api/7"]
  id = "example/api/7"
}

import {
  to = module.forgejo_team.forgejo_team.this["example/platform"]
  id = "example/platform"
}
