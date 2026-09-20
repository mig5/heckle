# SYNTHETIC EXAMPLE: not a live-validated import configuration.
variable "webhook_api_hook_example_test_7" {
  description = "Sensitive URL for api webhook 7 (hook_example_test). Supply it through your normal OpenTofu secret-input mechanism."
  type        = string
  sensitive   = true
}
