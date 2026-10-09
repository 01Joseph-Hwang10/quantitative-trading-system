output "vm_external_ip" {
  description = "Static external IP of the VM."
  value       = google_compute_instance.vm.network_interface[0].access_config[0].nat_ip
}

output "artifact_registry_url" {
  description = "Artifact Registry repository URL (docker)."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.repo_name}"
}

output "vm_zone" {
  description = "Zone of the VM (for gcloud IAP tunnel commands)."
  value       = var.zone
}

output "monitor_proxy_url" {
  description = "HTTPS URL of the Cloud Run monitor proxy (OAuth redirect domain)."
  value       = google_cloud_run_v2_service.monitor_proxy.uri
}
