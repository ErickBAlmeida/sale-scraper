# 1. Instância VM (e2-micro) otimizada para rodar o Docker do sale_scraper
resource "google_compute_instance" "app_vm" {
  name         = "sale-scraper-vm"
  machine_type = "e2-micro"
  zone         = "us-central1-a"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30 # 30 GB (incluso no Free Tier)
      type  = "pd-standard"
    }
  }

  network_interface {
    network = "default"
    access_config {
      // Deixar vazio atribui um IP público temporário para acesso e deploy
    }
  }

  # Script executado automaticamente no primeiro boot para instalar o Docker
  metadata_startup_script = <<-EOF
    #!/bin/bash
    sudo apt-get update
    sudo apt-get install -y docker.io docker-compose-v2 git
    sudo systemctl enable --now docker
  EOF
}

# 2. Bucket Privado no Cloud Storage para salvar os backups do SQLite
resource "google_storage_bucket" "backup_bucket" {
  name                     = "sale-scraper-backups-${random_id.bucket_prefix.hex}"
  location                 = "us-central1"
  force_destroy            = false
  public_access_prevention = "enforced"

  uniform_bucket_level_access = true
}

resource "random_id" "bucket_prefix" {
  byte_length = 4
}