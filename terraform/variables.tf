# Variables for Whisper GPU Terraform configuration

variable "whisper_model" {
  description = "Faster Whisper model to use"
  type        = string
  default     = "dvislobokov/faster-whisper-large-v3-turbo-russian"
}

variable "pubsub_subscription" {
  description = "Pub/Sub subscription for audio jobs"
  type        = string
  default     = "audio-processing-jobs-sub"
}

variable "enable_preemption_handler" {
  description = "Enable graceful shutdown on preemption"
  type        = bool
  default     = true
}
