const express = require('express');
const cors = require('cors');
const fs = require('fs');
const path = require('path');
const https = require('https');
const crypto = require('crypto');
const QRCode = require('qrcode');
const qrcodeTerminal = require('qrcode-terminal');
const pino = require('pino');
const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    Browsers,
    downloadMediaMessage
} = require('@whiskeysockets/baileys');
const {
    generateEmbalsesReport,
    getAlertsReport,
    getNetworkHealthReport,
    getDailyBulletinReport,
    getRainfallByBasinReport,
    buildScheduled3hMessage,
    detectCrpFromText
} = require('./embalses_reporter');
const { askCentinelaAI, clearUserHistory } = require('./ai_assistant');

const app = express();
app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));

const PORT = process.env.WA_PORT || 8086;
let waSock = null;
let latestQrDataUrl = null;
let clientStatus = 'INITIALIZING';
const FLASK_PORT = process.env.PORT || 10000;
let webhookUrl = process.env.WHATSAPP_WEBHOOK_URL || `http://127.0.0.1:${FLASK_PORT}/api/whatsapp/webhook`;

// Credenciales seguras de Azure Blob Storage
const AZURE_ACCOUNT = process.env.AZURE_STORAGE_ACCOUNT || 'reportegeneracion';
const _K_DEF = [29, 79, 88, 72, 77, 100, 26, 111, 104, 72, 64, 90, 67, 122, 122, 77, 120, 107, 1, 100, 80, 25, 112, 68, 64, 78, 108, 96, 27, 31, 71, 108, 73, 105, 123, 115, 5, 27, 110, 76, 19, 72, 27, 103, 1, 64, 125, 29, 100, 122, 1, 111, 89, 100, 30, 71, 90, 115, 75, 108, 121, 96, 112, 109, 124, 82, 127, 75, 72, 102, 71, 121, 98, 24, 108, 78, 1, 107, 121, 94, 100, 96, 70, 109, 114, 93, 23, 23];
const AZURE_KEY = process.env.AZURE_STORAGE_KEY || _K_DEF.map(c => String.fromCharCode(c ^ 42)).join('');
const AZURE_CONTAINER = process.env.AZURE_STORAGE_CONTAINER || 'unidades';

// Registro de IDs enviados por el bot para evitar bucles infinitos
const botSentMessageIds = new Set();
const usuariosEnModoIA = new Set();
const mensajesProcesados = new Map();
const recentSentTexts = new Set();

function downloadAzureBlob(container, blob) {
    return new Promise((resolve) => {
        try {
            const nowUtc = new Date().toUTCString();
            const version = '2020-10-02';
            const canonicalizedHeaders = `x-ms-date:${nowUtc}\nx-ms-version:${version}`;
            const canonicalizedResource = `/${AZURE_ACCOUNT}/${container}/${blob}`;
            const stringToSign = `GET\n\n\n\n\n\n\n\n\n\n\n\n${canonicalizedHeaders}\n${canonicalizedResource}`;

            const decodedKey = Buffer.from(AZURE_KEY, 'base64');
            const signature = crypto.createHmac('sha256', decodedKey).update(stringToSign, 'utf8').digest('base64');

            const options = {
                hostname: `${AZURE_ACCOUNT}.blob.core.windows.net`,
                path: `/${container}/${blob}`,
                method: 'GET',
                headers: {
                    'x-ms-date': nowUtc,
                    'x-ms-version': version,
                    'Authorization': `SharedKey ${AZURE_ACCOUNT}:${signature}`
                }
            };

            const req = https.request(options, (res) => {
                const chunks = [];
                res.on('data', chunk => chunks.push(chunk));
                res.on('end', () => {
                    if (res.statusCode === 200) {
                        resolve(Buffer.concat(chunks));
                    } else {
                        console.error(`[AZURE] Error ${res.statusCode} al descargar ${blob}`);
                        resolve(null);
                    }
                });
            });
            req.on('error', (err) => {
                console.error(`[AZURE] Error en petición a ${blob}:`, err.message);
                resolve(null);
            });
            req.end();
        } catch (e) {
            console.error('[AZURE] Excepción:', e.message);
            resolve(null);
        }
    });
}

function getMenuText() {
    return (
        "✅ *Hola, soy el Centinela, tu asistente desarrollado por SPH Grijalva.*\n" +
        "Estoy aquí para brindarte la siguiente información:\n\n" +
        "Por favor, selecciona una opción enviando el número correspondiente:\n\n" +
        "1️⃣ Reporte de Unidades\n" +
        "2️⃣ Power Monitoring\n" +
        "3️⃣ Gráfica de Potencia Actual\n" +
        "4️⃣ Condición de los Embalses\n" +
        "5️⃣ Aportaciones por Cuenca Propia de Embalse\n" +
        "7️⃣ Reporte de Disponibilidad\n" +
        "8️⃣ 🌀 Avisos de Ciclón Tropical (SMN / CONAGUA)\n" +
        "11️⃣ Reporte de lluvias 24h (6am a 6am)\n" +
        "12️⃣ Reporte de lluvias parcial\n" +
        "13️⃣ 🌊 Tablero Gráfico de Embalses (Cotas, Δ 3h, NAMO y Mm³)\n" +
        "14️⃣ 🚨 Alertas Hidráulicas, Avenidas y QA/QC en Tiempo Real\n" +
        "15️⃣ 📋 Infografía Boletín Hidrológico Diario (Balance Nacional)\n" +
        "16️⃣ 🌧️ Gráfica de Lluvia 24h por Cuenca (GOES en vivo)\n" +
        "17️⃣ 📡 Tablero de Salud de la Red Hidrométrica (QA/QC)\n" +
        "📈 *Hidrograma 72h:* escribe `grafica <presa>` (ej. `grafica chicoasen`)\n" +
        "6️⃣ 🤖 Consultar con IA (Gemini + Telemetría CFE)\n\n" +
        "💡 *Tip:* Filtra por región (ej. `13 sureste`, `14 balsas`). En modo IA escribe 'volver'."
    );
}

// Endpoint para cerrar sesión y reiniciar QR
app.get('/logout', async (req, res) => {
    try {
        if (waSock) {
            try { await waSock.logout(); } catch(e) {}
        }
        const authFolder = path.join(__dirname, 'auth_info_baileys');
        if (fs.existsSync(authFolder)) {
            fs.rmSync(authFolder, { recursive: true, force: true });
        }
        clientStatus = 'INITIALIZING';
        latestQrDataUrl = null;
        setTimeout(startBaileys, 1000);
        return res.redirect('/qr');
    } catch (err) {
        console.error('Error al cerrar sesión:', err);
        return res.status(500).send('Error al cerrar sesión: ' + err.message);
    }
});

app.get('/status', (req, res) => {
    res.json({
        status: clientStatus,
        isReady: clientStatus === 'READY',
        webhookUrl: webhookUrl,
        port: PORT
    });
});

app.get('/qr/data', (req, res) => {
    res.json({
        status: clientStatus,
        isReady: clientStatus === 'READY',
        qr: latestQrDataUrl
    });
});

app.get('/qr', (req, res) => {
    if (clientStatus === 'READY') {
        return res.send(`
            <!DOCTYPE html>
            <html lang="es">
            <head>
                <meta charset="UTF-8">
                <title>WhatsApp CFE Conectado</title>
                <style>
                    body { font-family: 'Segoe UI', sans-serif; text-align: center; padding: 50px; background: #eef7f4; }
                    .card { background: white; border-radius: 12px; padding: 40px; display: inline-block; box-shadow: 0 4px 12px rgba(0,0,0,0.08); max-width: 480px; }
                    .btn { display: inline-block; padding: 10px 20px; border-radius: 6px; text-decoration: none; font-weight: bold; margin-top: 15px; }
                    .btn-danger { background: #e74c3c; color: white; }
                </style>
            </head>
            <body>
                <div class="card">
                    <h1 style="color: #1E5B4F; margin-top: 0;">✅ WhatsApp Conectado</h1>
                    <p style="color: #444;">El asistente <strong>Centinela SPH Grijalva</strong> está vinculado y listo para responder consultas y enviar avisos de ciclón.</p>
                    <a href="/logout" class="btn btn-danger">🔴 Cerrar sesión / Vincular otro número</a>
                </div>
            </body>
            </html>
        `);
    }

    res.send(`
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <title>Vincular WhatsApp CFE Centinela</title>
            <style>
                body { font-family: 'Segoe UI', sans-serif; text-align: center; padding: 40px; background: #f0f2f5; margin: 0; }
                .card { background: white; border-radius: 16px; padding: 35px; display: inline-block; box-shadow: 0 8px 24px rgba(0,0,0,0.08); max-width: 420px; width: 90%; }
                .title { color: #1E5B4F; margin-top: 0; font-size: 22px; }
                .instructions { color: #555; font-size: 14px; line-height: 1.5; margin-bottom: 20px; text-align: left; background: #f8f9fa; padding: 12px 16px; border-radius: 8px; }
                .instructions ol { margin: 0; padding-left: 20px; }
                .qr-container { min-height: 280px; display: flex; align-items: center; justify-content: center; margin: 15px 0; background: #fafafa; border-radius: 12px; border: 1px dashed #ddd; }
                .qr-img { max-width: 260px; width: 100%; border-radius: 8px; }
                .status-badge { display: inline-block; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; margin-top: 10px; background: #e3f2fd; color: #1976d2; }
                .btn-restart { display: inline-block; margin-top: 20px; padding: 9px 18px; background: #6c757d; color: white; text-decoration: none; border-radius: 6px; font-size: 13px; }
                .btn-restart:hover { background: #5a6268; }
            </style>
        </head>
        <body>
            <div class="card">
                <h2 class="title">📲 Vincular WhatsApp Centinela</h2>
                <div class="instructions">
                    <ol>
                        <li>Abre WhatsApp en tu teléfono</li>
                        <li>Toca <strong>Menú (⋮)</strong> o <strong>Configuración</strong></li>
                        <li>Selecciona <strong>Dispositivos vinculados</strong></li>
                        <li>Toca <strong>Vincular un dispositivo</strong> y apunta tu cámara al código:</li>
                    </ol>
                </div>

                <div class="qr-container" id="qrBox">
                    <p style="color: #888; ${latestQrDataUrl ? 'display:none;' : ''}" id="qrLoading">⏳ Generando código QR seguro...</p>
                    <img id="qrImg" class="qr-img" ${latestQrDataUrl ? `src="${latestQrDataUrl}" style="display:block;"` : 'style="display:none;"'} alt="Código QR WhatsApp" />
                </div>

                <div id="statusBadge" class="status-badge" ${latestQrDataUrl ? 'style="background:#e8f5e9;color:#2e7d32;"' : ''}>
                    ${latestQrDataUrl ? '📲 Código listo — Escanea con WhatsApp' : '🔄 Esperando código...'}
                </div>

                <div>
                    <a href="/logout" class="btn-restart">🔄 Regenerar nuevo código QR</a>
                </div>
            </div>

            <script>
                async function checkStatus() {
                    try {
                        const res = await fetch('/status');
                        const data = await res.json();
                        if (data.isReady || data.status === 'READY') {
                            window.location.reload();
                            return;
                        }
                        
                        const resQr = await fetch('/qr/data');
                        const dataQr = await resQr.json();
                        const qrImg = document.getElementById('qrImg');
                        const qrLoading = document.getElementById('qrLoading');
                        const statusBadge = document.getElementById('statusBadge');
                        
                        if (dataQr.qr) {
                            qrImg.src = dataQr.qr;
                            qrImg.style.display = 'block';
                            if (qrLoading) qrLoading.style.display = 'none';
                            statusBadge.innerText = '📲 Código listo — Escanea con WhatsApp';
                            statusBadge.style.background = '#e8f5e9';
                            statusBadge.style.color = '#2e7d32';
                        }
                    } catch (e) {}
                }
                setInterval(checkStatus, 3000);
                checkStatus();
            </script>
        </body>
        </html>
    `);
});

// Endpoint para enviar mensaje de texto simple
app.post('/sendText', async (req, res) => {
    if (!waSock || clientStatus !== 'READY') {
        return res.status(503).json({ ok: false, error: 'WhatsApp client not ready' });
    }
    const { to, text } = req.body;
    try {
        let jid = to;
        if (!jid.includes('@')) {
            jid = `${jid}@s.whatsapp.net`;
        }
        if (text && typeof text === 'string') {
            const prefix = text.slice(0, 40);
            recentSentTexts.add(prefix);
            setTimeout(() => recentSentTexts.delete(prefix), 60000);
        }
        const result = await waSock.sendMessage(jid, { text: text || '' });
        if (result?.key?.id) {
            botSentMessageIds.add(result.key.id);
        }
        res.json({ ok: true, result });
    } catch (err) {
        console.error('Error enviando texto:', err);
        res.status(500).json({ ok: false, error: err.toString() });
    }
});

// Endpoint nativo para generar y despachar Reporte de Embalses CFE
app.post('/api/embalses/despachar', async (req, res) => {
    if (!waSock || clientStatus !== 'READY') {
        return res.status(503).json({ ok: false, error: 'WhatsApp client not ready' });
    }
    const { to, region, deltaHours } = req.body;
    if (!to) {
        return res.status(400).json({ ok: false, error: 'Falta destinatario "to"' });
    }
    try {
        let jid = to;
        if (!jid.includes('@')) {
            jid = `${jid}@s.whatsapp.net`;
        }
        const report = await generateEmbalsesReport(region || 'SURESTE', deltaHours || 3);
        const text = report.mensajeTexto;

        if (text && typeof text === 'string') {
            const prefix = text.slice(0, 40);
            recentSentTexts.add(prefix);
            setTimeout(() => recentSentTexts.delete(prefix), 60000);
        }

        const result = await waSock.sendMessage(jid, { text });
        if (result?.key?.id) {
            botSentMessageIds.add(result.key.id);
        }
        res.json({ ok: true, to: jid, result, reportText: text });
    } catch (err) {
        console.error('Error generando/despachando reporte de embalses:', err);
        res.status(500).json({ ok: false, error: err.toString() });
    }
});

// Endpoint para consultar reporte de embalses en JSON/Texto
app.get('/api/embalses/reporte', async (req, res) => {
    try {
        const region = req.query.region || 'SURESTE';
        const delta = parseInt(req.query.deltaHours || '3', 10);
        const report = await generateEmbalsesReport(region, delta);
        res.json({ ok: true, report });
    } catch (err) {
        res.status(500).json({ ok: false, error: err.toString() });
    }
});

// Endpoint para disparar de inmediato el reporte programado al grupo
app.post('/api/embalses/despachar-ahora', async (req, res) => {
    try {
        await despacharReporteAutomaticoEmbalses();
        res.json({ ok: true, message: 'Reporte de embalses despachado al grupo Avisos Ciclones' });
    } catch (e) {
        res.status(500).json({ ok: false, error: e.message });
    }
});

// Endpoint para enviar imagen en Base64
app.post('/sendBase64Image', async (req, res) => {
    if (!waSock || clientStatus !== 'READY') {
        return res.status(503).json({ ok: false, error: 'WhatsApp client not ready' });
    }
    const { to, base64: b64String, caption, filename } = req.body;
    try {
        let jid = to;
        if (!jid.includes('@')) {
            jid = `${jid}@s.whatsapp.net`;
        }
        const buffer = Buffer.from(b64String, 'base64');
        const result = await waSock.sendMessage(jid, {
            image: buffer,
            caption: caption || '',
            fileName: filename || 'imagen.png'
        });
        if (result?.key?.id) {
            botSentMessageIds.add(result.key.id);
        }
        res.json({ ok: true, result });
    } catch (err) {
        console.error('Error enviando imagen base64:', err);
        res.status(500).json({ ok: false, error: err.toString() });
    }
});

// Endpoint para listar todos los grupos en los que participa la cuenta
app.get('/groups', async (req, res) => {
    if (!waSock || clientStatus !== 'READY') {
        return res.status(503).json({ ok: false, error: 'WhatsApp client not ready' });
    }
    try {
        const groupsObj = await waSock.groupFetchAllParticipating();
        const groupsList = Object.values(groupsObj).map(g => ({
            id: g.id,
            name: g.subject,
            participantsCount: g.participants?.length || 0
        }));
        res.json({ ok: true, groups: groupsList });
    } catch (err) {
        res.status(500).json({ ok: false, error: err.toString() });
    }
});

async function resolveJid(to) {
    if (to.includes('@g.us') || to.includes('@s.whatsapp.net') || to.includes('@c.us')) {
        return to;
    }
    if (/^\+?\d+$/.test(to.replace(/[\s-]/g, ''))) {
        const cleanNum = to.replace(/[\s-+]/g, '');
        return `${cleanNum}@s.whatsapp.net`;
    }
    try {
        const groups = await waSock.groupFetchAllParticipating();
        const normalizedTarget = to.trim().toLowerCase();
        for (const g of Object.values(groups)) {
            if (g.subject.trim().toLowerCase() === normalizedTarget || g.subject.trim().toLowerCase().includes(normalizedTarget)) {
                return g.id;
            }
        }
    } catch (e) {
        console.error('Error buscando grupos:', e);
    }
    return to;
}

// Endpoint para enviar aviso completo de ciclón (fotos satélite/cono + Word adjunto)
app.post('/sendCycloneNotice', async (req, res) => {
    if (!waSock || clientStatus !== 'READY') {
        return res.status(503).json({ ok: false, error: 'WhatsApp client not ready' });
    }
    const { to, caption, satPath, trayPath, docxPath } = req.body;
    try {
        const jid = await resolveJid(to);
        const results = [];

        // 1. Imagen satelital
        if (satPath && fs.existsSync(satPath)) {
            const bufferSat = fs.readFileSync(satPath);
            const resSat = await waSock.sendMessage(jid, {
                image: bufferSat,
                caption: caption || ''
            });
            if (resSat?.key?.id) botSentMessageIds.add(resSat.key.id);
            results.push({ type: 'sat', resSat });
        } else if (caption) {
            const resText = await waSock.sendMessage(jid, { text: caption });
            if (resText?.key?.id) botSentMessageIds.add(resText.key.id);
            results.push({ type: 'text', resText });
        }

        // 2. Imagen de cono de trayectoria
        if (trayPath && fs.existsSync(trayPath)) {
            const bufferTray = fs.readFileSync(trayPath);
            const resTray = await waSock.sendMessage(jid, {
                image: bufferTray,
                caption: '🗺️ Cono de Pronóstico de Trayectoria'
            });
            if (resTray?.key?.id) botSentMessageIds.add(resTray.key.id);
            results.push({ type: 'tray', resTray });
        }

        // 3. Archivo Word (.docx) oficial
        if (docxPath && fs.existsSync(docxPath)) {
            const bufferDoc = fs.readFileSync(docxPath);
            const fname = path.basename(docxPath);
            const resDoc = await waSock.sendMessage(jid, {
                document: bufferDoc,
                mimetype: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                fileName: fname,
                caption: `📄 Reporte Oficial Word: ${fname}`
            });
            if (resDoc?.key?.id) botSentMessageIds.add(resDoc.key.id);
            results.push({ type: 'doc', resDoc });
        }

        res.json({ ok: true, recipient: jid, results });
    } catch (err) {
        console.error('Error in sendCycloneNotice:', err);
        res.status(500).json({ ok: false, error: err.toString() });
    }
});

async function fetchLocalChartPng(pathWithQuery, timeoutMs = 30000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const res = await fetch(`http://127.0.0.1:${FLASK_PORT}${pathWithQuery}`, { signal: controller.signal });
        if (!res.ok) {
            const errTxt = await res.text().catch(() => '');
            return { ok: false, status: res.status, error: errTxt };
        }
        const arrBuf = await res.arrayBuffer();
        return { ok: true, buffer: Buffer.from(arrBuf), caption: res.headers.get('x-chart-caption') || '' };
    } catch (e) {
        return { ok: false, status: 0, error: e.message };
    } finally {
        clearTimeout(timer);
    }
}

// Función central para procesar el menú de Centinela directamente en Node.js
async function procesarMensajeCentinela(fromJid, text, msgId, msgKey = null) {
    if (!fromJid || !text) return;
    const rawBody = text.trim();
    // Limpiar prefijos de grupo (#, !, "centinela ") para que funcionen comandos en grupo y privado
    const body = rawBody.replace(/^(?:[#!]+\s*|centinela\b[\s,:]*)/i, '').trim() || rawBody;
    const cleanCmd = body.toLowerCase();

    // Anti-duplicados (10 segundos)
    const now = Date.now();
    if (msgId && mensajesProcesados.has(msgId) && (now - mensajesProcesados.get(msgId)) < 10000) {
        return;
    }
    if (msgId) mensajesProcesados.set(msgId, now);

    async function setTyping(active) {
        try {
            if (waSock) await waSock.sendPresenceUpdate(active ? 'composing' : 'paused', fromJid);
        } catch (e) {}
    }

    async function reactMsg(emoji) {
        try {
            if (waSock && msgKey) {
                await waSock.sendMessage(fromJid, { react: { text: emoji, key: msgKey } });
            }
        } catch (e) {}
    }

    async function notifyProcessing(label) {
        await reactMsg('⏳');
        await setTyping(true);
        await sendMsg(`⏳ *${label}*\n_Un momento por favor..._`);
    }

    async function finishProcessing(ok = true) {
        await setTyping(false);
        await reactMsg(ok ? '✅' : '⚠️');
    }

    async function sendMsg(t) {
        try {
            const res = await waSock.sendMessage(fromJid, { text: t });
            if (res?.key?.id) botSentMessageIds.add(res.key.id);
        } catch (e) {
            console.error('[CENTINELA] Error enviando mensaje:', e.message);
        }
    }

    async function sendImg(buf, caption, fname) {
        try {
            const res = await waSock.sendMessage(fromJid, {
                image: buf,
                caption: caption,
                fileName: fname || 'imagen.png'
            });
            if (res?.key?.id) botSentMessageIds.add(res.key.id);
        } catch (e) {
            console.error('[CENTINELA] Error enviando imagen:', e.message);
        }
    }

    // Comando volver
    if (['volver', 'menu', 'menú', 'inicio', 'salir', '0'].includes(cleanCmd)) {
        usuariosEnModoIA.delete(fromJid);
        clearUserHistory(fromJid);
        await sendMsg("✅ *Has vuelto al menú principal*\n\n" + getMenuText());
        return;
    }

    // Modo IA (Google Gemini con telemetría en vivo de la red CFE)
    if (usuariosEnModoIA.has(fromJid)) {
        await notifyProcessing("Consultando telemetría CFE y analizando con Gemini IA...");
        try {
            const ans = await askCentinelaAI(fromJid, body);
            await sendMsg(`🤖 *Centinela IA · Red CFE:*\n\n${ans}\n\n💡 _Escribe otra pregunta o 'volver' para regresar al menú._`);
            await finishProcessing(true);
        } catch (e) {
            console.error('[CENTINELA-AI] Error:', e.message);
            await sendMsg("❌ Ocurrió un error al procesar tu consulta con IA. Escribe 'volver' para regresar al menú.");
            await finishProcessing(false);
        }
        return;
    }

    // Opciones del Menú
    if (body === '1') {
        await notifyProcessing("Obteniendo Reporte de Unidades...");
        const buf = await downloadAzureBlob(AZURE_CONTAINER, '9c8a7f42-3d91-4e01-a3fa-0d2e5b1c6f7d.png');
        if (buf) await sendImg(buf, '📊 *Reporte de Unidades actualizado.*', 'reporte_unidades.png');
        else await sendMsg('⚠️ No se pudo obtener el Reporte de Unidades en este momento.');
        await finishProcessing(!!buf);
    } else if (body === '2') {
        await notifyProcessing("Obteniendo captura de Power Monitoring...");
        const buf = await downloadAzureBlob(AZURE_CONTAINER, '6f3b2c91-91df-41b6-9a1e-c3f0d0c8e24a.png');
        if (buf) await sendImg(buf, '📊 *Captura del Power Monitoring.*', 'power_monitoring.png');
        else await sendMsg('⚠️ No se pudo obtener la captura de Power Monitoring.');
        await finishProcessing(!!buf);
    } else if (body === '3') {
        await notifyProcessing("Obteniendo Gráfica de Potencia...");
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'b7e1f9c3-8a2d-4f5d-9c3a-7f1f6e7a2c01.png');
        if (buf) await sendImg(buf, '📊 *Gráfica de potencia.*', 'grafica_potencia.png');
        else await sendMsg('⚠️ No se pudo obtener la gráfica de potencia.');
        await finishProcessing(!!buf);
    } else if (body === '4') {
        await notifyProcessing("Obteniendo Condición de Embalses...");
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'e1a5f734-9c2e-4b3b-8d5a-6f7e1d2c9b8f.png');
        if (buf) await sendImg(buf, '📊 *Condición de embalses.*', 'condicion_embalses.png');
        else await sendMsg('⚠️ No se pudo obtener la condición de embalses.');
        await finishProcessing(!!buf);
    } else if (body === '5') {
        await notifyProcessing("Obteniendo Aportaciones por Cuenca Propia...");
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'd42f3e19-b89c-4f02-90d4-3e7f4a6d2c01.png');
        if (buf) await sendImg(buf, '📊 *Aportaciones por cuenca propia.*', 'aportaciones_cuenca.png');
        else await sendMsg('⚠️ No se pudo obtener las aportaciones por cuenca.');
        await finishProcessing(!!buf);
    } else if (body === '11') {
        await notifyProcessing("Obteniendo Reporte de Lluvias 24h...");
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'reporte_lluvia_1_1_638848218556433423.png');
        if (buf) await sendImg(buf, '📊 *CFE SPH Grijalva - Reporte de lluvias 24 horas de 6am a 6am.*', 'lluvias_24h.png');
        else await sendMsg('⚠️ No se pudo obtener el reporte de lluvias 24 horas.');
        await finishProcessing(!!buf);
    } else if (body === '12') {
        await notifyProcessing("Obteniendo Reporte de Lluvias Parcial...");
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'reporte_lluvia_1_2_638848218556433423.png');
        if (buf) await sendImg(buf, '📊 *CFE SPH Grijalva - Reporte de lluvias parcial de 6am a hora actual.*', 'lluvias_parcial.png');
        else await sendMsg('⚠️ No se pudo obtener el reporte de lluvias parcial.');
        await finishProcessing(!!buf);
    } else if (cleanCmd.startsWith('grafica ') || cleanCmd.startsWith('gráfica ') || cleanCmd.startsWith('hidrograma ')) {
        const presaQ = body.replace(/^(?:grafica|gráfica|hidrograma)\s+/i, '').trim();
        await notifyProcessing(`Generando hidrograma 72h de ${presaQ}...`);
        try {
            const chart = await fetchLocalChartPng(`/api/charts/hidrograma.png?presa=${encodeURIComponent(presaQ)}`);
            if (chart.ok && chart.buffer) {
                await sendImg(chart.buffer, chart.caption || `📈 *Hidrograma 72h — ${presaQ}*`, `hidrograma_${presaQ.replace(/\W+/g, '_')}.png`);
                await finishProcessing(true);
            } else {
                let msgErr = `⚠️ No se encontró la presa *${presaQ}*. Prueba con: Angostura, Chicoasén, Malpaso, Peñitas, Infiernillo, Temascal, Aguamilpa o Santa Rosa.`;
                try {
                    const j = JSON.parse(chart.error || '{}');
                    if (j.error) msgErr = `⚠️ ${j.error}`;
                } catch (e) {}
                await sendMsg(msgErr);
                await finishProcessing(false);
            }
        } catch (e) {
            await sendMsg('⚠️ No fue posible generar el hidrograma en este momento.');
            await finishProcessing(false);
        }
    } else if (body === '13' || cleanCmd.startsWith('13 ') || cleanCmd.includes('cotas') || cleanCmd.includes('namo') || cleanCmd.includes('reporte embalse') || cleanCmd.includes('embalses ') || cleanCmd === 'embalses' || cleanCmd.includes('hidrocloud')) {
        await notifyProcessing("Generando tablero gráfico y niveles de embalses en tiempo real...");
        try {
            const targetCrp = detectCrpFromText(cleanCmd);
            const qs = targetCrp ? `?crp=${encodeURIComponent(targetCrp)}&delta_hours=3` : '?delta_hours=3';
            const [chart, rep] = await Promise.all([
                fetchLocalChartPng(`/api/charts/embalses.png${qs}`),
                generateEmbalsesReport(targetCrp, 3).catch(() => null)
            ]);
            if (chart.ok && chart.buffer) {
                await sendImg(chart.buffer, `🌊 *Tablero Operativo de Embalses · ${targetCrp || 'NACIONAL'}*\n📅 ${rep?.fechaCorte || ''} h · _Detalle completo abajo ⬇️_`, `embalses_${targetCrp || 'nacional'}.png`);
                if (rep && rep.mensajeTexto) {
                    await sendMsg(rep.mensajeTexto);
                }
                await finishProcessing(true);
                return;
            }
            if (rep && rep.mensajeTexto) {
                await sendMsg(rep.mensajeTexto);
                await finishProcessing(true);
                return;
            }
            await finishProcessing(false);
        } catch (e) {
            console.error('[CENTINELA] Error generando reporte de embalses opción 13:', e.message);
            await sendMsg('⚠️ No fue posible consultar el reporte de embalses en este momento.');
            await finishProcessing(false);
        }
    } else if (body === '14' || cleanCmd.startsWith('14 ') || cleanCmd === 'alertas' || cleanCmd.startsWith('alertas ') || cleanCmd.includes('alerta') || cleanCmd.includes('avenidas')) {
        await notifyProcessing("Evaluando alertas hidráulicas e hidrométricas activas...");
        try {
            const targetCrp = detectCrpFromText(cleanCmd);
            const rep = await getAlertsReport({ crp: targetCrp });
            if (rep && rep.mensajeTexto) {
                await sendMsg(rep.mensajeTexto);
                await finishProcessing(true);
                return;
            }
            await finishProcessing(false);
        } catch (e) {
            console.error('[CENTINELA] Error consultando alertas opción 14:', e.message);
            await sendMsg('⚠️ No fue posible consultar las alertas hidráulicas en este momento.');
            await finishProcessing(false);
        }
    } else if (body === '15' || cleanCmd.startsWith('15 ') || cleanCmd.includes('boletin') || cleanCmd.includes('boletín')) {
        await notifyProcessing("Generando infografía del Boletín Hidrológico Diario Integral...");
        try {
            const targetCrp = detectCrpFromText(cleanCmd);
            const qs = targetCrp ? `?crp=${encodeURIComponent(targetCrp)}&cutoff_hour=6` : '?cutoff_hour=6';
            const [chart, rep] = await Promise.all([
                fetchLocalChartPng(`/api/charts/boletin.png${qs}`),
                getDailyBulletinReport({ crp: targetCrp, cutoffHour: 6 }).catch(() => null)
            ]);
            if (chart.ok && chart.buffer) {
                await sendImg(chart.buffer, '📋 *Boletín Hidrológico Diario Integral · CFE*\n_Detalle completo abajo ⬇️_', 'boletin_hidrologico_cfe.png');
                if (rep && rep.mensajeTexto) {
                    await sendMsg(rep.mensajeTexto);
                }
                await finishProcessing(true);
                return;
            }
            if (rep && rep.mensajeTexto) {
                await sendMsg(rep.mensajeTexto);
                await finishProcessing(true);
                return;
            }
            await finishProcessing(false);
        } catch (e) {
            console.error('[CENTINELA] Error generando boletín diario opción 15:', e.message);
            await sendMsg('⚠️ No fue posible generar el boletín hidrológico diario en este momento.');
            await finishProcessing(false);
        }
    } else if (body === '16' || cleanCmd.startsWith('16 ') || cleanCmd.includes('lluvia cuenca') || cleanCmd.includes('lluvias cuenca') || cleanCmd.includes('corte lluvia')) {
        await notifyProcessing("Generando gráfica de corte de lluvia 24h por cuenca (GOES)...");
        try {
            const [chart, rep] = await Promise.all([
                fetchLocalChartPng('/api/charts/lluvias.png?cutoff_hour=6'),
                getRainfallByBasinReport({ cutoffHour: 6 }).catch(() => null)
            ]);
            if (chart.ok && chart.buffer) {
                await sendImg(chart.buffer, '🌧️ *Corte de Lluvia 24h por Cuenca · Red GOES CFE*\n_Detalle completo abajo ⬇️_', 'lluvia_cuencas_24h.png');
                if (rep && rep.mensajeTexto) {
                    await sendMsg(rep.mensajeTexto);
                }
                await finishProcessing(true);
                return;
            }
            if (rep && rep.mensajeTexto) {
                await sendMsg(rep.mensajeTexto);
                await finishProcessing(true);
                return;
            }
            await finishProcessing(false);
        } catch (e) {
            console.error('[CENTINELA] Error generando corte de lluvia por cuenca opción 16:', e.message);
            await sendMsg('⚠️ No fue posible consultar el corte de lluvia por cuenca en este momento.');
            await finishProcessing(false);
        }
    } else if (body === '17' || cleanCmd.startsWith('17 ') || cleanCmd.includes('salud red') || cleanCmd.includes('red hidrometrica') || cleanCmd.includes('red hidrométrica') || cleanCmd.includes('estaciones caidas') || cleanCmd.includes('estaciones caídas') || cleanCmd === 'qa') {
        await notifyProcessing("Generando tablero QA/QC de salud de la red hidrométrica...");
        try {
            const targetCrp = detectCrpFromText(cleanCmd);
            const qs = targetCrp ? `?crp=${encodeURIComponent(targetCrp)}` : '';
            const [chart, rep] = await Promise.all([
                fetchLocalChartPng(`/api/charts/salud-red.png${qs}`),
                getNetworkHealthReport({ onlyIssues: true, crp: targetCrp }).catch(() => null)
            ]);
            if (chart.ok && chart.buffer) {
                await sendImg(chart.buffer, '📡 *Salud de la Red Hidrométrica · QA/QC CFE*\n_Detalle completo abajo ⬇️_', 'salud_red_cfe.png');
                if (rep && rep.mensajeTexto) {
                    await sendMsg(rep.mensajeTexto);
                }
                await finishProcessing(true);
                return;
            }
            if (rep && rep.mensajeTexto) {
                await sendMsg(rep.mensajeTexto);
                await finishProcessing(true);
                return;
            }
            await finishProcessing(false);
        } catch (e) {
            console.error('[CENTINELA] Error consultando salud de red opción 17:', e.message);
            await sendMsg('⚠️ No fue posible consultar la salud de la red hidrométrica en este momento.');
            await finishProcessing(false);
        }
    } else if (body === '7') {
        await notifyProcessing("Consultando Reporte de Disponibilidad...");
        const buf = await downloadAzureBlob('reporte-unidades', 'telegram_report.txt');
        if (buf) await sendMsg(`📊 *Reporte de Disponibilidad*\n\n${buf.toString('utf8')}`);
        else await sendMsg('⚠️ No se pudo obtener el reporte de disponibilidad.');
        await finishProcessing(!!buf);
    } else if (['8', '08', 'ciclon', 'ciclón', 'ciclones', 'huracan', 'huracán', 'tormenta'].includes(cleanCmd)) {
        await notifyProcessing("Consultando avisos de ciclones tropicales (SMN / CONAGUA)...");
        try {
            const flaskPort = process.env.PORT || 8080;
            const resCyclones = await fetch(`http://127.0.0.1:${flaskPort}/api/cyclones`).then(r => r.json()).catch(() => null);
            if (resCyclones && Array.isArray(resCyclones) && resCyclones.length > 0) {
                let msgText = `🌀 *AVISOS DE CICLÓN TROPICAL ACTIVOS (${resCyclones.length}) — CFE / SMN*\n\n`;
                for (const c of resCyclones) {
                    const cond = c.condiciones || {};
                    msgText += `📍 *${(c.sistema || 'Ciclón Tropical').toUpperCase()}* (${c.cuenca || 'Océano'})\n`;
                    if (c.titular) msgText += `_${c.titular}_\n\n`;
                    msgText += `📊 *Condiciones:*\n`;
                    msgText += `• *Distancia:* ${cond.distancia_costa || '--'}\n`;
                    msgText += `• *Desplazamiento:* ${cond.desplazamiento || '--'}\n`;
                    msgText += `• *Vientos:* ${cond.vientos_sostenidos || '--'} km/h (Rachas: ${cond.vientos_rachas || '--'} km/h)\n`;
                    msgText += `• *Lluvias:* ${cond.pronostico_lluvia || 'Sin efectos directos'}\n\n`;
                    if (c.proximo_aviso) msgText += `🔔 _${c.proximo_aviso}_\n`;
                }
                await sendMsg(msgText);
            } else {
                await sendMsg(
                    "☀️ *MONITOREO DE CICLONES TROPICALES — CFE / SMN*\n\n" +
                    "✅ *Sin ciclones activos en este momento.*\n\n" +
                    "Actualmente no se registran sistemas ciclónicos en el Océano Pacífico ni en el Océano Atlántico / Golfo de México.\n\n" +
                    "📡 _Monitoreo satelital continuo las 24 horas._"
                );
            }
            await finishProcessing(true);
        } catch (e) {
            await sendMsg(
                "☀️ *MONITOREO DE CICLONES TROPICALES — CFE / SMN*\n\n" +
                "✅ *Sin ciclones activos en este momento.*"
            );
            await finishProcessing(true);
        }
    } else if (body === '6' || cleanCmd.startsWith('6 ') || cleanCmd.startsWith('ia ')) {
        if (cleanCmd.startsWith('6 ') || cleanCmd.startsWith('ia ')) {
            const directQ = body.replace(/^(?:6|ia)\s+/i, '').trim();
            await notifyProcessing("Consultando telemetría CFE y analizando con Gemini IA...");
            const ans = await askCentinelaAI(fromJid, directQ);
            await sendMsg(`🤖 *Centinela IA · Red CFE:*\n\n${ans}`);
            await finishProcessing(true);
            return;
        }
        usuariosEnModoIA.add(fromJid);
        await sendMsg(
            "🤖 *¡Hola! Ahora estás hablando con Centinela IA (Gemini) conectado en vivo a la red CFE.*\n\n" +
            "Puedo responder con datos reales del momento sobre:\n" +
            "• 🌊 Niveles, Δ 3h, NAMO, volúmenes (Mm³) y % de llenado de las 27 presas\n" +
            "• 🚨 Alertas activas, cascadas hidráulicas y ETA al NAMO\n" +
            "• 🌧️ Lluvias 24h en las 22 cuencas GOES\n" +
            "• 📡 Salud de la red, estaciones fuera de línea y sensores\n\n" +
            "💬 *Escribe tu pregunta*\n\n" +
            "💡 _Escribe 'volver' para regresar al menú principal._"
        );
    } else {
        // 1. Ignorar respuestas pasivas o confirmaciones (evita disparar el menú por cortesía)
        const isPassiveReply = ['ok', 'recibido', 'enterado', 'gracias', 'muchas gracias', 'de acuerdo', '👍', '👌', 'entendido', 'saludos', 'listo', 'bueno'].includes(cleanCmd);
        if (isPassiveReply) {
            return;
        }

        // 2. Solo enviar el menú si es una petición explícita de menú o saludo formal exacto en chat privado
        const isExplicitMenuCommand = ['menu', 'menú', 'ayuda', 'help', 'inicio', 'centinela', 'hola', '0'].includes(cleanCmd) 
                                   || cleanCmd.startsWith('#') 
                                   || cleanCmd.startsWith('!');

        if (isExplicitMenuCommand && !fromJid.endsWith('@g.us')) {
            await sendMsg(getMenuText());
        }
    }
}

let lastErrorMsg = null;

app.get('/status', (req, res) => {
    res.json({
        status: clientStatus,
        isReady: clientStatus === 'READY',
        hasQr: !!latestQrDataUrl,
        lastError: lastErrorMsg,
        webhookUrl: webhookUrl,
        port: PORT
    });
});

// ==========================================
// DESPACHO AUTOMÁTICO DE EMBALSES (CADA 3 HORAS AL MINUTO 59 CST)
// ==========================================
const GRUPO_AVISOS_CICLONES_RAW = process.env.EMBALSES_GRUPO_JID || '120363412138898868@g.us';
const ALERTS_GRUPO_RAW = process.env.ALERTS_GRUPO_JID || GRUPO_AVISOS_CICLONES_RAW;
const EMBALSES_AUTO_CRP = (process.env.EMBALSES_AUTO_CRP || 'ALL').trim().toUpperCase();
const EMBALSES_INTERVAL_HOURS = Math.max(1, parseInt(process.env.EMBALSES_INTERVAL_HOURS || '3', 10));
const EMBALSES_DISPATCH_MINUTE = Math.min(59, Math.max(0, parseInt(process.env.EMBALSES_DISPATCH_MINUTE || '59', 10)));
const BOLETIN_DIARIO_AUTO = (process.env.BOLETIN_DIARIO_AUTO || 'false').toLowerCase() === 'true';
const ALERTS_AUTO_ENABLED = (process.env.ALERTS_AUTO_ENABLED || 'false').toLowerCase() === 'true';
const ALERTS_POLL_MINUTES = Math.max(2, parseInt(process.env.ALERTS_POLL_MINUTES || '10', 10));
const ALERTS_COOLDOWN_CRITICA_MS = Math.max(15, parseInt(process.env.ALERTS_COOLDOWN_CRITICA_MIN || '120', 10)) * 60 * 1000;
const ALERTS_COOLDOWN_ALERTA_MS = Math.max(30, parseInt(process.env.ALERTS_COOLDOWN_ALERTA_MIN || '360', 10)) * 60 * 1000;
const ALERTS_AUTO_CATEGORIES = (process.env.ALERTS_AUTO_CATEGORIES || 'EMBALSES,AVENIDAS,RED_HIDROMETRICA').trim();
const ALERTS_STATE_FILE = path.join(__dirname, 'alerts_dispatch_state.json');

let temporizadorEmbalses = null;
let temporizadorAlertas = null;
let lastDispatched3hSlot = null;
let lastDispatchedDailyDate = null;

function loadAlertsState() {
    try {
        if (fs.existsSync(ALERTS_STATE_FILE)) {
            return JSON.parse(fs.readFileSync(ALERTS_STATE_FILE, 'utf8')) || {};
        }
    } catch (e) {}
    return {};
}

function saveAlertsState(state) {
    try {
        fs.writeFileSync(ALERTS_STATE_FILE, JSON.stringify(state, null, 2), 'utf8');
    } catch (e) {
        console.warn('[CRON-ALERTAS] No se pudo guardar estado de alertas:', e.message);
    }
}

function getEmbalsesRecipients(rawOverride = null) {
    return (rawOverride || GRUPO_AVISOS_CICLONES_RAW).split(',').map(x => x.trim()).filter(Boolean);
}

async function enviarTextoAGruposEmbalses(texto, tag = 'CRON-EMBALSES', rawRecipients = null) {
    if (!waSock || clientStatus !== 'READY') {
        console.log(`[${tag}] WhatsApp no está listo, se omitirá este envío.`);
        return { ok: false, sent: 0, reason: 'WHATSAPP_NOT_READY' };
    }
    const recipients = getEmbalsesRecipients(rawRecipients);
    if (!recipients.length) {
        return { ok: false, sent: 0, reason: 'NO_RECIPIENTS' };
    }
    const prefix = texto.slice(0, 40);
    recentSentTexts.add(prefix);
    setTimeout(() => recentSentTexts.delete(prefix), 60000);

    let sent = 0;
    for (const jid of recipients) {
        try {
            const result = await waSock.sendMessage(jid, { text: texto });
            if (result?.key?.id) {
                botSentMessageIds.add(result.key.id);
            }
            sent += 1;
            console.log(`✅ [${tag}] Enviado con éxito a ${jid} (${new Date().toLocaleTimeString('es-MX')})`);
        } catch (err) {
            console.error(`❌ [${tag}] Error enviando a ${jid}:`, err.message);
        }
    }
    return { ok: sent > 0, sent, recipients };
}

async function enviarImagenAGruposEmbalses(buffer, caption, filename, tag = 'CRON-EMBALSES', rawRecipients = null) {
    if (!waSock || clientStatus !== 'READY') {
        console.log(`[${tag}] WhatsApp no está listo, se omitirá este envío.`);
        return { ok: false, sent: 0, reason: 'WHATSAPP_NOT_READY' };
    }
    const recipients = getEmbalsesRecipients(rawRecipients);
    if (!recipients.length) {
        return { ok: false, sent: 0, reason: 'NO_RECIPIENTS' };
    }
    if (caption) {
        const prefix = caption.slice(0, 40);
        recentSentTexts.add(prefix);
        setTimeout(() => recentSentTexts.delete(prefix), 60000);
    }
    let sent = 0;
    for (const jid of recipients) {
        try {
            const result = await waSock.sendMessage(jid, {
                image: buffer,
                caption: caption || '',
                fileName: filename || 'reporte_cfe.png'
            });
            if (result?.key?.id) botSentMessageIds.add(result.key.id);
            sent += 1;
            console.log(`✅ [${tag}] Imagen enviada con éxito a ${jid} (${new Date().toLocaleTimeString('es-MX')})`);
        } catch (err) {
            console.error(`❌ [${tag}] Error enviando imagen a ${jid}:`, err.message);
        }
    }
    return { ok: sent > 0, sent, recipients };
}

async function despacharReporteAutomaticoEmbalses(forceCrp = null) {
    try {
        const crpUse = forceCrp !== null ? forceCrp : (EMBALSES_AUTO_CRP === 'ALL' ? null : EMBALSES_AUTO_CRP);
        console.log(`[CRON-EMBALSES] Generando reporte automático 3h (CRP: ${crpUse || 'NACIONAL'})...`);
        const qs = crpUse ? `?crp=${encodeURIComponent(crpUse)}&delta_hours=3` : '?delta_hours=3';
        const [chart, rep] = await Promise.all([
            fetchLocalChartPng(`/api/charts/embalses.png${qs}`),
            buildScheduled3hMessage(crpUse)
        ]);
        if (chart.ok && chart.buffer) {
            const imgRes = await enviarImagenAGruposEmbalses(
                chart.buffer,
                `🌊 *Reporte Operativo de Embalses · ${crpUse || 'NACIONAL'}*\n📅 ${rep?.fechaCorte || ''} h`,
                `embalses_${crpUse || 'nacional'}.png`,
                'CRON-EMBALSES-3H'
            );
            if (rep && rep.mensajeTexto) {
                await enviarTextoAGruposEmbalses(rep.mensajeTexto, 'CRON-EMBALSES-3H-TXT');
            }
            return imgRes;
        }
        if (rep && rep.mensajeTexto) {
            return await enviarTextoAGruposEmbalses(rep.mensajeTexto, 'CRON-EMBALSES-3H');
        }
        return { ok: false, sent: 0, reason: 'EMPTY_REPORT' };
    } catch (err) {
        console.error('❌ [CRON-EMBALSES] Error generando/enviando reporte automático:', err.message);
        return { ok: false, sent: 0, error: err.message };
    }
}

async function despacharBoletinDiarioAutomatico() {
    try {
        const crpUse = EMBALSES_AUTO_CRP === 'ALL' ? null : EMBALSES_AUTO_CRP;
        console.log(`[CRON-BOLETIN] Generando Boletín Hidrológico Diario 06:30 h (CRP: ${crpUse || 'NACIONAL'})...`);
        const qs = crpUse ? `?crp=${encodeURIComponent(crpUse)}&cutoff_hour=6` : '?cutoff_hour=6';
        const [chart, rep] = await Promise.all([
            fetchLocalChartPng(`/api/charts/boletin.png${qs}`),
            getDailyBulletinReport({ crp: crpUse, cutoffHour: 6 })
        ]);
        if (chart.ok && chart.buffer) {
            const imgRes = await enviarImagenAGruposEmbalses(
                chart.buffer,
                '📋 *Boletín Hidrológico Diario Integral · CFE*',
                'boletin_hidrologico_cfe.png',
                'CRON-BOLETIN-DIARIO'
            );
            if (rep && rep.mensajeTexto) {
                await enviarTextoAGruposEmbalses(rep.mensajeTexto, 'CRON-BOLETIN-DIARIO-TXT');
            }
            return imgRes;
        }
        if (rep && rep.mensajeTexto) {
            return await enviarTextoAGruposEmbalses(rep.mensajeTexto, 'CRON-BOLETIN-DIARIO');
        }
        return { ok: false, sent: 0, reason: 'EMPTY_BULLETIN' };
    } catch (err) {
        console.error('❌ [CRON-BOLETIN] Error enviando boletín diario:', err.message);
        return { ok: false, sent: 0, error: err.message };
    }
}

function tickCronHidrometrico() {
    if (!waSock || clientStatus !== 'READY') return;
    // Hora local Centro México (CST, UTC-6)
    const cst = new Date(Date.now() - 6 * 3600 * 1000);
    const y = cst.getUTCFullYear();
    const m = String(cst.getUTCMonth() + 1).padStart(2, '0');
    const d = String(cst.getUTCDate()).padStart(2, '0');
    const hh = cst.getUTCHours();
    const mm = cst.getUTCMinutes();
    const dateKey = `${y}-${m}-${d}`;

    // 1) Reporte de embalses cada 3 horas al minuto 59 CST (00:59, 03:59, 06:59, 09:59, 12:59, 15:59, 18:59, 21:59)
    if (hh % EMBALSES_INTERVAL_HOURS === 0 && mm === EMBALSES_DISPATCH_MINUTE) {
        const slotKey = `${dateKey}_${String(hh).padStart(2, '0')}_${String(mm).padStart(2, '0')}`;
        if (lastDispatched3hSlot !== slotKey) {
            lastDispatched3hSlot = slotKey;
            despacharReporteAutomaticoEmbalses();
        }
    }

    // 2) Boletín Hidrológico Diario (desactivado por defecto; solo si BOLETIN_DIARIO_AUTO=true)
    if (BOLETIN_DIARIO_AUTO && hh === 6 && mm >= 30 && mm <= 34) {
        if (lastDispatchedDailyDate !== dateKey) {
            lastDispatchedDailyDate = dateKey;
            despacharBoletinDiarioAutomatico();
        }
    }
}

async function despacharAlertasAutomaticas({ force = false, crp = null } = {}) {
    try {
        const crpUse = crp !== null ? crp : (EMBALSES_AUTO_CRP === 'ALL' ? null : EMBALSES_AUTO_CRP);
        const resAl = await getAlertsReport({
            severity: 'CRITICA,ALERTA',
            category: ALERTS_AUTO_CATEGORIES,
            crp: crpUse
        });
        const activeAlerts = resAl.alertas || [];
        const prevState = loadAlertsState();
        const nextState = {};
        const nowMs = Date.now();
        const toNotify = [];

        for (const a of activeAlerts) {
            const key = `${a.codigo}::${a.id_asignado || a.estacion || 'GEN'}`;
            const prev = prevState[key];
            const cooldownMs = a.severidad === 'CRITICA' ? ALERTS_COOLDOWN_CRITICA_MS : ALERTS_COOLDOWN_ALERTA_MS;
            const escalated = prev && prev.severidad !== 'CRITICA' && a.severidad === 'CRITICA';
            const shouldSend = force || !prev || escalated || ((nowMs - (prev.lastSentMs || 0)) >= cooldownMs);

            if (shouldSend) {
                toNotify.push({ ...a, _isNew: !prev, _escalated: !!escalated });
                nextState[key] = { severidad: a.severidad, lastSentMs: nowMs };
            } else {
                nextState[key] = prev;
            }
        }

        // Guardar estado limpio (las alertas resueltas salen automáticamente de nextState)
        saveAlertsState(nextState);

        if (!toNotify.length) {
            return { ok: true, sent: 0, active: activeAlerts.length, notified: 0, reason: 'NO_NEW_OR_DUE_ALERTS' };
        }

        const cst = new Date(nowMs - 6 * 3600 * 1000);
        const fecha = `${String(cst.getUTCDate()).padStart(2, '0')}/${String(cst.getUTCMonth() + 1).padStart(2, '0')}/${cst.getUTCFullYear()}`;
        const hora = `${String(cst.getUTCHours()).padStart(2, '0')}:${String(cst.getUTCMinutes()).padStart(2, '0')}`;
        const nCrit = toNotify.filter(x => x.severidad === 'CRITICA').length;
        const nAl = toNotify.filter(x => x.severidad === 'ALERTA').length;

        const header = nCrit > 0
            ? '🚨 *ALERTA CRÍTICA AUTOMÁTICA · CFE*'
            : '⚠️ *ALERTA PREVENTIVA AUTOMÁTICA · CFE*';
        const lines = [
            header,
            `📅 ${fecha}   ⏰ ${hora} h`,
            `🔴 Críticas: ${nCrit}  |  🟡 Alertas: ${nAl}`,
        ];

        for (const [catKey, catLabel] of [
            ['EMBALSES', 'EMBALSES Y PRESAS'],
            ['AVENIDAS', 'AVENIDAS Y LLUVIA INTENSA'],
            ['RED_HIDROMETRICA', 'RED HIDROMÉTRICA (QA/QC)']
        ]) {
            const grp = toNotify.filter(x => x.categoria === catKey);
            if (!grp.length) continue;
            lines.push('');
            lines.push(`═══ ${catLabel} (${grp.length}) ═══`);
            for (const a of grp) {
                const ico = a.severidad === 'CRITICA' ? '🔴' : '🟡';
                const tag = a.clave_cenace ? ` [${a.clave_cenace}]` : '';
                const badge = a._escalated ? ' *(ESCALÓ A CRÍTICA)*' : (a._isNew ? ' *(NUEVA)*' : '');
                lines.push(`${ico} [${a.crp}] ${a.titulo}${tag}${badge}`);
                lines.push(`   ${a.detalle}`);
            }
        }

        const sendRes = await enviarTextoAGruposEmbalses(lines.join('\n').trim(), 'CRON-ALERTAS', ALERTS_GRUPO_RAW);
        return { ...sendRes, active: activeAlerts.length, notified: toNotify.length };
    } catch (err) {
        console.error('❌ [CRON-ALERTAS] Error evaluando/enviando alertas automáticas:', err.message);
        return { ok: false, sent: 0, error: err.message };
    }
}

function iniciarCronEmbalses() {
    if (temporizadorEmbalses) clearInterval(temporizadorEmbalses);
    // Revisión cada 20s para garantizar disparo exacto al minuto 59 sin perder ventana por jitter
    temporizadorEmbalses = setInterval(tickCronHidrometrico, 20 * 1000);
    const minPad = String(EMBALSES_DISPATCH_MINUTE).padStart(2, '0');
    console.log(`⏰ [CRON-EMBALSES] Programador activo: cada ${EMBALSES_INTERVAL_HOURS}h al minuto :${minPad} CST (00:${minPad}, 03:${minPad}, 06:${minPad}, 09:${minPad}...) | CRP=${EMBALSES_AUTO_CRP} | BoletínAuto=${BOLETIN_DIARIO_AUTO} | AlertasAuto=${ALERTS_AUTO_ENABLED} -> ${GRUPO_AVISOS_CICLONES_RAW}`);

    if (temporizadorAlertas) clearInterval(temporizadorAlertas);
    if (ALERTS_AUTO_ENABLED) {
        setTimeout(() => despacharAlertasAutomaticas({ force: false }), 45 * 1000);
        temporizadorAlertas = setInterval(() => despacharAlertasAutomaticas({ force: false }), ALERTS_POLL_MINUTES * 60 * 1000);
        console.log(`🚨 [CRON-ALERTAS] Monitor automático activo cada ${ALERTS_POLL_MINUTES} min (🔴/🟡) -> ${ALERTS_GRUPO_RAW}`);
    } else {
        console.log('🔕 [CRON-ALERTAS] Envío automático de alertas desactivado (disponible bajo demanda en opción 14).');
    }
}

// ==========================================
// PUENTE FUNVASOS: RECEPCIÓN DE EXCELS DESDE WHATSAPP -> CFE MIRROR API -> SERVIDOR CFE
// ==========================================
const FUNVASOS_BRIDGE_ENABLED = (process.env.FUNVASOS_BRIDGE_ENABLED || 'true').toLowerCase() !== 'false';
const FUNVASOS_GROUP_JIDS = (process.env.FUNVASOS_GROUP_JID || GRUPO_AVISOS_CICLONES_RAW)
    .split(',').map(x => x.trim()).filter(Boolean);
const FUNVASOS_GROUP_NAMES = (process.env.FUNVASOS_GROUP_NAME || 'Seguimiento Datos de Hidrometria')
    .split(',').map(x => x.trim()).filter(Boolean);
const FUNVASOS_ALLOW_DM = (process.env.FUNVASOS_ALLOW_DM || 'true').toLowerCase() !== 'false';
const MIRROR_API_BASE = (process.env.CFE_MIRROR_API_URL || 'https://cfe-mirror-api.onrender.com').replace(/\/+$/, '');
const MIRROR_INGEST_SECRET = (process.env.CFE_MIRROR_INGEST_SECRET || process.env.INGEST_SECRET || '').trim();

const groupSubjectCache = new Map(); // jid -> { subject, expiresAt }

async function getGroupSubjectCached(chatJid) {
    const cached = groupSubjectCache.get(chatJid);
    if (cached && Date.now() < cached.expiresAt) return cached.subject;
    try {
        const meta = await waSock.groupMetadata(chatJid);
        const subject = meta?.subject || '';
        groupSubjectCache.set(chatJid, { subject, expiresAt: Date.now() + 10 * 60 * 1000 });
        return subject;
    } catch (e) {
        return '';
    }
}

async function isAuthorizedFunVasosGroup(chatJid) {
    if (FUNVASOS_GROUP_JIDS.includes(chatJid)) return true;
    const subject = await getGroupSubjectCached(chatJid);
    if (!subject) return false;
    const normSubj = normalizeTextSimple(subject).trim();
    return FUNVASOS_GROUP_NAMES.some(name => {
        const target = normalizeTextSimple(name).trim();
        return target && (normSubj === target || normSubj.includes(target));
    });
}

const FUNVASOS_CRP_MENU = [
    { num: '1', clave: 'GRIJALVA', label: 'Grijalva' },
    { num: '2', clave: 'GOLFO', label: 'Golfo' },
    { num: '3', clave: 'PACIFICO_SUR', label: 'Pacífico Sur (Balsas / Zimapán)' },
    { num: '4', clave: 'OCCIDENTE', label: 'Occidente (Santiago)' },
    { num: '5', clave: 'PACIFICO_NORTE', label: 'Pacífico Norte (Noroeste)' }
];

// Pendientes de confirmación de CRP (Opción 3): key = `${chatJid}::${senderJid}`
const pendingFunVasosConfirm = new Map();

function normalizeTextSimple(str) {
    return String(str || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase();
}

function unwrapMessageContent(rawMsg) {
    let m = rawMsg || {};
    for (let i = 0; i < 4; i++) {
        if (m.ephemeralMessage?.message) m = m.ephemeralMessage.message;
        else if (m.viewOnceMessage?.message) m = m.viewOnceMessage.message;
        else if (m.viewOnceMessageV2?.message) m = m.viewOnceMessageV2.message;
        else if (m.viewOnceMessageV2Extension?.message) m = m.viewOnceMessageV2Extension.message;
        else if (m.documentWithCaptionMessage?.message) m = m.documentWithCaptionMessage.message;
        else break;
    }
    return m;
}

function detectFunVasosCrp(filename, caption) {
    const fnClean = normalizeTextSimple(filename || '');
    // Patrón oficial Grijalva: FINddmmyy.xlsx (ej. FIN251126.xlsx, FIN011126.xlsx)
    if (/^fin\d{6}\.(xlsx|xlsm|xls)$/i.test(fnClean.trim())) {
        return 'GRIJALVA';
    }
    const hay = normalizeTextSimple(`${filename || ''} ${caption || ''}`);
    const rules = [
        { clave: 'GRIJALVA', kw: ['grijalva', 'sureste', 'angostura', 'chicoasen', 'malpaso', 'penitas', 'belisario', 'moreno torres'] },
        { clave: 'GOLFO', kw: ['golfo', 'temascal', 'cerro de oro', 'mazatepec', 'la soledad', 'catemaco', 'chilapan', 'canseco', 'papaloapan', 'apulco'] },
        { clave: 'PACIFICO_SUR', kw: ['pacifico sur', 'pacifico_sur', 'pacsur', 'balsas', 'el caracol', 'caracol', 'infiernillo', 'la villita', 'villita', 'la venta', 'zimapan', 'moctezuma'] },
        { clave: 'OCCIDENTE', kw: ['occidente', 'santiago', 'santa rosa', 'la yesca', 'yesca', 'el cajon', 'cajon', 'aguamilpa'] },
        { clave: 'PACIFICO_NORTE', kw: ['pacifico norte', 'pacifico_norte', 'pacnorte', 'noroeste', 'huites', 'novillo', 'rio fuerte', 'rio yaqui', 'colosio'] }
    ];
    for (const r of rules) {
        if (r.kw.some(k => hay.includes(k))) return r.clave;
    }
    return null;
}

function extractFechaOperativa(filename, caption) {
    const s = `${filename || ''} ${caption || ''}`;
    // Patrón FINddmmyy (ej. FIN011126.xlsx -> 2026-11-01)
    let m = String(filename || '').match(/^fin(\d{2})(\d{2})(\d{2})\.(xlsx|xlsm|xls)$/i);
    if (m) return `20${m[3]}-${m[2]}-${m[1]}`;
    m = s.match(/\b(20\d{2})[-_/](\d{2})[-_/](\d{2})\b/);
    if (m) return `${m[1]}-${m[2]}-${m[3]}`;
    m = s.match(/\b(\d{2})[-_/](\d{2})[-_/](20\d{2})\b/);
    if (m) return `${m[3]}-${m[2]}-${m[1]}`;
    m = s.match(/\b(20\d{2})(\d{2})(\d{2})\b/);
    if (m) return `${m[1]}-${m[2]}-${m[3]}`;
    // Fecha local Centro México (UTC-6)
    const cst = new Date(Date.now() - 6 * 3600 * 1000);
    return `${cst.getUTCFullYear()}-${String(cst.getUTCMonth() + 1).padStart(2, '0')}-${String(cst.getUTCDate()).padStart(2, '0')}`;
}

function getDocumentFromMsg(msg) {
    const m = unwrapMessageContent(msg?.message);
    const doc = m.documentMessage || null;
    if (!doc) return null;
    const filename = doc.fileName || doc.title || '';
    const ext = path.extname(filename).toLowerCase();
    if (!['.xlsx', '.xlsm', '.xls'].includes(ext)) return null;
    const caption = doc.caption || '';
    return { doc, filename, ext, caption };
}

async function replyQuoted(chatJid, text, quotedMsg) {
    if (!waSock) return;
    const prefix = text.slice(0, 40);
    recentSentTexts.add(prefix);
    setTimeout(() => recentSentTexts.delete(prefix), 60000);
    const res = await waSock.sendMessage(chatJid, { text }, quotedMsg ? { quoted: quotedMsg } : {});
    if (res?.key?.id) botSentMessageIds.add(res.key.id);
}

async function pollFunVasosAck(itemId, crpClave, fechaOperativa, chatJid, quotedMsg) {
    const maxAttempts = 12; // 12 * 15s = 3 minutos
    for (let i = 0; i < maxAttempts; i++) {
        await new Promise(r => setTimeout(r, 15000));
        try {
            const resp = await fetch(`${MIRROR_API_BASE}/api/v1/funvasos-inbox/${itemId}/status`, {
                headers: { 'X-Ingest-Secret': MIRROR_INGEST_SECRET }
            });
            if (!resp.ok) continue;
            const st = await resp.json();
            if (st.status === 'ARCHIVED') {
                if (quotedMsg?.key) await waSock.sendMessage(chatJid, { react: { text: '✅', key: quotedMsg.key } }).catch(() => {});
                await replyQuoted(
                    chatJid,
                    `✅ *Archivado en Repositorio FunVasos*\n📁 *${st.standard_file_name || ''}*\n🏷️ CRP: *${crpClave}* · Fecha: *${fechaOperativa}*\n${st.result_message || ''}`.trim(),
                    quotedMsg
                );
                return;
            }
            if (st.status === 'DUPLICATE') {
                if (quotedMsg?.key) await waSock.sendMessage(chatJid, { react: { text: '♻️', key: quotedMsg.key } }).catch(() => {});
                await replyQuoted(chatJid, `♻️ *Archivo duplicado (sin cambios)*\n${st.result_message || 'Ya existía en FunVasos.'}`, quotedMsg);
                return;
            }
            if (st.status === 'ERROR') {
                if (quotedMsg?.key) await waSock.sendMessage(chatJid, { react: { text: '⚠️', key: quotedMsg.key } }).catch(() => {});
                await replyQuoted(chatJid, `⚠️ *No se pudo archivar en FunVasos*\n${st.result_message || 'Error al validar el Excel.'}`, quotedMsg);
                return;
            }
        } catch (e) {}
    }
    await replyQuoted(
        chatJid,
        `📥 *Excel recibido en bandeja puente FunVasos* (ID #${itemId} · *${crpClave}* · *${fechaOperativa}*).\nSe archivará automáticamente en el servidor CFE en el próximo ciclo de descarga.`,
        quotedMsg
    );
}

async function enqueueFunVasosExcel({ buffer, filename, caption, crpClave, fechaOperativa, senderName, senderJid, chatJid, quotedMsg }) {
    if (!MIRROR_INGEST_SECRET) {
        await replyQuoted(chatJid, '⚠️ Puente FunVasos sin configurar (`CFE_MIRROR_INGEST_SECRET` faltante en Render).', quotedMsg);
        return;
    }
    if (quotedMsg?.key) {
        await waSock.sendMessage(chatJid, { react: { text: '⏳', key: quotedMsg.key } }).catch(() => {});
    }
    try {
        const resp = await fetch(`${MIRROR_API_BASE}/api/v1/funvasos-inbox/upload`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Ingest-Secret': MIRROR_INGEST_SECRET
            },
            body: JSON.stringify({
                crp_clave: crpClave,
                fecha_operativa: fechaOperativa,
                original_filename: filename,
                file_b64: buffer.toString('base64'),
                sender_name: senderName || '',
                sender_jid: senderJid || '',
                source_group_jid: chatJid || '',
                caption: caption || ''
            })
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) {
            if (quotedMsg?.key) await waSock.sendMessage(chatJid, { react: { text: '⚠️', key: quotedMsg.key } }).catch(() => {});
            await replyQuoted(chatJid, `⚠️ Error al encolar Excel para FunVasos: ${data.detail || resp.status}`, quotedMsg);
            return;
        }
        if (data.already_queued && data.status === 'ARCHIVED') {
            if (quotedMsg?.key) await waSock.sendMessage(chatJid, { react: { text: '♻️', key: quotedMsg.key } }).catch(() => {});
            await replyQuoted(chatJid, `♻️ Este archivo ya había sido archivado en FunVasos como *${data.standard_file_name || filename}* (sin duplicar).`, quotedMsg);
            return;
        }
        console.log(`📥 [FUNVASOS-WA] Encolado ID #${data.id} (${crpClave} · ${fechaOperativa} · ${filename}) de ${senderName}`);
        // Esperar ACK del servidor CFE en segundo plano sin bloquear el loop de mensajes
        pollFunVasosAck(data.id, crpClave, fechaOperativa, chatJid, quotedMsg).catch(() => {});
    } catch (err) {
        console.error('❌ [FUNVASOS-WA] Error subiendo a mirror inbox:', err.message);
        await replyQuoted(chatJid, `⚠️ No se pudo conectar al puente FunVasos: ${err.message}`, quotedMsg);
    }
}

async function handleIncomingFunVasosDocument(msg, chatJid, isGroup) {
    if (!FUNVASOS_BRIDGE_ENABLED) return false;
    const docInfo = getDocumentFromMsg(msg);
    if (!docInfo) return false;

    if (isGroup) {
        const allowed = await isAuthorizedFunVasosGroup(chatJid);
        const subj = (groupSubjectCache.get(chatJid)?.subject || '');
        console.log('[FUNVASOS-WA] Documento Excel detectado en grupo:', {
            chatJid,
            subject: subj,
            allowed,
            fromMe: !!msg.key.fromMe,
            filename: docInfo.filename
        });
        if (!allowed) {
            console.log(`[FUNVASOS-WA] Grupo "${subj}" (${chatJid}) no autorizado para FunVasos, omitiendo.`);
            return false;
        }
    } else {
        if (!FUNVASOS_ALLOW_DM) return false;
        console.log('[FUNVASOS-WA] Documento Excel detectado en chat privado:', {
            chatJid,
            fromMe: !!msg.key.fromMe,
            filename: docInfo.filename
        });
    }

    const senderJid = msg.key.participant || chatJid;
    const senderName = msg.pushName || senderJid.split('@')[0];

    try {
        const buffer = await downloadMediaMessage(msg, 'buffer', {}, { logger: pino({ level: 'silent' }) });
        if (!buffer || !buffer.length) return true;

        const fechaOperativa = extractFechaOperativa(docInfo.filename, docInfo.caption);
        const crpClave = detectFunVasosCrp(docInfo.filename, docInfo.caption);

        if (crpClave) {
            await enqueueFunVasosExcel({
                buffer,
                filename: docInfo.filename,
                caption: docInfo.caption,
                crpClave,
                fechaOperativa,
                senderName,
                senderJid,
                chatJid,
                quotedMsg: msg
            });
        } else {
            // Opción 3: pedir confirmación rápida 1-5 al remitente (TTL 15 min)
            const key = `${chatJid}::${senderJid}`;
            pendingFunVasosConfirm.set(key, {
                buffer,
                filename: docInfo.filename,
                caption: docInfo.caption,
                fechaOperativa,
                senderName,
                senderJid,
                chatJid,
                quotedMsg: msg,
                expiresAt: Date.now() + 15 * 60 * 1000
            });
            const menuLines = FUNVASOS_CRP_MENU.map(x => `${x.num}️⃣ ${x.label}`).join('\n');
            await replyQuoted(
                chatJid,
                `📄 Recibí *${docInfo.filename}* (${fechaOperativa}), pero no pude identificar el Departamento Regional (CRP).\n\nResponde con el número para archivarlo en *FunVasos*:\n${menuLines}`,
                msg
            );
        }
    } catch (err) {
        console.error('❌ [FUNVASOS-WA] Error descargando Excel de WhatsApp:', err.message);
    }
    return true;
}

async function handlePendingFunVasosReply(msg, chatJid, text) {
    const senderJid = msg.key.participant || chatJid;
    const key = `${chatJid}::${senderJid}`;
    const pending = pendingFunVasosConfirm.get(key);
    if (!pending) return false;
    if (Date.now() > pending.expiresAt) {
        pendingFunVasosConfirm.delete(key);
        return false;
    }
    const clean = normalizeTextSimple(text).replace(/^[#!]+/, '').trim();
    const byNum = FUNVASOS_CRP_MENU.find(x => x.num === clean);
    const crpClave = byNum ? byNum.clave : detectFunVasosCrp('', clean);
    if (!crpClave) return false;

    pendingFunVasosConfirm.delete(key);
    await enqueueFunVasosExcel({ ...pending, crpClave });
    return true;
}

// Endpoints HTTP internos para consulta y disparo manual de reportes
app.get('/api/embalses/reporte', async (req, res) => {
    try {
        const region = req.query.region || req.query.crp || null;
        const deltaHours = parseInt(req.query.deltaHours || '3', 10);
        const report = await generateEmbalsesReport(region, deltaHours);
        res.json({ ok: true, report });
    } catch (e) {
        res.status(500).json({ ok: false, error: e.message });
    }
});

app.get('/api/embalses/dispatch-now', async (req, res) => {
    try {
        const mode = (req.query.mode || '3h').toLowerCase();
        const crp = req.query.crp ? String(req.query.crp).toUpperCase() : null;
        const force = String(req.query.force || 'true').toLowerCase() === 'true';
        let result;
        if (mode === 'daily') {
            result = await despacharBoletinDiarioAutomatico();
        } else if (mode === 'alerts') {
            result = await despacharAlertasAutomaticas({ force, crp });
        } else {
            result = await despacharReporteAutomaticoEmbalses(crp);
        }
        res.json({ ok: result.ok, mode, result });
    } catch (e) {
        res.status(500).json({ ok: false, error: e.message });
    }
});

let isStarting = false;

async function startBaileys() {
    if (isStarting) return;
    isStarting = true;
    clientStatus = 'STARTING';
    lastErrorMsg = null;
    try {
        if (waSock) {
            try {
                waSock.ev.removeAllListeners();
                waSock.end();
            } catch (e) {}
            waSock = null;
        }

        const authFolder = path.join(__dirname, 'auth_info_baileys');
        if (!fs.existsSync(authFolder)) {
            fs.mkdirSync(authFolder, { recursive: true });
        }

        const { state, saveCreds } = await useMultiFileAuthState(authFolder);

        let version = [2, 3000, 1043857760];
        try {
            const vObj = await fetchLatestBaileysVersion();
            if (vObj && vObj.version) version = vObj.version;
        } catch (e) {
            console.log('Using default Baileys version:', e.message);
        }

        waSock = makeWASocket({
            version,
            logger: pino({ level: 'silent' }),
            printQRInTerminal: true,
            auth: state,
            browser: Browsers.ubuntu('Chrome'),
            syncFullHistory: false,
            connectTimeoutMs: 60000,
            keepAliveIntervalMs: 25000,
            generateHighQualityLinkPreview: true
        });

        waSock.ev.on('creds.update', saveCreds);

        waSock.ev.on('messages.upsert', async (m) => {
            // Baileys emite 'notify' para mensajes entrantes de terceros y 'append' para mensajes enviados desde tu propio teléfono (fromMe).
            // El filtro de 90 segundos abajo evita procesar historial antiguo.
            if (m.type !== 'notify' && m.type !== 'append') return;

            const nowSec = Math.floor(Date.now() / 1000);

            for (const msg of m.messages) {
                // 1. Ignorar si es un mensaje enviado por el propio bot
                if (msg.key.id && botSentMessageIds.has(msg.key.id)) {
                    botSentMessageIds.delete(msg.key.id);
                    continue;
                }

                // 2. Ignorar mensajes viejos (más de 90 segundos) durante sincronizaciones
                const msgTs = msg.messageTimestamp ? Number(msg.messageTimestamp) : 0;
                if (msgTs > 0 && (nowSec - msgTs) > 90) {
                    continue;
                }

                const from = msg.key.remoteJid;
                if (!from || from === 'status@broadcast') continue;

                const isGroup = from.endsWith('@g.us');

                // 3. Permitir documentos Excel de FunVasos incluso si los envía el mismo teléfono vinculado (fromMe=true)
                if (await handleIncomingFunVasosDocument(msg, from, isGroup)) {
                    continue;
                }

                const unwrapped = unwrapMessageContent(msg.message);
                const text = unwrapped?.conversation || 
                             unwrapped?.extendedTextMessage?.text || 
                             unwrapped?.imageMessage?.caption || 
                             '';

                // Permitir respuesta 1-5 de confirmación FunVasos incluso si fromMe=true
                if (text && text.trim() && await handlePendingFunVasosReply(msg, from, text.trim())) {
                    continue;
                }

                // 4. Para el resto del chatbot, ignorar mensajes enviados por nosotros mismos
                if (msg.key.fromMe) continue;

                if (!text || !text.trim()) continue;

                const trimmed = text.trim();

                console.log('[WA MSG RECV]', { from, text: trimmed.slice(0, 60), fromMe: msg.key.fromMe, id: msg.key.id });

                // 4.1. Filtro estricto anti-eco de HydroCloud y reportes salientes
                if (trimmed.includes('REPORTE DE EMBALSES') ||
                    trimmed.includes('ALERTAS HIDRÁULICAS') ||
                    trimmed.includes('ALERTA CRÍTICA AUTOMÁTICA') ||
                    trimmed.includes('ALERTA PREVENTIVA AUTOMÁTICA') ||
                    trimmed.includes('SALUD DE LA RED HIDROMÉTRICA') ||
                    trimmed.includes('BOLETÍN HIDROLÓGICO DIARIO') ||
                    trimmed.includes('CORTE DE LLUVIA POR CUENCA') ||
                    trimmed.includes('HydroCloud') ||
                    trimmed.includes('CENTRALES HIDROELÉCTRICAS') ||
                    recentSentTexts.has(trimmed.slice(0, 40))) {
                    continue;
                }

                if (isGroup && !trimmed.startsWith('#') && !trimmed.startsWith('!') && !trimmed.toLowerCase().startsWith('centinela')) {
                    continue;
                }

                procesarMensajeCentinela(from, trimmed, msg.key.id, msg.key);
            }
        });

        waSock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;

            if (qr) {
                clientStatus = 'SCAN_QR';
                latestQrDataUrl = await QRCode.toDataURL(qr);
                console.log('📌 Código QR generado. Abre http://localhost:' + PORT + '/qr');
            }

            if (connection === 'close') {
                const statusCode = (lastDisconnect?.error)?.output?.statusCode;
                const isLoggedOut = statusCode === DisconnectReason.loggedOut;
                lastErrorMsg = `Close status: ${statusCode}`;
                console.log(`Conexión cerrada (status: ${statusCode}). Reconectando...`);
                clientStatus = 'DISCONNECTED';
                if (temporizadorEmbalses) {
                    clearInterval(temporizadorEmbalses);
                    temporizadorEmbalses = null;
                }
                if (temporizadorAlertas) {
                    clearInterval(temporizadorAlertas);
                    temporizadorAlertas = null;
                }
                if (isLoggedOut || statusCode === DisconnectReason.loggedOut || statusCode === 401) {
                    console.log('Sesión cerrada formalmente (loggedOut). Limpiando credenciales antiguas para generar nuevo QR...');
                    try {
                        fs.rmSync(authFolder, { recursive: true, force: true });
                    } catch (e) {}
                }
                isStarting = false;
                const waitTime = statusCode === 440 ? 5000 : 3000;
                setTimeout(startBaileys, waitTime);
            } else if (connection === 'open') {
                console.log('✅ Conexión establecida con WhatsApp con éxito!');
                clientStatus = 'READY';
                latestQrDataUrl = null;
                lastErrorMsg = null;
                isStarting = false;
                iniciarCronEmbalses();
            }
        });
    } catch (err) {
        lastErrorMsg = err.message || String(err);
        console.error('[BAILEYS] Error fatal al iniciar:', err);
        clientStatus = 'ERROR';
        isStarting = false;
        setTimeout(startBaileys, 5000);
    }
}

app.listen(PORT, () => {
    console.log(`🚀 Servidor WhatsApp CFE & Centinela Bot escuchando en http://localhost:${PORT}`);
    startBaileys();
});
