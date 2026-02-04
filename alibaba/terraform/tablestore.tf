# Tablestore (OTS) configuration
# Replaces Google Cloud Firestore

# Tablestore Instance
resource "alicloud_ots_instance" "main" {
  name        = "twbot-prod"  # Max 16 chars: a-z, A-Z, 0-9, hyphen
  description = "Telegram Whisper Bot database"

  # High Performance instance for production
  instance_type = "HighPerformance"

  tags = local.common_tags
}

# Tablestore Tables

# Users table - stores user profiles and balances
resource "alicloud_ots_table" "users" {
  instance_name = alicloud_ots_instance.main.name
  table_name    = "users"

  primary_key {
    name = "user_id"
    type = "String"
  }

  time_to_live                  = -1  # Never expire
  max_version                   = 1
  deviation_cell_version_in_sec = 86400

  defined_column {
    name = "balance_minutes"
    type = "Integer"
  }

  defined_column {
    name = "trial_status"
    type = "String"
  }

  defined_column {
    name = "created_at"
    type = "String"
  }

  defined_column {
    name = "last_activity"
    type = "String"
  }

  defined_column {
    name = "user_name"
    type = "String"
  }

  defined_column {
    name = "settings"
    type = "String"  # JSON serialized
  }
}

# Audio Jobs table - stores processing jobs
resource "alicloud_ots_table" "audio_jobs" {
  instance_name = alicloud_ots_instance.main.name
  table_name    = "audio_jobs"

  primary_key {
    name = "job_id"
    type = "String"
  }

  time_to_live                  = 604800  # 7 days TTL
  max_version                   = 1
  deviation_cell_version_in_sec = 86400

  defined_column {
    name = "user_id"
    type = "String"
  }

  defined_column {
    name = "chat_id"
    type = "Integer"
  }

  defined_column {
    name = "file_id"
    type = "String"
  }

  defined_column {
    name = "status"
    type = "String"
  }

  defined_column {
    name = "created_at"
    type = "String"
  }

  defined_column {
    name = "result"
    type = "String"  # JSON serialized
  }
}

# Trial Requests table
resource "alicloud_ots_table" "trial_requests" {
  instance_name = alicloud_ots_instance.main.name
  table_name    = "trial_requests"

  primary_key {
    name = "user_id"
    type = "String"
  }

  time_to_live                  = 2592000  # 30 days TTL
  max_version                   = 1
  deviation_cell_version_in_sec = 86400

  defined_column {
    name = "status"
    type = "String"
  }

  defined_column {
    name = "user_name"
    type = "String"
  }

  defined_column {
    name = "request_timestamp"
    type = "String"
  }
}

# Transcription Logs table
resource "alicloud_ots_table" "transcription_logs" {
  instance_name = alicloud_ots_instance.main.name
  table_name    = "transcription_logs"

  primary_key {
    name = "log_id"
    type = "String"
  }

  time_to_live                  = 7776000  # 90 days TTL
  max_version                   = 1
  deviation_cell_version_in_sec = 86400

  defined_column {
    name = "user_id"
    type = "String"
  }

  defined_column {
    name = "timestamp"
    type = "String"
  }

  defined_column {
    name = "duration"
    type = "Integer"
  }

  defined_column {
    name = "char_count"
    type = "Integer"
  }

  defined_column {
    name = "status"
    type = "String"
  }
}

# Payment Logs table
resource "alicloud_ots_table" "payment_logs" {
  instance_name = alicloud_ots_instance.main.name
  table_name    = "payment_logs"

  primary_key {
    name = "payment_id"
    type = "String"
  }

  time_to_live                  = -1  # Never expire (financial records)
  max_version                   = 1
  deviation_cell_version_in_sec = 86400

  defined_column {
    name = "user_id"
    type = "String"
  }

  defined_column {
    name = "amount"
    type = "Integer"
  }

  defined_column {
    name = "stars_amount"
    type = "Integer"
  }

  defined_column {
    name = "minutes_added"
    type = "Integer"
  }

  defined_column {
    name = "timestamp"
    type = "String"
  }

  defined_column {
    name = "telegram_payment_charge_id"
    type = "String"
  }
}

# User State table (for batch processing state)
resource "alicloud_ots_table" "user_state" {
  instance_name = alicloud_ots_instance.main.name
  table_name    = "user_state"

  primary_key {
    name = "user_id"
    type = "String"
  }

  time_to_live                  = 86400  # 1 day TTL (minimum allowed)
  max_version                   = 1
  deviation_cell_version_in_sec = 86400

  defined_column {
    name = "state_data"
    type = "String"  # JSON serialized
  }
}

# Outputs
output "tablestore_endpoint" {
  value = "https://${alicloud_ots_instance.main.name}.${var.region}.ots.aliyuncs.com"
}
