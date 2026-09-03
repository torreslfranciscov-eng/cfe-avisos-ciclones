const express = require('express');
const cors = require('cors');
const fs = require('fs');
const path = require('path');
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
let webhookUrl = process.env.WHATSAPP_WEBHOOK_URL || 'http://localhost:10000/api/whatsapp/webhook';

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

// Función auxiliar para resolver nombre de grupo a JID
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

        // 1. Enviar imagen satelital con caption
        if (satPath && fs.existsSync(satPath)) {
            const bufferSat = fs.readFileSync(satPath);
            const resSat = await waSock.sendMessage(jid, {
                image: bufferSat,
                caption: caption || ''
            });
            results.push({ type: 'sat', resSat });
        } else if (caption) {
            const resText = await waSock.sendMessage(jid, { text: caption });
            results.push({ type: 'text', resText });
        }

        // 2. Enviar imagen de cono de trayectoria
        if (trayPath && fs.existsSync(trayPath)) {
            const bufferTray = fs.readFileSync(trayPath);
            const resTray = await waSock.sendMessage(jid, {
                image: bufferTray,
                caption: '🗺️ Cono de Pronóstico de Trayectoria'
            });
            results.push({ type: 'tray', resTray });
        }

        // 3. Enviar archivo Word (.docx) oficial
        if (docxPath && fs.existsSync(docxPath)) {
            const bufferDoc = fs.readFileSync(docxPath);
            const fname = path.basename(docxPath);
            const resDoc = await waSock.sendMessage(jid, {
                document: bufferDoc,
                mimetype: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                fileName: fname,
                caption: `📄 Reporte Oficial Word: ${fname}`
            });
            results.push({ type: 'doc', resDoc });
        }

        res.json({ ok: true, recipient: jid, results });
    } catch (err) {
        console.error('Error in sendCycloneNotice:', err);
        res.status(500).json({ ok: false, error: err.toString() });
    }
});

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

    // ESCUCHAR MENSAJES ENTRANTES Y REENVIARLOS AL CENTINELA BOT / WEBHOOK
    waSock.ev.on('messages.upsert', async (m) => {
        if (!webhookUrl || m.type !== 'notify') return;

        for (const msg of m.messages) {
            // Ignorar mensajes enviados por el propio bot
            if (msg.key.fromMe) continue;

            const from = msg.key.remoteJid;
            const isGroup = from.endsWith('@g.us');
            const senderNumber = (msg.key.participant || from).split('@')[0];
            const text = msg.message?.conversation || 
                         msg.message?.extendedTextMessage?.text || 
                         msg.message?.imageMessage?.caption || 
                         '';

            const payload = {
                from: from,
                sender: senderNumber,
                senderName: msg.pushName || 'Usuario',
                isGroup: isGroup,
                messageId: msg.key.id,
                text: text,
                timestamp: msg.messageTimestamp,
                messageType: Object.keys(msg.message || {})[0] || 'unknown',
                raw: msg
            };

            try {
                // Enviar tanto al bot local interno como a cualquier webhook configurado
                fetch('http://localhost:10000/api/whatsapp/webhook', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                }).catch(() => {});

                if (webhookUrl && webhookUrl !== 'http://localhost:10000/api/whatsapp/webhook') {
                    fetch(webhookUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    }).catch(() => {});
                }
            } catch (err) {
                console.error('[WHATSAPP] Error al procesar mensaje entrante:', err.message);
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
    console.log(`🚀 Servidor WhatsApp CFE escuchando en http://localhost:${PORT}`);
    startBaileys();
});
