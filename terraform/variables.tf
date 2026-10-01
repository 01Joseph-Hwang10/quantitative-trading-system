variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region for all resources."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone for the VM."
  type        = string
  default     = "us-central1-a"
}

variable "owner_email" {
  description = "Email of the deploying user (gets OS Login + IAP tunnel roles)."
  type        = string
}

variable "vm_machine_type" {
  description = "Machine type for the VM."
  type        = string
  default     = "e2-micro"
}

variable "vm_disk_gb" {
  description = "Boot disk size in GB."
  type        = number
  default     = 16
}

variable "repo_name" {
  description = "Artifact Registry repository name."
  type        = string
  default     = "quantitative-trading"
}
