/**
 * ai_assistant.js
 * Asistente de Inteligencia Artificial Centinela CFE (Google Gemini)
 * conectado en tiempo real a la telemetría de CFE Mirror API
 * (27 embalses + curvas EAC, 22 cuencas, alertas activas y salud de red GOES).
 */

const {
  generateEmbalsesReport,
  getAlertsReport,
  getNetworkHealthReport,
  getRainfallByBasinReport
} = require('./embalses_reporter');

const DEFAULT_MODELS = [
  'gemini-3.5-flash-lite',
  'gemini-flash-lite-latest',
  'gemini-flash-latest',
  'gemini-3.8-flash'
];

// Caché de contexto en vivo (5 minutos)
let liveContextCache = { text: '', fetchedAt: 0 };
const CONTEXT_TTL_MS = 5 * 60 * 1000;

// Memoria conversacional por usuario (últimos 6 turnos, TTL 30 min)
const userHistory = new Map();
const HISTORY_TTL_MS = 30 * 60 * 1000;
const MAX_TURNS = 6;

async function getLiveCfeContext() {
  const now = Date.now();
  if (liveContextCache.text && (now - liveContextCache.fetchedAt) < CONTEXT_TTL_MS) {
    return liveContextCache.text;
  }

  const [embRes, alRes, rainRes, healthRes] = await Promise.allSettled([
    generateEmbalsesReport(null, 3),
    getAlertsReport({ severity: 'CRITICA,ALERTA' }),
    getRainfallByBasinReport({ cutoffHour: 6 }),
    getNetworkHealthReport({ onlyIssues: true })
  ]);

  const parts = [];
  if (embRes.status === 'fulfilled' && embRes.value?.mensajeTexto) {
    parts.push('=== EMBALSES CFE ===\n' + embRes.value.mensajeTexto.slice(0, 2200));
  }
  if (alRes.status === 'fulfilled' && alRes.value?.mensajeTexto) {
    parts.push('=== ALERTAS ACTIVAS ===\n' + alRes.value.mensajeTexto.slice(0, 900));
  }
  if (rainRes.status === 'fulfilled' && rainRes.value?.mensajeTexto) {
    parts.push('=== LLUVIA 24H POR CUENCA ===\n' + rainRes.value.mensajeTexto.slice(0, 700));
  }
  if (healthRes.status === 'fulfilled' && healthRes.value?.mensajeTexto) {
    parts.push('=== SALUD RED HIDROMÉTRICA ===\n' + healthRes.value.mensajeTexto.slice(0, 700));
  }

  const combined = parts.join('\n\n') || 'Sin telemetría disponible en este momento.';
  liveContextCache = { text: combined, fetchedAt: now };
  return combined;
}

function getConversationHistory(userId) {
  const entry = userHistory.get(userId);
  if (!entry || (Date.now() - entry.updatedAt) > HISTORY_TTL_MS) {
    return [];
  }
  return entry.turns;
}

function pushConversationTurn(userId, userText, modelText) {
  const turns = getConversationHistory(userId);
  turns.push({ role: 'user', text: userText });
  turns.push({ role: 'model', text: modelText });
  while (turns.length > MAX_TURNS * 2) {
    turns.shift();
  }
  userHistory.set(userId, { turns, updatedAt: Date.now() });
}

function clearUserHistory(userId) {
  userHistory.delete(userId);
}

async function callGeminiModel(apiKey, modelName, systemInstruction, contents, timeoutMs = 30000) {
  const cleanModel = modelName.replace(/^models\//, '');
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${cleanModel}:generateContent?key=${encodeURIComponent(apiKey)}`;

  const payload = {
    system_instruction: { parts: [{ text: systemInstruction }] },
    contents,
    generationConfig: {
      temperature: 0.3,
      maxOutputTokens: 700
    }
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data?.error?.message || `HTTP ${res.status}`;
      const err = new Error(`[${cleanModel}] ${msg}`);
      err.status = res.status;
      throw err;
    }
    const text = data?.candidates?.[0]?.content?.parts?.map(p => p.text || '').join('').trim();
    if (!text) {
      throw new Error(`[${cleanModel}] Respuesta vacía de Gemini`);
    }
    return { text, model: cleanModel };
  } finally {
    clearTimeout(timer);
  }
}

async function askCentinelaAI(userId, question) {
  const apiKey = (process.env.GEMINI_API_KEY || '').trim();
  if (!apiKey) {
    return '⚠️ El motor de IA Gemini aún no tiene configurada la variable de entorno *GEMINI_API_KEY* en el servidor.';
  }

  const liveContext = await getLiveCfeContext();
  const systemInstruction = [
    'Eres *Centinela IA*, el asistente experto en Ingeniería Hidráulica, Hidrometría y Operación Hidroeléctrica de la CFE (Gerencia de Ingeniería Hidráulica / Subgerencia de Hidrometría).',
    'Conoces las 5 Coordinaciones Regionales de Producción (SURESTE, GOLFO, BALSAS, SANTIAGO, NOROESTE), las 27 presas principales, sus cascadas (Grijalva, Santiago, Balsas, Papaloapan, Río Fuerte), cotas oficiales (CORONA, NAME, NAMO, NAMinO) y las 22 cuencas GOES.',
    'REGLAS:',
    '1. Usa SIEMPRE los datos en tiempo real proporcionados abajo cuando respondas sobre niveles, volúmenes, lluvias, alertas o estaciones. Nunca inventes cifras.',
    '2. Si una presa reporta "escala local" (tirante sin cota msnm) o Peñitas reporta a la salida del embalse, acláralo técnicamente sin inventar porcentajes de llenado.',
    '3. Responde en español técnico claro, directo y ejecutivo (máximo 3 párrafos cortos o viñetas legibles para WhatsApp).',
    '4. Usa unidades correctas: msnm, Mm³, m³/s, mm, %, V.',
    '5. Cierra siempre firmando brevemente como: _*Centinela IA · CFE Hidrometría*_.',
    '',
    '=== TELEMETRÍA EN VIVO DE LA RED CFE ===',
    liveContext
  ].join('\n');

  const history = getConversationHistory(userId);
  const contents = [
    ...history.map(t => ({ role: t.role, parts: [{ text: t.text }] })),
    { role: 'user', parts: [{ text: question }] }
  ];

  const customModel = (process.env.GEMINI_MODEL || '').trim();
  const modelsToTry = customModel
    ? [customModel, ...DEFAULT_MODELS.filter(m => m !== customModel)]
    : DEFAULT_MODELS;

  let lastErr = null;
  for (const modelName of modelsToTry) {
    for (let attempt = 1; attempt <= 2; attempt++) {
      try {
        const { text } = await callGeminiModel(apiKey, modelName, systemInstruction, contents);
        pushConversationTurn(userId, question, text);
        return text;
      } catch (e) {
        lastErr = e;
        if (e.status === 503 && attempt === 1) {
          await new Promise(r => setTimeout(r, 1200));
          continue;
        }
        console.warn(`[CENTINELA-AI] Fallback tras error en ${modelName}:`, e.message);
        break;
      }
    }
  }

  console.error('[CENTINELA-AI] Todos los modelos Gemini fallaron:', lastErr?.message);
  return '⚠️ El servicio de IA Gemini está temporalmente saturado. Intenta de nuevo en unos segundos.';
}

module.exports = {
  askCentinelaAI,
  clearUserHistory,
  getLiveCfeContext
};
