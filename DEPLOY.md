# Hyperscope — Production Deployment Guide

## Prerequisites
- Ubuntu 22.04 server
- Docker + Docker Compose installed
- Domain name pointed at the server IP
- SSL certificate (Let's Encrypt recommended)

---

## Step 1 — Clone & configure

```bash
git clone <your-repo> /opt/hyperscope
cd /opt/hyperscope

# Copy and fill in all values
cp .env.production.example .env
nano .env
```

**Required values to set in `.env`:**
| Key | How to get it |
|-----|---------------|
| `ENVIRONMENT` | Must be `production` (otherwise bulk sends are simulated and `/docs` is public) |
| `SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_hex(32))"` (min 32 chars) |
| `MYSQL_ROOT_PASSWORD` | Choose a strong password |
| `MONGO_ROOT_PASSWORD` | `openssl rand -hex 24` (URL-safe; embedded in `MONGODB_URL`) |
| `MONGO_ROOT_USERNAME` | Optional, defaults to `hyperscope` |
| `WAHA_API_KEY` | Any random string, e.g. `openssl rand -hex 20` |
| `WAHA_WEBHOOK_SECRET` | `openssl rand -hex 32` (min 32 chars) |
| `PUBLIC_WEBHOOK_BASE_URL` | `https://yourdomain.com` |
| `ALLOWED_ORIGINS` | `["https://yourdomain.com"]` (`"*"` is rejected in production) |
| `GEMINI_API_KEY` | From Google AI Studio |

With `ENVIRONMENT=production` the app **refuses to start** if `SECRET_KEY` or
`WAHA_WEBHOOK_SECRET` is a placeholder / shorter than 32 characters, or if
`ALLOWED_ORIGINS` contains `"*"`. `DATABASE_URL`, `MONGODB_URL` and
`WAHA_BASE_URL` are overridden by `docker-compose.yml` to use the `mysql`,
`mongo` and `waha` services.

---

## Step 2 — SSL Certificate

```bash
# Install certbot
apt install certbot -y

# Get certificate (stop nginx first if running)
certbot certonly --standalone -d yourdomain.com

# Copy certs to nginx folder
mkdir -p /opt/hyperscope/nginx/certs
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem /opt/hyperscope/nginx/certs/
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem /opt/hyperscope/nginx/certs/

# Update nginx.conf with your domain
sed -i 's/YOUR_DOMAIN.com/yourdomain.com/g' /opt/hyperscope/nginx/nginx.conf
```

---

## Step 3 — Start the stack

```bash
cd /opt/hyperscope

# Build and start everything
docker compose up -d --build

# Check all containers are healthy (mysql, mongo, waha, app, nginx)
docker compose ps

# Watch logs
docker compose logs -f app
```

The stack runs MySQL (agents, contacts, settings), MongoDB (chats and
messages), WAHA, the app and nginx. Only nginx publishes ports (80/443). The
MySQL schema and MongoDB indexes are created automatically when the app starts
(`app/db/init_db.py`), so there is no separate migration step.

---

## Step 4 — Create admin user

```bash
# Prompts for the password (not echoed, not stored in shell history)
docker compose exec app python scripts/create_admin.py --email admin@yourdomain.com --name "Admin"
```

The script is idempotent: re-running it for an existing email resets that
agent's password and makes it an active admin. For non-interactive use:

```bash
docker compose exec -T -e ADMIN_EMAIL=admin@yourdomain.com -e ADMIN_PASSWORD='...' app python scripts/create_admin.py
```

Passwords must be 8–72 characters. There is no default password.

---

## Step 5 — Connect WhatsApp

1. Open the app at `https://yourdomain.com`
2. Go to **Settings → Phones → Add Phone**
3. The QR code will appear — scan it with WhatsApp on your phone

---

## Step 6 — Auto-renew SSL

```bash
# Add crontab for cert renewal
(crontab -l; echo "0 3 * * * certbot renew --quiet && cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem /opt/hyperscope/nginx/certs/ && cp /etc/letsencrypt/live/yourdomain.com/privkey.pem /opt/hyperscope/nginx/certs/ && docker compose -f /opt/hyperscope/docker-compose.yml restart nginx") | crontab -
```

---

## Useful commands

```bash
# Restart app only
docker compose restart app

# View app logs
docker compose logs -f app

# Backup MySQL
docker compose exec mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -u root whatsapp_periscope' > backup.sql

# Backup MongoDB (chats + messages)
docker compose exec mongo sh -c 'mongodump --archive -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' > mongo-backup.archive

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
ufw enable
```

**Note:** Docker writes its own iptables rules, so ports published by
containers **bypass UFW** — `ufw deny` does not protect them. That is why
`docker-compose.yml` publishes only nginx (80/443): WAHA (3000), the app
(8000), MySQL and MongoDB are reachable only on the internal Docker network.
If you need the WAHA dashboard temporarily, bind it to loopback
(`"127.0.0.1:3000:3000"`) and use an SSH tunnel
(`ssh -L 3000:127.0.0.1:3000 user@server`).

**Scaling:** keep a single app container with `--workers 1`. WebSocket
connections, the login lockout and the APScheduler jobs are all in-process.
