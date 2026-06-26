# Home Lab Deployment (Proxmox + Ubuntu Server VM)

This guide covers deploying docker-sentinel on a Proxmox home lab
running Ubuntu Server 24.04 in a VM — tested on a Beelink SER mini PC
with Proxmox VE 9.2.3.

## Environment

| Component | Version |
|---|---|
| Hypervisor | Proxmox VE 9.2.3 |
| OS | Ubuntu Server 24.04.3 LTS |
| Docker | 29.6.0 |
| Python | 3.12 |

## Prerequisites

- Ubuntu Server VM with Docker installed
- Static IP configured
- SSH access from your main machine

## Installation

```bash
git clone git@github.com:igalhub/docker-sentinel.git
cd docker-sentinel

sudo apt install -y python3.12-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

```bash
cp config/settings.yaml.example config/settings.yaml
```

Default thresholds are suitable for a home lab — no changes needed
to get started.

## Run the checker and start the dashboard

```bash
# Generate results.db
python3 -m checker.check

# Start dashboard container
docker compose up -d
```

## Verify

```bash
curl http://localhost:8081/status
```

## Access from your main machine

`http://<VM_IP>:8081`
`http://<VM_IP>:8081/status`
Replace `<VM_IP>` with your VM's static IP address.

## What docker-sentinel monitors in a home lab

On a typical home lab VM running multiple portfolio projects,
docker-sentinel will monitor all running containers automatically:

- vault (vault-secrets-demo)
- consumer-app (vault-secrets-demo)
- expiry-watcher-dashboard
- portainer
- Any other containers started on the host

No configuration needed — docker-sentinel discovers all running
containers via the Docker socket.

## Notes

- `python3.12-venv` must be installed explicitly on Ubuntu Server
- Dashboard runs on port 8081 (mapped from container port 8080) to
  avoid conflict with expiry-watcher on port 8080
- The Docker socket (`/var/run/docker.sock`) is the same path as on
  desktop Linux — no configuration changes needed

## Running alongside other projects

Tested running simultaneously with:
- **vault-secrets-demo** (ports 8000, 8200)
- **expiry-watcher** (port 8080)
- **kube-sentinel** (Grafana port 30093, Prometheus port 31664)
- **Portainer** (port 9000)

No port conflicts. Dashboard container visible in Portainer at
`http://<VM_IP>:9000`.
