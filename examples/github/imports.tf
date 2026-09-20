# SYNTHETIC EXAMPLE: not a live-validated import configuration.
import {
  to = module.access.github_team_repository.this["platform/api"]
  id = "2:api"
}

import {
  to = module.members.github_membership.this["alice"]
  id = "example:alice"
}

import {
  to = module.organization.github_organization_settings.this
  id = "1"
}

import {
  to = module.repositories.github_actions_secret.this["api/DEPLOY_KEY"]
  id = "api:DEPLOY_KEY"
}

import {
  to = module.repositories.github_repository.this["api"]
  id = "api"
}

import {
  to = module.repositories.github_repository_webhook.this["api/7"]
  id = "api/7"
}

import {
  to = module.teams.github_team.level_0["platform"]
  id = "2"
}

import {
  to = module.teams.github_team_membership.this["platform/alice"]
  id = "2:alice"
}
