# SYNTHETIC EXAMPLE: not a live-validated import configuration.
import {
  to = module.gitlab_branch_protection.gitlab_branch_protection.this["example/platform/api/main"]
  id = "10:main"
}

import {
  to = module.gitlab_group_level_0.gitlab_group.this["example"]
  id = "1"
}

import {
  to = module.gitlab_group_level_1.gitlab_group.this["example/platform"]
  id = "2"
}

import {
  to = module.gitlab_project.gitlab_project.this["example/platform/api"]
  id = "10"
}

import {
  to = module.gitlab_project_hook.gitlab_project_hook.this["example/platform/api/7"]
  id = "10:7"
}

import {
  to = module.gitlab_project_membership.gitlab_project_membership.this["example/platform/api/5"]
  id = "10:5"
}
