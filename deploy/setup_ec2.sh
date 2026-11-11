#!/bin/bash
# =============================================================================
# FitLife - Instalación completa en una EC2 Ubuntu 24.04 (AWS Academy)
#
# Instala PostgreSQL + Django (gunicorn) + Nginx en la MISMA instancia.
# Se ejecuta automáticamente desde el "User data" de la instancia, o a mano:
#
#   sudo REPO_URL=https://github.com/USUARIO/fitlife.git \
#        ADMIN_PASSWORD='ClaveAdmin' DEMO_PASSWORD='FitLife2026!' \
#        bash deploy/setup_ec2.sh
#
# Es idempotente: se puede volver a ejecutar para actualizar el código.
# Log: /var/log/fitlife-setup.log
# =============================================================================
set -euo pipefail
exec > >(tee -a /var/log/fitlife-setup.log) 2>&1
echo "==== Inicio instalación FitLife: $(date) ===="

REPO_URL="${REPO_URL:?Defina REPO_URL}"
APP_DIR="/opt/fitlife"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:?Defina ADMIN_PASSWORD}"
DEMO_PASSWORD="${DEMO_PASSWORD:-FitLife2026!}"
export DEBIAN_FRONTEND=noninteractive

# --- 1. Paquetes del sistema --------------------------------------------------
apt-get update -y
apt-get install -y python3-venv python3-pip postgresql postgresql-contrib nginx git openssl
timedatectl set-timezone America/Santiago || true
systemctl enable --now postgresql

# --- 2. Usuario del sistema para la app ----------------------------------------
id fitlife &>/dev/null || useradd --system --shell /usr/sbin/nologin --home-dir "$APP_DIR" fitlife

# --- 3. Código fuente ---------------------------------------------------------
if [ -d "$APP_DIR/.git" ]; then
  git config --global --add safe.directory "$APP_DIR"
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi

# --- 4. Base de datos PostgreSQL (local en la EC2) ------------------------------
if [ -f "$APP_DIR/.env" ]; then
  DB_PASSWORD=$(grep '^DB_PASSWORD=' "$APP_DIR/.env" | cut -d= -f2-)
  SECRET_KEY=$(grep '^DJANGO_SECRET_KEY=' "$APP_DIR/.env" | cut -d= -f2-)
else
  DB_PASSWORD=$(openssl rand -hex 16)
  SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n/+=')
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fitlife') THEN
    CREATE ROLE fitlife LOGIN PASSWORD '${DB_PASSWORD}';
  ELSE
    ALTER ROLE fitlife WITH LOGIN PASSWORD '${DB_PASSWORD}';
  END IF;
END
\$\$;
SQL
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='fitlife'" | grep -q 1 \
  || sudo -u postgres createdb -O fitlife -E UTF8 fitlife

# --- 5. Variables de entorno (.env, fuera del repositorio) ----------------------
cat > "$APP_DIR/.env" <<EOF
DJANGO_SECRET_KEY=${SECRET_KEY}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=*
DB_NAME=fitlife
DB_USER=fitlife
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=localhost
DB_PORT=5432
FITLIFE_CAPACIDAD=30
EOF
chmod 600 "$APP_DIR/.env"

# --- 6. Entorno virtual, migraciones, estáticos y datos demo ---------------------
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

cd "$APP_DIR"
PY="$APP_DIR/venv/bin/python"
$PY manage.py migrate --noinput
$PY manage.py collectstatic --noinput
$PY manage.py seed_demo --password "$DEMO_PASSWORD"
DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASSWORD" $PY manage.py createsuperuser \
  --noinput --username "$ADMIN_USER" --email "${ADMIN_USER}@fitlife.cl" || echo "Superusuario ya existe"
chown -R fitlife:www-data "$APP_DIR"

# --- 7. Servicio gunicorn (systemd) --------------------------------------------
cat > /etc/systemd/system/fitlife.service <<EOF
[Unit]
Description=FitLife Django (gunicorn)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=fitlife
Group=www-data
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60 --access-logfile -
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable fitlife
systemctl restart fitlife

# --- 8. Nginx como proxy inverso en el puerto 80 ---------------------------------
cat > /etc/nginx/sites-available/fitlife <<EOF
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 5M;

    location /static/ {
        alias ${APP_DIR}/staticfiles/;
        expires 7d;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/fitlife /etc/nginx/sites-enabled/fitlife
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl restart nginx

# --- 9. Tareas programadas (cron) ------------------------------------------------
cat > /etc/cron.d/fitlife <<EOF
# Vence membresías y genera sesiones de la próxima semana todos los días a las 00:05
5 0 * * * fitlife cd ${APP_DIR} && ${APP_DIR}/venv/bin/python manage.py actualizar_membresias && ${APP_DIR}/venv/bin/python manage.py generar_sesiones --dias 7
# Al reiniciar la instancia (el lab de Academy se apaga) también se regeneran las sesiones
@reboot fitlife sleep 60 && cd ${APP_DIR} && ${APP_DIR}/venv/bin/python manage.py actualizar_membresias && ${APP_DIR}/venv/bin/python manage.py generar_sesiones --dias 7
EOF
chmod 644 /etc/cron.d/fitlife

sleep 2
curl -fsS http://127.0.0.1/api/salud/ && echo
echo "==== FitLife instalado correctamente: $(date) ===="
