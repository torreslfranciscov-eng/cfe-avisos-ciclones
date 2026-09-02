FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=10000 \
    WA_PORT=8085 \
    OPENWA_SERVER_URL=http://localhost:8085

WORKDIR /app

# 1. Instalar Node.js 20 y fuentes del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    fonts-noto-core \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 2. Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. Instalar dependencias Node.js para WhatsApp
COPY whatsapp_server/package*.json ./whatsapp_server/
RUN cd whatsapp_server && npm install --no-audit

# 4. Copiar código fuente
COPY . .
RUN chmod +x start.sh

EXPOSE 10000 8085

CMD ["/app/start.sh"]
