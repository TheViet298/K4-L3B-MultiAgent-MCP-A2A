// A2A Multi-Agent Presentation Dashboard Logic

let currentCaseId = 'L3B_CASE_001';
let currentStepIndex = 0;
let isPlaying = false;
let playbackInterval = null;
let playbackSpeed = 1000; // ms per step
let filteredCaseIds = [];
let activeCategory = 'all';

// Initialize when DOM and window.A2A_DATA are ready
document.addEventListener('DOMContentLoaded', () => {
  if (!window.A2A_DATA) {
    console.error('Data not loaded');
    return;
  }
  initDashboard();
});

function initDashboard() {
  const data = window.A2A_DATA;
  filteredCaseIds = [...data.case_ids];

  // Populate Categories
  populateCategoryPills();

  // Populate Dropdown
  populateCaseDropdown();

  // Populate Agent Matrix in Architecture tab
  populateAgentMatrixTable();

  // Build Agents Visual Grid
  buildAgentsVisualGrid();

  // Load Initial Case
  loadCase(currentCaseId);
}

// Tab Switching
function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

  if (tabId === 'simulator') {
    document.getElementById('tabBtnSimulator').classList.add('active');
    document.getElementById('tabContentSimulator').classList.add('active');
  } else if (tabId === 'architecture') {
    document.getElementById('tabBtnArchitecture').classList.add('active');
    document.getElementById('tabContentArchitecture').classList.add('active');
  } else if (tabId === 'benchmark') {
    document.getElementById('tabBtnBenchmark').classList.add('active');
    document.getElementById('tabContentBenchmark').classList.add('active');
  }
}

// Populate Category Filter Pills
function populateCategoryPills() {
  const container = document.getElementById('categoryPillsContainer');
  const cats = [
    { id: 'all', label: 'Tất cả (100)' },
    { id: 'late_delivery_logistics', label: 'Logistics Delay (10)' },
    { id: 'late_delivery_seller', label: 'Seller Delay (10)' },
    { id: 'valid_split_payment', label: 'Split Payment (10)' },
    { id: 'payment_mismatch', label: 'Payment Mismatch (10)' },
    { id: 'duplicate_charge', label: 'Duplicate Charge (10)' },
    { id: 'refund_pending', label: 'Refund Pending (10)' },
    { id: 'refund_failed', label: 'Refund Failed (10)' },
    { id: 'canceled_order_paid', label: 'Canceled Order (10)' },
    { id: 'unavailable_order_paid', label: 'Unavailable Order (10)' },
    { id: 'unsupported_claim', label: 'Unsupported Claim (10)' }
  ];

  container.innerHTML = cats.map(c => `
    <button class="cat-pill ${c.id === 'all' ? 'active' : ''}" onclick="filterByCategory('${c.id}', this)">
      ${c.label}
    </button>
  `).join('');
}

function filterByCategory(catId, btnEl) {
  activeCategory = catId;
  document.querySelectorAll('.cat-pill').forEach(b => b.classList.remove('active'));
  if (btnEl) btnEl.classList.add('active');

  const data = window.A2A_DATA;
  if (catId === 'all') {
    filteredCaseIds = [...data.case_ids];
  } else {
    filteredCaseIds = data.case_ids.filter(cid => {
      const out = data.outputs[cid];
      return out && out.assessment && out.assessment.primary_issue === catId;
    });
  }

  populateCaseDropdown();
  if (filteredCaseIds.length > 0) {
    loadCase(filteredCaseIds[0]);
  }
}

function populateCaseDropdown() {
  const select = document.getElementById('caseSelect');
  const data = window.A2A_DATA;

  select.innerHTML = filteredCaseIds.map(cid => {
    const out = data.outputs[cid] || {};
    const issue = (out.assessment && out.assessment.primary_issue) || 'unknown';
    const refund = (out.financial_resolution && out.financial_resolution.recommended_refund_brl) || 0;
    return `<option value="${cid}">${cid} &bull; ${issue} &bull; R$ ${refund.toFixed(2)}</option>`;
  }).join('');

  select.value = currentCaseId;
}

function onCaseSelected(caseId) {
  loadCase(caseId);
}

function onSearchFilter(text) {
  const query = text.trim().toLowerCase();
  const data = window.A2A_DATA;

  filteredCaseIds = data.case_ids.filter(cid => {
    const inp = data.inputs[cid] || {};
    const out = data.outputs[cid] || {};
    const issue = (out.assessment && out.assessment.primary_issue) || '';
    const orderId = (out.affected_entities && out.affected_entities.order_ids && out.affected_entities.order_ids[0]) || '';
    return cid.toLowerCase().includes(query) || issue.toLowerCase().includes(query) || orderId.toLowerCase().includes(query);
  });

  populateCaseDropdown();
  if (filteredCaseIds.length > 0) {
    loadCase(filteredCaseIds[0]);
  }
}

function selectRandomCase() {
  const data = window.A2A_DATA;
  const randomIndex = Math.floor(Math.random() * data.case_ids.length);
  const randomId = data.case_ids[randomIndex];
  loadCase(randomId);
}

function navigateCase(delta) {
  const idx = filteredCaseIds.indexOf(currentCaseId);
  if (idx === -1) return;
  const nextIdx = (idx + delta + filteredCaseIds.length) % filteredCaseIds.length;
  loadCase(filteredCaseIds[nextIdx]);
}

// Main Load Case Function
function loadCase(caseId) {
  pausePlayback();
  currentCaseId = caseId;
  const data = window.A2A_DATA;
  const inp = data.inputs[caseId];
  const out = data.outputs[caseId];
  const events = data.traces[caseId] || [];

  if (!inp || !out) return;

  // Sync selector
  const select = document.getElementById('caseSelect');
  if (select) select.value = caseId;

  // 1. Left Column: Intake & Entity Resolution
  document.getElementById('caseBadge').textContent = caseId;
  document.getElementById('caseOpenedAt').textContent = (inp.opened_at || '').substring(0, 19).replace('T', ' ');
  document.getElementById('casePolicy').textContent = inp.policy_version || 'EC_POLICY_V2';
  document.getElementById('caseCustomerHint').textContent = inp.customer_unique_id_hint || 'None';
  document.getElementById('customerMessage').textContent = inp.customer_request ? inp.customer_request.message : 'No message';

  // Claims
  const claimsContainer = document.getElementById('claimsContainer');
  const claims = (inp.customer_request && inp.customer_request.claims) || [];
  const claimAssessments = out.claim_assessments || [];
  const assessmentMap = {};
  claimAssessments.forEach(ca => { assessmentMap[ca.claim_id] = ca; });

  claimsContainer.innerHTML = claims.map(c => {
    const ca = assessmentMap[c.claim_id] || {};
    const verdict = ca.verdict || 'pending';
    return `
      <div class="claim-item">
        <div class="claim-info">
          <span class="claim-id">${c.claim_id}</span>
          <span class="claim-topic">${c.topic}</span>
        </div>
        <span class="verdict-badge verdict-${verdict}">${verdict}</span>
      </div>
    `;
  }).join('');

  // Candidates
  const candContainer = document.getElementById('candidatesContainer');
  const candidates = inp.candidate_order_ids || [];
  const resolvedOrders = (out.entity_resolution && out.entity_resolution.resolved_order_ids) || [];
  const resolvedId = resolvedOrders[0] || '';

  candContainer.innerHTML = candidates.map(cand => {
    const isResolved = cand === resolvedId;
    return `
      <div class="candidate-item ${isResolved ? 'resolved' : 'rejected'}">
        <span>${cand}</span>
        <span>${isResolved ? '✓ RESOLVED GENUINE' : '✕ REJECTED SPOOF'}</span>
      </div>
    `;
  }).join('');

  const related = (out.customer_context && out.customer_context.related_order_ids) || [];
  document.getElementById('relatedOrders').textContent = related.length ? related.join(', ') : 'None';

  // 2. Right Column: Resolution & Adjudication
  const primaryIssue = (out.assessment && out.assessment.primary_issue) || 'UNKNOWN';
  const status = (out.assessment && out.assessment.case_status) || 'action_required';
  const actions = out.resolution_actions || [];
  const fin = out.financial_resolution || {};
  const refundBrl = fin.recommended_refund_brl || 0;

  document.getElementById('resPrimaryIssue').textContent = primaryIssue;
  document.getElementById('resActionTitle').textContent = actions.length ? actions[0].toUpperCase() : 'NO_ACTION';
  
  const statusPill = document.getElementById('resStatusPill');
  statusPill.className = `status-pill status-${status}`;
  statusPill.textContent = status;

  document.getElementById('recommendedRefundVal').textContent = `R$ ${refundBrl.toFixed(2)}`;

  // Refund lines table
  const linesBody = document.getElementById('refundLinesBody');
  const lines = fin.refund_lines || [];
  if (lines.length === 0) {
    linesBody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: var(--text-muted);">Không phát sinh hoàn tiền (No refund required)</td></tr>`;
  } else {
    linesBody.innerHTML = lines.map(line => `
      <tr>
        <td style="color: #38bdf8;">${line.reason_code}</td>
        <td style="color: #34d399; font-weight: 700;">R$ ${Number(line.amount_brl).toFixed(2)}</td>
        <td style="color: #a78bfa;">${line.entity_id || 'Platform / N/A'}</td>
      </tr>
    `).join('');
  }

  // Shipment & Payment verdicts
  const shipAnalysis = out.shipment_analysis || {};
  const payAnalysis = out.payment_analysis || {};
  document.getElementById('shipmentVerdict').textContent = shipAnalysis.verdict || 'N/A';
  document.getElementById('paymentVerdict').textContent = payAnalysis.verdict || 'N/A';
  document.getElementById('capturedTotal').textContent = payAnalysis.captured_total_brl !== null ? `R$ ${payAnalysis.captured_total_brl}` : 'N/A';
  
  const lateSellers = shipAnalysis.late_seller_ids || [];
  document.getElementById('lateSellers').textContent = lateSellers.length ? lateSellers.join(', ') : 'None';

  // Data Conflicts
  const conflictsContainer = document.getElementById('conflictsContainer');
  const conflicts = out.data_conflicts || [];
  document.getElementById('conflictCountChip').textContent = `${conflicts.length} Conflict${conflicts.length > 1 ? 's' : ''}`;
  if (conflicts.length === 0) {
    conflictsContainer.innerHTML = `<div style="font-size: 0.78rem; color: var(--text-muted); font-style: italic;">Không phát hiện mâu thuẫn dữ liệu đa nguồn (All sources reconciled).</div>`;
  } else {
    conflictsContainer.innerHTML = conflicts.map(cf => `
      <div class="meta-item" style="margin-bottom: 6px;">
        <div class="label" style="color: #f43f5e;">Field Conflict: ${cf.field}</div>
        <div style="font-size: 0.75rem; color: #cbd5e1; margin-top: 2px;">
          Sources: <span style="font-family: var(--font-mono); color: #38bdf8;">${cf.sources.join(' vs ')}</span>
        </div>
        <div style="font-size: 0.75rem; color: #34d399; margin-top: 2px;">
          Selected: <strong>${cf.selected_source}</strong> (${cf.resolution_code})
        </div>
      </div>
    `).join('');
  }

  // 3. Center Column: Events & Timeline Scrubber
  document.getElementById('traceTotalEvents').textContent = `${events.length} Events`;
  const scrubber = document.getElementById('traceScrubber');
  scrubber.max = Math.max(0, events.length - 1);
  scrubber.value = 0;
  currentStepIndex = 0;

  // Build Trace Feed
  const feed = document.getElementById('traceLogFeed');
  feed.innerHTML = events.map((ev, i) => {
    const actor = ev.actor || 'system';
    const target = ev.target || (ev.tool_name ? `tool: ${ev.tool_name}` : '');
    return `
      <div class="trace-row" id="traceRow-${i}" onclick="playbackGoTo(${i})">
        <span style="color: var(--text-muted);">${i + 1}</span>
        <span style="color: ${getActorColor(actor)}; font-weight: 700;">${actor}</span>
        <span style="color: #93c5fd;">${ev.event_type}</span>
        <span style="color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          ${target ? `&rarr; ${target}` : (ev.decision_code || JSON.stringify(ev.attributes || ''))}
        </span>
      </div>
    `;
  }).join('');

  // Tool Calls list
  const toolCallsList = document.getElementById('toolCallsList');
  const toolEvents = events.filter(e => e.event_type === 'tool_result_consumed');
  document.getElementById('mcpBudgetChip').textContent = `${toolEvents.length} / 5 Calls (Optimal Budget)`;

  toolCallsList.innerHTML = toolEvents.map((te, idx) => {
    const refs = te.evidence_refs || [];
    return `
      <div class="claim-item" style="border-left: 3px solid #6366f1;">
        <div class="claim-info" style="flex: 1;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-size: 0.82rem; font-weight: 700; color: #fff;">${idx + 1}. ${te.tool_name}</span>
            <span style="font-size: 0.7rem; color: #a5b4fc; font-family: var(--font-mono);">${te.actor}</span>
          </div>
          <div style="font-size: 0.72rem; color: var(--accent-cyan); font-family: var(--font-mono); margin-top: 4px; word-break: break-all;">
            Ref: ${refs.length ? refs[0] : 'None'}
          </div>
        </div>
      </div>
    `;
  }).join('');

  // Set initial step
  updateStepView(0);
}

// Build Visual Grid of the 8 Agents
function buildAgentsVisualGrid() {
  const container = document.getElementById('agentsVisualGrid');
  const agents = window.A2A_DATA.agents_info;
  const icons = {
    coordinator: '👑',
    entity_resolver: '🔍',
    policy_agent: '📜',
    order_product_specialist: '📦',
    shipment_specialist: '🚚',
    payment_refund_specialist: '💳',
    conflict_resolver: '⚖️',
    verifier: '🛡️'
  };

  container.innerHTML = Object.entries(agents).map(([id, info]) => `
    <div class="agent-node" id="agentNode-${id}" style="--glow-color: ${info.color}">
      <div class="node-icon" style="background: ${info.color}22; border: 1px solid ${info.color}88; color: ${info.color}">
        ${icons[id] || '🤖'}
      </div>
      <div class="node-name">${info.name.replace(' Agent', '')}</div>
      <div class="node-role">${info.badge}</div>
    </div>
  `).join('');
}

// Playback Timeline Functions
function updateStepView(index) {
  const data = window.A2A_DATA;
  const events = data.traces[currentCaseId] || [];
  if (!events.length) return;

  currentStepIndex = Math.min(Math.max(0, index), events.length - 1);
  const ev = events[currentStepIndex];

  // Update Scrubber & Counter
  document.getElementById('traceScrubber').value = currentStepIndex;
  document.getElementById('stepCounter').textContent = `Step ${currentStepIndex + 1} / ${events.length}`;

  // Highlight Trace Feed Row
  document.querySelectorAll('.trace-row').forEach(r => r.classList.remove('active-row'));
  const activeRow = document.getElementById(`traceRow-${currentStepIndex}`);
  if (activeRow) {
    activeRow.classList.add('active-row');
    activeRow.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // Update Event Card
  const badge = document.getElementById('currentEventTypeBadge');
  badge.textContent = ev.event_type;
  badge.style.background = `${getEventTypeColor(ev.event_type)}25`;
  badge.style.color = getEventTypeColor(ev.event_type);

  document.getElementById('currentEventActor').textContent = ev.actor || 'system';
  document.getElementById('currentEventAction').textContent = ev.tool_name 
    ? `called MCP tool: ${ev.tool_name}` 
    : (ev.target ? `handoff to &rarr; ${ev.target}` : (ev.decision_code || ''));
  document.getElementById('currentEventTime').textContent = `+${(currentStepIndex * 0.25).toFixed(2)}s`;

  // Highlight Active Agent
  const actor = ev.actor || 'coordinator';
  const target = ev.target;
  highlightActiveAgents(actor, target);
}

function highlightActiveAgents(actor, target) {
  document.querySelectorAll('.agent-node').forEach(node => node.classList.remove('active-glow'));

  const actorNode = document.getElementById(`agentNode-${actor}`);
  if (actorNode) actorNode.classList.add('active-glow');

  if (target && target !== actor) {
    const targetNode = document.getElementById(`agentNode-${target}`);
    if (targetNode) targetNode.classList.add('active-glow');
  }

  const label = document.getElementById('activeAgentLabel');
  if (label) {
    label.textContent = target ? `Collaboration: ${actor} &rarr; ${target}` : `Active: ${actor}`;
  }
}

function playbackStep(delta) {
  const data = window.A2A_DATA;
  const events = data.traces[currentCaseId] || [];
  const next = currentStepIndex + delta;
  if (next >= 0 && next < events.length) {
    updateStepView(next);
  }
}

function playbackGoTo(index) {
  updateStepView(index);
}

function playbackGoToLast() {
  const data = window.A2A_DATA;
  const events = data.traces[currentCaseId] || [];
  updateStepView(events.length - 1);
}

function onScrubberMoved(val) {
  updateStepView(parseInt(val, 10));
}

function togglePlayPause() {
  if (isPlaying) {
    pausePlayback();
  } else {
    startPlayback();
  }
}

function startPlayback() {
  const data = window.A2A_DATA;
  const events = data.traces[currentCaseId] || [];
  if (currentStepIndex >= events.length - 1) {
    currentStepIndex = 0;
  }

  isPlaying = true;
  document.getElementById('btnPlayPause').textContent = '⏸';

  playbackInterval = setInterval(() => {
    if (currentStepIndex < events.length - 1) {
      updateStepView(currentStepIndex + 1);
    } else {
      pausePlayback();
    }
  }, playbackSpeed);
}

function pausePlayback() {
  isPlaying = false;
  const btn = document.getElementById('btnPlayPause');
  if (btn) btn.textContent = '▶';
  if (playbackInterval) {
    clearInterval(playbackInterval);
    playbackInterval = null;
  }
}

function setSpeed(multiplier) {
  playbackSpeed = 1000 / multiplier;
  document.querySelectorAll('.cat-pill').forEach(b => {
    if (b.id && b.id.startsWith('speed')) b.classList.remove('active');
  });
  const btn = document.getElementById(`speed${multiplier}x`);
  if (btn) btn.classList.add('active');

  if (isPlaying) {
    pausePlayback();
    startPlayback();
  }
}

// Helpers
function getActorColor(actor) {
  const colors = {
    coordinator: '#818cf8',
    entity_resolver: '#22d3ee',
    policy_agent: '#a78bfa',
    order_product_specialist: '#60a5fa',
    shipment_specialist: '#34d399',
    payment_refund_specialist: '#fbbf24',
    conflict_resolver: '#f472b6',
    verifier: '#2dd4bf'
  };
  return colors[actor] || '#e2e8f0';
}

function getEventTypeColor(type) {
  const colors = {
    case_received: '#38bdf8',
    task_assigned: '#818cf8',
    tool_result_consumed: '#fbbf24',
    handoff: '#c084fc',
    policy_decided: '#a78bfa',
    verification_completed: '#34d399',
    case_finalized: '#10b981'
  };
  return colors[type] || '#94a3b8';
}

function populateAgentMatrixTable() {
  const tbody = document.getElementById('agentMatrixTableBody');
  const agents = window.A2A_DATA.agents_info;
  const toolMap = {
    coordinator: 'Không gọi tool trực tiếp (quản lý phiên vòng đời)',
    entity_resolver: '<code>get_customer_history</code>, <code>get_order</code>',
    policy_agent: '<code>get_policy</code>',
    order_product_specialist: '<code>get_order_items</code>',
    shipment_specialist: '<code>get_shipment_summary</code>',
    payment_refund_specialist: '<code>get_payment_timeline</code>, <code>get_refund_timeline</code>',
    conflict_resolver: 'Thuật toán nội bộ (temporal proximity rule)',
    verifier: 'Kiểm định 7 invariants hợp đồng JSON'
  };
  const eventsMap = {
    coordinator: '<code>case_received</code>, <code>task_assigned</code>, <code>case_finalized</code>',
    entity_resolver: '<code>tool_result_consumed</code>, <code>handoff</code>',
    policy_agent: '<code>tool_result_consumed</code>, <code>policy_decided</code>, <code>handoff</code>',
    order_product_specialist: '<code>tool_result_consumed</code>, <code>handoff</code>',
    shipment_specialist: '<code>tool_result_consumed</code>, <code>handoff</code>',
    payment_refund_specialist: '<code>tool_result_consumed</code>, <code>handoff</code>',
    conflict_resolver: 'Ghi nhận mục <code>data_conflicts</code>',
    verifier: '<code>verification_completed</code>, <code>handoff</code>'
  };

  tbody.innerHTML = Object.entries(agents).map(([id, info]) => `
    <tr>
      <td>
        <span style="color: ${info.color}; font-weight: 700;">${info.name}</span>
        <div style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-mono);">${id}</div>
      </td>
      <td><span class="status-pill status-needs_investigation">${info.badge}</span></td>
      <td style="max-width: 320px; font-size: 0.8rem; color: #cbd5e1;">${info.description}</td>
      <td style="font-size: 0.76rem;">${toolMap[id] || '--'}</td>
      <td style="font-size: 0.76rem;">${eventsMap[id] || '--'}</td>
    </tr>
  `).join('');
}
