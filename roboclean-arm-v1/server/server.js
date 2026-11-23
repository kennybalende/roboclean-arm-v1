'use strict';
// RoboClean Arm-V1 — serveur de gestion (aucune dépendance : Node >= 22.5, SQLite intégré)
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');

const WEB = path.join(__dirname, '..', 'web');
const MIME = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml', '.json': 'application/json' };
const DEFAULTS = { force_setpoint: 12.5, force_limit: 15, ke: 4000, tank_low: 2, tank_high: 9, speed: 4 };

function openDb(file) {
  if (file !== ':memory:') fs.mkdirSync(path.dirname(file), { recursive: true });
  const db = new DatabaseSync(file);
  db.exec(fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8'));
  const ins = db.prepare('INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)');
  for (const [k, v] of Object.entries(DEFAULTS)) ins.run(k, String(v));
  return db;
}

const send = (res, code, body, type = 'application/json; charset=utf-8') => {
  res.writeHead(code, { 'Content-Type': type, 'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' });
  res.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body));
};
const readJson = (req) => new Promise((ok, ko) => {
  let s = '';
  req.on('data', (c) => { s += c; if (s.length > 100_000) { ko(new Error('Corps trop volumineux')); req.destroy(); } });
  req.on('end', () => { try { ok(s ? JSON.parse(s) : {}); } catch { ko(new Error('JSON invalide')); } });
});
const num = (v, min = -Infinity, max = Infinity) => (Number.isFinite(+v) && +v >= min && +v <= max ? +v : null);

function createApp(db) {
  const q = (sql) => db.prepare(sql);
  const lim = (u, d = 50) => Math.min(Math.max(parseInt(u.searchParams.get('limit')) || d, 1), 500);

  async function api(req, res, u) {
    const p = u.pathname, m = req.method;
    if (p === '/api/health') return send(res, 200, { ok: true });

    if (p === '/api/cycles' && m === 'GET') {
      const mode = u.searchParams.get('mode');
      const rows = mode ? q('SELECT * FROM cycles WHERE mode = ? ORDER BY id DESC LIMIT ?').all(mode, lim(u))
                        : q('SELECT * FROM cycles ORDER BY id DESC LIMIT ?').all(lim(u));
      return send(res, 200, rows);
    }
    if (p === '/api/cycles' && m === 'POST') {
      const b = await readJson(req);
      const mode = ['ind', 'proto'].includes(b.mode) ? b.mode : null;
      const status = ['DONE', 'STO', 'ABORT'].includes(b.status) ? b.status : null;
      const d = num(b.duration_s, 0, 86400), fmax = num(b.fmax ?? 0, 0, 1000), w = num(b.water_ml ?? 0, 0, 1e6);
      const passes = num(b.passes ?? 0, 0, 1000);
      if (!mode || !status || d === null || fmax === null || w === null || passes === null)
        return send(res, 400, { error: 'Champs invalides (mode, status, duration_s, passes, fmax, water_ml)' });
      const notes = String(b.notes ?? '').slice(0, 500), source = String(b.source ?? 'web').slice(0, 30);
      const r = q('INSERT INTO cycles(mode,status,duration_s,passes,fmax,water_ml,source,notes) VALUES (?,?,?,?,?,?,?,?)')
        .run(mode, status, d, passes, fmax, w, source, notes);
      const id = Number(r.lastInsertRowid);
      q('INSERT INTO events(cycle_id,level,message) VALUES (?,?,?)').run(id,
        status === 'STO' ? 'critical' : 'info',
        status === 'STO' ? `Arrêt STO (cycle ${id}) : ${notes || 'cause non précisée'} — Fz max ${fmax.toFixed(1)} N`
                         : `Cycle ${id} terminé (${mode}) en ${d.toFixed(0)} s, Fz max ${fmax.toFixed(1)} N`);
      return send(res, 201, q('SELECT * FROM cycles WHERE id = ?').get(id));
    }
    if (p === '/api/cycles.csv' && m === 'GET') {
      const rows = q('SELECT * FROM cycles ORDER BY id').all();
      const cols = ['id', 'started_at', 'mode', 'status', 'duration_s', 'passes', 'fmax', 'water_ml', 'source', 'notes'];
      const cell = (v) => `"${String(v).replace(/"/g, '""')}"`;
      return send(res, 200, [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n'), 'text/csv; charset=utf-8');
    }
    const one = p.match(/^\/api\/cycles\/(\d+)$/);
    if (one && m === 'GET') {
      const r = q('SELECT * FROM cycles WHERE id = ?').get(+one[1]);
      return r ? send(res, 200, r) : send(res, 404, { error: 'Cycle introuvable' });
    }
    if (one && m === 'DELETE') {
      const r = q('DELETE FROM cycles WHERE id = ?').run(+one[1]);
      return r.changes ? send(res, 200, { deleted: +one[1] }) : send(res, 404, { error: 'Cycle introuvable' });
    }
    if (p === '/api/stats' && m === 'GET') {
      const t = q(`SELECT COUNT(*) AS total, COALESCE(SUM(status='DONE'),0) AS done, COALESCE(SUM(status='STO'),0) AS sto,
        COALESCE(AVG(CASE WHEN status='DONE' THEN duration_s END),0) AS avg_duration,
        COALESCE(MAX(fmax),0) AS fmax_max, COALESCE(SUM(water_ml),0) AS water_ml FROM cycles`).get();
      const byMode = q('SELECT mode, COUNT(*) AS n, AVG(fmax) AS fmax_avg FROM cycles GROUP BY mode').all();
      return send(res, 200, { ...t, success_rate: t.total ? t.done / t.total : null, by_mode: byMode });
    }
    if (p === '/api/events' && m === 'GET') return send(res, 200, q('SELECT * FROM events ORDER BY id DESC LIMIT ?').all(lim(u, 30)));
    if (p === '/api/events' && m === 'POST') {
      const b = await readJson(req), message = String(b.message ?? '').trim().slice(0, 300);
      if (!message || !['info', 'warning', 'critical'].includes(b.level)) return send(res, 400, { error: 'level et message requis' });
      const r = q('INSERT INTO events(level,message) VALUES (?,?)').run(b.level, message);
      return send(res, 201, q('SELECT * FROM events WHERE id = ?').get(Number(r.lastInsertRowid)));
    }
    if (p === '/api/settings' && m === 'GET')
      return send(res, 200, Object.fromEntries(q('SELECT key, value FROM settings').all().map((r) => [r.key, +r.value])));
    if (p === '/api/settings' && m === 'PUT') {
      const b = await readJson(req), up = q('UPDATE settings SET value = ? WHERE key = ?');
      for (const [k, v] of Object.entries(b)) {
        if (!(k in DEFAULTS)) return send(res, 400, { error: `Paramètre inconnu : ${k}` });
        if (num(v) === null) return send(res, 400, { error: `Valeur numérique requise pour ${k}` });
      }
      if (num(b.force_limit ?? 0, 0, 15) === null) return send(res, 400, { error: 'force_limit doit rester ≤ 15 N (CdC)' });
      for (const [k, v] of Object.entries(b)) up.run(String(+v), k);
      return send(res, 200, Object.fromEntries(q('SELECT key, value FROM settings').all().map((r) => [r.key, +r.value])));
    }
    return send(res, 404, { error: 'Route inconnue' });
  }

  function serveStatic(res, pathname) {
    const rel = pathname === '/' ? 'index.html' : decodeURIComponent(pathname).replace(/^\/+/, '');
    const file = path.normalize(path.join(WEB, rel));
    if (!file.startsWith(WEB + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile())
      return send(res, 404, 'Introuvable', 'text/plain; charset=utf-8');
    send(res, 200, fs.readFileSync(file), MIME[path.extname(file)] || 'application/octet-stream');
  }

  return http.createServer(async (req, res) => {
    const u = new URL(req.url, 'http://localhost');
    if (req.method === 'OPTIONS') return send(res, 204, '');
    try {
      if (u.pathname.startsWith('/api/')) await api(req, res, u);
      else serveStatic(res, u.pathname);
    } catch (e) {
      send(res, 400, { error: e.message });
    }
  });
}

module.exports = { openDb, createApp };

if (require.main === module) {
  const port = +process.env.PORT || 3000;
  const dbFile = process.env.DB_PATH || path.join(__dirname, '..', 'data', 'roboclean.db');
  createApp(openDb(dbFile)).listen(port, () => console.log(`RoboClean : http://localhost:${port}  (base : ${dbFile})`));
}
