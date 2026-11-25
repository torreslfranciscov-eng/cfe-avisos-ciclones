/**
 * embalses_reporter.js
 * Cliente oficial de CFE Mirror API (https://cfe-mirror-api.onrender.com)
 * para Centinela Bot (WhatsApp Baileys) y envíos automáticos cada 3 horas.
 */

const fs = require('fs');
const path = require('path');

// Cargar variables de entorno desde .env si existe localmente
const envPath = path.resolve(__dirname, '../.env');
if (fs.existsSync(envPath)) {
  try {
    const envContent = fs.readFileSync(envPath, 'utf8');
    envContent.split('\n').forEach(line => {
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith('#') && trimmed.includes('=')) {
        const [k, ...v] = trimmed.split('=');
        const key = k.trim();
        const val = v.join('=').trim().replace(/^['"]|['"]$/g, '');
        if (!process.env[key]) {
          process.env[key] = val;
        }
      }
    });
  } catch (e) {}
}

const MIRROR_API_URL = (process.env.CFE_MIRROR_API_URL || 'https://cfe-mirror-api.onrender.com').replace(/\/+$/, '');
const MIRROR_API_KEY = (process.env.CFE_MIRROR_API_KEY || 'cfe_live_rWCDtjwhU-nEbp-jlMq0uMV4-AiwrU9lp1jWRw').trim();

const VALID_CRPS = ['SURESTE', 'GOLFO', 'BALSAS', 'SANTIAGO', 'NOROESTE'];

function detectCrpFromText(text) {
  const low = (text || '').toLowerCase();
  if (low.includes('sureste') || low.includes('grijalva')) return 'SURESTE';
  if (low.includes('golfo') || low.includes('papaloapan')) return 'GOLFO';
  if (low.includes('balsas')) return 'BALSAS';
  if (low.includes('santiago')) return 'SANTIAGO';
  if (low.includes('noroeste') || low.includes('pacifico')) return 'NOROESTE';
  if (low.includes('nacional') || low.includes('todos') || low.includes('todas')) return null;
  return null;
}

async function fetchMirrorJson(endpoint, params = {}, timeoutMs = 25000) {
  const url = new URL(`${MIRROR_API_URL}${endpoint}`);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      url.searchParams.set(k, String(v));
    }
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url.toString(), {
      method: 'GET',
      headers: {
        'X-API-Key': MIRROR_API_KEY,
        'Accept': 'application/json',
        'User-Agent': 'Centinela-CFE-Bot/2.0'
      },
      signal: controller.signal
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status} en ${endpoint}: ${body.slice(0, 180)}`);
    }
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 1) Reporte Ejecutivo de Embalses (Δ 3h + NAMO + Volúmenes EAC en Mm³)
 */
async function generateEmbalsesReport(region = null, deltaHours = 3) {
  const crpClean = region ? String(region).trim().toUpperCase() : null;
  const crpParam = (crpClean && VALID_CRPS.includes(crpClean)) ? crpClean : null;
  const delta = deltaHours > 0 ? deltaHours : 3;

  const data = await fetchMirrorJson('/api/v1/water-levels', {
    only_dams: 'true',
    delta_hours: delta,
    include_text: 'true',
    ...(crpParam ? { crp: crpParam } : {})
  });

  return {
    titulo: 'REPORTE DE EMBALSES · CENTRALES HIDROELÉCTRICAS CFE',
    region: crpParam || 'NACIONAL',
    mensajeTexto: data.resumen_texto || '⚠️ Sin datos de embalses disponibles en este momento.',
    fechaCorte: `${data.fecha_local || ''} ${data.hora_local || ''}`.trim(),
    resumenCrp: data.resumen_crp || {},
    presas: data.data || []
  };
}

/**
 * 2) Alertas Hidráulicas e Hidrométricas en Tiempo Real
 */
async function getAlertsReport({ severity = null, category = null, crp = null } = {}) {
  const data = await fetchMirrorJson('/api/v1/alerts', {
    include_text: 'true',
    ...(severity ? { severity } : {}),
    ...(category ? { category } : {}),
    ...(crp ? { crp } : {})
  });
  return {
    total: data.total_alertas || 0,
    porSeveridad: data.por_severidad || {},
    porCategoria: data.por_categoria || {},
    alertas: data.alertas || [],
    mensajeTexto: data.resumen_texto || '🟢 Sin alertas activas en este momento.'
  };
}

/**
 * 3) Salud de la Red Hidrométrica (QA/QC)
 */
async function getNetworkHealthReport({ onlyIssues = true, onlyDams = false, crp = null } = {}) {
  const data = await fetchMirrorJson('/api/v1/network-health', {
    only_issues: onlyIssues ? 'true' : 'false',
    only_dams: onlyDams ? 'true' : 'false',
    include_text: 'true',
    ...(crp ? { crp } : {})
  });
  return {
    resumenNacional: data.resumen_nacional || {},
    mensajeTexto: data.resumen_texto || '⚠️ No se pudo consultar la salud de la red.'
  };
}

/**
 * 4) Boletín Hidrológico Diario Integral (Corte 06:00)
 */
async function getDailyBulletinReport({ crp = null, cutoffHour = 6 } = {}) {
  const data = await fetchMirrorJson('/api/v1/daily-bulletin', {
    cutoff_hour: cutoffHour,
    include_text: 'true',
    ...(crp ? { crp } : {})
  });
  return {
    balanceNacional: data.balance_nacional || {},
    mensajeTexto: data.resumen_texto || '⚠️ No se pudo generar el boletín diario.'
  };
}

/**
 * 5) Corte de Lluvia 24h por Cuenca (06:01 -> 06:00 local)
 */
async function getRainfallByBasinReport({ cutoffHour = 6, onlyWithRain = false } = {}) {
  const data = await fetchMirrorJson('/api/v1/rainfall-cut/by-basin', {
    cutoff_hour: cutoffHour,
    only_with_rain: onlyWithRain ? 'true' : 'false'
  });

  const basins = data.data || [];
  const rainy = basins.filter(b => (b.lamina_max_24h_mm || 0) > 0);
  const lines = [
    'CORTE DE LLUVIA POR CUENCA · CFE',
    `📅 Corte: ${data.cutoff_date_local || ''} (${String(cutoffHour).padStart(2, '0')}:00 h)`,
    `🕒 Ventana: ${data.window_local || ''}`,
    `🌧️ Cuencas con lluvia: ${data.basins_with_rain || 0} de ${data.basins_count || basins.length}`,
    ''
  ];

  if (rainy.length === 0) {
    lines.push('☀️ Sin precipitaciones registradas en las cuencas CFE durante el corte de 24 horas.');
  } else {
    for (const b of rainy.slice(0, 15)) {
      lines.push(
        `🌧️ *${b.cuenca}*: media ${Number(b.lamina_media_24h_mm).toFixed(1)} mm · máx ${Number(b.lamina_max_24h_mm).toFixed(1)} mm (${b.estacion_max_nombre || 'N/D'}) [${b.estaciones_con_lluvia}/${b.estaciones_con_datos} est.]`
      );
    }
  }

  return {
    basinsCount: data.basins_count || 0,
    basinsWithRain: data.basins_with_rain || 0,
    data: basins,
    mensajeTexto: lines.join('\n').trim()
  };
}

/**
 * Combina el reporte de embalses cada 3h con un bloque compacto de alertas críticas/preventivas activas (EMBALSES/AVENIDAS).
 */
async function buildScheduled3hMessage(crp = null) {
  const rep = await generateEmbalsesReport(crp, 3);
  let msg = rep.mensajeTexto;

  try {
    const al = await getAlertsReport({
      severity: 'CRITICA,ALERTA',
      category: 'EMBALSES,AVENIDAS',
      crp
    });
    if (al && al.total > 0) {
      const top = al.alertas.slice(0, 6);
      const aLines = ['', `🚨 *ALERTAS ACTIVAS (${al.total})*`];
      for (const a of top) {
        const ico = a.severidad === 'CRITICA' ? '🔴' : '🟡';
        aLines.push(`${ico} [${a.crp}] ${a.titulo}`);
        aLines.push(`   ${a.detalle}`);
      }
      msg = `${msg}\n${aLines.join('\n')}`;
    }
  } catch (e) {
    console.warn('[EMBALSES-REPORTER] No se pudieron anexar alertas al reporte 3h:', e.message);
  }

  return { ...rep, mensajeTexto: msg };
}

module.exports = {
  generateEmbalsesReport,
  getAlertsReport,
  getNetworkHealthReport,
  getDailyBulletinReport,
  getRainfallByBasinReport,
  buildScheduled3hMessage,
  detectCrpFromText,
  MIRROR_API_URL
};
