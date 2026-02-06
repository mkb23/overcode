/**
 * Overcode Relay Worker
 *
 * Receives state updates from your Mac's monitor daemon and serves
 * them to your phone's browser. Runs on Cloudflare's edge network.
 *
 * Endpoints:
 *   POST /update     - Push new state (requires API key)
 *   GET  /status     - Get current state JSON
 *   GET  /timeline   - Get timeline data JSON
 *   GET  /           - Dashboard HTML
 *   GET  /health     - Health check
 */

const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-API-Key',
};

// Dashboard HTML (mobile-optimized, TUI-style tabular layout)
const DASHBOARD_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <title>Overcode</title>
  <style>
    :root {
      --bg: #0d2137;
      --bg-alt: #0d1117;
      --border: #30363d;
      --text: #e6edf3;
      --dim: #7d8590;
      --green: #3fb950;
      --yellow: #d29922;
      --orange: #db6d28;
      --red: #f85149;
      --cyan: #58a6ff;
      --magenta: #a371f7;
      --blue: #58a6ff;
      --white: #ffffff;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', monospace;
      background: var(--bg-alt);
      color: var(--text);
      min-height: 100vh;
      padding: 4px;
      padding-top: max(4px, env(safe-area-inset-top));
      padding-bottom: max(4px, env(safe-area-inset-bottom));
      font-size: 11px;
      line-height: 1.4;
    }

    /* Header bar */
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 4px 8px;
      background: var(--bg);
      border-bottom: 1px solid var(--border);
      margin-bottom: 4px;
    }
    .header-left { display: flex; align-items: center; gap: 8px; }
    .header h1 { font-size: 12px; font-weight: 600; color: var(--cyan); }
    .status-indicator {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 10px;
      color: var(--dim);
    }
    .status-dot {
      width: 6px; height: 6px;
      border-radius: 50%;
      display: inline-block;
    }
    .status-dot.connected { background: var(--green); }
    .status-dot.stale { background: var(--yellow); }
    .status-dot.disconnected { background: var(--red); }

    /* Summary bar */
    .summary-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      padding: 4px 8px;
      background: var(--bg);
      border-bottom: 1px solid var(--border);
      margin-bottom: 4px;
      font-size: 10px;
    }
    .summary-item { display: flex; align-items: center; gap: 3px; }
    .summary-item .label { color: var(--dim); }
    .summary-item .value { font-weight: 600; }
    .summary-item .value.green { color: var(--green); }
    .summary-item .value.red { color: var(--red); }

    /* Agent table - fixed width columns for alignment */
    .agent-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 11px;
    }
    .agent-table td {
      padding: 4px 2px;
      white-space: nowrap;
      vertical-align: middle;
    }
    .agent-row {
      background: var(--bg);
      border-left: 3px solid var(--green);
    }
    .agent-row.waiting_user { border-left-color: var(--red); }
    .agent-row.waiting_approval { border-left-color: var(--orange); }
    .agent-row.waiting_heartbeat { border-left-color: var(--yellow); }
    .agent-row.running_heartbeat { border-left-color: var(--green); }
    .agent-row.terminated { border-left-color: var(--dim); }

    /* Fixed-width columns for alignment */
    .col-status { width: 55px; text-align: left; padding-left: 6px !important; }
    .status-emoji { font-size: 11px; }

    .col-name { width: 90px; color: var(--cyan); font-weight: 600; overflow: hidden; text-overflow: ellipsis; }

    .col-repo { width: 140px; color: var(--dim); font-size: 10px; overflow: hidden; text-overflow: ellipsis; }

    .col-uptime { width: 45px; text-align: right; color: var(--white); font-weight: 600; }
    .col-green { width: 50px; text-align: right; color: var(--green); font-weight: 600; }
    .col-red { width: 55px; text-align: right; color: var(--red); font-weight: 600; }
    .col-pct { width: 35px; text-align: right; font-weight: 600; }
    .col-pct.high { color: var(--green); }
    .col-pct.low { color: var(--red); }

    .col-tokens { width: 50px; text-align: right; color: var(--orange); font-weight: 600; }
    .col-diff { width: 25px; text-align: right; color: var(--magenta); }
    .col-diff-plus { width: 40px; text-align: right; color: var(--green); }
    .col-diff-minus { width: 35px; text-align: right; color: var(--red); }
    .col-median { width: 45px; text-align: right; color: var(--blue); font-weight: 600; }
    .col-perm { width: 20px; text-align: center; }
    .col-human { width: 35px; text-align: right; color: var(--yellow); font-weight: 600; }
    .col-robot { width: 35px; text-align: right; color: var(--cyan); font-weight: 600; }
    .col-orders { width: 20px; text-align: center; color: var(--magenta); }

    /* Activity row */
    .activity-row td {
      padding: 0 6px 4px 6px !important;
      font-size: 9px;
      color: var(--dim);
      background: var(--bg);
    }
    .activity-text {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 600px;
    }

    /* Summary row (AI-generated) */
    .summary-row td {
      padding: 0 6px 8px 6px !important;
      font-size: 10px;
      color: var(--cyan);
      background: var(--bg);
      font-style: italic;
    }
    .summary-text {
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      max-width: 600px;
      line-height: 1.3;
    }
    .summary-age {
      color: var(--dim);
      font-size: 8px;
      font-style: normal;
    }

    /* Footer */
    .footer {
      text-align: center;
      font-size: 9px;
      color: var(--dim);
      padding: 8px 4px 4px;
    }

    .no-data {
      text-align: center;
      padding: 30px;
      color: var(--dim);
      background: var(--bg);
    }

    /* Mobile: hide some columns */
    @media (max-width: 600px) {
      .col-repo, .col-diff, .col-diff-plus, .col-diff-minus, .col-median { display: none; }
      .agent-table { font-size: 10px; }
    }
    @media (max-width: 450px) {
      .col-uptime, .col-pct { display: none; }
    }
  </style>
</head>
<body>
  <div class="header">
    <div class="header-left">
      <h1>Overcode</h1>
      <div class="status-indicator">
        <span class="status-dot disconnected" id="conn-dot"></span>
        <span id="daemon-status">--</span>
      </div>
      <div class="status-indicator" id="summarizer-status" style="display: none;">
        <span style="color: var(--cyan);">💭</span>
        <span id="summarizer-calls" style="color: var(--dim);">0</span>
      </div>
    </div>
    <div id="spin-rate" style="color: var(--green); font-weight: 600; font-size: 11px;">--</div>
  </div>

  <div class="summary-bar">
    <div class="summary-item">
      <span class="label">Spin:</span>
      <span class="value green" id="green-count">-</span>/<span class="value" id="total-count">-</span>
    </div>
    <div class="summary-item">
      <span class="value green">▶</span>
      <span class="value green" id="total-green">-</span>
    </div>
    <div class="summary-item">
      <span class="value red">⏸</span>
      <span class="value red" id="total-red">-</span>
    </div>
  </div>

  <table class="agent-table" id="agents">
    <tbody>
      <tr><td colspan="17" class="no-data">Waiting for data...</td></tr>
    </tbody>
  </table>

  <div class="footer" id="updated">--</div>

  <script>
    function timeAgo(isoString) {
      if (!isoString) return 'never';
      const seconds = (Date.now() - new Date(isoString).getTime()) / 1000;
      if (seconds < 60) return Math.round(seconds) + 's ago';
      if (seconds < 3600) return Math.round(seconds / 60) + 'm ago';
      return Math.round(seconds / 3600) + 'h ago';
    }

    function formatDuration(seconds) {
      if (seconds < 60) return Math.round(seconds) + 's';
      if (seconds < 3600) return (seconds / 60).toFixed(1) + 'm';
      if (seconds < 86400) return (seconds / 3600).toFixed(1) + 'h';
      return (seconds / 86400).toFixed(1) + 'd';
    }

    async function refresh() {
      try {
        const res = await fetch('/status');
        if (!res.ok) throw new Error('Failed to fetch');
        const data = await res.json();
        render(data);
      } catch (e) {
        document.getElementById('conn-dot').className = 'status-dot disconnected';
        document.getElementById('daemon-status').textContent = 'offline';
      }
    }

    function render(data) {
      const dot = document.getElementById('conn-dot');
      const daemonStatus = document.getElementById('daemon-status');

      // Connection status
      if (data.daemon?.running) {
        dot.className = 'status-dot connected';
        daemonStatus.textContent = '#' + data.daemon.loop_count;
      } else {
        dot.className = 'status-dot stale';
        daemonStatus.textContent = 'stopped';
      }

      // Summarizer status
      const summarizerEl = document.getElementById('summarizer-status');
      const summarizerCalls = document.getElementById('summarizer-calls');
      if (data.daemon?.summarizer_enabled) {
        summarizerEl.style.display = 'flex';
        summarizerCalls.textContent = data.daemon.summarizer_calls || 0;
      } else {
        summarizerEl.style.display = 'none';
      }

      // Summary
      const greenCount = data.summary?.green_agents ?? 0;
      const totalCount = data.summary?.total_agents ?? 0;
      document.getElementById('green-count').textContent = greenCount;
      document.getElementById('total-count').textContent = totalCount;

      const totalGreen = data.summary?.total_green_time ?? 0;
      const totalRed = data.summary?.total_non_green_time ?? 0;
      document.getElementById('total-green').textContent = formatDuration(totalGreen);
      document.getElementById('total-red').textContent = formatDuration(totalRed);

      // Spin rate
      let spinRate = 0;
      if (data.agents && data.agents.length > 0) {
        const totalPct = data.agents.reduce((sum, a) => sum + (a.percent_active || 0), 0);
        spinRate = totalPct / data.agents.length;
      }
      const spinEl = document.getElementById('spin-rate');
      spinEl.textContent = 'μ' + spinRate.toFixed(1) + '%';
      spinEl.style.color = spinRate >= 50 ? 'var(--green)' : 'var(--red)';

      // Agents table
      const table = document.getElementById('agents');
      if (!data.agents || data.agents.length === 0) {
        table.innerHTML = '<tbody><tr><td colspan="17" class="no-data">No agents</td></tr></tbody>';
        return;
      }

      table.innerHTML = '<tbody>' + data.agents.map(a => {
        const pctClass = a.percent_active >= 50 ? 'high' : 'low';
        const repoStr = a.repo && a.branch ? a.repo + ':' + a.branch : '';
        const ordersStr = a.standing_orders
          ? (a.standing_orders_complete ? '✓' : '📋')
          : '➖';
        const permEmoji = a.perm_emoji || '👮';
        const diffFiles = a.git_diff_files || 0;
        const diffIns = a.git_diff_insertions || 0;
        const diffDel = a.git_diff_deletions || 0;
        const medianStr = a.median_work_time || '-';

        // TUI format: 🟢 1.4h → name repo:branch ↑up ▶green ⏸red pct% tokens Δn +ins -del ⏱med 🔥 👤n 🤖n 📋
        // Summary row (if available)
        const summaryRow = a.activity_summary ? \`
          <tr class="summary-row">
            <td colspan="16">
              <div class="summary-text">💭 \${a.activity_summary}</div>
              <span class="summary-age">\${timeAgo(a.activity_summary_updated)}</span>
            </td>
          </tr>
        \` : '';

        return \`
          <tr class="agent-row \${a.status}">
            <td class="col-status"><span class="status-emoji">\${a.status_emoji}</span> \${a.time_in_state}</td>
            <td class="col-name">\${a.name}</td>
            <td class="col-repo">\${repoStr}</td>
            <td class="col-uptime">↑\${a.uptime || '-'}</td>
            <td class="col-green">▶\${a.green_time}</td>
            <td class="col-red">⏸\${a.non_green_time}</td>
            <td class="col-pct \${pctClass}">\${a.percent_active}%</td>
            <td class="col-tokens">\${a.tokens}</td>
            <td class="col-diff">Δ\${diffFiles}</td>
            <td class="col-diff-plus">+\${diffIns}</td>
            <td class="col-diff-minus">-\${diffDel}</td>
            <td class="col-median">⏱\${medianStr}</td>
            <td class="col-perm">\${permEmoji}</td>
            <td class="col-human">👤\${a.human_interactions}</td>
            <td class="col-robot">🤖\${a.robot_steers}</td>
            <td class="col-orders">\${ordersStr}</td>
          </tr>
          <tr class="activity-row">
            <td colspan="16"><div class="activity-text">\${a.activity || 'Idle'}</div></td>
          </tr>
          \${summaryRow}
        \`;
      }).join('') + '</tbody>';

      // Footer
      document.getElementById('updated').textContent = timeAgo(data.relay_updated_at || data.timestamp);
    }

    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>`;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS_HEADERS });
    }

    // Route requests
    switch (path) {
      case '/':
        return new Response(DASHBOARD_HTML, {
          headers: { 'Content-Type': 'text/html', ...CORS_HEADERS },
        });

      case '/health':
        return jsonResponse({ status: 'ok', timestamp: new Date().toISOString() });

      case '/update':
        return handleUpdate(request, env);

      case '/status':
        return handleGetStatus(env);

      case '/timeline':
        return handleGetTimeline(env);

      default:
        return new Response('Not found', { status: 404 });
    }
  },
};

async function handleUpdate(request, env) {
  if (request.method !== 'POST') {
    return new Response('Method not allowed', { status: 405 });
  }

  // Check API key
  const apiKey = request.headers.get('X-API-Key');
  if (!env.API_KEY || apiKey !== env.API_KEY) {
    return new Response('Unauthorized', { status: 401 });
  }

  try {
    const data = await request.json();

    // Add relay timestamp
    data.relay_updated_at = new Date().toISOString();

    // Store in KV (or fall back to in-memory for testing)
    if (env.STATE) {
      await env.STATE.put('status', JSON.stringify(data));
      if (data.timeline) {
        await env.STATE.put('timeline', JSON.stringify(data.timeline));
      }
    } else {
      // Fallback: store in global (won't persist across worker restarts)
      globalThis.__overcode_status = data;
      if (data.timeline) {
        globalThis.__overcode_timeline = data.timeline;
      }
    }

    return jsonResponse({ success: true });
  } catch (e) {
    return jsonResponse({ error: e.message }, 400);
  }
}

async function handleGetStatus(env) {
  let data = null;

  if (env.STATE) {
    const stored = await env.STATE.get('status');
    if (stored) {
      data = JSON.parse(stored);
    }
  } else {
    data = globalThis.__overcode_status;
  }

  if (!data) {
    return jsonResponse({
      timestamp: new Date().toISOString(),
      daemon: { running: false },
      summary: { total_agents: 0, green_agents: 0 },
      agents: [],
    });
  }

  return jsonResponse(data);
}

async function handleGetTimeline(env) {
  let data = null;

  if (env.STATE) {
    const stored = await env.STATE.get('timeline');
    if (stored) {
      data = JSON.parse(stored);
    }
  } else {
    data = globalThis.__overcode_timeline;
  }

  return jsonResponse(data || { agents: {} });
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...CORS_HEADERS,
    },
  });
}
