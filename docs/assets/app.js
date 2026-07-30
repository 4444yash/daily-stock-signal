/* Squeeze Breakout Signals — dashboard renderer.
   Reads docs/data/dashboard_data.json, produced by build_dashboard.py on every scan. */

const RAW_FALLBACK =
  'https://raw.githubusercontent.com/4444yash/daily-stock-signal/main/docs/data/dashboard_data.json';

const charts = {};
let DATA = null;

/* ---------------- formatting helpers ---------------- */

const has = v => v !== null && v !== undefined && v !== '';

function pct(v, digits = 2, signed = true) {
  if (!has(v)) return '—';
  const n = Number(v);
  const sign = signed && n > 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}%`;
}

function num(v, digits = 2) {
  if (!has(v)) return '—';
  return Number(v).toLocaleString('en-IN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function cls(v) {
  if (!has(v)) return 'dim';
  return Number(v) > 0 ? 'pos' : Number(v) < 0 ? 'neg' : 'dim';
}

function shortDate(s) {
  if (!s) return '—';
  const d = new Date(`${s}T00:00:00`);
  if (isNaN(d)) return s;
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', yy: undefined, year: '2-digit' });
}

function monthLabel(s) {
  if (!s) return '—';
  const [y, m] = s.split('-');
  const d = new Date(Number(y), Number(m) - 1, 1);
  return d.toLocaleDateString('en-GB', { month: 'short', year: '2-digit' });
}

function daysAgo(dateStr) {
  if (!dateStr) return null;
  const d = new Date(`${dateStr}T00:00:00`);
  if (isNaN(d)) return null;
  return Math.floor((Date.now() - d.getTime()) / 86400000);
}

function el(html) {
  const t = document.createElement('template');
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function set(id, html) {
  const node = document.getElementById(id);
  if (node) node.innerHTML = html;
}

/* ---------------- small builders ---------------- */

function kpi({ label, value, note, tone }) {
  const t = tone === 'auto' ? cls(value) : tone || 'neutral';
  const toneClass = t === 'pos' ? 'pos' : t === 'neg' ? 'neg' : 'neutral';
  return `<div class="kpi ${toneClass}">
    <div class="k-label">${label}</div>
    <div class="k-value">${value}</div>
    ${note ? `<div class="k-note">${note}</div>` : ''}
  </div>`;
}

function statRows(rows) {
  return rows
    .map(([k, v, tone]) =>
      `<li><span class="s-key">${k}</span><span class="s-val ${tone || ''}">${v}</span></li>`)
    .join('');
}

function table(cols, rows, opts = {}) {
  if (!rows.length) {
    return `<p class="empty">${opts.empty || 'Nothing to show yet.'}</p>`;
  }
  const head = cols
    .map((c, i) => `<th class="${c.sort === false ? '' : 'sortable'}" data-i="${i}" data-type="${c.type || 'text'}">${c.label}</th>`)
    .join('');
  const body = rows
    .map(r => `<tr>${r.map(c => `<td class="${c.cls || ''}">${c.html}</td>`).join('')}</tr>`)
    .join('');
  return `<div class="t-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/** Attach click-to-sort to every rendered table inside a container. */
function makeSortable(container) {
  container.querySelectorAll('table').forEach(tbl => {
    tbl.querySelectorAll('th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const i = Number(th.dataset.i);
        const numeric = th.dataset.type === 'num';
        const dir = th.dataset.dir === 'desc' ? 'asc' : 'desc';
        tbl.querySelectorAll('th').forEach(h => h.removeAttribute('data-dir'));
        th.dataset.dir = dir;

        const tbody = tbl.querySelector('tbody');
        const rows = [...tbody.rows];
        rows.sort((a, b) => {
          const av = a.cells[i]?.textContent.trim() ?? '';
          const bv = b.cells[i]?.textContent.trim() ?? '';
          if (numeric) {
            const an = parseFloat(av.replace(/[^0-9.\-]/g, ''));
            const bn = parseFloat(bv.replace(/[^0-9.\-]/g, ''));
            const aa = isNaN(an) ? -Infinity : an;
            const bb = isNaN(bn) ? -Infinity : bn;
            return dir === 'asc' ? aa - bb : bb - aa;
          }
          return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
        });
        rows.forEach(r => tbody.appendChild(r));
      });
    });
  });
}

/* ---------------- chart defaults ---------------- */

function chartTheme() {
  Chart.defaults.color = '#93a2bd';
  Chart.defaults.font.family =
    'Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif';
  Chart.defaults.font.size = 11;
  Chart.defaults.animation.duration = 450;
}

function grid() {
  return { color: 'rgba(255,255,255,.055)', drawTicks: false };
}

function draw(id, config) {
  const canvas = document.getElementById(id);
  if (!canvas) return;
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(canvas, config);
}

function equityChart(id, curve) {
  const points = [{ x: 'Start', equity: 100, label: 'Start' }].concat(
    curve.map(p => ({ x: p.date, equity: p.equity, label: `${p.symbol} ${pct(p.pnl)}` }))
  );
  draw(id, {
    type: 'line',
    data: {
      labels: points.map((p, i) => (i === 0 ? 'Start' : shortDate(p.x))),
      datasets: [{
        data: points.map(p => p.equity),
        borderColor: '#5b9dff',
        borderWidth: 2,
        fill: true,
        backgroundColor: ctx => {
          const { chart } = ctx;
          if (!chart.chartArea) return 'rgba(91,157,255,.12)';
          const g = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
          g.addColorStop(0, 'rgba(91,157,255,.32)');
          g.addColorStop(1, 'rgba(91,157,255,0)');
          return g;
        },
        pointRadius: points.length > 60 ? 0 : 3,
        pointHoverRadius: 5,
        pointBackgroundColor: '#5b9dff',
        tension: 0.18,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            afterLabel: c => points[c.dataIndex].label,
            label: c => `Equity ${num(c.parsed.y)} (base 100)`,
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
        y: { grid: grid(), ticks: { callback: v => num(v, 0) } },
      },
    },
  });
}

function monthlyChart(id, monthly) {
  draw(id, {
    type: 'bar',
    data: {
      labels: monthly.map(m => monthLabel(m.month)),
      datasets: [{
        data: monthly.map(m => m.return_pct),
        backgroundColor: monthly.map(m => (m.return_pct >= 0 ? 'rgba(49,209,138,.75)' : 'rgba(255,107,122,.75)')),
        borderRadius: 4,
        borderSkipped: false,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: c => `${pct(c.parsed.y)} · ${monthly[c.dataIndex].trades} trades, ${monthly[c.dataIndex].wins} won`,
          },
        },
      },
      scales: {
        x: { grid: { display: false } },
        y: { grid: grid(), ticks: { callback: v => `${v}%` } },
      },
    },
  });
}

/* ---------------- panels ---------------- */

function allocationNote(d) {
  const a = num(d.config.allocation_pct, 0);
  const slots = Math.round(100 / d.config.allocation_pct);
  return `Base 100. Each position is sized at ${a}% of equity (${slots} concurrent slots), `
       + `sequenced by exit date, net of a ${num(d.config.cost_pct, 2)}% round-trip cost. `
       + `Signals overlap in time, so full-capital compounding would overstate the result.`;
}

function renderHeader(d) {
  const stale = daysAgo(d.as_of);
  set('as-of', `Data as of ${shortDate(d.as_of)}`);
  const built = d.generated_at_utc
    ? new Date(d.generated_at_utc).toLocaleString('en-GB', {
        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
      })
    : '—';
  set('built-at', `Built ${built}`);
  const dot = document.getElementById('live-dot');
  if (stale !== null && stale > 4) dot.classList.add('stale');
  set('footer-stamp',
    `Universe ${d.universe.total} symbols · ${d.activity.total_runs} recorded scans · payload generated ${built} UTC.`);
}

function renderOverview(d) {
  const h = d.headline;
  const live = d.live;
  set('kpis', [
    kpi({ label: 'Open positions', value: h.open_positions, tone: 'neutral',
          note: `${d.universe.total} symbols scanned daily` }),
    kpi({ label: 'Open P&L', value: pct(h.unrealized_pct), tone: 'auto',
          note: 'sum of unrealised returns' }),
    kpi({ label: 'Closed trades', value: h.closed_trades, tone: 'neutral',
          note: live.first_exit ? `since ${shortDate(live.first_exit)}` : 'none yet' }),
    kpi({ label: 'Win rate', value: has(h.win_rate) ? `${num(h.win_rate, 1)}%` : '—', tone: 'neutral',
          note: `${live.wins}W / ${live.losses}L` }),
    kpi({ label: 'Expectancy', value: pct(h.expectancy), tone: 'auto',
          note: 'avg net return per trade' }),
    kpi({ label: 'Profit factor', value: has(h.profit_factor) ? num(h.profit_factor) : '—', tone: 'neutral',
          note: 'gross wins / gross losses' }),
    kpi({ label: 'Realised return', value: pct(h.realized_return_pct), tone: 'auto',
          note: `on a ${num(d.config.allocation_pct, 0)}%-per-trade portfolio` }),
    kpi({ label: 'Max drawdown', value: pct(h.max_drawdown_pct), tone: has(h.max_drawdown_pct) && h.max_drawdown_pct < 0 ? 'neg' : 'neutral',
          note: 'peak to trough of equity curve' }),
  ].join(''));

  set('equity-hint', allocationNote(d));

  const hasCurve = live.equity_curve.length > 0;
  document.getElementById('equity-empty').hidden = hasCurve;
  document.getElementById('chart-equity').closest('.chart-wrap').hidden = !hasCurve;
  if (hasCurve) equityChart('chart-equity', live.equity_curve);

  const hasMonthly = live.monthly.length > 0;
  document.getElementById('monthly-empty').hidden = hasMonthly;
  document.getElementById('chart-monthly').closest('.chart-wrap').hidden = !hasMonthly;
  if (hasMonthly) monthlyChart('chart-monthly', live.monthly);

  set('split-stats', statRows([
    ['Realised (closed)', pct(h.realized_return_pct), cls(h.realized_return_pct)],
    ['Unrealised (open)', pct(h.unrealized_pct), cls(h.unrealized_pct)],
    ['Best trade', pct(h.best), cls(h.best)],
    ['Worst trade', pct(h.worst), cls(h.worst)],
    ['Avg win', pct(live.avg_win), 'pos'],
    ['Avg loss', pct(live.avg_loss), 'neg'],
    ['Payoff ratio', has(live.payoff_ratio) ? `${num(live.payoff_ratio)} : 1` : '—'],
    ['Avg hold', has(h.avg_hold_days) ? `${num(h.avg_hold_days, 1)} days` : '—'],
    ['Longest win streak', live.max_win_streak],
    ['Longest loss streak', live.max_loss_streak],
  ]));

  const a = d.activity;
  set('activity-stats', statRows([
    ['Last scan', shortDate(a.last_run)],
    ['Scans on record', a.total_runs],
    ['With detailed logs', a.logged_runs],
    ['Technical triggers', a.total_triggers],
    ['Cleared model gate', a.total_taken],
    ['Gate pass rate', has(a.gate_pass_rate) ? `${num(a.gate_pass_rate, 1)}%` : '—'],
    ['Score threshold', `${(d.config.prob_threshold * 100).toFixed(0)}%`],
    ['Cost assumption', `${num(d.config.cost_pct, 2)}% round trip`],
  ]));

  renderOpenTable('overview-open', d.open, true);
}

function positionRow(p) {
  const probPct = has(p.prob) ? Math.round(p.prob * 100) : null;
  const riskTone = p.risk_state === 'Profit locked' ? 'win'
    : p.risk_state === 'Break-even stop' ? 'info' : 'warn';
  return [
    { html: `<span class="sym">${p.symbol}</span>`, cls: '' },
    { html: shortDate(p.entry_date), cls: 'dim' },
    { html: has(p.days_held) ? `${p.days_held}d` : '—', cls: 'dim' },
    { html: num(p.entry_price), cls: 'mono' },
    { html: num(p.latest_price), cls: 'mono' },
    { html: pct(p.day_change_pct), cls: cls(p.day_change_pct) },
    { html: `<strong>${pct(p.unrealized_pct)}</strong>`, cls: cls(p.unrealized_pct) },
    { html: has(p.r_multiple) ? `${num(p.r_multiple, 2)}R` : '—', cls: cls(p.r_multiple) },
    { html: num(p.current_stop), cls: 'mono' },
    { html: pct(p.stop_distance_pct, 1, false), cls: 'dim' },
    { html: `<span class="pill ${riskTone}">${pct(p.locked_pct)}</span>`, cls: '' },
    { html: probPct === null ? '—' :
        `<span class="meter"><span class="meter-bar"><span class="meter-fill" style="width:${probPct}%"></span></span>${probPct}%</span>`,
      cls: '' },
  ];
}

function renderOpenTable(targetId, positions, compact) {
  const cols = [
    { label: 'Symbol' },
    { label: 'Entry date' },
    { label: 'Held', type: 'num' },
    { label: 'Entry', type: 'num' },
    { label: 'Last', type: 'num' },
    { label: 'Day', type: 'num' },
    { label: 'Unrealised', type: 'num' },
    { label: 'R', type: 'num' },
    { label: 'Stop', type: 'num' },
    { label: 'To stop', type: 'num' },
    { label: 'Locked', type: 'num' },
    { label: 'Score', type: 'num' },
  ];
  const target = document.getElementById(targetId);
  target.innerHTML = table(cols, positions.map(positionRow), {
    empty: 'No open positions. The scanner is flat and waiting for the next qualifying setup.',
  });
  makeSortable(target);
}

function renderOpen(d) {
  const ps = d.open;
  const total = ps.reduce((s, p) => s + (p.unrealized_pct || 0), 0);
  const winners = ps.filter(p => (p.unrealized_pct || 0) > 0).length;
  const locked = ps.filter(p => p.risk_state === 'Profit locked').length;
  const avgHold = ps.length
    ? ps.reduce((s, p) => s + (p.days_held || 0), 0) / ps.length : null;

  set('open-kpis', [
    kpi({ label: 'Positions', value: ps.length, tone: 'neutral' }),
    kpi({ label: 'Aggregate open P&L', value: pct(total), tone: 'auto' }),
    kpi({ label: 'In profit', value: `${winners} / ${ps.length}`, tone: 'neutral' }),
    kpi({ label: 'Stop above entry', value: `${locked} / ${ps.length}`, tone: 'neutral',
          note: 'downside already removed' }),
    kpi({ label: 'Avg days held', value: has(avgHold) ? num(avgHold, 0) : '—', tone: 'neutral' }),
  ].join(''));

  renderOpenTable('open-table', ps, false);

  const cards = ps.map(p => {
    // Where price sits between the stop and the running peak.
    const lo = p.current_stop, hi = p.peak_price, cur = p.latest_price;
    let fill = 0;
    if (has(lo) && has(hi) && hi > lo) fill = Math.max(0, Math.min(100, (cur - lo) / (hi - lo) * 100));
    const tone = (p.unrealized_pct || 0) >= 0 ? 'var(--up)' : 'var(--down)';
    return `<div class="pos-card">
      <div class="pos-card-head">
        <span class="sym">${p.symbol}</span>
        <span class="pnl ${cls(p.unrealized_pct)}">${pct(p.unrealized_pct)}</span>
      </div>
      <ul class="pos-rows">
        <li><span>Entry</span><b>${num(p.entry_price)} · ${shortDate(p.entry_date)}</b></li>
        <li><span>Last close</span><b>${num(p.latest_price)}</b></li>
        <li><span>Trailing stop</span><b>${num(p.current_stop)}</b></li>
        <li><span>Peak seen</span><b>${num(p.peak_price)}</b></li>
        <li><span>Off peak</span><b class="${cls(p.drawdown_from_peak_pct)}">${pct(p.drawdown_from_peak_pct)}</b></li>
        <li><span>Risk state</span><b>${p.risk_state}</b></li>
        <li><span>Model score</span><b>${has(p.prob) ? `${(p.prob * 100).toFixed(1)}%` : '—'}</b></li>
        <li><span>Batch</span><b>${p.batch || '—'}</b></li>
      </ul>
      <div class="ladder">
        <div class="ladder-track"><span class="ladder-fill" style="left:0;width:${fill}%;background:${tone}"></span></div>
        <div class="ladder-labels"><span>stop ${num(p.current_stop, 0)}</span><span>peak ${num(p.peak_price, 0)}</span></div>
      </div>
    </div>`;
  }).join('');
  set('open-cards', cards || '<p class="empty">No open positions.</p>');
}

function tradeRow(t) {
  const win = (t.net_pnl_pct || 0) > 0;
  const probPct = has(t.prob) ? (t.prob * 100).toFixed(0) : null;
  return [
    { html: `<span class="sym">${t.symbol}</span>` },
    { html: shortDate(t.entry_date), cls: 'dim' },
    { html: shortDate(t.exit_date), cls: 'dim' },
    { html: has(t.hold_days) ? `${t.hold_days}d` : '—', cls: 'dim' },
    { html: num(t.entry_price), cls: 'mono' },
    { html: num(t.exit_price), cls: 'mono' },
    { html: `<strong>${pct(t.net_pnl_pct)}</strong>`, cls: cls(t.net_pnl_pct) },
    { html: probPct === null ? '—' : `${probPct}%` },
    { html: `<span class="pill mute">${t.reason || '—'}</span>` },
    { html: `<span class="pill ${win ? 'win' : 'loss'}">${win ? 'Win' : 'Loss'}</span>` },
  ];
}

function historyCols() {
  return [
    { label: 'Symbol' },
    { label: 'Entry' },
    { label: 'Exit' },
    { label: 'Held', type: 'num' },
    { label: 'Entry px', type: 'num' },
    { label: 'Exit px', type: 'num' },
    { label: 'Net P&L', type: 'num' },
    { label: 'Score', type: 'num' },
    { label: 'Reason' },
    { label: 'Result' },
  ];
}

function activeHistorySet() {
  const src = document.getElementById('hist-source').value;
  if (src === 'backtest' && DATA.backtest) return DATA.backtest;
  return DATA.live;
}

function renderHistoryTable() {
  const stats = activeHistorySet();
  const q = document.getElementById('hist-search').value.trim().toUpperCase();
  const f = document.getElementById('hist-filter').value;

  let rows = stats.trades.slice().reverse();
  if (q) rows = rows.filter(t => (t.symbol || '').toUpperCase().includes(q));
  if (f === 'win') rows = rows.filter(t => (t.net_pnl_pct || 0) > 0);
  if (f === 'loss') rows = rows.filter(t => (t.net_pnl_pct || 0) <= 0);

  const target = document.getElementById('hist-table');
  target.innerHTML = table(historyCols(), rows.map(tradeRow), {
    empty: q || f !== 'all'
      ? 'No trades match this filter.'
      : 'No closed trades in this ledger yet. Positions are logged here the day their trailing stop triggers.',
  });
  makeSortable(target);
}

function distributionChart(id, trades) {
  const buckets = [
    { label: '< -15%', lo: -Infinity, hi: -15 },
    { label: '-15 to -8', lo: -15, hi: -8 },
    { label: '-8 to -3', lo: -8, hi: -3 },
    { label: '-3 to 0', lo: -3, hi: 0 },
    { label: '0 to 5', lo: 0, hi: 5 },
    { label: '5 to 15', lo: 5, hi: 15 },
    { label: '15 to 30', lo: 15, hi: 30 },
    { label: '> 30%', lo: 30, hi: Infinity },
  ];
  const counts = buckets.map(b =>
    trades.filter(t => (t.net_pnl_pct ?? 0) > b.lo && (t.net_pnl_pct ?? 0) <= b.hi).length);
  draw(id, {
    type: 'bar',
    data: {
      labels: buckets.map(b => b.label),
      datasets: [{
        data: counts,
        backgroundColor: buckets.map(b => (b.hi <= 0 ? 'rgba(255,107,122,.75)' : 'rgba(49,209,138,.75)')),
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => `${c.parsed.y} trade${c.parsed.y === 1 ? '' : 's'}` } },
      },
      scales: {
        x: { grid: { display: false } },
        y: { grid: grid(), ticks: { precision: 0 }, beginAtZero: true },
      },
    },
  });
}

function renderHistory(d) {
  const s = d.live;
  set('hist-kpis', [
    kpi({ label: 'Closed trades', value: s.count, tone: 'neutral',
          note: s.first_exit ? `${shortDate(s.first_exit)} → ${shortDate(s.last_exit)}` : '' }),
    kpi({ label: 'Win rate', value: has(s.win_rate) ? `${num(s.win_rate, 1)}%` : '—', tone: 'neutral',
          note: `${s.wins}W / ${s.losses}L` }),
    kpi({ label: 'Avg win', value: pct(s.avg_win), tone: 'pos' }),
    kpi({ label: 'Avg loss', value: pct(s.avg_loss), tone: 'neg' }),
    kpi({ label: 'Payoff ratio', value: has(s.payoff_ratio) ? `${num(s.payoff_ratio)} : 1` : '—', tone: 'neutral',
          note: 'avg win / avg loss' }),
    kpi({ label: 'Cumulative P&L', value: pct(s.cumulative_pnl_pct), tone: 'auto',
          note: 'sum of trade returns' }),
    kpi({ label: 'Current streak', value: s.current_streak === 0 ? '—'
            : `${Math.abs(s.current_streak)} ${s.current_streak > 0 ? 'wins' : 'losses'}`,
          tone: s.current_streak > 0 ? 'pos' : s.current_streak < 0 ? 'neg' : 'neutral' }),
    kpi({ label: 'Avg hold', value: has(s.avg_hold_days) ? `${num(s.avg_hold_days, 1)}d` : '—', tone: 'neutral' }),
  ].join(''));

  distributionChart('chart-dist', s.trades);

  set('exit-reasons', s.exit_reasons.length
    ? statRows(s.exit_reasons.map(r => [r.reason, r.count]))
    : '<li><span class="s-key">No exits recorded yet</span></li>');

  const batchTarget = document.getElementById('batch-table');
  batchTarget.innerHTML = table(
    [{ label: 'Batch' }, { label: 'Trades', type: 'num' }, { label: 'Win rate', type: 'num' },
     { label: 'Avg P&L', type: 'num' }, { label: 'Total P&L', type: 'num' }],
    s.batches.map(b => [
      { html: b.batch },
      { html: b.trades },
      { html: `${num(b.win_rate, 1)}%` },
      { html: pct(b.avg_pnl), cls: cls(b.avg_pnl) },
      { html: pct(b.total_pnl), cls: cls(b.total_pnl) },
    ]),
    { empty: 'Batch breakdown appears once trades close.' });
  makeSortable(batchTarget);

  const integrity = d.integrity || [];
  document.getElementById('integrity-card').hidden = integrity.length === 0;
  if (integrity.length) {
    set('integrity-table', table(
      [{ label: 'Symbol', sort: false }, { label: 'Date', sort: false },
       { label: 'Recorded P&L', sort: false }, { label: 'Classification', sort: false },
       { label: 'Detail', sort: false }],
      integrity.map(r => [
        { html: `<span class="sym">${r.symbol}</span>` },
        { html: shortDate(r.exit_date), cls: 'dim' },
        { html: has(r.pnl_pct) ? pct(r.pnl_pct) : '—', cls: cls(r.pnl_pct) },
        { html: `<span class="pill warn">${r.reason || '—'}</span>` },
        { html: `<span class="dim" style="white-space:normal;display:block;max-width:52ch">${r.detail}</span>` },
      ])));
  }

  renderHistoryTable();
}

function renderModel(d) {
  const a = d.activity;
  const s = d.live;
  const bt = d.backtest;

  set('model-kpis', [
    kpi({ label: 'Score threshold', value: `${(d.config.prob_threshold * 100).toFixed(0)}%`, tone: 'neutral',
          note: 'minimum to take a trade' }),
    kpi({ label: 'Triggers seen', value: a.total_triggers, tone: 'neutral',
          note: 'technical setups, all scans' }),
    kpi({ label: 'Signals taken', value: a.total_taken, tone: 'neutral',
          note: 'cleared the gate' }),
    kpi({ label: 'Gate pass rate', value: has(a.gate_pass_rate) ? `${num(a.gate_pass_rate, 1)}%` : '—',
          tone: 'neutral', note: 'how selective the model is' }),
    kpi({ label: 'Live hit rate', value: has(s.win_rate) ? `${num(s.win_rate, 1)}%` : '—', tone: 'neutral',
          note: `on ${s.count} closed trades` }),
    kpi({ label: 'Backtest hit rate', value: bt && has(bt.win_rate) ? `${num(bt.win_rate, 1)}%` : '—',
          tone: 'neutral', note: bt ? `on ${bt.count} historical trades` : 'no backtest loaded' }),
  ].join(''));

  // Calibration prefers the larger sample so the chart is meaningful from day one.
  const calibSource = (s.prob_buckets.length >= 3 || !bt) ? s : bt;
  const buckets = calibSource.prob_buckets;
  const canvasWrap = document.getElementById('chart-calib').closest('.chart-wrap');
  canvasWrap.hidden = buckets.length === 0;
  if (buckets.length) {
    draw('chart-calib', {
      type: 'bar',
      data: {
        labels: buckets.map(b => b.range),
        datasets: [
          {
            label: 'Realised win rate %',
            data: buckets.map(b => b.win_rate),
            backgroundColor: 'rgba(91,157,255,.8)',
            borderRadius: 4,
            yAxisID: 'y',
          },
          {
            label: 'Avg net return %',
            data: buckets.map(b => b.avg_pnl),
            type: 'line',
            borderColor: '#31d18a',
            backgroundColor: '#31d18a',
            borderWidth: 2,
            pointRadius: 4,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
          tooltip: { callbacks: { afterBody: c => `${buckets[c[0].dataIndex].trades} trades` } },
        },
        scales: {
          x: { grid: { display: false }, title: { display: true, text: 'model score at entry' } },
          y: { position: 'left', grid: grid(), ticks: { callback: v => `${v}%` }, beginAtZero: true },
          y1: { position: 'right', grid: { display: false }, ticks: { callback: v => `${v}%` } },
        },
      },
    });
  }

  const calibTarget = document.getElementById('calib-table');
  calibTarget.innerHTML = `${buckets.length ? `<p class="hint">Sample: ${calibSource.label}.</p>` : ''}` + table(
    [{ label: 'Score band' }, { label: 'Trades', type: 'num' }, { label: 'Avg score', type: 'num' },
     { label: 'Win rate', type: 'num' }, { label: 'Avg net P&L', type: 'num' }],
    buckets.map(b => [
      { html: b.range },
      { html: b.trades },
      { html: `${num(b.avg_prob, 1)}%` },
      { html: `${num(b.win_rate, 1)}%` },
      { html: pct(b.avg_pnl), cls: cls(b.avg_pnl) },
    ]),
    { empty: 'Calibration needs closed trades. It fills in as positions exit.' });
  makeSortable(calibTarget);

  const runs = a.runs;
  const funnelWrap = document.getElementById('chart-funnel').closest('.chart-wrap');
  const funnelNote = document.getElementById('funnel-note');
  funnelWrap.hidden = runs.length === 0;
  if (runs.length === 0) {
    funnelNote.hidden = false;
    funnelNote.textContent =
      `Detailed scan logging starts with the next run. ${a.total_runs} earlier runs are known from `
      + `the commit history but did not record trigger counts.`;
  } else {
    funnelNote.hidden = true;
  }
  if (runs.length) draw('chart-funnel', {
    type: 'bar',
    data: {
      labels: runs.map(r => shortDate(r.date)),
      datasets: [
        { label: 'Triggers', data: runs.map(r => r.triggers || 0), backgroundColor: 'rgba(147,162,189,.55)', borderRadius: 3 },
        { label: 'Taken', data: runs.map(r => r.signals_taken || 0), backgroundColor: 'rgba(49,209,138,.85)', borderRadius: 3 },
        { label: 'Exits', data: runs.map(r => r.exits || 0), backgroundColor: 'rgba(255,107,122,.85)', borderRadius: 3 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10 } } },
      scales: {
        x: { stacked: false, grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        y: { grid: grid(), beginAtZero: true, ticks: { precision: 0 } },
      },
    },
  });

  set('model-card', statRows([
    ['Algorithm', 'XGBoost classifier'],
    ['Features', '12'],
    ['Class weighting', 'Asymmetric'],
    ['Decision threshold', `${(d.config.prob_threshold * 100).toFixed(0)}%`],
    ['Retraining', 'Manual, offline'],
    ['Scan cadence', 'Weekdays 16:15 IST'],
    ['Universe size', d.universe.total],
  ]));

  const rejTarget = document.getElementById('rejected-table');
  rejTarget.innerHTML = table(
    [{ label: 'Symbol' }, { label: 'Trigger date' }, { label: 'Close', type: 'num' }, { label: 'Score', type: 'num' }, { label: 'Outcome' }],
    a.recent_rejected.map(r => [
      { html: `<span class="sym">${r.symbol}</span>` },
      { html: shortDate(r.date), cls: 'dim' },
      { html: num(r.close_price), cls: 'mono' },
      { html: `${(r.prob * 100).toFixed(1)}%` },
      { html: '<span class="pill mute">Skipped</span>' },
    ]),
    { empty: 'No rejected triggers recorded in the logged scans so far.' });
  makeSortable(rejTarget);
}

function renderBacktest(d) {
  const bt = d.backtest;
  const notice = document.getElementById('bt-notice');

  if (!bt) {
    notice.innerHTML = '<strong>No backtest loaded.</strong> Run <code>python build_backtest.py --years 4</code> and commit <code>backtest_history.json</code> to populate this tab.';
    set('bt-kpis', '');
    ['chart-bt-equity', 'chart-bt-monthly'].forEach(id => {
      const w = document.getElementById(id).closest('.chart-wrap');
      if (w) w.hidden = true;
    });
    set('compare-table', '<p class="empty">Nothing to compare.</p>');
    return;
  }

  const m = bt.meta || {};
  notice.innerHTML = `<strong>${bt.label}.</strong> ${m.symbols_scanned || '—'} symbols, `
    + `${m.technical_triggers || '—'} technical triggers, ${m.passed_model_gate || '—'} cleared the `
    + `${((m.prob_threshold ?? d.config.prob_threshold) * 100).toFixed(0)}% gate. ${m.note || ''}`;

  set('bt-kpis', [
    kpi({ label: 'Trades', value: bt.count, tone: 'neutral',
          note: bt.first_exit ? `${shortDate(bt.first_exit)} → ${shortDate(bt.last_exit)}` : '' }),
    kpi({ label: 'Win rate', value: has(bt.win_rate) ? `${num(bt.win_rate, 1)}%` : '—', tone: 'neutral',
          note: `${bt.wins}W / ${bt.losses}L` }),
    kpi({ label: 'Expectancy', value: pct(bt.expectancy), tone: 'auto' }),
    kpi({ label: 'Profit factor', value: has(bt.profit_factor) ? num(bt.profit_factor) : '—', tone: 'neutral' }),
    kpi({ label: 'Compounded return', value: pct(bt.total_return_pct), tone: 'auto' }),
    kpi({ label: 'Max drawdown', value: pct(bt.max_drawdown_pct), tone: 'neg' }),
    kpi({ label: 'Payoff ratio', value: has(bt.payoff_ratio) ? `${num(bt.payoff_ratio)} : 1` : '—', tone: 'neutral' }),
    kpi({ label: 'Avg hold', value: has(bt.avg_hold_days) ? `${num(bt.avg_hold_days, 1)}d` : '—', tone: 'neutral' }),
  ].join(''));

  set('bt-equity-hint',
    `Same indicators, same model, same trailing stop, applied to historical data. ${allocationNote(d)}`);
  equityChart('chart-bt-equity', bt.equity_curve);
  monthlyChart('chart-bt-monthly', bt.monthly);

  const rows = [
    ['Trades', d.live.count, bt.count],
    ['Win rate', has(d.live.win_rate) ? `${num(d.live.win_rate, 1)}%` : '—', `${num(bt.win_rate, 1)}%`],
    ['Expectancy', pct(d.live.expectancy), pct(bt.expectancy)],
    ['Profit factor', has(d.live.profit_factor) ? num(d.live.profit_factor) : '—',
      has(bt.profit_factor) ? num(bt.profit_factor) : '—'],
    ['Avg win', pct(d.live.avg_win), pct(bt.avg_win)],
    ['Avg loss', pct(d.live.avg_loss), pct(bt.avg_loss)],
    ['Avg hold', has(d.live.avg_hold_days) ? `${num(d.live.avg_hold_days, 1)}d` : '—',
      `${num(bt.avg_hold_days, 1)}d`],
    ['Max drawdown', pct(d.live.max_drawdown_pct), pct(bt.max_drawdown_pct)],
  ];
  set('compare-table', table(
    [{ label: 'Metric', sort: false }, { label: 'Live', sort: false }, { label: 'Backtest', sort: false }],
    rows.map(r => [{ html: r[0] }, { html: r[1] }, { html: r[2] }])));
}

function renderStrategy(d) {
  const c = d.config;
  set('strategy-prose', `
    <p>The system hunts for volatility compression that resolves upward: a stock coils into a tight
    Bollinger squeeze, then breaks out on a volume surge with momentum confirming. A machine-learning
    model scores each breakout and only the strongest are taken.</p>

    <h3>Entry — all four must fire on the same day</h3>
    <ol>
      <li><strong>Squeeze.</strong> Bollinger band width below 10% at some point in the last five sessions.</li>
      <li><strong>Volume expansion.</strong> Volume above 1.5&times; the 20-day average.</li>
      <li><strong>Momentum ignition.</strong> RSI(14) between 55 and 70 and rising more than 8 points in a day.</li>
      <li><strong>Structure.</strong> A new 20-day high.</li>
    </ol>

    <h3>The model gate</h3>
    <p>Every technical trigger is scored by an ${c.model.toLowerCase()}. Features cover the squeeze
    (band width, days compressed), the breakout (volume multiple, close position in range, RSI level
    and change), volatility (ATR%), trend context (distance from the 50-day average), market regime
    (Nifty trend and its distance from its own 50-day average) and relative strength plus prior 90-day
    run-up. Only setups scoring <strong>${(c.prob_threshold * 100).toFixed(0)}% or higher</strong>
    become positions, which is why most triggers never turn into trades.</p>

    <h3>Exit</h3>
    <p>${c.exit_rule}. The stop only ever ratchets upward. There is no profit target and no time limit,
    so winners are allowed to run and losers are cut mechanically.</p>

    <h3>Automation</h3>
    <p>${c.schedule}. Each run pulls fresh daily bars, recomputes indicators, updates every open stop,
    logs any exit to the permanent ledger, scores new triggers, pushes a phone alert, and commits the
    updated state back to the repository. This dashboard is rebuilt from that committed state, so what
    you see is exactly what the automation recorded, with no manual editing in between.</p>
  `);

  set('universe-stats', statRows([
    ['Symbols tracked', d.universe.total],
    ['Watchlist updated', shortDate(d.universe.updated)],
    ...d.universe.batches.map(b => [b.batch, b.count]),
    ['Cost assumption', `${num(d.config.cost_pct, 2)}%`],
  ]));
}

/* ---------------- tabs ---------------- */

function initTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function select(tab) {
    tabs.forEach(t => {
      const on = t === tab;
      t.setAttribute('aria-selected', String(on));
      document.getElementById(`panel-${t.dataset.panel}`).classList.toggle('active', on);
    });
    // Charts sized while hidden need a nudge once visible.
    Object.values(charts).forEach(c => c.resize());
    if (location.hash.slice(1) !== tab.dataset.panel) {
      history.replaceState(null, '', `#${tab.dataset.panel}`);
    }
  }
  tabs.forEach(t => t.addEventListener('click', () => select(t)));
  document.querySelector('.tabs').addEventListener('keydown', e => {
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
    if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
    if (e.key === 'Home') next = tabs[0];
    if (e.key === 'End') next = tabs[tabs.length - 1];
    if (next) { e.preventDefault(); next.focus(); select(next); }
  });
  const fromHash = tabs.find(t => t.dataset.panel === location.hash.slice(1));
  if (fromHash) select(fromHash);
}

/* ---------------- boot ---------------- */

async function loadData() {
  const bust = `?t=${Date.now()}`;
  const sources = [`data/dashboard_data.json${bust}`, `${RAW_FALLBACK}${bust}`];
  let lastErr;
  for (const url of sources) {
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

function showBanner(msg) {
  const b = document.getElementById('banner');
  b.hidden = false;
  b.textContent = msg;
}

(async function main() {
  chartTheme();
  initTabs();
  try {
    DATA = await loadData();
  } catch (e) {
    showBanner(`Could not load dashboard data (${e.message}). If you just deployed, wait for the first scan to publish docs/data/dashboard_data.json.`);
    return;
  }

  renderHeader(DATA);
  renderOverview(DATA);
  renderOpen(DATA);
  renderHistory(DATA);
  renderModel(DATA);
  renderBacktest(DATA);
  renderStrategy(DATA);

  const srcSel = document.getElementById('hist-source');
  if (!DATA.backtest) {
    [...srcSel.options].find(o => o.value === 'backtest').disabled = true;
  }
  ['hist-search', 'hist-filter', 'hist-source'].forEach(id =>
    document.getElementById(id).addEventListener('input', renderHistoryTable));

  const stale = daysAgo(DATA.as_of);
  if (stale !== null && stale > 5) {
    showBanner(`Latest data is ${stale} days old (${DATA.as_of}). The daily workflow may not have run — check the Actions tab.`);
  }
})();
