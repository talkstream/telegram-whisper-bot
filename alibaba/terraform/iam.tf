# IAM (RAM) configuration for service roles

# RAM Role for Function Compute
resource "alicloud_ram_role" "fc_role" {
  name                     = "${local.name_prefix}-fc-role"
  assume_role_policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = ["fc.aliyuncs.com"]
        }
      }
    ]
  })
  description = "Role for Function Compute to access other services"
}

# Policy for Tablestore access
resource "alicloud_ram_policy" "tablestore_access" {
  policy_name     = "${local.name_prefix}-tablestore-access"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Action = [
          "ots:*"
        ]
        Effect   = "Allow"
        Resource = [
          "acs:ots:*:*:instance/${alicloud_ots_instance.main.name}",
          "acs:ots:*:*:instance/${alicloud_ots_instance.main.name}/table/*"
        ]
      }
    ]
  })
  description = "Allow access to Tablestore instance"
}

# Policy for MNS access
resource "alicloud_ram_policy" "mns_access" {
  policy_name     = "${local.name_prefix}-mns-access"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Action = [
          "mns:*"
        ]
        Effect   = "Allow"
        Resource = [
          "acs:mns:*:*:/queues/${alicloud_mns_queue.audio_jobs.name}",
          "acs:mns:*:*:/topics/${alicloud_mns_topic.notifications.name}"
        ]
      }
    ]
  })
  description = "Allow access to MNS queues and topics"
}

# Policy for OSS access (for audio files)
resource "alicloud_ram_policy" "oss_access" {
  policy_name     = "${local.name_prefix}-oss-access"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Action = [
          "oss:GetObject",
          "oss:PutObject",
          "oss:DeleteObject"
        ]
        Effect   = "Allow"
        Resource = [
          "acs:oss:*:*:telegram-whisper-audio/*"
        ]
      }
    ]
  })
  description = "Allow access to OSS bucket for audio files"
}

# Policy for KMS access (secrets)
resource "alicloud_ram_policy" "kms_access" {
  policy_name     = "${local.name_prefix}-kms-access"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Action = [
          "kms:GetSecretValue",
          "kms:DescribeSecret"
        ]
        Effect   = "Allow"
        Resource = ["*"]
      }
    ]
  })
  description = "Allow access to KMS secrets"
}

# Policy for SLS (logging)
resource "alicloud_ram_policy" "sls_access" {
  policy_name     = "${local.name_prefix}-sls-access"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Action = [
          "log:PostLogStoreLogs"
        ]
        Effect   = "Allow"
        Resource = [
          "acs:log:*:*:project/${alicloud_log_project.main.project_name}/logstore/*"
        ]
      }
    ]
  })
  description = "Allow writing to SLS logs"
}

# Attach policies to FC role
resource "alicloud_ram_role_policy_attachment" "fc_tablestore" {
  role_name   = alicloud_ram_role.fc_role.name
  policy_name = alicloud_ram_policy.tablestore_access.name
  policy_type = "Custom"
}

resource "alicloud_ram_role_policy_attachment" "fc_mns" {
  role_name   = alicloud_ram_role.fc_role.name
  policy_name = alicloud_ram_policy.mns_access.name
  policy_type = "Custom"
}

resource "alicloud_ram_role_policy_attachment" "fc_oss" {
  role_name   = alicloud_ram_role.fc_role.name
  policy_name = alicloud_ram_policy.oss_access.name
  policy_type = "Custom"
}

resource "alicloud_ram_role_policy_attachment" "fc_kms" {
  role_name   = alicloud_ram_role.fc_role.name
  policy_name = alicloud_ram_policy.kms_access.name
  policy_type = "Custom"
}

resource "alicloud_ram_role_policy_attachment" "fc_sls" {
  role_name   = alicloud_ram_role.fc_role.name
  policy_name = alicloud_ram_policy.sls_access.name
  policy_type = "Custom"
}

# Outputs
output "fc_role_arn" {
  value = alicloud_ram_role.fc_role.arn
}
