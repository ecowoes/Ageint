/**
 * Workday Integration Monitor — Frontend JS
 * Handles API calls, UI state management, loading animations.
 */

const API = {
  query: '/api/v1/agent/query',
  analyzeTicket: '/api/v1/agent/analyze-ticket',
  pollTickets: '/api/v1/agent/poll-tickets',
  openTickets: '/api/v1/tickets/open',
  health: '/api/v1/health',
  kbIngest: '/api/v1/knowledge/ingest',
  kbIngestFile: '/api/v1/knowledge/ingest-file',
  kbSearch: '/api/v1/knowledge/search',
  kbStats: '/api/v1/knowledge/stats',
};

// ─── Panel Switching ──────────────────────────────────────────────────────

function switchPanel(name, btn) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById(`panel-${name}`).classList.add('active');
  btn.classList.add('active');

  if (name === 'tickets') loadTickets();
  if (name === 'knowledge') loadKBStats();
}

// ─── Health Check ─────────────────────────────────────────────────────────

async function checkHealth() {
  const dots = ['freshservice', 'workday', 'rag_vectorstore', 'llm'];
  dots.forEach(d => {
    const el = document.getElementById(`dot-${d}`);
    if (el) el.className = 'status-dot checking';
  });

  try {
    const res = await fetch(API.health);
    if (!res.ok) throw new Error('health check failed');
    const data = await res.json();
    for (const [service, ok] of Object.entries(data.checks)) {
      const el = document.getElementById(`dot-${service}`);
      if (el) el.className = `status-dot ${ok ? 'ok' : 'error'}`;
    }
  } catch (e) {
    dots.forEach(d => {
      const el = document.getElementById(`dot-${d}`);
      if (el) el.className = 'status-dot error';
    });
  }
}

// ─── Query Agent ──────────────────────────────────────────────────────────

async function submitQuery() {
  const query = document.getElementById('queryInput').value.trim();
  if (!query) { toast('Please enter a query.', 'error'); return; }

  const ticketIdVal = document.getElementById('ticketIdInput').value;
  const ticketId = ticketIdVal ? parseInt(ticketIdVal, 10) : null;
  const autoResolve = document.getElementById('autoResolveToggle').checked;
  const includeRag = document.getElementById('includeRagToggle').checked;

  showLoading(true);
  hideResults();

  const payload = {
    query,
    ticket_id: ticketId || null,
    include_rag: includeRag,
    auto_resolve: autoResolve,
  };

  try {
    animateLoadingSteps();
    const res = await fetch(API.query, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Request failed');
    }

    const data = await res.json();
    renderResults(data);
    toast('Analysis complete', 'success');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  } finally {
    showLoading(false);
  }
}

// ─── Render Results ───────────────────────────────────────────────────────

function renderResults(data) {
  // Confidence
  const pct = Math.round((data.confidence_score || 0) * 100);
  document.getElementById('confidenceLabel').textContent = `${pct}%`;
  setTimeout(() => {
    document.getElementById('confidenceFill').style.width = `${pct}%`;
  }, 100);

  // Main answer
  document.getElementById('mainAnswer').textContent = data.answer || 'No analysis generated.';

  // Meta grid
  const priorityLabels = { 1: 'Low', 2: 'Medium', 3: 'High', 4: 'Urgent' };
  const metaGrid = document.getElementById('metaGrid');
  const confClass = pct >= 85 ? 'ok' : pct >= 60 ? 'warn' : 'err';

  metaGrid.innerHTML = `
    <div class="meta-item">
      <div class="meta-key">Ticket ID</div>
      <div class="meta-val">${data.ticket_id ? `#${data.ticket_id}` : '—'}</div>
    </div>
    <div class="meta-item">
      <div class="meta-key">Confidence</div>
      <div class="meta-val ${confClass}">${pct}%</div>
    </div>
    <div class="meta-item">
      <div class="meta-key">Ticket Updated</div>
      <div class="meta-val ${data.ticket_updated ? 'ok' : ''}">${data.ticket_updated ? 'Yes' : 'No'}</div>
    </div>
    <div class="meta-item">
      <div class="meta-key">Processing</div>
      <div class="meta-val">${data.processing_time_ms}ms</div>
    </div>
    <div class="meta-item">
      <div class="meta-key">RAG Sources</div>
      <div class="meta-val">${data.rag_sources?.length ?? 0}</div>
    </div>
    <div class="meta-item">
      <div class="meta-key">Agent Steps</div>
      <div class="meta-val">${data.agent_iterations}</div>
    </div>
  `;

  // Sources
  const sourcesList = document.getElementById('sourcesList');
  if (data.rag_sources && data.rag_sources.length > 0) {
    sourcesList.innerHTML = data.rag_sources.map(s => `
      <div class="source-item">
        <div class="source-title">${escHtml(s.source)}</div>
        <div class="source-score">Relevance: ${(s.similarity_score * 100).toFixed(1)}%
          · ${escHtml(s.metadata?.category || '')}</div>
        <div class="source-snippet">${escHtml(s.content_snippet)}</div>
      </div>
    `).join('');
  } else {
    sourcesList.innerHTML = '<div class="no-sources">No matching knowledge base documents found</div>';
  }

  // Steps
  const stepsList = document.getElementById('stepsList');
  if (data.resolution_steps && data.resolution_steps.length > 0) {
    stepsList.innerHTML = data.resolution_steps
      .map(step => `<li>${escHtml(step)}</li>`)
      .join('');
  } else {
    stepsList.innerHTML = '<li>See analysis above for guidance</li>';
  }

  showResults();
}

// ─── Tickets ──────────────────────────────────────────────────────────────

async function loadTickets() {
  const container = document.getElementById('ticketsContainer');
  container.innerHTML = '<div class="empty-state"><div>Loading tickets...</div></div>';

  try {
    const res = await fetch(API.openTickets);
    if (!res.ok) throw new Error(`${res.status}`);
    const tickets = await res.json();

    if (!tickets.length) {
      container.innerHTML = '<div class="empty-state"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/></svg><p>No open tickets found</p></div>';
      return;
    }

    const priorityLabels = { 1: 'Low', 2: 'Medium', 3: 'High', 4: 'Urgent' };
    container.innerHTML = tickets.map(t => `
      <div class="ticket-card">
        <div class="ticket-header">
          <span class="ticket-id">#${t.id}</span>
          <span class="ticket-priority priority-${t.priority}">${priorityLabels[t.priority] || 'Unknown'}</span>
        </div>
        <div class="ticket-subject">${escHtml(t.subject)}</div>
        <div class="ticket-desc">${escHtml(t.description_text || t.description || '')}</div>
        <div class="ticket-actions">
          <button class="btn-sm btn-analyze" onclick="analyzeTicket(${t.id}, false)">Analyze</button>
          <button class="btn-sm btn-resolve" onclick="analyzeTicket(${t.id}, true)">Analyze + Resolve</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    container.innerHTML = `<div class="empty-state"><p style="color:var(--accent-red)">Failed to load tickets: ${e.message}</p></div>`;
  }
}

async function analyzeTicket(ticketId, autoResolve) {
  switchPanel('query', document.querySelector('[data-panel="query"]'));
  document.getElementById('ticketIdInput').value = ticketId;
  document.getElementById('autoResolveToggle').checked = autoResolve;
  document.getElementById('queryInput').value = `Analyze Freshservice ticket #${ticketId} for root cause and resolution.`;
  await submitQuery();
}

async function batchAnalyze() {
  toast('Batch analysis started...', 'info');
  try {
    const res = await fetch(API.pollTickets, { method: 'POST' });
    const results = await res.json();
    toast(`Batch complete: ${results.length} tickets processed`, 'success');
    loadTickets();
  } catch (e) {
    toast(`Batch failed: ${e.message}`, 'error');
  }
}

// ─── Knowledge Base ───────────────────────────────────────────────────────

async function ingestDocument() {
  const title = document.getElementById('kbTitle').value.trim();
  const content = document.getElementById('kbContent').value.trim();
  const category = document.getElementById('kbCategory').value;

  if (!title || !content) { toast('Title and content are required', 'error'); return; }

  try {
    const res = await fetch(API.kbIngest, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, content, category }),
    });
    if (!res.ok) throw new Error((await res.json()).detail);
    const data = await res.json();
    toast(`Ingested: "${title}"`, 'success');
    document.getElementById('kbTitle').value = '';
    document.getElementById('kbContent').value = '';
    loadKBStats();
  } catch (e) {
    toast(`Ingest failed: ${e.message}`, 'error');
  }
}

async function uploadFile() {
  const file = document.getElementById('kbFileInput').files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append('file', file);
  formData.append('category', document.getElementById('kbCategory').value);

  try {
    const res = await fetch(API.kbIngestFile, { method: 'POST', body: formData });
    if (!res.ok) throw new Error((await res.json()).detail);
    toast(`Uploaded: ${file.name}`, 'success');
    loadKBStats();
  } catch (e) {
    toast(`Upload failed: ${e.message}`, 'error');
  }
}

async function searchKB() {
  const query = document.getElementById('kbSearchInput').value.trim();
  if (!query) { toast('Enter a search query', 'error'); return; }

  const resultsEl = document.getElementById('kbResults');
  resultsEl.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px">Searching...</div>';

  try {
    const res = await fetch(API.kbSearch + `?query=${encodeURIComponent(query)}&top_k=5`, {
      method: 'POST',
    });
    const data = await res.json();

    if (!data.length) {
      resultsEl.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:8px">No results found</div>';
      return;
    }

    resultsEl.innerHTML = data.map(s => `
      <div class="source-item">
        <div class="source-title">${escHtml(s.source)}</div>
        <div class="source-score">Score: ${(s.similarity_score * 100).toFixed(1)}% · ${escHtml(s.metadata?.category || '')}</div>
        <div class="source-snippet">${escHtml(s.content_snippet)}</div>
      </div>
    `).join('');
  } catch (e) {
    resultsEl.innerHTML = `<div style="color:var(--accent-red);font-size:12px">Error: ${e.message}</div>`;
  }
}

async function loadKBStats() {
  try {
    const res = await fetch(API.kbStats);
    const data = await res.json();
    const el = document.getElementById('kbStatsContent');
    el.innerHTML = `
      <div style="display:flex;gap:20px;flex-wrap:wrap;margin-top:8px">
        <div class="meta-item" style="min-width:120px">
          <div class="meta-key">Total Chunks</div>
          <div class="meta-val">${data.total_chunks ?? 0}</div>
        </div>
        <div class="meta-item" style="min-width:120px">
          <div class="meta-key">Status</div>
          <div class="meta-val ${data.initialized ? 'ok' : 'err'}">${data.initialized ? 'Ready' : 'Offline'}</div>
        </div>
      </div>
    `;
  } catch (e) {
    document.getElementById('kbStatsContent').textContent = 'Could not load stats';
  }
}

// ─── Loading State ────────────────────────────────────────────────────────

let loadingStepTimer = null;

function animateLoadingSteps() {
  const steps = ['lstep-1', 'lstep-2', 'lstep-3', 'lstep-4'];
  steps.forEach(id => document.getElementById(id)?.classList.remove('active', 'done'));
  let i = 0;
  document.getElementById(steps[0])?.classList.add('active');

  loadingStepTimer = setInterval(() => {
    if (i < steps.length - 1) {
      document.getElementById(steps[i])?.classList.remove('active');
      document.getElementById(steps[i])?.classList.add('done');
      i++;
      document.getElementById(steps[i])?.classList.add('active');
    }
  }, 1800);
}

function showLoading(show) {
  const loading = document.getElementById('loadingState');
  const btn = document.getElementById('submitQueryBtn');
  loading.classList.toggle('hidden', !show);
  btn.disabled = show;
  if (!show && loadingStepTimer) {
    clearInterval(loadingStepTimer);
    loadingStepTimer = null;
  }
}

function showResults() {
  document.getElementById('resultsContainer').classList.remove('hidden');
}

function hideResults() {
  document.getElementById('resultsContainer').classList.add('hidden');
}

// ─── Toast ────────────────────────────────────────────────────────────────

function toast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      ${type === 'success' ? '<polyline points="20 6 9 17 4 12"/>'
        : type === 'error' ? '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'
        : '<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>'}
    </svg>
    ${escHtml(message)}
  `;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ─── Utilities ────────────────────────────────────────────────────────────

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ─── Keyboard Shortcut ────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    const activePanel = document.querySelector('.panel.active');
    if (activePanel?.id === 'panel-query') submitQuery();
  }
});

// ─── Init ─────────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', () => {
  checkHealth();
  setInterval(checkHealth, 30000);
  loadKBStats();
});
