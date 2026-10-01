# ── API enablement ────────────────────────────────────────────────────────────
resource "google_project_service" "compute" {
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "artifactregistry" {
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "oslogin" {
  service            = "oslogin.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "iap" {
  service            = "iap.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "logging" {
  service            = "logging.googleapis.com"
  disable_on_destroy = false
}

# ── Artifact Registry ─────────────────────────────────────────────────────────
resource "google_artifact_registry_repository" "docker" {
  project       = var.project_id
  location      = var.region
  repository_id = var.repo_name
  description   = "Quantitative trading system application images"
  format        = "DOCKER"

  # Retention: prune old image versions, keeping only the 5 most recent.
  # Cleanup runs on upload and on a daily schedule, so pruning is eventual.
  cleanup_policies {
    id     = "keep-latest-5"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  depends_on = [google_project_service.artifactregistry]
}

# ── Static external IP ────────────────────────────────────────────────────────
resource "google_compute_address" "vm" {
  project      = var.project_id
  name         = "quantitative-trading-ip"
  region       = var.region
  address_type = "EXTERNAL"

  depends_on = [google_project_service.compute]
}

# ── VM service account ────────────────────────────────────────────────────────
resource "google_service_account" "vm" {
  project      = var.project_id
  account_id   = "quantitative-trading-vm"
  display_name = "Quantitative trading VM service account"
}

resource "google_project_iam_member" "vm_artifact_registry_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.vm.email}"
}

resource "google_project_iam_member" "vm_logging_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.vm.email}"
}

# ── Deploying user IAM (OS Login + IAP tunnel) ────────────────────────────────
resource "google_project_iam_member" "owner_oslogin" {
  project = var.project_id
  role    = "roles/compute.osLogin"
  member  = "user:${var.owner_email}"
}

resource "google_project_iam_member" "owner_iap_tunnel" {
  project = var.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = "user:${var.owner_email}"
}

# ── VM ────────────────────────────────────────────────────────────────────────
resource "google_compute_instance" "vm" {
  project      = var.project_id
  name         = "quantitative-trading-vm"
  machine_type = var.vm_machine_type
  zone         = var.zone

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      type  = "pd-balanced"
      size  = var.vm_disk_gb
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.vm.address
    }
  }

  service_account {
    email  = google_service_account.vm.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  tags = ["quantitative-trading-vm"]

  # e2-micro comfortably runs both containers; no accelerators or scheduling.
  scheduling {
    provisioning_model = "STANDARD"
    automatic_restart  = true
  }

  depends_on = [
    google_project_service.compute,
    google_project_service.oslogin,
  ]
}

# ── Firewall: SSH via IAP only, nothing else exposed ─────────────────────────
resource "google_compute_firewall" "ssh_via_iap" {
  project     = var.project_id
  name        = "quantitative-trading-ssh-via-iap"
  network     = "default"
  description = "Allow SSH only from the Google IAP range (no public SSH, no other ports)."

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["quantitative-trading-vm"]

  depends_on = [google_project_service.compute]
}
