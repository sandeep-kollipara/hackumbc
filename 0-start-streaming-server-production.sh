#!/bin/bash

set -e
#set -x

# ============================================================
# VectorCompare Production Server Setup
# Domain: vector.compare
#
# Run this script from the directory containing:
#
#   0-setup-stream-server.sh
#   streaming-server/
#
# Usage:
#   chmod +x 0-setup-stream-server.sh
#   ./0-setup-stream-server.sh
# ============================================================

MODE="${1:-local}"

if [ "$MODE" = "local" ]; then
    DOMAIN="localhost"
    NGINX_PORT="8080"
    ENABLE_HTTPS=false
elif [ "$MODE" = "production" ]; then
    DOMAIN="vector.compare"
    NGINX_PORT="80"
    ENABLE_HTTPS=true   
else
    echo "ERROR: Invalid mode '$MODE'"
    echo "Usage: $0 [local|production]"
    exit 1
fi

#DOMAIN="vector.compare"
PROJECT_DIR="streaming-server"
VENV_DIR=".venv"
DJANGO_MODULE="vectorcompare.wsgi:application"
SERVICE_NAME="vectorcompare"

# Absolute paths based on the directory containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_PATH="$SCRIPT_DIR/$PROJECT_DIR"
VENV_PATH="$SCRIPT_DIR/$VENV_DIR"
STATIC_PATH="$PROJECT_PATH/staticfiles"

echo "============================================"
echo " VectorCompare Production Setup"
echo "============================================"
echo ""
echo "Domain:       $DOMAIN"
echo "Project:      $PROJECT_PATH"
echo "Virtual env:  $VENV_PATH"
echo ""


# ============================================================
# 1. Install system packages
# ============================================================

echo "[1/9] Installing system packages..."

sudo apt update

sudo apt install -y \
    python3 \
    python3-venv \
    python3-pip \
    nginx \
    certbot \
    python3-certbot-nginx


# ============================================================
# 2. Create Python virtual environment
# ============================================================

echo "[2/9] Creating Python virtual environment..."

if [ ! -d "$VENV_PATH" ]; then
    python3 -m venv "$VENV_PATH"
fi

source "$VENV_PATH/bin/activate"


# ============================================================
# 3. Install Python dependencies
# ============================================================

echo "[3/9] Installing Python dependencies..."

python -m pip install --upgrade pip
pip install -r "$PROJECT_PATH/requirements.txt"

# Install Gunicorn if it isn't already in requirements.txt.
pip install gunicorn


# ============================================================
# 4. Run Django setup
# ============================================================

echo "[4/9] Running Django migrations..."

python "$PROJECT_PATH/manage.py" migrate

echo "Collecting static files..."

python "$PROJECT_PATH/manage.py" collectstatic --noinput


# ============================================================
# 5. Create Gunicorn systemd service
# ============================================================

echo "[5/9] Configuring Gunicorn systemd service..."

CURRENT_USER="$(whoami)"

sudo tee "/etc/systemd/system/$SERVICE_NAME.service" > /dev/null <<EOF
[Unit]
Description=VectorCompare Gunicorn Server
After=network.target

[Service]
User=$CURRENT_USER
Group=$CURRENT_USER

WorkingDirectory=$PROJECT_PATH

EnvironmentFile=$PROJECT_PATH/.env

ExecStart=$VENV_PATH/bin/gunicorn \
    --workers 3 \
    --bind 127.0.0.1:8000 \
    $DJANGO_MODULE

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF


# Reload systemd configuration
sudo systemctl daemon-reload

# Enable Gunicorn on boot
sudo systemctl enable "$SERVICE_NAME"

# Restart if it already exists; otherwise start it
sudo systemctl restart "$SERVICE_NAME"


# ============================================================
# 6. Configure Nginx
# ============================================================

echo "[6/9] Configuring Nginx..."

NGINX_CONFIG="/etc/nginx/sites-available/$SERVICE_NAME"

sudo tee "$NGINX_CONFIG" > /dev/null <<EOF
server {
    listen $NGINX_PORT;
    listen [::]:$NGINX_PORT;

    server_name $DOMAIN;

    client_max_body_size 10M;

    location /static/ {
        alias $STATIC_PATH/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;

        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_connect_timeout 30s;
        proxy_read_timeout 60s;
    }
}
EOF


# Enable VectorCompare Nginx site
sudo ln -sf \
    "$NGINX_CONFIG" \
    "/etc/nginx/sites-enabled/$SERVICE_NAME"

# Remove default Nginx configuration
sudo rm -f /etc/nginx/sites-enabled/default


# Validate Nginx configuration
sudo nginx -t

# Enable Nginx on boot
sudo systemctl enable nginx

# Restart Nginx
sudo systemctl restart nginx
#echo "Starting Nginx..."
#
#if ! sudo systemctl restart nginx; then
#    echo ""
#    echo "ERROR: Nginx failed to start."
#    echo ""
#
#    echo "===== NGINX STATUS ====="
#    sudo systemctl status nginx --no-pager -l || true
#
#    echo ""
#    echo "===== NGINX JOURNAL ====="
#    sudo journalctl -u nginx.service \
#        --no-pager \
#        -n 100 || true
#
#    echo ""
#    echo "===== PORT 80/443 USAGE ====="
#    sudo ss -ltnp | grep -E ':(80|443)\b' || true
#
#    echo ""
#    echo "===== NGINX ERROR LOG ====="
#    sudo tail -100 /var/log/nginx/error.log || true
#
#    exit 1
#fi
#echo "===== ENABLING NGINX ====="
#
#sudo systemctl enable nginx
#ENABLE_RESULT=$?
#
#echo "systemctl enable exit code: $ENABLE_RESULT"
#
#echo ""
#echo "===== STARTING NGINX ====="
#
#sudo systemctl restart nginx
#RESTART_RESULT=$?
#
#echo "systemctl restart exit code: $RESTART_RESULT"


# ============================================================
# 7. Verify HTTP site
# ============================================================

echo "[7/9] Checking local services..."

sleep 2

if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Gunicorn: OK"
else
    echo "ERROR: Gunicorn failed to start."
    echo ""
    sudo systemctl status "$SERVICE_NAME" --no-pager
    exit 1
fi

if sudo systemctl is-active --quiet nginx; then
    echo "Nginx: OK"
else
    echo "ERROR: Nginx failed to start."
    echo ""
    sudo systemctl status nginx --no-pager
    exit 1
fi


echo ""
echo "Testing Django through Gunicorn..."

curl --fail --silent \
    --show-error \
    -H "Host: $DOMAIN" \
    "http://127.0.0.1:$NGINX_PORT/" \
    > /dev/null

echo "Django + Gunicorn + Nginx: OK"


# ============================================================
# 8. Configure HTTPS using Let's Encrypt
# ============================================================

echo "[8/9] Configuring HTTPS..."

echo ""
echo "Certbot will request a TLS certificate for:"
echo "https://$DOMAIN"
echo ""

if [ "$ENABLE_HTTPS" = true ]; then

    echo "Configuring HTTPS..."

    # Check whether Certbot already knows about this certificate.
    if sudo certbot certificates 2>/dev/null \
        | grep -q "Certificate Name: $DOMAIN"; then

        echo "Existing certificate found."
        echo "Skipping certificate creation."

    else
        
        echo "Requesting Let's Encrypt certificate..."

        sudo certbot \
            --nginx \
            -d "$DOMAIN" \
            --redirect

    fi

else

    echo "Local mode: skipping HTTPS/Certbot."

fi


# ============================================================
# 9. Final checks
# ============================================================

echo "[9/9] Running final checks..."

sudo nginx -t

#sudo systemctl restart nginx
#sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "Gunicorn status:"
sudo systemctl is-active "$SERVICE_NAME"

echo ""
echo "Nginx status:"
sudo systemctl is-active nginx


if [ "$ENABLE_HTTPS" = true ]; then

    echo ""
    echo "Checking HTTPS..."

    curl --fail --silent \
    --show-error \
    "https://$DOMAIN/" \
    > /dev/null

else

    echo ""
    echo "Checking HTTP..."

    curl --fail --silent \
    --show-error \
    -H "Host: $DOMAIN" \
    "http://127.0.0.1:$NGINX_PORT/" \
    > /dev/null

fi


echo ""
echo "============================================"
echo " VectorCompare deployment complete!"
echo "============================================"
echo ""
echo "Website:"
if [ "$ENABLE_HTTPS" = true ]; then
    echo "    https://$DOMAIN"
else
    echo "    http://$DOMAIN:$NGINX_PORT"
fi
echo ""
echo "Gunicorn logs:"
echo "    sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "Gunicorn status:"
echo "    sudo systemctl status $SERVICE_NAME"
echo ""
echo "Nginx status:"
echo "    sudo systemctl status nginx"
echo ""
echo "Test certificate renewal:"
echo "    sudo certbot renew --dry-run"
echo ""