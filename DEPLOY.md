# Periskope — Production Deployment Guide

## Prerequisites
- Ubuntu 22.04 server
- Docker + Docker Compose installed
- Domain name pointed at the server IP
- SSL certificate (Let's Encrypt recommended)

---

## Step 1 — Clone & configure

```bash
git clone <your-repo> /opt/periskope
cd /opt/periskope

# Copy and fill in all values
cp .env.production.example .env
nano .env
```

**Required values to set in `.env`:**
| Key | How to get it |
|-----|---------------|
| `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `MYSQL_ROOT_PASSWORD` | Choose a strong password |
| `WAHA_API_KEY` | Any random string, e.g. `openssl rand -hex 20` |
| `WAHA_WEBHOOK_SECRET` | Any random string, e.g. `openssl rand -hex 20` |
| `PUBLIC_WEBHOOK_BASE_URL` | `https://yourdomain.com` |
| `GEMINI_API_KEY` | From Google AI Studio |

---

## Step 2 — SSL Certificate

```bash
# Install certbot
apt install certbot -y

# Get certificate (stop nginx first if running)
certbot certonly --standalone -d yourdomain.com

# Copy certs to nginx folder
mkdir -p /opt/periskope/nginx/certs
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem /opt/periskope/nginx/certs/
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem /opt/periskope/nginx/certs/

# Update nginx.conf with your domain
sed -i 's/YOUR_DOMAIN.com/yourdomain.com/g' /opt/periskope/nginx/nginx.conf
```

---

## Step 3 — Start the stack

```bash
cd /opt/periskope

# Build and start everything
docker compose up -d --build

# Check all containers are healthy
docker compose ps

# Watch logs
docker compose logs -f app
```

---

## Step 4 — Create admin user

```bash
docker compose exec app python scripts/seed_admin.py
```

Then log in and **immediately change the password** from Settings.

---

## Step 5 — Connect WhatsApp

1. Open the app at `https://yourdomain.com`
2. Go to **Settings → Phones → Add Phone**
3. The QR code will appear — scan it with WhatsApp on your phone

---

## Step 6 — Auto-renew SSL

```bash
# Add crontab for cert renewal
(crontab -l; echo "0 3 * * * certbot renew --quiet && cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem /opt/periskope/nginx/certs/ && cp /etc/letsencrypt/live/yourdomain.com/privkey.pem /opt/periskope/nginx/certs/ && docker compose -f /opt/periskope/docker-compose.yml restart nginx") | crontab -
```

---

## Useful commands

```bash
# Restart app only
docker compose restart app

# View app logs
docker compose logs -f app

# Run DB migrations (if using Alembic)
docker compose exec app alembic upgrade head

# Backup database
docker compose exec mysql mysqldump -u root -p$MYSQL_ROOT_PASSWORD whatsapp_periscope > backup.sql

# Update to latest version
git pull
docker compose up -d --build app
```

---

## Firewall (UFW)

```bash
ufw allow 22    # SSH
ufw allow 80    # HTTP (redirects to HTTPS)
ufw allow 443   # HTTPS
ufw deny 3000   # Block direct WAHA access from internet
ufw deny 8000   # Block direct app access (use nginx)
ufw enable
```
