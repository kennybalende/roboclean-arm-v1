'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { openDb, createApp } = require('../server.js');

let srv, base;
test.before(async () => {
  srv = createApp(openDb(':memory:'));
  await new Promise((r) => srv.listen(0, r));
  base = `http://127.0.0.1:${srv.address().port}`;
});
test.after(() => srv.close());
const call = (p, method = 'GET', body) => fetch(base + p, { method,
  headers: { 'Content-Type': 'application/json' }, body: body && JSON.stringify(body) });

test('enregistre un cycle et crée un événement', async () => {
  const r = await call('/api/cycles', 'POST', { mode: 'ind', status: 'DONE', duration_s: 83.5, passes: 2, fmax: 12.6, water_ml: 138 });
  assert.equal(r.status, 201);
  assert.equal((await r.json()).mode, 'ind');
  const ev = await (await call('/api/events')).json();
  assert.equal(ev[0].level, 'info');
});
test('un STO génère une alerte critique', async () => {
  await call('/api/cycles', 'POST', { mode: 'proto', status: 'STO', duration_s: 12, fmax: 15.4, notes: 'effort > 15 N' });
  const ev = await (await call('/api/events')).json();
  assert.equal(ev[0].level, 'critical');
});
test('rejette les données invalides', async () => {
  assert.equal((await call('/api/cycles', 'POST', { mode: 'x', status: 'DONE', duration_s: 1 })).status, 400);
  assert.equal((await call('/api/cycles', 'POST', { mode: 'ind', status: 'DONE', duration_s: -5 })).status, 400);
});
test('statistiques et export CSV', async () => {
  const s = await (await call('/api/stats')).json();
  assert.equal(s.total, 2); assert.equal(s.sto, 1); assert.equal(s.success_rate, 0.5);
  assert.match(await (await call('/api/cycles.csv')).text(), /^id,started_at/);
});
test('paramètres : limite de force plafonnée à 15 N', async () => {
  assert.equal((await call('/api/settings', 'PUT', { force_limit: 20 })).status, 400);
  assert.equal((await call('/api/settings', 'PUT', { force_setpoint: 12 })).status, 200);
  assert.equal((await (await call('/api/settings')).json()).force_setpoint, 12);
});
test('suppression et sécurité des fichiers statiques', async () => {
  assert.equal((await call('/api/cycles/1', 'DELETE')).status, 200);
  assert.equal((await call('/api/cycles/1')).status, 404);
  assert.equal((await call('/..%2fserver%2fserver.js')).status, 404);
  assert.equal((await call('/')).status, 200);
});
