terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = "***REMOVED***"
  region  = "us-central1"
  zone    = "us-central1-a"
}