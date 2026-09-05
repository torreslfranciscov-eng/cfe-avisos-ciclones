#!/bin/bash
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PORT="${PORT:-10000}"
export WA_PORT="${WA_PORT:-8085}"
export OPENWA_SERVER_URL="${OPENWA_SERVER_URL:-http://localhost:8085}"

echo "🚀 Iniciando servidor WhatsApp Baileys en puerto ${WA_PORT}..."
(cd "$ROOT_DIR/whatsapp_server" && node server.js) &

echo "🚀 Iniciando servidor Flask Gunicorn en puerto ${PORT}..."
cd "$ROOT_DIR" && exec gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 8 --timeout 0 app:app
