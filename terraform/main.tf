# Terraform configuration for Whisper GPU processing on GCP Spot VM
# v2.0.0 - Cost optimization: $0.24/hour vs $0.36/hour OpenAI API

terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

variable "project_id" {
  description = "GCP Project ID"
  type        = string
  default     = "editorials-robot"
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "europe-west1"
}

variable "zone" {
  description = "GCP Zone (must have T4 GPUs available)"
  type        = string
  default     = "europe-west1-b"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Service account for Whisper processor
resource "google_service_account" "whisper_processor" {
  account_id   = "whisper-processor"
  display_name = "Whisper GPU Processor Service Account"
  description  = "Service account for Spot VM running faster-whisper"
}

# IAM roles for service account
resource "google_project_iam_member" "whisper_pubsub_subscriber" {
  project = var.project_id
  role    = "roles/pubsub.subscriber"
  member  = "serviceAccount:${google_service_account.whisper_processor.email}"
}

resource "google_project_iam_member" "whisper_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.whisper_processor.email}"
}

resource "google_project_iam_member" "whisper_storage_viewer" {
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${google_service_account.whisper_processor.email}"
}

resource "google_project_iam_member" "whisper_secretmanager" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.whisper_processor.email}"
}

resource "google_project_iam_member" "whisper_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.whisper_processor.email}"
}

# Firewall rule for health checks
resource "google_compute_firewall" "whisper_health_check" {
  name    = "whisper-health-check"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }

  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]  # GCP health check ranges
  target_tags   = ["whisper-processor"]
}

# Spot VM with T4 GPU for Whisper processing
resource "google_compute_instance" "whisper_processor" {
  name         = "whisper-processor"
  machine_type = "n1-standard-4"  # 4 vCPU, 15GB RAM - optimal for T4
  zone         = var.zone

  tags = ["whisper-processor"]

  # Spot VM configuration (preemptible replacement)
  scheduling {
    preemptible                 = true
    automatic_restart           = false
    on_host_maintenance         = "TERMINATE"
    provisioning_model          = "SPOT"
    instance_termination_action = "STOP"
  }

  # T4 GPU
  guest_accelerator {
    type  = "nvidia-tesla-t4"
    count = 1
  }

  boot_disk {
    initialize_params {
      # Deep Learning VM with PyTorch and CUDA pre-installed
      image = "deeplearning-platform-release/pytorch-latest-gpu"
      size  = 100  # GB - enough for model weights
      type  = "pd-ssd"
    }
  }

  network_interface {
    network = "default"
    access_config {
      # Ephemeral public IP
    }
  }

  service_account {
    email  = google_service_account.whisper_processor.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    install-nvidia-driver = "True"
  }

  # Startup script to initialize the Whisper service
  metadata_startup_script = <<-EOF
    #!/bin/bash
    set -e

    # Wait for GPU driver
    until nvidia-smi; do
      echo "Waiting for GPU driver..."
      sleep 5
    done

    # Install faster-whisper
    pip install faster-whisper google-cloud-pubsub google-cloud-firestore google-cloud-secret-manager

    # Download model (cached on persistent disk)
    python3 -c "from faster_whisper import WhisperModel; WhisperModel('dvislobokov/faster-whisper-large-v3-turbo-russian', device='cuda')"

    # Start the processor service
    cd /opt/whisper-processor
    python3 main.py
  EOF

  lifecycle {
    ignore_changes = [
      metadata_startup_script  # Allow manual updates without recreation
    ]
  }
}

# Instance group for auto-healing
resource "google_compute_instance_group" "whisper_group" {
  name        = "whisper-processor-group"
  description = "Instance group for Whisper processors"
  zone        = var.zone

  instances = [google_compute_instance.whisper_processor.self_link]

  named_port {
    name = "http"
    port = 8080
  }
}

# Health check
resource "google_compute_health_check" "whisper_health" {
  name               = "whisper-processor-health"
  check_interval_sec = 30
  timeout_sec        = 10

  http_health_check {
    port         = 8080
    request_path = "/health"
  }
}

# Outputs
output "instance_name" {
  value = google_compute_instance.whisper_processor.name
}

output "instance_zone" {
  value = google_compute_instance.whisper_processor.zone
}

output "service_account_email" {
  value = google_service_account.whisper_processor.email
}

output "ssh_command" {
  value = "gcloud compute ssh ${google_compute_instance.whisper_processor.name} --zone=${var.zone} --project=${var.project_id}"
}
