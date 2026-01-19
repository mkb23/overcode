"""
HTML/CSS/JS templates for web dashboard.

Single-page application embedded as Python string for zero external dependencies.
Mobile-first responsive design with dark theme matching the TUI aesthetic.
"""


def get_dashboard_html() -> str:
    """Return the complete dashboard HTML page."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="theme-color" content="#0f172a">
    <title>Overcode Dashboard</title>
    <style>
        :root {
            --bg-primary: #0f172a;
            --bg-secondary: #1e293b;
            --bg-tertiary: #334155;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-dim: #64748b;
            --green: #22c55e;
            --yellow: #eab308;
            --orange: #f97316;
            --red: #ef4444;
            --cyan: #06b6d4;
            --dim: #6b7280;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 12px;
            font-size: 14px;
            line-height: 1.4;
        }

        /* Header */
        header {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 8px;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--bg-tertiary);
        }

        h1 {
            font-size: 20px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .header-status {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-left: auto;
            font-size: 12px;
            color: var(--text-secondary);
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }

        .status-dot.green { background: var(--green); }
        .status-dot.red { background: var(--red); }
        .status-dot.yellow { background: var(--yellow); }

        /* Summary bar */
        .summary {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 8px;
            margin-bottom: 16px;
        }

        .summary-card {
            background: var(--bg-secondary);
            border-radius: 8px;
            padding: 12px;
            text-align: center;
        }

        .summary-value {
            font-size: 24px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .summary-value.green { color: var(--green); }
        .summary-value.yellow { color: var(--yellow); }

        .summary-label {
            font-size: 11px;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        /* Agent cards */
        .agents {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 16px;
        }

        .agent-card {
            background: var(--bg-secondary);
            border-radius: 8px;
            padding: 12px;
            border-left: 4px solid var(--dim);
        }

        .agent-header {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
        }

        .agent-emoji {
            font-size: 24px;
            line-height: 1;
        }

        .agent-name {
            font-weight: 600;
            font-size: 16px;
            flex: 1;
        }

        .agent-time {
            font-size: 12px;
            color: var(--text-secondary);
            background: var(--bg-tertiary);
            padding: 2px 6px;
            border-radius: 4px;
        }

        .agent-repo {
            font-size: 11px;
            color: var(--text-dim);
            margin-bottom: 8px;
        }

        .agent-activity {
            font-size: 12px;
            color: var(--text-secondary);
            margin-bottom: 8px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .agent-stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 4px;
            font-size: 11px;
        }

        .stat {
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 4px;
            background: var(--bg-tertiary);
            border-radius: 4px;
        }

        .stat-value {
            font-weight: 600;
            color: var(--text-primary);
        }

        .stat-label {
            color: var(--text-dim);
            font-size: 10px;
        }

        /* Timeline section */
        .timeline-section {
            background: var(--bg-secondary);
            border-radius: 8px;
            padding: 12px;
        }

        .timeline-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
            font-size: 12px;
            color: var(--text-secondary);
        }

        .timeline-row {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }

        .timeline-label {
            width: 80px;
            font-size: 11px;
            color: var(--text-secondary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .timeline-bar {
            flex: 1;
            font-family: monospace;
            font-size: 12px;
            letter-spacing: -1px;
            overflow-x: auto;
            white-space: nowrap;
            -webkit-overflow-scrolling: touch;
        }

        .timeline-pct {
            width: 36px;
            text-align: right;
            font-size: 11px;
            color: var(--text-dim);
        }

        /* Legend */
        .legend {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 8px;
            font-size: 10px;
            color: var(--text-dim);
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 4px;
        }

        /* Update indicator */
        .update-info {
            text-align: center;
            font-size: 11px;
            color: var(--text-dim);
            margin-top: 16px;
            padding-top: 12px;
            border-top: 1px solid var(--bg-tertiary);
        }

        .updating {
            animation: pulse 1s ease-in-out infinite;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        /* Error state */
        .error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid var(--red);
            border-radius: 8px;
            padding: 16px;
            text-align: center;
            color: var(--red);
        }

        /* Empty state */
        .empty {
            text-align: center;
            padding: 32px;
            color: var(--text-dim);
        }

        /* Desktop enhancements */
        @media (min-width: 640px) {
            body {
                padding: 24px;
                max-width: 800px;
                margin: 0 auto;
            }

            .summary {
                grid-template-columns: repeat(4, 1fr);
            }

            .agent-stats {
                grid-template-columns: repeat(6, 1fr);
            }

            h1 {
                font-size: 24px;
            }
        }
    </style>
</head>
<body>
    <header>
        <h1>Overcode</h1>
        <div class="header-status">
            <span id="daemon-status">
                <span class="status-dot" id="daemon-dot"></span>
                <span id="daemon-text">Loading...</span>
            </span>
        </div>
    </header>

    <div id="content">
        <div class="empty">Loading dashboard...</div>
    </div>

    <div class="update-info">
        <span id="update-text">Last update: -</span>
    </div>

    <script>
        const REFRESH_INTERVAL = 5000;
        let lastUpdate = null;

        async function fetchStatus() {
            try {
                const response = await fetch('/api/status');
                if (!response.ok) throw new Error('API error');
                return await response.json();
            } catch (e) {
                console.error('Fetch error:', e);
                return null;
            }
        }

        async function fetchTimeline() {
            try {
                const response = await fetch('/api/timeline');
                if (!response.ok) throw new Error('API error');
                return await response.json();
            } catch (e) {
                console.error('Timeline fetch error:', e);
                return null;
            }
        }

        function formatTime(isoString) {
            if (!isoString) return '-';
            const date = new Date(isoString);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        }

        function updateDaemonStatus(daemon) {
            const dot = document.getElementById('daemon-dot');
            const text = document.getElementById('daemon-text');

            if (daemon.running) {
                dot.className = 'status-dot green';
                text.textContent = daemon.status;
            } else {
                dot.className = 'status-dot red';
                text.textContent = 'stopped';
            }
        }

        function renderSummary(summary) {
            const greenPct = summary.total_agents > 0
                ? Math.round((summary.green_agents / summary.total_agents) * 100)
                : 0;

            return `
                <div class="summary">
                    <div class="summary-card">
                        <div class="summary-value green">${summary.green_agents}</div>
                        <div class="summary-label">Green</div>
                    </div>
                    <div class="summary-card">
                        <div class="summary-value yellow">${summary.total_agents - summary.green_agents}</div>
                        <div class="summary-label">Not Green</div>
                    </div>
                    <div class="summary-card">
                        <div class="summary-value">${summary.total_agents}</div>
                        <div class="summary-label">Total</div>
                    </div>
                    <div class="summary-card">
                        <div class="summary-value">${greenPct}%</div>
                        <div class="summary-label">Active</div>
                    </div>
                </div>
            `;
        }

        function renderAgent(agent) {
            return `
                <div class="agent-card" style="border-left-color: ${agent.status_color_hex}">
                    <div class="agent-header">
                        <span class="agent-emoji">${agent.status_emoji}</span>
                        <span class="agent-name">${agent.name}</span>
                        <span class="agent-time">${agent.time_in_state}</span>
                    </div>
                    ${agent.repo ? `<div class="agent-repo">${agent.repo}:${agent.branch}</div>` : ''}
                    ${agent.activity ? `<div class="agent-activity">${agent.activity}</div>` : ''}
                    <div class="agent-stats">
                        <div class="stat">
                            <span class="stat-value" style="color: var(--green)">${agent.green_time}</span>
                            <span class="stat-label">Active</span>
                        </div>
                        <div class="stat">
                            <span class="stat-value" style="color: var(--yellow)">${agent.non_green_time}</span>
                            <span class="stat-label">Paused</span>
                        </div>
                        <div class="stat">
                            <span class="stat-value">${agent.percent_active}%</span>
                            <span class="stat-label">Efficiency</span>
                        </div>
                        <div class="stat">
                            <span class="stat-value" style="color: var(--orange)">${agent.tokens}</span>
                            <span class="stat-label">Tokens</span>
                        </div>
                        <div class="stat">
                            <span class="stat-value" style="color: var(--yellow)">${agent.human_interactions}</span>
                            <span class="stat-label">Human</span>
                        </div>
                        <div class="stat">
                            <span class="stat-value" style="color: var(--cyan)">${agent.robot_steers}</span>
                            <span class="stat-label">Robot</span>
                        </div>
                    </div>
                </div>
            `;
        }

        function renderTimeline(timeline) {
            if (!timeline || !timeline.agents || Object.keys(timeline.agents).length === 0) {
                return '';
            }

            const agentRows = Object.entries(timeline.agents).map(([name, data]) => {
                // Build timeline string from slots
                let timelineStr = '';
                const colors = timeline.status_colors || {};
                const chars = timeline.status_chars || {};

                // Create array for all slots
                const slotMap = {};
                data.slots.forEach(s => { slotMap[s.index] = s; });

                for (let i = 0; i < timeline.slot_count; i++) {
                    if (slotMap[i]) {
                        const s = slotMap[i];
                        timelineStr += `<span style="color:${s.color}">${s.char}</span>`;
                    } else {
                        timelineStr += '<span style="color:var(--bg-tertiary)">-</span>';
                    }
                }

                return `
                    <div class="timeline-row">
                        <span class="timeline-label">${name}</span>
                        <span class="timeline-bar">${timelineStr}</span>
                        <span class="timeline-pct">${data.percent_green}%</span>
                    </div>
                `;
            }).join('');

            return `
                <div class="timeline-section">
                    <div class="timeline-header">
                        <span>-${timeline.hours}h</span>
                        <span>Timeline</span>
                        <span>now</span>
                    </div>
                    ${agentRows}
                    <div class="legend">
                        <span class="legend-item"><span style="color:var(--green)">█</span> running</span>
                        <span class="legend-item"><span style="color:var(--yellow)">▓</span> idle</span>
                        <span class="legend-item"><span style="color:var(--orange)">▒</span> waiting</span>
                        <span class="legend-item"><span style="color:var(--red)">░</span> blocked</span>
                        <span class="legend-item"><span style="color:var(--dim)">×</span> terminated</span>
                    </div>
                </div>
            `;
        }

        function renderError(message) {
            return `<div class="error">${message}</div>`;
        }

        function renderEmpty() {
            return `<div class="empty">No agents found. Start a session with:<br><code>overcode launch --name my-agent</code></div>`;
        }

        async function refresh() {
            const updateText = document.getElementById('update-text');
            updateText.classList.add('updating');

            const [status, timeline] = await Promise.all([
                fetchStatus(),
                fetchTimeline()
            ]);

            updateText.classList.remove('updating');

            if (!status) {
                document.getElementById('content').innerHTML = renderError('Failed to connect to server');
                document.getElementById('daemon-dot').className = 'status-dot red';
                document.getElementById('daemon-text').textContent = 'error';
                return;
            }

            updateDaemonStatus(status.daemon);

            let html = '';

            if (status.agents.length === 0) {
                html = renderEmpty();
            } else {
                html = renderSummary(status.summary);
                html += '<div class="agents">';
                status.agents.forEach(agent => {
                    html += renderAgent(agent);
                });
                html += '</div>';
                html += renderTimeline(timeline);
            }

            document.getElementById('content').innerHTML = html;
            lastUpdate = new Date();
            updateText.textContent = `Last update: ${formatTime(lastUpdate.toISOString())}`;
        }

        // Initial load
        refresh();

        // Auto-refresh
        setInterval(refresh, REFRESH_INTERVAL);
    </script>
</body>
</html>"""


def get_analytics_html() -> str:
    """Return the complete analytics dashboard HTML page."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#0f172a">
    <title>Overcode Analytics</title>
    <script src="/static/chart.min.js"></script>
    <style>
        :root {
            --bg-primary: #0f172a;
            --bg-secondary: #1e293b;
            --bg-tertiary: #334155;
            --bg-hover: #475569;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-dim: #64748b;
            --green: #22c55e;
            --yellow: #eab308;
            --orange: #f97316;
            --red: #ef4444;
            --cyan: #06b6d4;
            --purple: #a855f7;
            --border: #334155;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            font-size: 14px;
            line-height: 1.5;
        }

        /* Navigation */
        .nav {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border);
            padding: 12px 24px;
            display: flex;
            align-items: center;
            gap: 24px;
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .nav-brand {
            font-size: 18px;
            font-weight: 700;
            color: var(--orange);
            text-decoration: none;
        }

        .nav-links {
            display: flex;
            gap: 4px;
        }

        .nav-link {
            padding: 8px 16px;
            border-radius: 6px;
            color: var(--text-secondary);
            text-decoration: none;
            font-weight: 500;
            transition: all 0.2s;
        }

        .nav-link:hover { background: var(--bg-tertiary); color: var(--text-primary); }
        .nav-link.active { background: var(--bg-tertiary); color: var(--text-primary); }

        .nav-right {
            margin-left: auto;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        /* Time range selector */
        .time-range {
            background: var(--bg-tertiary);
            border-radius: 8px;
            padding: 12px 16px;
            margin: 16px 24px;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 12px;
        }

        .time-range-label {
            font-weight: 500;
            color: var(--text-secondary);
        }

        .preset-btn {
            padding: 6px 12px;
            border-radius: 4px;
            border: 1px solid var(--border);
            background: var(--bg-secondary);
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 13px;
            transition: all 0.2s;
        }

        .preset-btn:hover { border-color: var(--orange); color: var(--text-primary); }
        .preset-btn.active { background: var(--orange); border-color: var(--orange); color: #000; font-weight: 500; }

        .time-inputs {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-left: auto;
        }

        .time-input {
            padding: 6px 10px;
            border-radius: 4px;
            border: 1px solid var(--border);
            background: var(--bg-secondary);
            color: var(--text-primary);
            font-size: 13px;
        }

        .time-input:focus { outline: none; border-color: var(--orange); }

        .refresh-btn {
            padding: 6px 12px;
            border-radius: 4px;
            border: none;
            background: var(--green);
            color: #000;
            cursor: pointer;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 4px;
        }

        .refresh-btn:hover { opacity: 0.9; }

        /* Main content */
        .main { padding: 0 24px 24px; max-width: 1400px; margin: 0 auto; }

        /* Page containers */
        .page { display: none; }
        .page.active { display: block; }

        /* Summary cards */
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .summary-card {
            background: var(--bg-secondary);
            border-radius: 8px;
            padding: 20px;
            border-left: 4px solid var(--border);
        }

        .summary-card.green { border-left-color: var(--green); }
        .summary-card.orange { border-left-color: var(--orange); }
        .summary-card.yellow { border-left-color: var(--yellow); }
        .summary-card.cyan { border-left-color: var(--cyan); }

        .summary-value {
            font-size: 32px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 4px;
        }

        .summary-label {
            font-size: 13px;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        /* Chart containers */
        .chart-container {
            background: var(--bg-secondary);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 24px;
        }

        .chart-title {
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 16px;
            color: var(--text-primary);
        }

        .chart-wrapper { position: relative; height: 300px; }

        /* Sessions table */
        .sessions-table {
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-secondary);
            border-radius: 8px;
            overflow: hidden;
        }

        .sessions-table th {
            text-align: left;
            padding: 12px 16px;
            background: var(--bg-tertiary);
            font-weight: 600;
            color: var(--text-secondary);
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            cursor: pointer;
            user-select: none;
        }

        .sessions-table th:hover { color: var(--text-primary); }
        .sessions-table th.sorted { color: var(--orange); }
        .sessions-table th .sort-icon { margin-left: 4px; }

        .sessions-table td {
            padding: 12px 16px;
            border-top: 1px solid var(--border);
            color: var(--text-primary);
        }

        .sessions-table tr:hover { background: var(--bg-tertiary); }

        .session-name {
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .session-badge {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 4px;
            background: var(--bg-tertiary);
            color: var(--text-dim);
        }

        .session-badge.active { background: var(--green); color: #000; }

        .expandable-row { cursor: pointer; }
        .expanded-content {
            display: none;
            background: var(--bg-tertiary);
            padding: 16px;
        }

        .expanded-content.show { display: table-row; }

        /* Timeline visualization */
        .timeline-container {
            background: var(--bg-secondary);
            border-radius: 8px;
            padding: 20px;
        }

        .timeline-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 16px;
        }

        .timeline-controls {
            display: flex;
            gap: 8px;
        }

        .timeline-btn {
            padding: 4px 8px;
            border-radius: 4px;
            border: 1px solid var(--border);
            background: transparent;
            color: var(--text-secondary);
            cursor: pointer;
            font-size: 12px;
        }

        .timeline-btn:hover { border-color: var(--orange); color: var(--text-primary); }

        .timeline-row {
            display: flex;
            align-items: center;
            margin-bottom: 8px;
        }

        .timeline-label {
            width: 120px;
            font-size: 13px;
            color: var(--text-secondary);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .timeline-bar {
            flex: 1;
            height: 24px;
            background: var(--bg-tertiary);
            border-radius: 4px;
            overflow: hidden;
            position: relative;
        }

        .timeline-segment {
            position: absolute;
            top: 0;
            height: 100%;
        }

        .timeline-legend {
            display: flex;
            gap: 16px;
            margin-top: 16px;
            font-size: 12px;
            color: var(--text-dim);
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .legend-color {
            width: 12px;
            height: 12px;
            border-radius: 2px;
        }

        /* Metrics grid */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 24px;
        }

        .metric-card {
            background: var(--bg-secondary);
            border-radius: 8px;
            padding: 20px;
        }

        .metric-title {
            font-size: 14px;
            font-weight: 600;
            color: var(--text-secondary);
            margin-bottom: 12px;
        }

        .metric-value {
            font-size: 28px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .metric-sub {
            font-size: 12px;
            color: var(--text-dim);
            margin-top: 4px;
        }

        .work-times-table {
            width: 100%;
            margin-top: 12px;
        }

        .work-times-table td {
            padding: 4px 8px;
            font-size: 13px;
        }

        .work-times-table td:first-child {
            color: var(--text-secondary);
        }

        .work-times-table td:last-child {
            text-align: right;
            font-weight: 500;
        }

        /* Loading and empty states */
        .loading, .empty {
            text-align: center;
            padding: 48px;
            color: var(--text-dim);
        }

        .spinner {
            width: 32px;
            height: 32px;
            border: 3px solid var(--border);
            border-top-color: var(--orange);
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 16px;
        }

        @keyframes spin { to { transform: rotate(360deg); } }

        /* Responsive */
        @media (max-width: 768px) {
            .nav { padding: 12px 16px; flex-wrap: wrap; }
            .nav-right { width: 100%; justify-content: flex-end; margin-top: 8px; }
            .time-range { margin: 12px 16px; flex-direction: column; align-items: stretch; }
            .time-inputs { margin-left: 0; margin-top: 8px; flex-wrap: wrap; }
            .main { padding: 0 16px 16px; }
            .sessions-table { font-size: 12px; }
            .sessions-table th, .sessions-table td { padding: 8px; }
        }
    </style>
</head>
<body>
    <nav class="nav">
        <a href="#" class="nav-brand">Overcode Analytics</a>
        <div class="nav-links">
            <a href="#dashboard" class="nav-link active" data-page="dashboard">Dashboard</a>
            <a href="#sessions" class="nav-link" data-page="sessions">Sessions</a>
            <a href="#timeline" class="nav-link" data-page="timeline">Timeline</a>
            <a href="#efficiency" class="nav-link" data-page="efficiency">Efficiency</a>
        </div>
        <div class="nav-right">
            <span id="last-update" style="font-size: 12px; color: var(--text-dim);">Loading...</span>
        </div>
    </nav>

    <div class="time-range">
        <span class="time-range-label">Time Range:</span>
        <div id="presets-container"></div>
        <div class="time-inputs">
            <input type="date" class="time-input" id="start-date">
            <input type="time" class="time-input" id="start-time" value="00:00">
            <span style="color: var(--text-dim);">to</span>
            <input type="date" class="time-input" id="end-date">
            <input type="time" class="time-input" id="end-time" value="23:59">
            <button class="preset-btn" id="apply-range">Apply</button>
            <button class="refresh-btn" id="refresh-btn">Refresh</button>
        </div>
    </div>

    <main class="main">
        <!-- Dashboard Page -->
        <div id="page-dashboard" class="page active">
            <div id="dashboard-summary" class="summary-grid"></div>
            <div class="chart-container">
                <h3 class="chart-title">Daily Activity</h3>
                <div class="chart-wrapper">
                    <canvas id="daily-chart"></canvas>
                </div>
            </div>
            <div class="chart-container">
                <h3 class="chart-title">Recent Sessions</h3>
                <div id="recent-sessions"></div>
            </div>
        </div>

        <!-- Sessions Page -->
        <div id="page-sessions" class="page">
            <div style="margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center;">
                <h2 style="font-size: 20px;">Session History</h2>
                <label style="display: flex; align-items: center; gap: 8px; color: var(--text-secondary); font-size: 13px;">
                    <input type="checkbox" id="show-all-sessions"> Show all (ignore time filter)
                </label>
            </div>
            <div id="sessions-table-container"></div>
        </div>

        <!-- Timeline Page -->
        <div id="page-timeline" class="page">
            <div class="timeline-container">
                <div class="timeline-header">
                    <h2 style="font-size: 20px;">Agent Timeline</h2>
                    <div class="timeline-controls">
                        <button class="timeline-btn" id="zoom-in">Zoom In</button>
                        <button class="timeline-btn" id="zoom-out">Zoom Out</button>
                    </div>
                </div>
                <div id="timeline-content"></div>
                <h3 class="chart-title" style="margin-top: 24px;">User Presence</h3>
                <div id="presence-timeline"></div>
                <div class="timeline-legend">
                    <div class="legend-item"><div class="legend-color" style="background: var(--green)"></div> Running</div>
                    <div class="legend-item"><div class="legend-color" style="background: var(--yellow)"></div> Idle/Waiting</div>
                    <div class="legend-item"><div class="legend-color" style="background: var(--orange)"></div> Needs Input</div>
                    <div class="legend-item"><div class="legend-color" style="background: var(--red)"></div> Error/Blocked</div>
                    <div class="legend-item"><div class="legend-color" style="background: var(--text-dim)"></div> Terminated</div>
                </div>
            </div>
        </div>

        <!-- Efficiency Page -->
        <div id="page-efficiency" class="page">
            <div id="efficiency-summary" class="summary-grid"></div>
            <div class="metrics-grid">
                <div class="metric-card">
                    <div class="metric-title">Presence Efficiency</div>
                    <div id="presence-efficiency-metrics"></div>
                </div>
                <div class="metric-card">
                    <div class="metric-title">Cost Efficiency</div>
                    <div id="cost-metrics"></div>
                </div>
                <div class="metric-card">
                    <div class="metric-title">Work Cycle Times</div>
                    <div id="work-time-metrics"></div>
                </div>
                <div class="metric-card">
                    <div class="metric-title">Interaction Breakdown</div>
                    <div id="interaction-metrics"></div>
                </div>
            </div>
            <div class="chart-container" style="margin-top: 24px;">
                <h3 class="chart-title">Efficiency Trend</h3>
                <div class="chart-wrapper">
                    <canvas id="efficiency-chart"></canvas>
                </div>
            </div>
        </div>
    </main>

    <script>
        // State
        let state = {
            currentPage: 'dashboard',
            presets: [],
            activePreset: null,
            startDate: null,
            endDate: null,
            data: {
                sessions: null,
                timeline: null,
                stats: null,
                daily: null,
            },
            charts: {
                daily: null,
                efficiency: null,
            },
            sortColumn: 'start_time',
            sortAsc: false,
        };

        // Initialize
        async function init() {
            await loadPresets();
            setupNavigation();
            setupTimeRangeControls();
            loadFromHash();
            await refreshData();
        }

        // Load presets from API
        async function loadPresets() {
            try {
                const resp = await fetch('/api/analytics/presets');
                state.presets = await resp.json();
            } catch (e) {
                state.presets = [
                    { name: 'Morning', start: '09:00', end: '12:00' },
                    { name: 'Full Day', start: '09:00', end: '17:00' },
                    { name: 'All Time', start: null, end: null },
                ];
            }
            renderPresets();
        }

        function renderPresets() {
            const container = document.getElementById('presets-container');
            container.innerHTML = state.presets.map(p => `
                <button class="preset-btn ${p.name === state.activePreset ? 'active' : ''}"
                        data-preset="${p.name}" data-start="${p.start || ''}" data-end="${p.end || ''}">
                    ${p.name}
                </button>
            `).join('');

            container.querySelectorAll('.preset-btn').forEach(btn => {
                btn.addEventListener('click', () => applyPreset(btn.dataset.preset, btn.dataset.start, btn.dataset.end));
            });
        }

        function applyPreset(name, startTime, endTime) {
            state.activePreset = name;
            const today = new Date().toISOString().split('T')[0];

            if (!startTime && !endTime) {
                // All time
                state.startDate = null;
                state.endDate = null;
            } else {
                state.startDate = `${today}T${startTime}:00`;
                state.endDate = `${today}T${endTime}:00`;
            }

            updateHash();
            renderPresets();
            refreshData();
        }

        function setupTimeRangeControls() {
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('start-date').value = today;
            document.getElementById('end-date').value = today;

            document.getElementById('apply-range').addEventListener('click', () => {
                const sd = document.getElementById('start-date').value;
                const st = document.getElementById('start-time').value;
                const ed = document.getElementById('end-date').value;
                const et = document.getElementById('end-time').value;

                state.startDate = `${sd}T${st}:00`;
                state.endDate = `${ed}T${et}:00`;
                state.activePreset = null;
                updateHash();
                renderPresets();
                refreshData();
            });

            document.getElementById('refresh-btn').addEventListener('click', refreshData);
        }

        function setupNavigation() {
            document.querySelectorAll('.nav-link').forEach(link => {
                link.addEventListener('click', (e) => {
                    e.preventDefault();
                    navigateTo(link.dataset.page);
                });
            });
        }

        function navigateTo(page) {
            state.currentPage = page;
            document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
            document.querySelector(`[data-page="${page}"]`).classList.add('active');
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            document.getElementById(`page-${page}`).classList.add('active');
            updateHash();
        }

        function updateHash() {
            const params = new URLSearchParams();
            params.set('page', state.currentPage);
            if (state.startDate) params.set('start', state.startDate);
            if (state.endDate) params.set('end', state.endDate);
            if (state.activePreset) params.set('preset', state.activePreset);
            window.location.hash = params.toString();
        }

        function loadFromHash() {
            const hash = window.location.hash.slice(1);
            if (!hash) return;

            const params = new URLSearchParams(hash);
            if (params.get('page')) state.currentPage = params.get('page');
            if (params.get('start')) state.startDate = params.get('start');
            if (params.get('end')) state.endDate = params.get('end');
            if (params.get('preset')) state.activePreset = params.get('preset');

            navigateTo(state.currentPage);
            renderPresets();
        }

        // Data fetching
        async function refreshData() {
            document.getElementById('last-update').textContent = 'Loading...';

            const params = new URLSearchParams();
            if (state.startDate) params.set('start', state.startDate);
            if (state.endDate) params.set('end', state.endDate);
            const qs = params.toString() ? '?' + params.toString() : '';

            try {
                const [sessions, timeline, stats, daily] = await Promise.all([
                    fetch('/api/analytics/sessions' + qs).then(r => r.json()),
                    fetch('/api/analytics/timeline' + qs).then(r => r.json()),
                    fetch('/api/analytics/stats' + qs).then(r => r.json()),
                    fetch('/api/analytics/daily' + qs).then(r => r.json()),
                ]);

                state.data = { sessions, timeline, stats, daily };
                renderAll();
                document.getElementById('last-update').textContent = 'Updated ' + new Date().toLocaleTimeString();
            } catch (e) {
                console.error('Failed to fetch data:', e);
                document.getElementById('last-update').textContent = 'Error loading data';
            }
        }

        // Rendering
        function renderAll() {
            renderDashboard();
            renderSessions();
            renderTimeline();
            renderEfficiency();
        }

        function renderDashboard() {
            const s = state.data.sessions?.summary || {};
            document.getElementById('dashboard-summary').innerHTML = `
                <div class="summary-card green">
                    <div class="summary-value">${s.session_count || 0}</div>
                    <div class="summary-label">Sessions</div>
                </div>
                <div class="summary-card orange">
                    <div class="summary-value">${formatTokens(s.total_tokens || 0)}</div>
                    <div class="summary-label">Total Tokens</div>
                </div>
                <div class="summary-card yellow">
                    <div class="summary-value">$${(s.total_cost_usd || 0).toFixed(2)}</div>
                    <div class="summary-label">Total Cost</div>
                </div>
                <div class="summary-card cyan">
                    <div class="summary-value">${s.avg_green_percent || 0}%</div>
                    <div class="summary-label">Avg Green Time</div>
                </div>
            `;

            renderDailyChart();
            renderRecentSessions();
        }

        function renderDailyChart() {
            const daily = state.data.daily;
            if (!daily?.days?.length) return;

            const ctx = document.getElementById('daily-chart').getContext('2d');
            if (state.charts.daily) state.charts.daily.destroy();

            state.charts.daily = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: daily.labels,
                    datasets: [{
                        label: 'Tokens',
                        data: daily.days.map(d => d.tokens),
                        backgroundColor: '#f97316',
                        yAxisID: 'y',
                    }, {
                        label: 'Cost ($)',
                        data: daily.days.map(d => d.cost_usd),
                        backgroundColor: '#eab308',
                        yAxisID: 'y1',
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: '#94a3b8' } } },
                    scales: {
                        x: { ticks: { color: '#64748b' }, grid: { color: '#334155' } },
                        y: { type: 'linear', position: 'left', ticks: { color: '#f97316' }, grid: { color: '#334155' } },
                        y1: { type: 'linear', position: 'right', ticks: { color: '#eab308' }, grid: { display: false } },
                    }
                }
            });
        }

        function renderRecentSessions() {
            const sessions = state.data.sessions?.sessions?.slice(0, 5) || [];
            if (!sessions.length) {
                document.getElementById('recent-sessions').innerHTML = '<div class="empty">No sessions found</div>';
                return;
            }

            document.getElementById('recent-sessions').innerHTML = `
                <table class="sessions-table">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Date</th>
                            <th>Tokens</th>
                            <th>Cost</th>
                            <th>Green %</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${sessions.map(s => `
                            <tr>
                                <td class="session-name">
                                    ${s.name}
                                    <span class="session-badge ${s.is_archived ? '' : 'active'}">${s.is_archived ? 'archived' : 'active'}</span>
                                </td>
                                <td>${formatDate(s.start_time)}</td>
                                <td>${formatTokens(s.total_tokens)}</td>
                                <td>$${s.estimated_cost_usd.toFixed(2)}</td>
                                <td style="color: ${s.green_percent > 50 ? 'var(--green)' : 'var(--yellow)'}">${s.green_percent}%</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
        }

        function renderSessions() {
            const showAll = document.getElementById('show-all-sessions').checked;
            let sessions = state.data.sessions?.sessions || [];

            // Sort
            sessions = [...sessions].sort((a, b) => {
                let av = a[state.sortColumn], bv = b[state.sortColumn];
                if (typeof av === 'string') return state.sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
                return state.sortAsc ? av - bv : bv - av;
            });

            const container = document.getElementById('sessions-table-container');
            if (!sessions.length) {
                container.innerHTML = '<div class="empty">No sessions found for this time range</div>';
                return;
            }

            const columns = [
                { key: 'name', label: 'Name' },
                { key: 'start_time', label: 'Date' },
                { key: 'green_time_seconds', label: 'Duration' },
                { key: 'total_tokens', label: 'Tokens' },
                { key: 'estimated_cost_usd', label: 'Cost' },
                { key: 'green_percent', label: 'Green %' },
                { key: 'interaction_count', label: 'Interactions' },
                { key: 'steers_count', label: 'Steers' },
            ];

            container.innerHTML = `
                <table class="sessions-table">
                    <thead>
                        <tr>
                            ${columns.map(c => `
                                <th class="${state.sortColumn === c.key ? 'sorted' : ''}"
                                    data-column="${c.key}">
                                    ${c.label}
                                    <span class="sort-icon">${state.sortColumn === c.key ? (state.sortAsc ? '↑' : '↓') : ''}</span>
                                </th>
                            `).join('')}
                        </tr>
                    </thead>
                    <tbody>
                        ${sessions.map(s => `
                            <tr class="expandable-row" data-id="${s.id}">
                                <td class="session-name">
                                    ${s.name}
                                    <span class="session-badge ${s.is_archived ? '' : 'active'}">${s.is_archived ? 'archived' : 'active'}</span>
                                </td>
                                <td>${formatDate(s.start_time)}</td>
                                <td>${formatDuration(s.green_time_seconds + s.non_green_time_seconds)}</td>
                                <td>${formatTokens(s.total_tokens)}</td>
                                <td>$${s.estimated_cost_usd.toFixed(2)}</td>
                                <td style="color: ${s.green_percent > 50 ? 'var(--green)' : 'var(--yellow)'}">${s.green_percent}%</td>
                                <td>${s.interaction_count}</td>
                                <td>${s.steers_count}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;

            // Sort handlers
            container.querySelectorAll('th[data-column]').forEach(th => {
                th.addEventListener('click', () => {
                    const col = th.dataset.column;
                    if (state.sortColumn === col) state.sortAsc = !state.sortAsc;
                    else { state.sortColumn = col; state.sortAsc = true; }
                    renderSessions();
                });
            });
        }

        function renderTimeline() {
            const timeline = state.data.timeline;
            if (!timeline?.agents || !Object.keys(timeline.agents).length) {
                document.getElementById('timeline-content').innerHTML = '<div class="empty">No timeline data available</div>';
                return;
            }

            const start = new Date(timeline.start).getTime();
            const end = new Date(timeline.end).getTime();
            const range = end - start;

            let html = '';
            for (const [agent, events] of Object.entries(timeline.agents)) {
                html += `<div class="timeline-row">
                    <div class="timeline-label">${agent}</div>
                    <div class="timeline-bar">
                        ${renderTimelineSegments(events, start, range)}
                    </div>
                </div>`;
            }
            document.getElementById('timeline-content').innerHTML = html;

            // Presence timeline
            if (timeline.presence?.length) {
                document.getElementById('presence-timeline').innerHTML = `
                    <div class="timeline-row">
                        <div class="timeline-label">User</div>
                        <div class="timeline-bar">
                            ${renderPresenceSegments(timeline.presence, start, range)}
                        </div>
                    </div>
                `;
            }
        }

        function renderTimelineSegments(events, start, range) {
            if (!events.length) return '';

            let html = '';
            for (let i = 0; i < events.length; i++) {
                const e = events[i];
                const eTime = new Date(e.timestamp).getTime();
                const left = ((eTime - start) / range) * 100;
                const nextTime = events[i + 1] ? new Date(events[i + 1].timestamp).getTime() : start + range;
                const width = Math.max(0.5, ((nextTime - eTime) / range) * 100);

                html += `<div class="timeline-segment" style="left:${left}%;width:${width}%;background:${e.color}" title="${e.status}: ${e.activity}"></div>`;
            }
            return html;
        }

        function renderPresenceSegments(events, start, range) {
            if (!events.length) return '';

            let html = '';
            for (let i = 0; i < events.length; i++) {
                const e = events[i];
                const eTime = new Date(e.timestamp).getTime();
                const left = ((eTime - start) / range) * 100;
                const nextTime = events[i + 1] ? new Date(events[i + 1].timestamp).getTime() : start + range;
                const width = Math.max(0.5, ((nextTime - eTime) / range) * 100);

                html += `<div class="timeline-segment" style="left:${left}%;width:${width}%;background:${e.color}" title="${e.state_name}"></div>`;
            }
            return html;
        }

        function renderEfficiency() {
            const stats = state.data.stats;
            if (!stats) return;

            const s = stats.summary || {};
            const e = stats.efficiency || {};
            const i = stats.interactions || {};
            const w = stats.work_times || {};
            const p = stats.presence_efficiency || {};

            document.getElementById('efficiency-summary').innerHTML = `
                <div class="summary-card green">
                    <div class="summary-value">${e.green_percent || 0}%</div>
                    <div class="summary-label">Green Time</div>
                </div>
                <div class="summary-card orange">
                    <div class="summary-value">$${e.cost_per_hour || 0}</div>
                    <div class="summary-label">Cost/Hour</div>
                </div>
                <div class="summary-card yellow">
                    <div class="summary-value">${e.spin_rate_percent || 0}%</div>
                    <div class="summary-label">Spin Rate</div>
                </div>
                <div class="summary-card cyan">
                    <div class="summary-value">${formatDuration(w.median || 0)}</div>
                    <div class="summary-label">Median Work Time</div>
                </div>
            `;

            // Presence efficiency metrics
            const presenceHasData = p.has_data;
            const presenceContent = presenceHasData ? `
                <div class="metric-value" style="color: var(--green)">${p.present_efficiency || 0}%</div>
                <div class="metric-sub">Green while you're active</div>
                <table class="work-times-table">
                    <tr><td>AFK efficiency</td><td style="color: var(--yellow)">${p.afk_efficiency || 0}%</td></tr>
                    <tr><td>Present samples</td><td>${p.present_samples || 0}</td></tr>
                    <tr><td>AFK samples</td><td>${p.afk_samples || 0}</td></tr>
                </table>
            ` : `
                <div class="metric-value dim">—</div>
                <div class="metric-sub">No presence data available</div>
                <div style="color: var(--text-muted); font-size: 12px; margin-top: 8px;">
                    Install presence tracking:<br>
                    <code style="color: var(--cyan)">pip install overcode[presence]</code>
                </div>
            `;
            document.getElementById('presence-efficiency-metrics').innerHTML = presenceContent;

            document.getElementById('cost-metrics').innerHTML = `
                <div class="metric-value">$${s.total_cost_usd || 0}</div>
                <div class="metric-sub">Total cost for period</div>
                <table class="work-times-table">
                    <tr><td>Per interaction</td><td>$${e.cost_per_interaction || 0}</td></tr>
                    <tr><td>Per hour</td><td>$${e.cost_per_hour || 0}</td></tr>
                    <tr><td>Total tokens</td><td>${formatTokens(s.total_tokens || 0)}</td></tr>
                </table>
            `;

            document.getElementById('work-time-metrics').innerHTML = `
                <div class="metric-value">${formatDuration(w.median || 0)}</div>
                <div class="metric-sub">Median work cycle time</div>
                <table class="work-times-table">
                    <tr><td>Mean</td><td>${formatDuration(w.mean || 0)}</td></tr>
                    <tr><td>P5</td><td>${formatDuration(w.p5 || 0)}</td></tr>
                    <tr><td>P95</td><td>${formatDuration(w.p95 || 0)}</td></tr>
                    <tr><td>Min</td><td>${formatDuration(w.min || 0)}</td></tr>
                    <tr><td>Max</td><td>${formatDuration(w.max || 0)}</td></tr>
                </table>
            `;

            document.getElementById('interaction-metrics').innerHTML = `
                <div class="metric-value">${i.total || 0}</div>
                <div class="metric-sub">Total interactions</div>
                <table class="work-times-table">
                    <tr><td>Human</td><td>${i.human || 0}</td></tr>
                    <tr><td>Robot steers</td><td>${i.robot_steers || 0}</td></tr>
                    <tr><td>Spin rate</td><td>${e.spin_rate_percent || 0}%</td></tr>
                </table>
            `;

            renderEfficiencyChart();
        }

        function renderEfficiencyChart() {
            const daily = state.data.daily;
            if (!daily?.days?.length) return;

            const ctx = document.getElementById('efficiency-chart').getContext('2d');
            if (state.charts.efficiency) state.charts.efficiency.destroy();

            state.charts.efficiency = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: daily.labels,
                    datasets: [{
                        label: 'Green %',
                        data: daily.days.map(d => d.green_percent),
                        borderColor: '#22c55e',
                        backgroundColor: 'rgba(34, 197, 94, 0.1)',
                        fill: true,
                        tension: 0.3,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: '#94a3b8' } } },
                    scales: {
                        x: { ticks: { color: '#64748b' }, grid: { color: '#334155' } },
                        y: { min: 0, max: 100, ticks: { color: '#22c55e' }, grid: { color: '#334155' } },
                    }
                }
            });
        }

        // Helpers
        function formatTokens(n) {
            if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
            if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
            return n.toString();
        }

        function formatDuration(seconds) {
            if (!seconds) return '-';
            if (seconds < 60) return Math.round(seconds) + 's';
            if (seconds < 3600) return Math.round(seconds / 60) + 'm';
            return (seconds / 3600).toFixed(1) + 'h';
        }

        function formatDate(iso) {
            if (!iso) return '-';
            const d = new Date(iso);
            return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }

        // Event handlers
        document.getElementById('show-all-sessions').addEventListener('change', renderSessions);
        window.addEventListener('hashchange', loadFromHash);

        // Start
        init();
    </script>
</body>
</html>"""
