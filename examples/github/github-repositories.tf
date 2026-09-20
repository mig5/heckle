# SYNTHETIC EXAMPLE: not a live-validated import configuration.
locals {
  github_repositories = merge(
    local.github_repository_api,
  )
}
