# A tiny estate that needs no cloud account: a random service name, two local files and a
# Consul key prefix (Consul runs as a throwaway dev server in CI). run.sh changes all of it
# behind Terraform's back, then checks that plandrift reports exactly what changed.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    consul = {
      source  = "hashicorp/consul"
      version = "~> 2.21"
    }
  }
}

variable "consul_address" {
  type    = string
  default = "127.0.0.1:8500"
}

variable "name_words" {
  type    = number
  default = 2
}

provider "consul" {
  address    = var.consul_address
  datacenter = "dc1"
}

resource "random_pet" "service" {
  length = var.name_words
}

resource "local_file" "app_config" {
  filename = "${path.module}/out/app.conf"
  content  = "service = ${random_pet.service.id}\nreplicas = 2\n"
}

resource "local_file" "runbook" {
  filename = "${path.module}/out/RUNBOOK.md"
  content  = "# ${random_pet.service.id}\n\nRestart with: systemctl restart app\n"
}

resource "consul_key_prefix" "app" {
  path_prefix = "apps/${random_pet.service.id}/"

  subkeys = {
    feature_flag    = "off"
    max_connections = "100"
  }
}

output "service" {
  value = random_pet.service.id
}
