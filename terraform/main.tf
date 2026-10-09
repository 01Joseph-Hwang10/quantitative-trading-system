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

resource "google_project_service" "run" {
  service            = "run.googleapis.com"
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

# ── Cloud Run: OAuth HTTPS proxy (stock nginx → VM monitor) ──────────────────
# Spec 010: gives Google OAuth a valid HTTPS redirect domain (a *.run.app URL)
# while the Streamlit monitor on the VM stays unchanged.
#
# Uses the stock nginx image — no Dockerfile, no Artifact Registry image, no
# auxiliary resources. The nginx config is written in-line at container start
# (heredoc in `args`) with the VM's STATIC IP interpolated by Terraform, so
# the proxy is fire-and-forget after deploy: `just update` only ever touches
# the VM, and an (rare) IP change propagates on the next `terraform apply`.
resource "google_cloud_run_v2_service" "monitor_proxy" {
  project             = var.project_id
  name                = "quantitative-trading-monitor"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    # Long-lived dashboard websockets (Cloud Run's maximum).
    timeout = "3600s"

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = "docker.io/library/nginx:1.27-alpine"

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          # 512Mi floor: CPU-always-allocated Cloud Run instances reject <512Mi.
          # Nginx uses a fraction of this; free tier still covers the profile.
          cpu    = "1"
          memory = "512Mi"
        }
      }

      command = ["/bin/sh"]

      args = [
        "-c",
        <<-SCRIPT
          set -eu

          cat > /etc/nginx/conf.d/default.conf <<'NGINX'
          map $http_upgrade $connection_upgrade {
              default upgrade;
              ''      close;
          }

          server {
              listen 8080;
              server_name _;

              location = /healthz {
                  access_log off;
                  return 200;
              }

              # Required by the OAuth consent screen's production publishing
              # (branding needs a public privacy-policy URL; the proxy itself
              # is the only public surface this project has).
              location = /privacy {
                  access_log off;
                  default_type text/html;
                  return 200 '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Privacy Policy — Quantitative Trading Monitor</title></head><body><h1>Privacy Policy</h1><p>This dashboard is a single-user trading monitor operated privately by its owner.</p><p>When you sign in with Google, the app receives only your OpenID Connect identity (name and email, via the <code>openid</code> and <code>email</code> scopes) to enforce its access allowlist. Nothing else is requested or stored by Google sign-in.</p><p>No analytics, advertising, or third-party tracking is used. Market data and records stay on the private server and are never shared.</p><p>Contact: joseph95501@gmail.com</p></body></html>';
              }

              location / {
                  proxy_pass http://${google_compute_address.vm.address}:8501;
                  proxy_http_version 1.1;

                  proxy_set_header Host $host;
                  proxy_set_header X-Real-IP $remote_addr;
                  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                  proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
                  proxy_set_header X-Forwarded-Port 443;

                  # Streamlit websocket upgrades (/_stcore/stream).
                  proxy_set_header Upgrade $http_upgrade;
                  proxy_set_header Connection $connection_upgrade;

                  # Idle dashboards hold the websocket open for the full timeout.
                  proxy_connect_timeout 10s;
                  proxy_read_timeout 3600s;
                  proxy_send_timeout 3600s;

                  # Streamed responses; modest dashboard uploads only.
                  proxy_buffering off;
                  client_max_body_size 10m;
              }
          }
          NGINX

          nginx -t
          exec nginx -g 'daemon off;'
        SCRIPT
      ]
    }
  }

  depends_on = [google_project_service.run]
}

# Streamlit enforces Google OIDC + the email allowlist itself, so Cloud Run
# IAM stays open. Do not proxy anything else through this service.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.monitor_proxy.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── Firewall: monitor port (Streamlit via the Cloud Run proxy) ───────────────
# Cloud Run egress IPs are not stable, so the source must be 0.0.0.0/0;
# Streamlit's own Google-OIDC + allowlist gate is the access control.
resource "google_compute_firewall" "monitor_proxy" {
  project     = var.project_id
  name        = "quantitative-trading-monitor-8501"
  network     = "default"
  description = "Allow TCP 8501 from anywhere (Cloud Run proxy); Streamlit OIDC + allowlist gates access."

  allow {
    protocol = "tcp"
    ports    = ["8501"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["quantitative-trading-vm"]

  depends_on = [google_project_service.compute]
}
