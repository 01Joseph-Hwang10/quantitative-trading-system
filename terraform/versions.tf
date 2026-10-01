terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
  }
  # Local state for now (gitignored). Migrate to a GCS backend when a second
  # operator joins.
}

provider "google" {
  project = var.project_id
  region  = var.region
}
