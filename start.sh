#!/bin/bash
set -e

echo "🚀 Iniciando servidor de WhatsApp (Node.js/Baileys) en puerto 8085..."
cd /app/whatsapp_server && node server.js &

echo "🚀 Iniciando servidor Web Flask/Gunicorn en puerto $PORT..."
cd /app && exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 0 app:app
