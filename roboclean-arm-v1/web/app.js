'use strict';
const $ = (id) => document.getElementById(id);
let BASE = new URLSearchParams(location.search).get('api') || localStorage.getItem('rc_api') || location.origin;
const LABELS = { force_setpoint: 'Consigne de force (N)', force_limit: 'Limite STO (N)', ke: 'Raideur Ke (N/m)',
  tank_low: 'Niveau bas tampon (L)', tank_high: 'Niveau haut tampon (L)', speed: 'Vitesse simulation (×)' };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const state = (t, err) => { $('state').textContent = t; $('state').className = err ? 'err' : ''; };
async function api(p, opt) {
  const r = await fetch(BASE + p, { headers: { 'Content-Type': 'application/json' }, ...opt });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
function kpis(s) {
  const k = [['Cycles', s.total], ['Réussis', s.done], ['Arrêts STO', s.sto],
    ['Taux de réussite', s.success_rate == null ? '—' : Math.round(s.success_rate * 100) + ' %'],
    ['Durée moyenne', s.avg_duration ? s.avg_duration.toFixed(0) + ' s' : '—'],
    ['Fz max', s.fmax_max.toFixed(1) + ' N'], ['Eau utilisée', (s.water_ml / 1000).toFixed(2) + ' L']];
  $('kpis').innerHTML = k.map(([a, b]) => `<div class="kpi"><b>${esc(b)}</b><span>${a}</span></div>`).join('');
}
function chart(rows) {
  const d = rows.slice(0, 20).reverse(), Y = (f) => 180 - (f / 18) * 170, w = 560 / Math.max(d.length, 1);
  const bars = d.map((r, i) => `<rect x="${40 + i * w + 2}" y="${Y(r.fmax)}" width="${Math.max(w - 4, 2)}" height="${180 - Y(r.fmax)}" fill="var(${r.status === 'STO' ? '--er' : '--ac'})"><title>Cycle ${r.id} : ${r.fmax.toFixed(1)} N</title></rect>`).join('');
  $('chart').innerHTML = `<line x1="40" x2="600" y1="${Y(15)}" y2="${Y(15)}" stroke="var(--er)" stroke-dasharray="4"/><text x="4" y="${Y(15) + 4}" font-size="11" fill="var(--mut)">15 N</text>
   <line x1="40" x2="600" y1="${Y(12.5)}" y2="${Y(12.5)}" stroke="var(--mut)" stroke-dasharray="4"/><text x="4" y="${Y(12.5) + 4}" font-size="11" fill="var(--mut)">12,5</text>${bars}`;
}
async function load() {
  try {
    const m = $('fmode').value;
    const [s, c, e, st] = await Promise.all([api('/api/stats'), api('/api/cycles?limit=100' + (m ? '&mode=' + m : '')), api('/api/events?limit=30'), api('/api/settings')]);
    state('Connecté à ' + BASE);
    kpis(s); chart(c);
    $('rows').innerHTML = c.map((r) => `<tr><td>${r.id}</td><td>${esc(r.started_at)}</td><td>${r.mode === 'ind' ? 'Industriel' : 'Prototype'}</td>
      <td><span class="tag ${r.status}">${r.status}</span></td><td>${r.duration_s.toFixed(0)} s</td><td>${r.passes}</td><td>${r.fmax.toFixed(1)} N</td>
      <td>${r.water_ml.toFixed(0)} mL</td><td>${esc(r.source)}</td><td>${esc(r.notes)}</td><td><button class="del" data-id="${r.id}" aria-label="Supprimer le cycle ${r.id}">Supprimer</button></td></tr>`).join('')
      || '<tr><td colspan="11">Aucun cycle enregistré. Lancez le simulateur ou le programme Python.</td></tr>';
    $('events').innerHTML = e.map((x) => `<li><span class="tag ${x.level}">${x.level}</span><span>${esc(x.message)}<br><time>${esc(x.ts)}</time></span></li>`).join('') || '<li>Aucune alarme.</li>';
    $('set').innerHTML = Object.entries(st).map(([k, v]) => `<label>${LABELS[k] || k}<input name="${k}" type="number" step="any" value="${v}"></label>`).join('') + '<button class="pri" type="submit" style="align-self:end">Enregistrer</button>';
    $('csv').href = BASE + '/api/cycles.csv';
  } catch (err) {
    state('Serveur injoignable : lancez « npm start » ou saisissez son adresse', true);
  }
}
$('rows').addEventListener('click', async (ev) => {
  const id = ev.target.dataset && ev.target.dataset.id;
  if (id && confirm(`Supprimer définitivement le cycle ${id} ?`)) { await api('/api/cycles/' + id, { method: 'DELETE' }); load(); }
});
$('set').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const body = Object.fromEntries(new FormData(ev.target));
  try { await api('/api/settings', { method: 'PUT', body: JSON.stringify(body) }); state('Paramètres enregistrés'); }
  catch (err) { state('Erreur : ' + err.message, true); }
});
$('conn').onclick = () => { BASE = $('api').value.replace(/\/$/, '') || location.origin; localStorage.setItem('rc_api', BASE); load(); };
$('fmode').onchange = load;
$('api').value = BASE;
load(); setInterval(load, 15000);
