#!/usr/bin/env bash
set -e

APP_DIR="/home/opc/PLAYWRIGHT"
ENV_FILE="$APP_DIR/.env"
LOG_FILE="/home/opc/playwright-deploy.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "=== Deploiement START ==="
cd "$APP_DIR"

# Creation du .env s'il n'existe pas encore (a personnaliser ensuite)
if [ ! -f "$ENV_FILE" ]; then
    cp "$APP_DIR/.env.example" "$ENV_FILE"
    log "Fichier .env cree depuis .env.example (pensez a personnaliser OPENAI_API_KEY / APP_SECRET_KEY / ADMIN_*)"
fi

# Preparation de l'environnement Python
python3 -m venv venv || true
source venv/bin/activate
pip install -q -r requirements.txt
log "pip install OK"

python -m playwright install chromium --with-deps 2>/dev/null || python -m playwright install chromium
log "playwright install OK"

# Redemarrage de l'application
pkill -f "sdet_app/run.py" 2>/dev/null || true
sleep 1
PORT="${PORT:-5000}" nohup python sdet_app/run.py > "$APP_DIR/sdet_app/nohup.out" 2>&1 &
log "App lancee (pid $!) sur le port ${PORT:-5000}"

log "=== Deploiement END ==="