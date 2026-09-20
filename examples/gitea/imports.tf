# SYNTHETIC EXAMPLE: not a live-validated import configuration.
import {
  to = module.gitea_org.gitea_org.this["example"]
  id = "1"
}

import {
  to = module.gitea_repository.gitea_repository.this["example/api"]
  id = "10"
}

import {
  to = module.gitea_repository_webhook.gitea_repository_webhook.this["example/api/7"]
  id = "example/api/7"
}

import {
  to = module.gitea_team.gitea_team.this["example/platform"]
  id = "2"
}

import {
  to = module.gitea_team_members.gitea_team_members.this["example/platform/members"]
  id = "2"
}
