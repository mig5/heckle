# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "repositories" {
  type = any
}

variable "team_ids" {
  type    = map(number)
  default = {}
}

locals {
  actions_secret = merge({}, [
    for repository_key, repository in var.repositories : {
      for item_key, item in try(repository.actions.secrets, {}) :
      "${repository_key}/${item_key}" => {
        repository_key = repository_key
        item_key        = item_key
        config          = item
      }
    }
  ]...)
  repository_webhook = merge({}, [
    for repository_key, repository in var.repositories : {
      for item_key, item in try(repository.webhooks, {}) :
      "${repository_key}/${item_key}" => {
        repository_key = repository_key
        item_key        = item_key
        config          = item
      }
    }
  ]...)
}

locals {
  instances_github_repository_this = var.repositories
}

resource "github_repository" "this" {
  for_each = toset(try(nonsensitive(keys(local.instances_github_repository_this)), keys(local.instances_github_repository_this)))

  name = each.key
  visibility = local.instances_github_repository_this[each.key].settings.visibility
  description = local.instances_github_repository_this[each.key].settings.description
  allow_merge_commit = local.instances_github_repository_this[each.key].settings.allow_merge_commit
  allow_squash_merge = local.instances_github_repository_this[each.key].settings.allow_squash_merge
  archived = local.instances_github_repository_this[each.key].settings.archived

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  instances_github_actions_secret_this = local.actions_secret
}

resource "github_actions_secret" "this" {
  for_each = toset(try(nonsensitive(keys(local.instances_github_actions_secret_this)), keys(local.instances_github_actions_secret_this)))

  repository  = github_repository.this[local.instances_github_actions_secret_this[each.key].repository_key].name
  secret_name = local.instances_github_actions_secret_this[each.key].item_key
  value       = "managed-outside-opentofu"

  lifecycle {
    ignore_changes = [value]
  }
}

locals {
  instances_github_repository_webhook_this = local.repository_webhook
}

resource "github_repository_webhook" "this" {
  for_each = toset(try(nonsensitive(keys(local.instances_github_repository_webhook_this)), keys(local.instances_github_repository_webhook_this)))

  repository = github_repository.this[local.instances_github_repository_webhook_this[each.key].repository_key].name
  active = local.instances_github_repository_webhook_this[each.key].config.active
  events = local.instances_github_repository_webhook_this[each.key].config.events

  dynamic "configuration" {
    for_each = try(local.instances_github_repository_webhook_this[each.key].config.__blocks.configuration, [])
    iterator = github_repository_webhook_configuration_eafc8
    content {
      url = github_repository_webhook_configuration_eafc8.value.url
      content_type = github_repository_webhook_configuration_eafc8.value.content_type
      insecure_ssl = github_repository_webhook_configuration_eafc8.value.insecure_ssl
    }
  }

  lifecycle {
    ignore_changes = [configuration[0].secret]
  }
}

output "names" {
  value = { for key, repository in github_repository.this : key => repository.name }
}

output "ids" {
  value = { for key, repository in github_repository.this : key => repository.repo_id }
}

output "node_ids" {
  value = { for key, repository in github_repository.this : key => repository.node_id }
}
