# ZTAM Production Deployment Guide

This guide covers how to deploy the ZTAM platform on a fresh Ubuntu 22.04+ VPS.

## 1. Prerequisites (VPS Setup)

- **Ubuntu 22.04/24.04** with root or sudo access.
- **Domain Name** pointing to your VPS IP (e.g., `ztam.yourdomain.com`).
- **Ports Open**: 80 (HTTP), 443 (HTTPS), 22 (SSH).

### Install Docker
```bash
sudo apt update && sudo apt install -y docker.io docker-compose
sudo systemctl enable --now docker
```

## 2. Clone and Configure

```bash
git clone https://github.com/Mohammed-seddik/ztam-platform.git
cd ztam-platform
cp .env.example .env
```

### Edit .env
Crucial variables for production:
- `KC_HOSTNAME`: `ztam.yourdomain.com` (your public FQDN)
- `KC_ISSUER_URL`: `https://ztam.yourdomain.com`
- `ZTAM_PUBLIC_URL`: `https://ztam.yourdomain.com`
- `PG_PASS`: Strong random password
- `KC_ADMIN_PASS`: Strong random password
- `TESTAPP_JWT_SECRET`: Strong random string

## 3. SSL Certificates (Let's Encrypt)

We recommend using **Certbot** on the host.

```bash
sudo apt install -y certbot
sudo certbot certonly --standalone -d ztam.yourdomain.com
```

Link the certificates to the Envoy directory:
```bash
mkdir -p envoy/certs
sudo ln -sf /etc/letsencrypt/live/ztam.yourdomain.com/fullchain.pem envoy/certs/server.crt
sudo ln -sf /etc/letsencrypt/live/ztam.yourdomain.com/privkey.pem envoy/certs/server.key
```

## 4. Start the Platform

```bash
docker compose up -d
```

Verify everything is running:
```bash
docker compose ps
```

## 5. First-Time Setup

Run the demo setup script (one-time) to initialize the realm:
```bash
python3 setup_demo.py --force
```

## 6. Onboarding your first Client

Example: Protecting a legacy app with NO login page.

```bash
./scripts/onboard-tenant.sh \
  --name legacyapp \
  --backend http://internal-ip:8080 \
  --hostname app.yourdomain.com \
  --login-mode keycloak \
  --no-spi
```

**Note:** You must also point `app.yourdomain.com` to the ZTAM server IP.

## 7. Hardening Check

- [ ] **Firewall**: `sudo ufw allow 80,443,22/tcp && sudo ufw enable`
- [ ] **Keycloak**: Verify admin console is only accessible via SSH tunnel or bound to 127.0.0.1 (default in our `docker-compose.yml`).
- [ ] **Secrets**: Ensure all passwords in `.env` are changed from defaults.

## 8. Scaling and Backups

- **Database**: For high availability, use a managed RDS/PostgreSQL instead of the containerized one.
- **Backups**: Periodically dump the `postgres` database:
  `docker exec -t ztam-postgres pg_dumpall -c -U keycloak > dump.sql`
