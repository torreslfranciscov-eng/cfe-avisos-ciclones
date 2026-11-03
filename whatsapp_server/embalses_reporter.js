const fs = require('fs');
const path = require('path');
const { Pool } = require('pg');

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

const pool = new Pool({
  host: process.env.PG_HOST || 'pg-1ab7e72d-torreslfranciscov-58fb.g.aivencloud.com',
  port: parseInt(process.env.PG_PORT || '19960', 10),
  user: process.env.PG_USER || 'avnadmin',
  password: process.env.PG_PASSWORD,
  database: process.env.PG_DATABASE || 'defaultdb',
  ssl: { rejectUnauthorized: false },
  max: 5,
  idleTimeoutMillis: 30000
});

async function generateEmbalsesReport(region = 'SURESTE', deltaHours = 3) {
  const reg = (region || 'SURESTE').trim().toUpperCase();
  const delta = deltaHours > 0 ? deltaHours : 3;

  const damsRes = await pool.query(
    'SELECT * FROM dbo.embalses_catalogo WHERE activo = true AND UPPER(region) = $1 ORDER BY orden',
    [reg]
  );

  const now = new Date();
  // Hora México Central (CST, UTC-6)
  const cstDate = new Date(now.getTime() - 6 * 3600 * 1000);
  const day = String(cstDate.getUTCDate()).padStart(2, '0');
  const month = String(cstDate.getUTCMonth() + 1).padStart(2, '0');
  const year = cstDate.getUTCFullYear();
  const hours = String(cstDate.getUTCHours()).padStart(2, '0');
  const mins = String(cstDate.getUTCMinutes()).padStart(2, '0');

  let text = 'REPORTE DE EMBALSES · CENTRALES HIDROELÉCTRICAS CFE\n' +
             `📅 ${day}/${month}/${year}   ⏰ ${hours}:${mins} h\n\n` +
             `${reg}\n`;

  const presas = [];

  for (const d of damsRes.rows) {
    const latestQuery = `
      SELECT ts, valor
      FROM public.dcp_datos
      WHERE id_asignado = $1 AND sensor_id = $2 AND valido = true
      ORDER BY ts DESC LIMIT 1;
    `;
    const latRes = await pool.query(latestQuery, [d.id_asignado, d.sensor_id]);
    if (!latRes.rows.length) continue;

    const lTs = latRes.rows[0].ts;
    const lVal = parseFloat(latRes.rows[0].valor);
    if (isNaN(lVal)) continue;

    const prevQuery = `
      SELECT ts, valor
      FROM public.dcp_datos
      WHERE id_asignado = $1 AND sensor_id = $2 AND valido = true
        AND ts <= ($3::timestamptz - ($4 || ' hour')::interval)
      ORDER BY ts DESC LIMIT 1;
    `;
    const prevRes = await pool.query(prevQuery, [d.id_asignado, d.sensor_id, lTs, delta]);
    const pVal = prevRes.rows.length ? parseFloat(prevRes.rows[0].valor) : null;
    const dVal = (pVal !== null && !isNaN(pVal)) ? (lVal - pVal) : 0;

    let trend = '➡️';
    let deltaSign = '+0.00';
    if (dVal > 0.005) { trend = '⬆️'; deltaSign = `+${dVal.toFixed(2)}`; }
    else if (dVal < -0.005) { trend = '⬇️'; deltaSign = dVal.toFixed(2); }

    const isSalida = (d.nombre_corto || '').toLowerCase().includes('salida') ||
                     (d.nombre_oficial || '').toLowerCase().includes('salida');

    const cotaNamo = parseFloat(d.cota_namo);
    const deltaNamo = lVal - cotaNamo;
    const deltaNamoSign = deltaNamo >= 0 ? `+${deltaNamo.toFixed(2)}` : deltaNamo.toFixed(2);

    let semaforo = '🟢';
    if (!isSalida) {
      if (deltaNamo >= 0) semaforo = '🔴';
      else if (deltaNamo >= -1.0) semaforo = '🟡';
      else semaforo = '🟢';
    }

    text += `${d.nombre_corto}\n`;
    text += `${semaforo} ${trend} ${lVal.toFixed(2)} msnm\n`;
    text += `Δ ${deltaSign} m en ${delta} horas\n`;
    if (isSalida) {
      text += `*(Nivel medido a la salida · NAMO presa: ${cotaNamo.toFixed(2)} msnm)*\n\n`;
    } else {
      text += `Δ NAMO ${deltaNamoSign} m (${cotaNamo.toFixed(2)} msnm)\n\n`;
    }

    presas.push({
      idAsignado: d.id_asignado,
      nombreCorto: d.nombre_corto,
      cotaNamo: cotaNamo,
      cotaName: parseFloat(d.cota_name),
      nivelActual: lVal,
      timestampActual: lTs,
      delta: dVal,
      deltaHoras: delta,
      deltaNamo: deltaNamo,
      trend,
      semaforo,
      isSalida
    });
  }

  return {
    titulo: 'REPORTE DE EMBALSES · CENTRALES HIDROELÉCTRICAS CFE',
    region: reg,
    mensajeTexto: text.trim(),
    fechaCorte: now.toISOString(),
    presas
  };
}

module.exports = {
  generateEmbalsesReport,
  pool
};

