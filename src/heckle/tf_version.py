"""Shared CLI compatibility bounds for preflight and generated HCL."""

TF_MIN_VERSION = (1, 8, 0)
TF_MAX_VERSION = (2, 0, 0)
TF_VERSION_CONSTRAINT = ">= " + ".".join(map(str, TF_MIN_VERSION)) + ", < " + ".".join(map(str, TF_MAX_VERSION))
