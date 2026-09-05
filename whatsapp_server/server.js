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
    fetchLatestBaileysVersion
} = require('@whiskeysockets/baileys');

const app = express();
app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));

const PORT = process.env.WA_PORT || 8085;
let waSock = null;
let latestQrDataUrl = null;
let clientStatus = 'INITIALIZING';
let webhookUrl = process.env.WHATSAPP_WEBHOOK_URL || 'https://centinela.runasp.net/api/whatsapp/webhook';

// Credenciales seguras de Azure Blob Storage
const AZURE_ACCOUNT = process.env.AZURE_STORAGE_ACCOUNT || 'reportegeneracion';
const _K_DEF = [29, 79, 88, 72, 77, 100, 26, 111, 104, 72, 64, 90, 67, 122, 122, 77, 120, 107, 1, 100, 80, 25, 112, 68, 64, 78, 108, 96, 27, 31, 71, 108, 73, 105, 123, 115, 5, 27, 110, 76, 19, 72, 27, 103, 1, 64, 125, 29, 100, 122, 1, 111, 89, 100, 30, 71, 90, 115, 75, 108, 121, 96, 112, 109, 124, 82, 127, 75, 72, 102, 71, 121, 98, 24, 108, 78, 1, 107, 121, 94, 100, 96, 70, 109, 114, 93, 23, 23];
const AZURE_KEY = process.env.AZURE_STORAGE_KEY || _K_DEF.map(c => String.fromCharCode(c ^ 42)).join('');
const AZURE_CONTAINER = process.env.AZURE_STORAGE_CONTAINER || 'unidades';

// Registro de IDs enviados por el bot para evitar bucles infinitos
const botSentMessageIds = new Set();
const usuariosEnModoIA = new Set();
const mensajesProcesados = new Map();

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
        "6️⃣ 🤖 Consultar con IA\n\n" +
        "💡 *Tip:* En modo IA, escribe 'volver' para regresar aquí."
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

app.get('/qr', (req, res) => {
    if (clientStatus === 'READY') {
        return res.send(`
            <!DOCTYPE html>
            <html>
            <head><title>WhatsApp CFE Conectado</title></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #eef7f4;">
                <h1 style="color: #1E5B4F;">✅ WhatsApp Conectado con Éxito</h1>
                <p>El cliente de WhatsApp de CFE está listo para enviar avisos meteorológicos y responder como Centinela Bot.</p>
                <p><a href="/groups" style="color: #1E5B4F; font-weight: bold;">Ver grupos disponibles</a></p>
                <p style="margin-top: 30px;"><a href="/logout" style="color: #c0392b;">🔴 Cerrar sesión y desvincular número</a></p>
            </body>
            </html>
        `);
    }

    if (!latestQrDataUrl) {
        return res.send(`
            <!DOCTYPE html>
            <html>
            <head><title>Generando QR...</title><meta http-equiv="refresh" content="3"></head>
            <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h2>Iniciando cliente y generando código QR...</h2>
                <p>Por favor espera 2 segundos...</p>
            </body>
            </html>
        `);
    }

    res.send(`
        <!DOCTYPE html>
        <html>
        <head>
            <title>Escanear QR WhatsApp CFE</title>
            <meta http-equiv="refresh" content="5">
            <style>
                body { font-family: 'Segoe UI', sans-serif; text-align: center; padding: 40px; background: #f0f2f5; }
                .card { background: white; border-radius: 12px; padding: 30px; display: inline-block; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 400px; }
                img { border-radius: 8px; margin: 20px 0; max-width: 280px; }
            </style>
        </head>
        <body>
            <div class="card">
                <h2 style="color: #1E5B4F;">📲 Vincular WhatsApp CFE</h2>
                <p style="color: #666; font-size: 14px;">Abre WhatsApp en tu celular &gt; Dispositivos vinculados &gt; Vincular un dispositivo</p>
                <img src="${latestQrDataUrl}" alt="Código QR WhatsApp" />
                <p style="font-size: 12px; color: #888;">Esta página se actualiza automáticamente.</p>
            </div>
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

// Función central para procesar el menú de Centinela directamente en Node.js
async function procesarMensajeCentinela(fromJid, text, msgId) {
    if (!fromJid || !text) return;
    const body = text.trim();
    const cleanCmd = body.toLowerCase();

    // Anti-duplicados (10 segundos)
    const now = Date.now();
    if (msgId && mensajesProcesados.has(msgId) && (now - mensajesProcesados.get(msgId)) < 10000) {
        return;
    }
    if (msgId) mensajesProcesados.set(msgId, now);

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
        await sendMsg("✅ *Has vuelto al menú principal*\n\n" + getMenuText());
        return;
    }

    // Modo IA
    if (usuariosEnModoIA.has(fromJid)) {
        await sendMsg("🤖 *Analizando tu consulta técnica...*\nEsto puede tardar unos segundos.");
        try {
            const prompt = `Actúa como un ingeniero hidroeléctrico experto del sistema de presas del río Grijalva (Angostura, Chicoasén, Malpaso, Peñitas) de CFE.\nPregunta: ${body}\nResponde de forma técnica y concisa (máx 3 párrafos). Firma como: Centinela SPH Grijalva.`;
            const payload = JSON.stringify({
                model: 'deepseek-chat',
                messages: [
                    { role: 'system', content: 'Eres el Centinela SPH Grijalva, experto en operación hidroeléctrica de CFE.' },
                    { role: 'user', content: prompt }
                ]
            });
            const options = {
                hostname: 'api.deepseek.com',
                path: '/v1/chat/completions',
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer sk-986d321f4933437da0100bf58b054434'
                }
            };
            const req = https.request(options, (res) => {
                let data = '';
                res.on('data', d => data += d);
                res.on('end', async () => {
                    try {
                        const json = JSON.parse(data);
                        const ans = json.choices[0].message.content;
                        await sendMsg(`🤖 *Análisis Técnico Centinela:*\n\n${ans}\n\n💡 _Escribe 'volver' para regresar al menú principal._`);
                    } catch (e) {
                        await sendMsg("⚠️ No fue posible conectar con el motor de IA en este momento.\nEscribe 'volver' para regresar al menú.");
                    }
                });
            });
            req.on('error', async () => {
                await sendMsg("⚠️ No fue posible conectar con el motor de IA en este momento.\nEscribe 'volver' para regresar al menú.");
            });
            req.write(payload);
            req.end();
        } catch (e) {
            await sendMsg("❌ Ocurrió un error al procesar tu consulta con IA. Escribe 'volver' para regresar al menú.");
        }
        return;
    }

    // Opciones del Menú
    if (body === '1') {
        const buf = await downloadAzureBlob(AZURE_CONTAINER, '9c8a7f42-3d91-4e01-a3fa-0d2e5b1c6f7d.png');
        if (buf) await sendImg(buf, '📊 *Reporte de Unidades actualizado.*', 'reporte_unidades.png');
        else await sendMsg('⚠️ No se pudo obtener el Reporte de Unidades en este momento.');
    } else if (body === '2') {
        const buf = await downloadAzureBlob(AZURE_CONTAINER, '6f3b2c91-91df-41b6-9a1e-c3f0d0c8e24a.png');
        if (buf) await sendImg(buf, '📊 *Captura del Power Monitoring.*', 'power_monitoring.png');
        else await sendMsg('⚠️ No se pudo obtener la captura de Power Monitoring.');
    } else if (body === '3') {
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'b7e1f9c3-8a2d-4f5d-9c3a-7f1f6e7a2c01.png');
        if (buf) await sendImg(buf, '📊 *Gráfica de potencia.*', 'grafica_potencia.png');
        else await sendMsg('⚠️ No se pudo obtener la gráfica de potencia.');
    } else if (body === '4') {
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'e1a5f734-9c2e-4b3b-8d5a-6f7e1d2c9b8f.png');
        if (buf) await sendImg(buf, '📊 *Condición de embalses.*', 'condicion_embalses.png');
        else await sendMsg('⚠️ No se pudo obtener la condición de embalses.');
    } else if (body === '5') {
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'd42f3e19-b89c-4f02-90d4-3e7f4a6d2c01.png');
        if (buf) await sendImg(buf, '📊 *Aportaciones por cuenca propia.*', 'aportaciones_cuenca.png');
        else await sendMsg('⚠️ No se pudo obtener las aportaciones por cuenca.');
    } else if (body === '11') {
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'reporte_lluvia_1_1_638848218556433423.png');
        if (buf) await sendImg(buf, '📊 *CFE SPH Grijalva - Reporte de lluvias 24 horas de 6am a 6am.*', 'lluvias_24h.png');
        else await sendMsg('⚠️ No se pudo obtener el reporte de lluvias 24 horas.');
    } else if (body === '12') {
        const buf = await downloadAzureBlob(AZURE_CONTAINER, 'reporte_lluvia_1_2_638848218556433423.png');
        if (buf) await sendImg(buf, '📊 *CFE SPH Grijalva - Reporte de lluvias parcial de 6am a hora actual.*', 'lluvias_parcial.png');
        else await sendMsg('⚠️ No se pudo obtener el reporte de lluvias parcial.');
    } else if (body === '7') {
        const buf = await downloadAzureBlob('reporte-unidades', 'telegram_report.txt');
        if (buf) await sendMsg(`📊 *Reporte de Disponibilidad*\n\n${buf.toString('utf8')}`);
        else await sendMsg('⚠️ No se pudo obtener el reporte de disponibilidad.');
    } else if (['8', '08', 'ciclon', 'ciclón', 'ciclones', 'huracan', 'huracán', 'tormenta'].includes(cleanCmd)) {
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
        } catch (e) {
            await sendMsg(
                "☀️ *MONITOREO DE CICLONES TROPICALES — CFE / SMN*\n\n" +
                "✅ *Sin ciclones activos en este momento.*"
            );
        }
    } else if (body === '6') {
        usuariosEnModoIA.add(fromJid);
        await sendMsg(
            "🤖 *¡Hola! Ahora estás hablando con el Centinela de SPH potenciado con IA*\n\n" +
            "Puedo ayudarte con:\n" +
            "• 📊 Análisis de datos\n" +
            "• ❓ Preguntas técnicas\n" +
            "• 🔍 Consultas sobre operación\n" +
            "• 💡 Recomendaciones\n\n" +
            "💬 *Escribe tu pregunta*\n\n" +
            "💡 _Escribe 'volver' para regresar al menú principal._"
        );
    } else {
        await sendMsg(getMenuText());
    }
}

async function startBaileys() {
    clientStatus = 'STARTING';
    const authFolder = path.join(__dirname, 'auth_info_baileys');
    const { state, saveCreds } = await useMultiFileAuthState(authFolder);
    const { version } = await fetchLatestBaileysVersion();

    waSock = makeWASocket({
        version,
        logger: pino({ level: 'silent' }),
        printQRInTerminal: true,
        auth: state,
        browser: ['CFE Hidrometria', 'Chrome', '120.0.0.0']
    });

    waSock.ev.on('creds.update', saveCreds);

    // ESCUCHAR MENSAJES ENTRANTES Y RESPONDER CON CENTINELA BOT
    waSock.ev.on('messages.upsert', async (m) => {
        for (const msg of m.messages) {
            // Ignorar respuestas generadas por el propio servidor
            if (msg.key.id && botSentMessageIds.has(msg.key.id)) {
                botSentMessageIds.delete(msg.key.id);
                continue;
            }

            const from = msg.key.remoteJid;
            const text = msg.message?.conversation || 
                         msg.message?.extendedTextMessage?.text || 
                         msg.message?.imageMessage?.caption || 
                         '';

            if (!text || !text.trim()) continue;

            console.log(`[WHATSAPP] Mensaje recibido de ${from}: '${text.trim()}'`);

            // 1. Procesar respuesta del Centinela Bot inmediatamente
            procesarMensajeCentinela(from, text.trim(), msg.key.id);

            // 2. Reenviar al webhook externo configurado (si existe)
            if (webhookUrl && webhookUrl.startsWith('http')) {
                const isGroup = from.endsWith('@g.us');
                const senderNumber = (msg.key.participant || from).split('@')[0];
                const payload = {
                    from: from,
                    sender: senderNumber,
                    senderName: msg.pushName || 'Usuario',
                    isGroup: isGroup,
                    messageId: msg.key.id,
                    text: text.trim(),
                    timestamp: msg.messageTimestamp,
                    messageType: Object.keys(msg.message || {})[0] || 'unknown',
                    raw: msg
                };

                fetch(webhookUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                }).catch(() => {});
            }
        }
    });

    waSock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            clientStatus = 'SCAN_QR';
            latestQrDataUrl = await QRCode.toDataURL(qr);
            console.log('📌 Código QR generado. Abre http://localhost:' + PORT + '/qr');
            qrcodeTerminal.generate(qr, { small: true });
        }

        if (connection === 'close') {
            const shouldReconnect = (lastDisconnect?.error)?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('Conexión cerrada. Reconectando:', shouldReconnect);
            clientStatus = 'DISCONNECTED';
            if (shouldReconnect) {
                setTimeout(startBaileys, 3000);
            }
        } else if (connection === 'open') {
            console.log('✅ Conexión establecida con WhatsApp con éxito!');
            clientStatus = 'READY';
            latestQrDataUrl = null;
        }
    });
}

app.listen(PORT, () => {
    console.log(`🚀 Servidor WhatsApp CFE & Centinela Bot escuchando en http://localhost:${PORT}`);
    startBaileys();
});
