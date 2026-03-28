"""
LCO Dashboard — served at /lco/dashboard
Self-contained single-file HTML. Polls /lco/status and /lco/recent every 3s.
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LCO Dashboard</title>
<style>
:root {
  --bg:#0f1117;--surface:#1a1d27;--surface2:#21253a;
  --border:#2d3148;--border2:#3d4268;
  --text:#e2e8f0;--muted:#8892a4;--hint:#555e72;
  --accent:#6c8aff;--green:#4ade80;--amber:#fbbf24;
  --red:#f87171;--teal:#2dd4bf;--purple:#a78bfa;
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.6}
a{color:var(--accent);text-decoration:none}
.hdr{display:flex;align-items:center;justify-content:space-between;
     padding:14px 24px;border-bottom:1px solid var(--border);background:var(--surface)}
.logo{font-size:15px;font-weight:600;color:var(--accent);letter-spacing:.02em}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);
     display:inline-block;margin-right:7px;animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.badge{font-size:11px;color:var(--muted);background:rgba(255,255,255,.06);
       padding:3px 10px;border-radius:20px}
.ts{font-size:11px;color:var(--muted)}
.main{padding:20px 24px;max-width:1160px;margin:0 auto}
.sec{font-size:11px;font-weight:500;color:var(--muted);text-transform:uppercase;
     letter-spacing:.08em;margin:0 0 12px}
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
          gap:10px;margin-bottom:24px}
.kpi{background:var(--surface);border:1px solid var(--border);
     border-radius:10px;padding:14px 16px}
.kpi-label{font-size:11px;color:var(--muted);margin-bottom:5px}
.kpi-value{font-size:24px;font-weight:600}
.kpi-sub{font-size:11px;color:var(--hint);margin-top:2px}
.c-green{color:var(--green)}.c-teal{color:var(--teal)}.c-accent{color:var(--accent)}
.c-amber{color:var(--amber)}.c-purple{color:var(--purple)}.c-text{color:var(--text)}
.c-red{color:var(--red)}

.row2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
@media(max-width:680px){.row2{grid-template-columns:1fr}}
.panel{background:var(--surface);border:1px solid var(--border);
       border-radius:10px;padding:16px 18px}
.cfg-row{display:flex;justify-content:space-between;align-items:center;
         padding:7px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.cfg-row:last-child{border-bottom:none}
.cfg-k{color:var(--muted);font-size:12px}
.cfg-v{font-weight:500;font-size:12px}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:500}
.p-on{background:rgba(74,222,128,.15);color:var(--green)}
.p-off{background:rgba(255,255,255,.07);color:var(--hint)}
.p-pass{background:rgba(255,255,255,.07);color:var(--hint)}
.p-light{background:rgba(251,191,36,.12);color:var(--amber)}
.p-medium{background:rgba(108,138,255,.14);color:var(--accent)}
.p-aggressive{background:rgba(248,113,113,.14);color:var(--red)}

.chart-wrap{height:100px;position:relative;margin-bottom:8px}
canvas{width:100%;height:100%}
.chart-meta{display:flex;gap:16px;font-size:11px;color:var(--muted)}
.chart-meta strong{color:var(--text)}

.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th{color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-size:10px;
   font-weight:500;padding:8px 10px;border-bottom:1px solid var(--border);text-align:left;
   white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid rgba(255,255,255,.03);white-space:nowrap}
tr:hover td{background:rgba(255,255,255,.02)}
.bar{display:inline-block;height:5px;border-radius:3px;background:var(--accent);
     vertical-align:middle;min-width:2px;margin-right:4px}
.saved{color:var(--green);font-weight:500}
.empty{color:var(--hint);text-align:center;padding:28px;font-style:italic}
.footer{text-align:center;color:var(--hint);font-size:11px;
        padding:16px;border-top:1px solid var(--border);margin-top:8px}
</style>
</head>
<body>
<div class="hdr">
  <div class="logo"><span class="dot" id="dot"></span>LCO — LLM Context Optimizer</div>
  <div style="display:flex;gap:10px;align-items:center">
    <span class="ts">updated <span id="ts">—</span></span>
    <span class="badge" id="ver">—</span>
  </div>
</div>

<div class="main">

  <div class="sec" style="margin-top:4px">Cost savings</div>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Input tokens saved</div>
      <div class="kpi-value c-green" id="k-in-saved">—</div>
      <div class="kpi-sub">compressed away</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Output tokens saved</div>
      <div class="kpi-value c-teal" id="k-out-saved">—</div>
      <div class="kpi-sub">compressed away</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Total tokens saved</div>
      <div class="kpi-value c-accent" id="k-total-saved">—</div>
      <div class="kpi-sub">input + output</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Avg quality score</div>
      <div class="kpi-value c-purple" id="k-quality">—</div>
      <div class="kpi-sub">similarity gate</div>
    </div>
  </div>

  <div class="sec">Traffic</div>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Total requests</div>
      <div class="kpi-value c-text" id="k-total">—</div>
      <div class="kpi-sub">AI API calls proxied</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Input tokens used</div>
      <div class="kpi-value c-text" id="k-in">—</div>
      <div class="kpi-sub">after compression</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Output tokens</div>
      <div class="kpi-value c-text" id="k-out">—</div>
      <div class="kpi-sub">delivered to client</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Avg latency</div>
      <div class="kpi-value c-amber" id="k-lat">—</div>
      <div class="kpi-sub">proxy overhead ms</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Safe zone hits</div>
      <div class="kpi-value c-purple" id="k-safe">—</div>
      <div class="kpi-sub">protected messages</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Streaming</div>
      <div class="kpi-value c-text" id="k-stream">—</div>
      <div class="kpi-sub">of total requests</div>
    </div>
  </div>

  <div class="row2">
    <div class="panel">
      <div class="sec">Configuration</div>
      <div id="cfg"></div>
    </div>
    <div class="panel">
      <div class="sec">Latency (ms) — last 40 requests</div>
      <div class="chart-wrap"><canvas id="cv"></canvas></div>
      <div class="chart-meta">
        <span>min <strong id="lat-min">—</strong></span>
        <span>avg <strong id="lat-avg" style="color:var(--accent)">—</strong></span>
        <span>max <strong id="lat-max">—</strong></span>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="sec" style="margin-bottom:14px">Recent requests</div>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>#</th><th>Provider</th><th>Model</th>
          <th>In used</th><th>In saved</th>
          <th>Out used</th><th>Out saved</th>
          <th>Latency</th><th>Mode</th><th>Quality</th><th>Safe</th>
        </tr></thead>
        <tbody id="tbl"></tbody>
      </table>
    </div>
  </div>

</div>
<div class="footer">
  <a href="/lco/docs">API docs</a> &nbsp;·&nbsp;
  <a href="/lco/status">status JSON</a> &nbsp;·&nbsp;
  <a href="/lco/recent">recent JSON</a>
</div>

<script>
const latHist = [];

function f(v, s='', d=0) {
  if (v == null || v === '') return '—';
  if (typeof v === 'number') return v.toFixed(d) + s;
  return String(v) + s;
}
function fk(v) {
  if (v == null) return '—';
  return v >= 1000 ? (v/1000).toFixed(1)+'k' : String(v);
}

function modePill(m) {
  const cls = {passthrough:'p-pass',light:'p-light',medium:'p-medium',aggressive:'p-aggressive'}[m]||'p-pass';
  return `<span class="pill ${cls}">${m||'passthrough'}</span>`;
}

function renderCfg(d) {
  const rows = [
    ['Mode',              modePill(d.compression_mode)],
    ['Output optimization', `<span class="pill ${d.output_optimization?'p-on':'p-off'}">${d.output_optimization?'on':'off'}</span>`],
    ['Memory compression',  `<span class="pill ${d.memory_compression?'p-on':'p-off'}">${d.memory_compression?'on':'off'}</span>`],
    ['Memory window',      d.memory_window + ' turns'],
    ['Quality gate',      `<span class="pill ${d.quality_gate_enabled?'p-on':'p-off'}">${d.quality_gate_enabled?'on':'off'}</span>`],
    ['Providers',         (d.providers_supported||[]).join(', ')],
  ];
  document.getElementById('cfg').innerHTML = rows.map(([k,v])=>
    `<div class="cfg-row"><span class="cfg-k">${k}</span><span class="cfg-v">${v}</span></div>`
  ).join('');
}

function drawChart() {
  const cv = document.getElementById('cv');
  const ctx = cv.getContext('2d');
  cv.width = cv.offsetWidth; cv.height = cv.offsetHeight;
  const W = cv.width, H = cv.height;
  ctx.clearRect(0,0,W,H);
  if (!latHist.length) return;
  const mx = Math.max(...latHist,1);
  const bw = Math.max(3, Math.floor((W-4)/latHist.length)-1);
  const step = (W-4)/latHist.length;
  latHist.forEach((v,i)=>{
    const bh = Math.max(2,(v/mx)*(H-8));
    ctx.fillStyle = v>200?'rgba(248,113,113,0.4)':v>50?'rgba(251,191,36,0.3)':'rgba(108,138,255,0.25)';
    ctx.fillRect(4+i*step, H-bh-4, bw, bh);
  });
  const avg = latHist.reduce((a,b)=>a+b,0)/latHist.length;
  const ay = H-4-(avg/mx)*(H-8);
  ctx.strokeStyle='rgba(108,138,255,0.6)';ctx.lineWidth=1;ctx.setLineDash([3,3]);
  ctx.beginPath();ctx.moveTo(0,ay);ctx.lineTo(W,ay);ctx.stroke();ctx.setLineDash([]);
}

function renderTable(rows) {
  const tbody = document.getElementById('tbl');
  if (!rows||!rows.length) {
    tbody.innerHTML = `<tr><td colspan="11" class="empty">No requests yet — make an API call through LCO to see data here</td></tr>`;
    return;
  }
  const mxL = Math.max(...rows.map(r=>r.latency_ms||0),1);
  tbody.innerHTML = rows.map((r,i)=>{
    const inSaved  = r.input_tokens_saved  || 0;
    const outSaved = r.output_tokens_saved || 0;
    const model = (r.model||'—').length > 22 ? (r.model||'—').slice(0,20)+'…' : (r.model||'—');
    const quality = r.quality_score != null ? r.quality_score.toFixed(2) : '—';
    return `<tr>
      <td style="color:var(--hint)">${i+1}</td>
      <td>${r.provider||'—'}</td>
      <td title="${r.model||''}">${model}</td>
      <td>${fk(r.input_tokens)}</td>
      <td class="${inSaved>0?'saved':''}">${inSaved>0?'+'+fk(inSaved):'—'}</td>
      <td>${fk(r.output_tokens)}</td>
      <td class="${outSaved>0?'saved':''}">${outSaved>0?'+'+fk(outSaved):'—'}</td>
      <td>
        <span class="bar" style="width:${Math.round((r.latency_ms||0)/mxL*50)}px"></span>
        ${f(r.latency_ms,'ms',0)}
      </td>
      <td>${modePill(r.compression_mode)}</td>
      <td>${quality}</td>
      <td style="color:var(--${r.safe_zone_hit?'green':'hint'})">${r.safe_zone_hit?'✓':'·'}</td>
    </tr>`;
  }).join('');
}

async function refresh() {
  try {
    const r = await fetch('/lco/status');
    const d = await r.json();
    document.getElementById('dot').style.background = 'var(--green)';
    document.getElementById('ver').textContent = d.version||'';
    document.getElementById('ts').textContent = new Date().toLocaleTimeString();
    const m = d.metrics||{};
    document.getElementById('k-in-saved').textContent    = fk(m.total_input_saved);
    document.getElementById('k-out-saved').textContent   = fk(m.total_output_saved);
    document.getElementById('k-total-saved').textContent = fk(m.total_tokens_saved);
    document.getElementById('k-quality').textContent     = m.avg_quality_score!=null ? m.avg_quality_score.toFixed(2) : '—';
    document.getElementById('k-total').textContent  = fk(m.total_requests);
    document.getElementById('k-in').textContent     = fk(m.total_input_tokens);
    document.getElementById('k-out').textContent    = fk(m.total_output_tokens);
    document.getElementById('k-lat').textContent    = f(m.avg_latency_ms,'ms',0);
    document.getElementById('k-safe').textContent   = fk(m.safe_zone_hits);
    document.getElementById('k-stream').textContent = fk(m.streaming_requests);
    document.getElementById('lat-min').textContent  = f(m.min_latency_ms,'ms',0);
    document.getElementById('lat-avg').textContent  = f(m.avg_latency_ms,'ms',0);
    document.getElementById('lat-max').textContent  = f(m.max_latency_ms,'ms',0);
    renderCfg(d);
    if (m.avg_latency_ms) {
      latHist.push(m.avg_latency_ms);
      if (latHist.length>40) latHist.shift();
      drawChart();
    }
  } catch(e) {
    document.getElementById('dot').style.background = 'var(--red)';
  }
  try {
    const r2 = await fetch('/lco/recent');
    if (r2.ok) renderTable(await r2.json());
  } catch(e) {}
}

refresh();
setInterval(refresh,3000);
window.addEventListener('resize', drawChart);
</script>
</body>
</html>"""