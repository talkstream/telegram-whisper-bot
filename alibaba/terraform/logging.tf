# SLS (Log Service) configuration
# Replaces Google Cloud Logging

# Log Project
resource "alicloud_log_project" "main" {
  name        = "${local.name_prefix}-logs"
  description = "Telegram Whisper Bot logs"
}

# Log Store for Function Compute logs
resource "alicloud_log_store" "fc_logs" {
  project               = alicloud_log_project.main.name
  name                  = "fc-logs"
  retention_period      = 7  # 7 days retention (cost optimization)
  shard_count           = 2
  auto_split            = true
  max_split_shard_count = 10
  append_meta           = true
}

# Log Store for application logs
resource "alicloud_log_store" "app_logs" {
  project               = alicloud_log_project.main.name
  name                  = "app-logs"
  retention_period      = 30  # 30 days for app logs
  shard_count           = 2
  auto_split            = true
  max_split_shard_count = 10
  append_meta           = true
}

# Index for FC logs (enables search)
resource "alicloud_log_store_index" "fc_logs_index" {
  project  = alicloud_log_project.main.name
  logstore = alicloud_log_store.fc_logs.name

  full_text {
    case_sensitive = false
    token          = " #$^*\r\n\t"
  }

  field_search {
    name             = "level"
    type             = "text"
    case_sensitive   = false
    include_chinese  = false
    token            = " #$^*\r\n\t"
    enable_analytics = true
  }

  field_search {
    name             = "message"
    type             = "text"
    case_sensitive   = false
    include_chinese  = false
    token            = " #$^*\r\n\t"
    enable_analytics = true
  }

  field_search {
    name             = "user_id"
    type             = "text"
    case_sensitive   = false
    include_chinese  = false
    token            = " #$^*\r\n\t"
    enable_analytics = true
  }
}

# Outputs
output "log_project_name" {
  value = alicloud_log_project.main.name
}

output "log_endpoint" {
  value = "${var.region}.log.aliyuncs.com"
}
