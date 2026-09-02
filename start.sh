#!/bin/bash
ROOT_DIR=$(pwd)

echo "🚀 Iniciando servidor WhatsApp Baileys en puerto 8085..."
cd "$ROOT_DIR/whatsapp_server" && node server.js &

echo "🚀 Iniciando servidor Flask Gunicorn en puerto ${PORT:-10000}..."
cd "$ROOT_DIR" && exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 0 app:app
