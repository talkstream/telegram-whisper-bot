# Function Compute configuration
# Replaces Google Cloud Functions and App Engine

# FC Service (container for functions)
resource "alicloud_fc_service" "main" {
  name        = local.name_prefix
  description = "Telegram Whisper Bot service"

  # Enable logging to SLS
  log_config {
    project  = alicloud_log_project.main.project_name
    logstore = alicloud_log_store.fc_logs.logstore_name
  }

  # IAM role for FC
  role = alicloud_ram_role.fc_role.arn

  # Internet access for Telegram API calls
  internet_access = true
}

# Webhook Handler Function
resource "alicloud_fc_function" "webhook_handler" {
  service     = alicloud_fc_service.main.name
  name        = "webhook-handler"
  description = "Telegram webhook handler"

  runtime     = "python3.10"
  handler     = "main.handler"
  memory_size = 512
  timeout     = 60

  # Code will be deployed separately via Serverless Devs
  filename = "${path.module}/../webhook-handler/code.zip"

  environment_variables = {
    TABLESTORE_ENDPOINT     = "https://${alicloud_ots_instance.main.name}.${var.region}.ots.aliyuncs.com"
    TABLESTORE_INSTANCE     = alicloud_ots_instance.main.name
    MNS_ENDPOINT            = "https://${data.alicloud_account.current.id}.mns.${var.region}.aliyuncs.com"
    AUDIO_JOBS_QUEUE        = alicloud_mns_queue.audio_jobs.name
    REGION                  = var.region
    LOG_LEVEL               = "WARNING"
  }
}

# HTTP Trigger for Webhook
resource "alicloud_fc_trigger" "webhook_http" {
  service  = alicloud_fc_service.main.name
  function = alicloud_fc_function.webhook_handler.name
  name     = "http-trigger"
  type     = "http"

  config = jsonencode({
    authType = "anonymous"
    methods  = ["POST", "GET"]
  })
}

# Audio Processor Function
resource "alicloud_fc_function" "audio_processor" {
  service     = alicloud_fc_service.main.name
  name        = "audio-processor"
  description = "Audio transcription processor"

  runtime     = "python3.10"
  handler     = "handler.handler"
  memory_size = 1024
  timeout     = 540  # 9 minutes

  # Code will be deployed separately
  filename = "${path.module}/../audio-processor/code.zip"

  environment_variables = {
    TABLESTORE_ENDPOINT = "https://${alicloud_ots_instance.main.name}.${var.region}.ots.aliyuncs.com"
    TABLESTORE_INSTANCE = alicloud_ots_instance.main.name
    MNS_ENDPOINT        = "https://${data.alicloud_account.current.id}.mns.${var.region}.aliyuncs.com"
    REGION              = var.region
    WHISPER_BACKEND     = "qwen-asr"
    LOG_LEVEL           = "WARNING"
  }
}

# MNS Trigger for Audio Processor
# TODO: Configure proper MNS queue trigger
# For MVP, using synchronous processing or timer-based polling
# resource "alicloud_fc_trigger" "audio_mns" {
#   service  = alicloud_fc_service.main.name
#   function = alicloud_fc_function.audio_processor.name
#   name     = "mns-trigger"
#   type     = "mns_topic"
#   ...
# }

# Timer trigger for polling MNS queue (alternative to direct MNS trigger)
resource "alicloud_fc_trigger" "audio_timer" {
  service  = alicloud_fc_service.main.name
  function = alicloud_fc_function.audio_processor.name
  name     = "timer-trigger"
  type     = "timer"

  config = jsonencode({
    cronExpression = "@every 1m"  # Poll every minute
    enable         = true
    payload        = jsonencode({action = "poll_queue"})
  })
}

# Outputs
output "webhook_url" {
  value = "https://${data.alicloud_account.current.id}.${var.region}.fc.aliyuncs.com/2016-08-15/proxy/${alicloud_fc_service.main.name}/${alicloud_fc_function.webhook_handler.name}/"
}

output "fc_service_name" {
  value = alicloud_fc_service.main.name
}
