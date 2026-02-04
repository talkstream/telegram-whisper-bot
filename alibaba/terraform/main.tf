# Terraform configuration for Telegram Whisper Bot on Alibaba Cloud
# Version: v3.0.0

terraform {
  required_version = ">= 1.0"
  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.220"
    }
  }
}

# Provider configuration
# Credentials are set via environment variables:
# ALICLOUD_ACCESS_KEY and ALICLOUD_SECRET_KEY
provider "alicloud" {
  region     = var.region
  access_key = var.access_key
  secret_key = var.secret_key
}

variable "access_key" {
  description = "Alibaba Cloud Access Key ID"
  type        = string
  sensitive   = true
}

variable "secret_key" {
  description = "Alibaba Cloud Access Key Secret"
  type        = string
  sensitive   = true
}

# Variables
variable "region" {
  description = "Alibaba Cloud region"
  type        = string
  default     = "eu-central-1"  # Frankfurt
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "telegram-whisper-bot"
}

variable "environment" {
  description = "Environment (dev/staging/prod)"
  type        = string
  default     = "prod"
}

# Local values
locals {
  name_prefix = "${var.project_name}-${var.environment}"
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# Data sources
data "alicloud_account" "current" {}

data "alicloud_regions" "current" {
  current = true
}

# Outputs
output "region" {
  value = var.region
}

output "account_id" {
  value = data.alicloud_account.current.id
}

output "tablestore_instance_name" {
  value = alicloud_ots_instance.main.name
}

output "mns_queue_name" {
  value = alicloud_mns_queue.audio_jobs.name
}
