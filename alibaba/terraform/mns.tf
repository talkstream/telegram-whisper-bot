# MNS (Simple Message Queue) configuration
# Replaces Google Cloud Pub/Sub

# MNS Queue for audio processing jobs
resource "alicloud_mns_queue" "audio_jobs" {
  name                     = "${local.name_prefix}-audio-jobs"
  delay_seconds            = 0
  maximum_message_size     = 65536  # 64 KB
  message_retention_period = 345600 # 4 days
  visibility_timeout       = 600    # 10 minutes (processing timeout)
  polling_wait_seconds     = 0

  logging_enabled = true
}

# MNS Topic for notifications (optional, for fan-out patterns)
resource "alicloud_mns_topic" "notifications" {
  name                 = "${local.name_prefix}-notifications"
  maximum_message_size = 65536
  logging_enabled      = true
}

# Subscription for admin notifications (optional)
# Can be configured later to send to HTTP endpoints

# Outputs
output "mns_queue_endpoint" {
  value = "https://${data.alicloud_account.current.id}.mns.${var.region}.aliyuncs.com"
}

output "audio_jobs_queue_name" {
  value = alicloud_mns_queue.audio_jobs.name
}
