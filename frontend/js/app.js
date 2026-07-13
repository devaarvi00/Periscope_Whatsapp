/* ── Hyperscope SPA ─────────────────────────────────────────────── */

// ── Global State ──────────────────────────────────────────────── //
const State = {
  agent: null,
  currentView: 'inbox',
  inbox: {
    chats: [], selectedChatId: null,
    messages: [], filter: 'all', search: '',
  },
  tickets: { list: [], filter: 'all' },
  contacts: { list: [], search: '' },
  labels: [],
  phones: [],
  ws: null,
};

// ── Utils ──────────────────────────────────────────────────────── //
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function timeAgo(ts) {
  if (!ts) return '';
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
  const diff = Date.now() - d.getTime();
  if (diff < 60000) return 'now';
  if (diff < 3600000) return Math.floor(diff/60000) + 'm';
  if (diff < 86400000) return Math.floor(diff/3600000) + 'h';
  return d.toLocaleDateString('en', {month:'short', day:'numeric'});
}

function fmt(ts) {
  if (!ts) return '';
  const d = new Date(typeof ts === 'number' ? ts * 1000 : ts);
  return d.toLocaleTimeString('en', {hour:'2-digit', minute:'2-digit'});
}

function initials(name) {
  if (!name) return '?';
  return name.split(' ').slice(0, 2).map(w => w[0]).join('').toUpperCase();
}

// Human-friendly chat name: hide raw WhatsApp IDs like 1203...@g.us / 987...@lid
function displayName(nameOrChat) {
  const raw = typeof nameOrChat === 'object'
    ? (nameOrChat.name || nameOrChat.chat_wid || '')
    : (nameOrChat || '');
  if (!raw.includes('@')) return raw;
  const [id, domain] = raw.split('@');
  if (domain === 'g.us') return `Group ${id.slice(-6)}`;
  if (domain === 'lid') return 'WhatsApp user';
  if (/^\d{6,}$/.test(id)) return `+${id}`;
  return id;
}

// Thread subtitle: never expose raw WhatsApp IDs (@lid/@c.us/@g.us)
function chatSubtitle(chat) {
  if (!chat) return '';
  if (chat.is_group) return '👥 Group';
  const wid = chat.chat_wid || '';
  const [id, domain] = wid.split('@');
  if (domain === 'lid') return 'WhatsApp';           // anonymised id — not a dialable number
  if (/^\d{6,}$/.test(id)) return `+${id}`;
  return '';
}

function avatarColor(name) {
  const colors = ['#0D8C7C','#2563EB','#7C3AED','#DB2777','#D97706','#059669'];
  let h = 0;
  for (let i = 0; i < (name||'').length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xfffffff;
  return colors[h % colors.length];
}

// ── Label picker popover (create-on-the-fly, per docs) ──────────
let _labelPickerEl = null;
function closeLabelPicker() { if (_labelPickerEl) { _labelPickerEl.remove(); _labelPickerEl = null; } }
document.addEventListener('click', e => {
  if (_labelPickerEl && !_labelPickerEl.contains(e.target)) closeLabelPicker();
});

/**
 * openLabelPicker(anchorEl, { applied:Set<int>, onToggle(label, nowApplied) })
 * Shows all org labels with checkmarks; typing a new name offers "Create".
 */
function openLabelPicker(anchorEl, opts) {
  closeLabelPicker();
  const rect = anchorEl.getBoundingClientRect();
  const el = document.createElement('div');
  el.className = 'label-picker';
  el.style.left = Math.min(rect.left, window.innerWidth - 250) + 'px';
  el.style.top = (rect.bottom + 6) + 'px';
  el.style.position = 'fixed';
  document.body.appendChild(el);
  _labelPickerEl = el;

  function renderRows(query) {
    const q = (query || '').toLowerCase();
    const matches = State.labels.filter(l => !q || l.name.toLowerCase().includes(q));
    const exact = State.labels.some(l => l.name.toLowerCase() === q);
    el.querySelector('.lp-list').innerHTML =
      matches.map(l => `
        <div class="lp-row" data-lid="${l.id}">
          <span class="lp-dot" style="background:${l.color}"></span>
          <span style="flex:1">${esc(l.name)}</span>
          ${opts.applied.has(l.id) ? '<span style="color:var(--accent)">✓</span>' : ''}
        </div>`).join('') +
      (q && !exact ? `<div class="lp-row lp-create" data-create="${esc(query)}">+ Create "${esc(query)}"</div>` : '') +
      (!matches.length && !q ? '<div class="lp-row" style="color:var(--text-3)">No labels yet — type to create</div>' : '');

    el.querySelectorAll('.lp-row[data-lid]').forEach(row => row.addEventListener('click', async () => {
      const label = State.labels.find(l => l.id == row.dataset.lid);
      const nowApplied = !opts.applied.has(label.id);
      try {
        await opts.onToggle(label, nowApplied);
        nowApplied ? opts.applied.add(label.id) : opts.applied.delete(label.id);
        renderRows(el.querySelector('input').value.trim());
      } catch(e) { toast(e.message, 'error'); }
    }));
    const createRow = el.querySelector('.lp-row[data-create]');
    if (createRow) createRow.addEventListener('click', async () => {
      try {
        const label = await Api.labels.create({ name: createRow.dataset.create });
        State.labels.push(label);
        await opts.onToggle(label, true);
        opts.applied.add(label.id);
        renderRows('');
        el.querySelector('input').value = '';
        toast(`Label "${label.name}" created`, 'success');
      } catch(e) { toast(e.message, 'error'); }
    });
  }

  el.innerHTML = `<input type="text" placeholder="Search or create label..."><div class="lp-list"></div>`;
  const input = el.querySelector('input');
  input.addEventListener('input', () => renderRows(input.value.trim()));
  input.addEventListener('click', e => e.stopPropagation());
  renderRows('');
  setTimeout(() => input.focus(), 30);
}

function toast(msg, type = 'default') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'toast' + (type !== 'default' ? ' ' + type : '');
  el.style.display = 'block';
  clearTimeout(el._t);
  el._t = setTimeout(() => el.style.display = 'none', 3200);
}

function showModal(title, bodyHTML, onClose) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = bodyHTML;
  document.getElementById('modal-overlay').style.display = 'flex';
  document.getElementById('modal-close').onclick = () => closeModal(onClose);
  document.getElementById('modal-overlay').onclick = e => {
    if (e.target === document.getElementById('modal-overlay')) closeModal(onClose);
  };
}

function closeModal(cb) {
  document.getElementById('modal-overlay').style.display = 'none';
  if (cb) cb();
}

function pillClass(val) {
  const map = {
    open:'pill-open', in_progress:'pill-in_progress', resolved:'pill-resolved', closed:'pill-closed',
    low:'pill-low', medium:'pill-medium', high:'pill-high', urgent:'pill-urgent',
    active:'pill-active', inactive:'pill-inactive', ACTIVE:'pill-active', INACTIVE:'pill-inactive',
    THINKING:'pill-in_progress', SNOOZED:'pill-closed',
  };
  return 'pill ' + (map[val] || '');
}

function labelChip(label) {
  return `<span class="label-chip" style="background:${esc(label.color)}22;color:${esc(label.color)};border:1px solid ${esc(label.color)}55">${esc(label.name)}</span>`;
}

function formatTrigger(trigger) {
  const map = {
    message_received: '📩 Message Received',
    message_keyword: '🔑 Keyword Match',
    chat_created: '💬 Chat Created',
    ticket_created: '🎫 Ticket Created',
    ticket_updated: '🔄 Ticket Updated',
    chat_assigned: '👤 Chat Assigned',
    no_reply_timeout: '⏰ No Reply Timeout',
    label_added: '🏷️ Label Added',
  };
  return map[trigger] || String(trigger).split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

function formatAction(action) {
  const type = typeof action === 'object' ? action.type : action;
  const map = {
    send_message: '💬 Send Message',
    assign_to_agent: '👤 Assign to Agent',
    create_ticket: '🎫 Create Ticket',
    add_label: '🏷️ Add Label',
    remove_label: '🏷️ Remove Label',
    flag_chat: '🚩 Flag Chat',
    archive_chat: '📥 Archive Chat',
    activate_ai: '🤖 Activate AI Agent',
    send_note: '📝 Add Private Note',
    escalate: '🚨 Escalate Alert',
  };
  return map[type] || String(type).split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

// ── Auth ───────────────────────────────────────────────────────── //
async function checkAuth() {
  if (!Api.getToken()) { showLogin(); return; }
  try {
    State.agent = await Api.auth.me();
    showApp();
  } catch(_) { showLogin(); }
}

function showLogin() {
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('app-shell').style.display = 'none';
}

function showApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app-shell').style.display = 'flex';
  renderAgent();
  const hashView = location.hash.replace('#','');
  navigateTo(hashView || 'dashboard');
  loadLabels();
  loadPhones();
  connectWS();
}

function renderAgent() {
  const a = State.agent;
  if (!a) return;
  document.getElementById('agent-name').textContent = a.name;
  document.getElementById('agent-role').textContent = a.role;
  const av = document.getElementById('agent-avatar');
  av.textContent = initials(a.name);
  av.style.background = avatarColor(a.name);
  const be = document.getElementById('brand-agent-email');
  if (be) be.textContent = a.email || '';
  // Topbar
  const ta = document.getElementById('topbar-avatar');
  const tn = document.getElementById('topbar-name');
  if (ta) { ta.textContent = initials(a.name); ta.style.background = avatarColor(a.name); }
  if (tn) tn.textContent = a.name.split(' ')[0];

  // Topbar dropdown agent info
  const dan = document.getElementById('dropdown-agent-name');
  const dae = document.getElementById('dropdown-agent-email');
  if (dan) dan.textContent = a.name;
  if (dae) dae.textContent = a.email || '';
}

// ── Topbar: refresh current view ─────────────────────────────────
document.getElementById('topbar-refresh')?.addEventListener('click', () => {
  navigateTo(State.currentView || 'dashboard');
});

// ── Topbar: global search with dropdown results ──────────────────
(() => {
  const input = document.getElementById('global-search');
  if (!input) return;
  const wrap = input.parentElement;
  let box = null, timer = null;

  function closeResults() { if (box) { box.remove(); box = null; } }
  document.addEventListener('click', e => { if (!wrap.contains(e.target)) closeResults(); });

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { closeResults(); return; }
    timer = setTimeout(async () => {
      try {
        const res = await Api.search(q);
        closeResults();
        box = document.createElement('div');
        box.className = 'gs-results';
        const section = (title, rows) => rows && rows.length
          ? `<div class="gs-group">${title}</div>` + rows.join('') : '';
        const chatRows = (res.chats || []).slice(0, 5).map(c =>
          `<div class="gs-row" data-go="chat" data-id="${c.id}">💬 ${esc(c.name)}<span class="gs-sub">${c.is_group ? 'group' : 'chat'}</span></div>`);
        const msgRows = (res.messages || []).slice(0, 5).map(m =>
          `<div class="gs-row" data-go="chat" data-id="${m.chat_id}">📩 ${esc((m.body || '').slice(0, 60))}<span class="gs-sub">message</span></div>`);
        const tkRows = (res.tickets || []).slice(0, 5).map(t =>
          `<div class="gs-row" data-go="tickets">🎫 ${esc(t.title)}<span class="gs-sub">${esc(t.status || '')}</span></div>`);
        const ctRows = (res.contacts || []).slice(0, 5).map(c =>
          `<div class="gs-row" data-go="contacts">👤 ${esc(c.name || c.phone_number)}<span class="gs-sub">contact</span></div>`);
        const html = section('Chats', chatRows) + section('Messages', msgRows)
                   + section('Tickets', tkRows) + section('Contacts', ctRows);
        box.innerHTML = html || '<div class="gs-row" style="color:var(--text-3)">No results</div>';
        wrap.appendChild(box);
        box.querySelectorAll('.gs-row[data-go]').forEach(row => {
          row.addEventListener('click', () => {
            const go = row.dataset.go;
            closeResults(); input.value = '';
            if (go === 'chat' && row.dataset.id) {
              navigateTo('inbox');
              setTimeout(() => { const c = State.inbox.chats?.find(x => x.id == row.dataset.id); if (c) openChat(c); }, 600);
            } else if (go) navigateTo(go);
          });
        });
      } catch(_) {}
    }, 300);
  });
})();

// Password show/hide toggle
document.getElementById('toggle-password')?.addEventListener('click', () => {
  const inp = document.getElementById('login-password');
  const ico = document.getElementById('eye-icon');
  const show = inp.type === 'password';
  inp.type = show ? 'text' : 'password';
  ico.innerHTML = show
    ? '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>'
    : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
});

document.getElementById('login-form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn     = document.getElementById('login-btn');
  const errEl   = document.getElementById('login-error');
  const errMsg  = document.getElementById('login-error-msg');
  const arrow   = document.getElementById('login-btn-arrow');
  const spinner = document.getElementById('login-btn-spinner');

  btn.disabled = true;
  arrow.style.display   = 'none';
  spinner.style.display = 'inline';
  errEl.style.display   = 'none';

  try {
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;
    console.log('[login] attempting', email);
    const res = await Api.auth.login(email, password);
    console.log('[login] response', res);
    Api.setToken(res.access_token);
    State.agent = { id: res.agent_id, name: res.name, email: res.email, role: res.role };
    console.log('[login] calling showApp');
    showApp();
    console.log('[login] showApp done');
  } catch(err) {
    console.error('[login] error', err);
    errMsg.textContent    = err.message || 'Invalid email or password';
    errEl.style.display   = 'flex';
    btn.disabled          = false;
    arrow.style.display   = 'inline';
    spinner.style.display = 'none';
  }
});

const logoutFn = () => {
  if (State.ws) State.ws.close();
  Api.clearToken(); State.agent = null; showLogin();
};
document.getElementById('logout-btn')?.addEventListener('click', logoutFn);
document.getElementById('topbar-logout-btn')?.addEventListener('click', logoutFn);

// Topbar user menu dropdown toggle
const taAgent = document.getElementById('topbar-agent');
const taDropdown = document.getElementById('topbar-dropdown');
if (taAgent && taDropdown) {
  taAgent.addEventListener('click', (e) => {
    e.stopPropagation();
    taDropdown.style.display = taDropdown.style.display === 'none' ? 'block' : 'none';
  });
  document.addEventListener('click', () => {
    taDropdown.style.display = 'none';
  });
}

// ── Navigation ─────────────────────────────────────────────────── //
const VIEW_LABELS = {
  dashboard: 'Dashboard', inbox: 'Chats', tickets: 'Tickets',
  contacts: 'Contacts', 'chat-list': 'Chat List',
  analytics: 'Analytics', 'ai-agent': 'AI',
  automation: 'Automation Rules', 'knowledge-base': 'Media',
  bulk: 'Bulk Messages', settings: 'Settings',
  communities: 'Groups', logs: 'Logs', scheduled: 'Scheduled Messages',
};

function navigateTo(view) {
  _stopDashWahaPoller();
  State.currentView = view;
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.view === view);
  });
  const bc = document.getElementById('app-breadcrumb');
  if (bc) bc.innerHTML = `<strong>${VIEW_LABELS[view] || view}</strong>`;
  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
  ({
    dashboard:        renderDashboard,
    inbox:            renderInbox,
    tickets:          renderTickets,
    contacts:         renderContacts,
    'chat-list':      renderChatListView,
    analytics:        renderAnalytics,
    'ai-agent':       renderAIAgent,
    automation:       renderAutomation,
    'knowledge-base': renderKnowledgeBase,
    bulk:             renderBulk,
    settings:         renderSettings,
    communities:      renderCommunities,
    logs:             renderLogs,
    scheduled:        renderScheduled,
  }[view] || (() => { main.innerHTML = `<div class="loading-center">View not found</div>`; }))();
}

document.querySelectorAll('.nav-item').forEach(el => {
  el.addEventListener('click', e => { e.preventDefault(); navigateTo(el.dataset.view); });
});

// ── WebSocket ──────────────────────────────────────────────────── //
const WS = {
  socket: null,
  retryDelay: 1000,    // ms — doubles on each failure, capped at 30s
  maxDelay: 30000,
  pongTimeout: null,
  alive: false,
};

function wsSetStatus(status, label) {
  const el = document.getElementById('ws-status');
  const lb = document.getElementById('ws-label');
  if (!el) return;
  el.className = 'ws-status ' + status;
  if (lb) lb.textContent = label;
  el.title = label;
}

function connectWS() {
  if (!State.agent || !Api.getToken()) return;

  // Close any existing socket cleanly
  if (WS.socket) {
    WS.socket.onclose = null;
    WS.socket.close();
    WS.socket = null;
  }

  wsSetStatus('reconnecting', 'Connecting…');

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}/ws?token=${encodeURIComponent(Api.getToken())}`;
  const ws = new WebSocket(url);
  WS.socket = ws;
  WS.alive = false;

  ws.onopen = () => {
    WS.retryDelay = 1000;   // reset backoff on success
    WS.alive = true;
    wsSetStatus('connected', 'Live');
    State.ws = ws;
    // Reload messages for whichever chat is open so any messages missed during the
    // disconnect gap appear immediately (pass true to skip the WAHA re-sync step).
    if (State.currentView === 'inbox' && State.inbox.selectedChatId) {
      loadMessages(State.inbox.selectedChatId, true);
    }
  };

  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data);

      // ── Heartbeat ──────────────────────────────────── //
      if (msg.type === 'ping') {
        ws.send(JSON.stringify({ type: 'pong' }));
        return;
      }
      if (msg.type === 'pong' || msg.type === 'connected') {
        return;
      }

      // ── Application events ─────────────────────────── //
      handleWSEvent(msg);
    } catch(_) {}
  };

  ws.onerror = () => {
    wsSetStatus('disconnected', 'Error');
  };

  ws.onclose = () => {
    WS.socket = null;
    State.ws = null;
    WS.alive = false;
    wsSetStatus('reconnecting', 'Reconnecting…');

    // Exponential backoff
    const delay = Math.min(WS.retryDelay, WS.maxDelay);
    WS.retryDelay = Math.min(WS.retryDelay * 2, WS.maxDelay);
    setTimeout(connectWS, delay);
  };
}

function handleWSEvent(data) {
  const { event, data: d } = data;

  if (event === 'new_message') {
    // Append to open thread if it matches (WS is the real-time source of truth for outbound too)
    if (State.currentView === 'inbox' && State.inbox.selectedChatId == d.chat_id) {
      appendMessage(d);
    }

    // Update chat entry in state and re-render list — no network round-trip
    if (State.currentView === 'inbox') {
      const chatEntry = State.inbox.chats?.find(c => c.id == d.chat_id);
      if (chatEntry) {
        chatEntry.last_message = d.body || '';
        chatEntry.last_message_time = d.timestamp;
        if (!d.from_me && State.inbox.selectedChatId != d.chat_id) {
          chatEntry.unread_count = (chatEntry.unread_count || 0) + 1;
        }
        // Bubble this chat to the top
        State.inbox.chats = [chatEntry, ...State.inbox.chats.filter(c => c.id !== d.chat_id)];
        renderChatList(State.inbox.chats);
        const total = State.inbox.chats.reduce((s, c) => s + (c.unread_count || 0), 0);
        const badge = document.getElementById('unread-badge');
        if (badge) { badge.textContent = total; badge.style.display = total ? 'inline-flex' : 'none'; }
      } else {
        // New chat not yet in state — full refresh
        refreshChatList();
      }
    }

    // Show toast/notify when user is on another view
    if (State.currentView !== 'inbox' && !d.from_me) {
      const preview = (d.body || '').substring(0, 60);
      toast(`💬 New message: ${preview}`, 'default');
    }
    if (!d.from_me && typeof notifyUser === 'function') {
      notifyUser('new_messages', displayName(d.sender_name || d.chat_wid || 'New message'), d.body || 'Media message');
    }
    return;
  }

  if (event === 'note_mention') {
    toast(`📝 ${esc(d.by)} mentioned you in ${esc(displayName(d.chat_name))}`, 'default');
    if (typeof notifyUser === 'function') {
      notifyUser('new_note', `${d.by} mentioned you`, d.content || '');
    }
    return;
  }

  if (event === 'ticket_assigned') {
    toast(`🎫 ${esc(d.by)} assigned you ticket #${d.ticket_id}: ${esc(d.title)}`, 'default');
    if (typeof notifyUser === 'function') notifyUser('ticket_assign', 'Ticket assigned to you', d.title || '');
    return;
  }

  if (event === 'task_assigned') {
    toast(`✅ ${esc(d.by)} assigned you a task: ${esc(d.title)}`, 'default');
    if (typeof notifyUser === 'function') notifyUser('task_assign', 'Task assigned to you', d.title || '');
    return;
  }

  if (event === 'task_reminder') {
    toast(`⏰ Task reminder: ${esc(d.title)}`, 'default');
    if (typeof notifyUser === 'function') notifyUser('task_assign', '⏰ Task reminder', d.title || '');
    return;
  }

  if (event === 'chat_updated') {
    if (State.currentView === 'inbox') refreshChatList();
    return;
  }

  if (event === 'ticket_created' || event === 'ticket_updated') {
    if (State.currentView === 'tickets') {
      loadTickets();
    }
    return;
  }

  if (event === 'phone_status_changed') {
    // Update phone status in local state so loadChats() picks it up
    const ph = State.phones.find(p => p.id === d.phone_id);
    if (ph) ph.waha_status = d.status;
    updatePhoneBadge();
    // If phone became WORKING and we're on inbox, reload chats
    if (d.status === 'WORKING' && State.currentView === 'inbox') {
      _chatAutoSynced = false;
      loadChats();
    }
    return;
  }

  if (event === 'data_cleared') {
    // WAHA session stopped — hide chats in UI (data stays in DB for when they reconnect)
    const ph = State.phones.find(p => p.id === d.phone_id);
    if (ph) ph.waha_status = 'STOPPED';
    updatePhoneBadge();
    State.inbox.chats = [];
    State.inbox.selectedChatId = null;
    State.inbox.messages = [];
    _chatAutoSynced = false;

    if (State.currentView === 'inbox') {
      loadChats(); // will show the disconnected empty state since phone is now STOPPED
    }
    if (State.currentView === 'dashboard') {
      const dsTotal = document.getElementById('ds-total');
      const dsUnread = document.getElementById('ds-unread');
      const dsFlagged = document.getElementById('ds-flagged');
      if (dsTotal) dsTotal.textContent = '0';
      if (dsUnread) dsUnread.textContent = '0';
      if (dsFlagged) dsFlagged.textContent = '0';
    }

    toast('WhatsApp disconnected', 'warning');
    return;
  }
}

// ── Labels & Phones (global load) ─────────────────────────────── //
async function loadLabels() {
  try { State.labels = await Api.labels.list(); } catch(_) {}
}
function updatePhoneBadge() {
  const badge = document.getElementById('topbar-phone-count');
  const num = document.getElementById('topbar-phone-num');
  const total = document.getElementById('topbar-phone-total');
  const dot = badge ? badge.querySelector('.phone-dot') : null;
  if (badge && num) {
    const working = State.phones.filter(p => p.waha_status === 'WORKING').length;
    num.textContent = working;
    if (total) total.textContent = State.phones.length;
    badge.style.display = State.phones.length ? 'flex' : 'none';
    if (dot) {
      const totalCount = State.phones.length;
      if (working === 0) {
        dot.style.background = '#ef4444';
        badge.style.background = '#fef2f2';
        badge.style.borderColor = '#fecaca';
        badge.style.color = '#991b1b';
      } else if (working < totalCount) {
        dot.style.background = '#f59e0b';
        badge.style.background = '#fffbeb';
        badge.style.borderColor = '#fde68a';
        badge.style.color = '#92400e';
      } else {
        dot.style.background = '#10b981';
        badge.style.background = '#f0fdf4';
        badge.style.borderColor = '#bbf7d0';
        badge.style.color = '#166534';
      }
    }
  }
}
async function loadPhones() {
  try {
    State.phones = await Api.phones.list();
    updatePhoneBadge();
  } catch(_) {}
}

// ── INBOX VIEW ─────────────────────────────────────────────────── //
async function renderInbox() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="inbox-layout h-full" id="inbox-layout">
      <div class="chat-list-panel" id="chat-list-panel">
        <div class="chat-list-header">
          <div class="search-bar" style="flex:1">
            <input type="search" id="chat-search" placeholder="Search chats...">
          </div>
          <button class="btn btn-primary btn-sm" id="sync-btn" title="Sync">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:13px;height:13px"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>
        <div class="chat-list-filters" id="chat-filters">
          <span class="filter-chip active" data-f="all">All chats</span>
          <span class="filter-chip" data-f="inbox">Inbox</span>
          <span class="filter-chip" data-f="mine">Assigned to me</span>
          <span class="filter-chip" data-f="unread">Unread</span>
          <span class="filter-chip" data-f="flagged">Flagged</span>
          <span class="filter-chip" data-f="awaiting">Awaiting reply</span>
          <span class="filter-chip" id="label-filter-chip">🏷 Label ▾</span>
        </div>
        <div class="chat-list" id="chat-list">
          <div class="loading-center"><div class="spinner"></div></div>
        </div>
      </div>
      <div class="thread-panel" id="thread-panel">
        <div class="empty-state" style="flex:1">
          <div class="empty-state-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:48px;height:48px;opacity:.15"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          </div>
          <p style="font-size:15px;font-weight:600;color:var(--text-2);opacity:.6">Select a conversation</p>
          <p style="font-size:13px;color:var(--text-3)">Choose a chat from the list to start messaging</p>
        </div>
      </div>
      <div class="contact-detail-panel" id="detail-panel" style="display:none">
        <div class="detail-panel-header">
          <span>Details</span>
          <button class="detail-panel-close" id="close-detail-btn" title="Close panel">×</button>
        </div>
        <div class="detail-panel-body" id="detail-panel-body"></div>
      </div>
    </div>`;

  await loadChats();

  document.getElementById('chat-search').addEventListener('input', e => {
    State.inbox.search = e.target.value;
    debounceLoadChats();
  });

  document.querySelectorAll('.filter-chip').forEach(c => {
    c.addEventListener('click', () => {
      document.querySelectorAll('.filter-chip').forEach(x => x.classList.remove('active'));
      c.classList.add('active');
      State.inbox.filter = c.dataset.f;
      loadChats();
    });
  });

  document.getElementById('sync-btn').addEventListener('click', async () => {
    const phone = State.phones.find(p => p.waha_status === 'WORKING') || State.phones[0];
    if (!phone) return toast('No WhatsApp connected', 'error');
    const btn = document.getElementById('sync-btn');
    if (btn) btn.disabled = true;
    try {
      await Api.inbox.sync(phone.id);
      _chatAutoSynced = false;
      toast('Synced from WhatsApp', 'success');
      await loadChats();
    } catch(e) { toast(e.message || 'Sync failed — is WhatsApp connected?', 'error'); }
    finally { if (btn) btn.disabled = false; }
  });

  // Label filter: show only chats carrying a chosen label
  const lfChip = document.getElementById('label-filter-chip');
  if (lfChip) lfChip.addEventListener('click', e => {
    e.stopPropagation();
    openLabelPicker(lfChip, {
      applied: new Set(State.inbox.labelFilter ? [State.inbox.labelFilter] : []),
      onToggle: async (label, nowApplied) => {
        State.inbox.labelFilter = nowApplied ? label.id : null;
        lfChip.textContent = nowApplied ? `🏷 ${label.name} ×` : '🏷 Label ▾';
        lfChip.classList.toggle('active', nowApplied);
        closeLabelPicker();
        loadChats();
      },
    });
  });

  document.getElementById('close-detail-btn').addEventListener('click', () => {
    document.getElementById('detail-panel').style.display = 'none';
    document.getElementById('inbox-layout').classList.remove('detail-open');
  });
}

let _chatDebounce = null;
let _chatAutoSynced = false;
let _dashWahaTimer = null;
let _dashWahaUpdating = false;
let _dashWahaPrevStatus = '';
function debounceLoadChats() {
  clearTimeout(_chatDebounce);
  _chatDebounce = setTimeout(loadChats, 300);
}

let _chatLoadOffset = 0;
const CHAT_PAGE = 200;

async function loadChats() {
  _chatLoadOffset = 0;

  // Refresh phone state from server so status is always current
  try { State.phones = await Api.phones.list(); } catch(_) {}

  const phone = State.phones[0];
  const phoneConnected = phone && phone.waha_status === 'WORKING';

  // Hide all chats when WhatsApp is not connected — show a clear disconnected state
  if (!phoneConnected) {
    State.inbox.chats = [];
    State.inbox.messages = [];
    State.inbox.selectedChatId = null;
    const chatList = document.getElementById('chat-list');
    if (chatList) chatList.innerHTML = `<div class="loading-center text-muted" style="flex-direction:column;gap:1rem;padding:2rem;text-align:center">
      <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1.4"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.93 3.35 2 2 0 0 1 3.98 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 8.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
      <div>
        <p style="font-weight:600;color:var(--text-2);margin:0 0 .35rem">WhatsApp disconnected</p>
        <span style="font-size:12px;color:var(--text-3)">Connect your WhatsApp to see conversations</span>
      </div>
      <button class="btn btn-primary btn-sm" onclick="switchView('settings')">Connect WhatsApp</button>
    </div>`;
    const threadPanel = document.getElementById('thread-panel');
    if (threadPanel) {
      threadPanel.innerHTML = `<div class="empty-state whatsapp-disconnected-thread" style="flex:1">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:48px;height:48px;opacity:.25;color:var(--text-3)">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          <path d="M2 2l20 20"/>
        </svg>
        <p style="font-size:15px;font-weight:600;color:var(--text-2);opacity:.8;margin:0 0 .25rem">WhatsApp Disconnected</p>
        <span style="font-size:13px;color:var(--text-3);max-width:320px;line-height:1.4">Connect your WhatsApp to start viewing conversations and sending messages.</span>
        <button class="btn btn-primary btn-sm" style="margin-top:0.75rem" onclick="switchView('settings')">Connect WhatsApp</button>
      </div>`;
    }
    _updateUnreadBadge([]);
    return;
  }

  // Restore thread panel empty state if it was showing the disconnected message
  const threadPanel = document.getElementById('thread-panel');
  if (threadPanel && !State.inbox.selectedChatId) {
    if (threadPanel.querySelector('.whatsapp-disconnected-thread') || threadPanel.innerHTML.includes('WhatsApp Disconnected')) {
      threadPanel.innerHTML = `<div class="empty-state" style="flex:1">
        <div class="empty-state-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:48px;height:48px;opacity:.15"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        </div>
        <p style="font-size:15px;font-weight:600;color:var(--text-2);opacity:.6">Select a conversation</p>
        <p style="font-size:13px;color:var(--text-3)">Choose a chat from the list to start messaging</p>
      </div>`;
    }
  }

  const q = _buildChatQuery();
  q.limit = CHAT_PAGE;
  q.offset = 0;

  try {
    let chats = await Api.inbox.chats(q);
    if (!Array.isArray(chats)) chats = [];

    // Auto-sync from WAHA when inbox is empty and phone is connected
    if (chats.length === 0 && !_chatAutoSynced) {
      _chatAutoSynced = true;
      const chatList = document.getElementById('chat-list');
      if (chatList) chatList.innerHTML = `<div class="loading-center" style="flex-direction:column;gap:.5rem">
        <div class="spinner"></div>
        <span style="font-size:12px;color:var(--text-3)">Syncing chats from WhatsApp…</span>
      </div>`;
      try {
        await Api.inbox.sync(phone.id);
        chats = await Api.inbox.chats(q);
        if (!Array.isArray(chats)) chats = [];
      } catch(_) {}
    }

    chats = _filterChats(chats);
    State.inbox.chats = chats;
    renderChatList(chats, chats.length === CHAT_PAGE);
    _updateUnreadBadge(chats);
  } catch(err) {
    const chatList = document.getElementById('chat-list');
    if (chatList) chatList.innerHTML = `<div class="loading-center text-muted" style="flex-direction:column;gap:.5rem">
      <span>Failed to load chats</span>
      <button class="btn btn-secondary btn-sm" onclick="loadChats()">Retry</button>
    </div>`;
  }
}

function refreshChatList() { loadChats(); }

function _buildChatQuery() {
  const f = State.inbox.filter;
  const q = {};
  if (f === 'flagged') q.is_flagged = true;
  if (f === 'archived') q.is_archived = true;
  if (f === 'inbox') q.is_archived = false;
  if (f === 'mine' && State.agent) q.assigned_to = State.agent.id;
  if (State.inbox.labelFilter) q.label_id = State.inbox.labelFilter;
  if (State.inbox.search) q.search = State.inbox.search;
  return q;
}

function _filterChats(chats) {
  const f = State.inbox.filter;
  if (f === 'unread') return chats.filter(c => c.unread_count > 0);
  if (f === 'inbox') return chats.filter(c => !c.is_archived);
  if (f === 'awaiting') return chats.filter(c => c.last_message_from_me === false || (c.unread_count === 0 && !c.last_from_me));
  return chats;
}

function _updateUnreadBadge(chats) {
  const total = chats.reduce((s, c) => s + (c.unread_count || 0), 0);
  const badge = document.getElementById('unread-badge');
  if (badge) { badge.textContent = total; badge.style.display = total ? 'inline-flex' : 'none'; }
}

async function loadMoreChats() {
  const btn = document.getElementById('load-more-chats-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
  _chatLoadOffset += CHAT_PAGE;
  const q = _buildChatQuery();
  q.limit = CHAT_PAGE;
  q.offset = _chatLoadOffset;
  try {
    let more = await Api.inbox.chats(q);
    if (!Array.isArray(more)) more = [];
    more = _filterChats(more);
    State.inbox.chats = State.inbox.chats.concat(more);
    // Re-render full list with "load more" button if we got a full page
    renderChatList(State.inbox.chats, more.length === CHAT_PAGE);
    _updateUnreadBadge(State.inbox.chats);
  } catch(e) {
    if (btn) { btn.disabled = false; btn.textContent = 'Load more'; }
  }
}

function renderChatList(chats, hasMore) {
  const el = document.getElementById('chat-list');
  if (!el) return;
  if (!chats.length) {
    el.innerHTML = `<div class="loading-center text-muted">No conversations</div>`; return;
  }
  el.innerHTML = chats.map(c => {
    const active = c.id == State.inbox.selectedChatId ? ' active' : '';
    const color = avatarColor(displayName(c));
    const isGroup = c.is_group;
    const unread = c.unread_count || 0;

    // Phone tag: find phone name from State.phones using c.phone_id
    let phoneTagHtml = '';
    if (isGroup) {
      phoneTagHtml = `<span class="chat-phone-tag">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>
        Group
      </span>`;
    } else if (c.phone_id && State.phones.length) {
      const phone = State.phones.find(p => p.id === c.phone_id);
      if (phone) {
        phoneTagHtml = `<span class="chat-phone-tag">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/></svg>
          ${esc(phone.name || phone.phone_number)}
        </span>`;
      }
    }

    // Label chips
    let labelsHtml = '';
    if (c.labels && c.labels.length) {
      labelsHtml = `<div class="chat-item-labels">${c.labels.slice(0,4).map(lbl => {
        const labelObj = State.labels.find(l => l.id === lbl || l.name === lbl);
        const color2 = labelObj ? labelObj.color : '#9ca3af';
        const name = labelObj ? labelObj.name : (lbl || '');
        return `<span class="chat-label-mini" style="background:${color2}22;color:${color2};border:1px solid ${color2}44">${esc(name)}</span>`;
      }).join('')}</div>`;
    }

    return `<div class="chat-item${active}" data-cid="${c.id}">
      <div class="chat-avatar${isGroup?' group':''}" style="background:${color}">${initials(displayName(c))}</div>
      <div class="chat-meta">
        <div class="chat-meta-top">
          <span class="chat-name">${esc(displayName(c))}</span>
          <span class="chat-time">${timeAgo(c.last_message_at)}</span>
        </div>
        <div class="chat-meta-bottom">
          <span class="chat-preview">${esc((c.last_message||'').substring(0,55))}</span>
          <div class="chat-badges-right">
            ${c.is_flagged ? `<svg viewBox="0 0 24 24" fill="#f59e0b" style="width:11px;height:11px;flex-shrink:0"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15" stroke="#f59e0b" stroke-width="2"/></svg>` : ''}
            ${c.ai_active ? `<span class="ai-badge">AI</span>` : ''}
            ${unread ? `<span class="unread-dot">${unread > 99 ? '99+' : unread}</span>` : ''}
          </div>
        </div>
        ${phoneTagHtml ? `<div style="display:flex;align-items:center;gap:.25rem;margin-top:2px">${phoneTagHtml}${!labelsHtml ? `<span class="add-label-chip" data-addlabel="${c.id}">+ Label</span>` : ''}</div>` : (!labelsHtml ? `<div style="margin-top:2px"><span class="add-label-chip" data-addlabel="${c.id}">+ Label</span></div>` : '')}
        ${labelsHtml}
      </div>
    </div>`;
  }).join('');

  // "+ Label" chips: inline label picker with create-on-the-fly
  el.querySelectorAll('.add-label-chip').forEach(chip => {
    chip.addEventListener('click', e => {
      e.stopPropagation();
      const chat = State.inbox.chats?.find(x => x.id == chip.dataset.addlabel);
      if (!chat) return;
      openLabelPicker(chip, {
        applied: new Set(chat.labels || []),
        onToggle: async (label, nowApplied) => {
          if (nowApplied) await Api.inbox.addLabel(chat.id, label.id);
          else await Api.inbox.removeLabel(chat.id, label.id);
          chat.labels = nowApplied
            ? [...(chat.labels || []), label.id]
            : (chat.labels || []).filter(id => id !== label.id);
        },
      });
    });
  });

  el.querySelectorAll('.chat-item').forEach(el => {
    el.addEventListener('click', () => openChat(+el.dataset.cid));
  });

  // "Load more chats" button when there's a full page (more may exist)
  if (hasMore) {
    const morBtn = document.createElement('div');
    morBtn.style.cssText = 'text-align:center;padding:.75rem 1rem';
    morBtn.innerHTML = `<button id="load-more-chats-btn" class="btn btn-secondary btn-sm" style="width:100%;font-size:12px">Load more conversations</button>`;
    el.appendChild(morBtn);
    document.getElementById('load-more-chats-btn').addEventListener('click', loadMoreChats);
  }
}

async function openChat(chatId) {
  State.inbox.selectedChatId = chatId;
  document.querySelectorAll('.chat-item').forEach(el => {
    el.classList.toggle('active', +el.dataset.cid === chatId);
  });
  const chat = State.inbox.chats.find(c => c.id === chatId);
  if (!chat) return;
  // Immediately mark as read in state so unread badge clears without a re-fetch
  if (chat.unread_count) {
    chat.unread_count = 0;
    _updateUnreadBadge(State.inbox.chats);
    document.querySelectorAll(`.chat-item[data-cid="${chatId}"] .unread-dot`).forEach(d => d.remove());
  }
  const wasDetailOpen = document.getElementById('detail-panel')?.style.display !== 'none';
  renderThread(chat);
  if (wasDetailOpen) renderContactDetail(chat);
  Api.inbox.markRead(chatId).catch(()=>{});
  await loadMessages(chatId);
}

// ── Contact Detail Panel ────────────────────────────────────────── //
async function renderContactDetail(chat) {
  const panel = document.getElementById('detail-panel');
  const body = document.getElementById('detail-panel-body');
  const layout = document.getElementById('inbox-layout');
  if (!panel || !body || !layout) return;

  panel.style.display = 'flex';
  layout.classList.add('detail-open');

  const color = avatarColor(chat.name || chat.chat_wid);
  const chatLabels = chat.labels || [];

  // Build label chips
  const labelsMarkup = chatLabels.map(lbl => {
    const labelObj = State.labels.find(l => l.id === lbl || l.name === lbl);
    const lColor = labelObj ? labelObj.color : '#9ca3af';
    const lName = labelObj ? labelObj.name : String(lbl);
    return `<span class="detail-label-chip" style="background:${lColor}22;color:${lColor};border:1px solid ${lColor}44" data-label="${esc(lbl)}">
      ${esc(lName)}<span class="chip-remove" data-remove-label="${esc(lbl)}">×</span>
    </span>`;
  }).join('');

  // Build agent options
  let agentOpts = `<option value="">— Unassigned —</option>`;
  try {
    const agents = await Api.auth.agents();
    agentOpts += agents.map(a =>
      `<option value="${a.id}" ${chat.assigned_to == a.id ? 'selected' : ''}>${esc(a.name)}</option>`
    ).join('');
  } catch(_) {}

  // Available labels for "add" list
  const availableLabels = State.labels.filter(l => !chatLabels.includes(l.id) && !chatLabels.includes(l.name));
  const addLabelOpts = availableLabels.map(l =>
    `<option value="${l.id}">${esc(l.name)}</option>`
  ).join('');

  body.innerHTML = `
    <div class="detail-contact-top">
      <div class="detail-avatar" style="background:${color}">${initials(displayName(chat))}</div>
      <div class="detail-contact-name">${esc(displayName(chat))}</div>
      <div class="detail-contact-wid">${esc(chatSubtitle(chat))}</div>
    </div>

    <div class="detail-section">
      <div class="detail-section-label">Assigned To</div>
      <select class="detail-assign-select" id="detail-assign-select">
        ${agentOpts}
      </select>
    </div>

    <div class="detail-section">
      <div class="detail-section-label">Labels</div>
      <div class="detail-labels-row" id="detail-labels-row">
        ${labelsMarkup || '<span style="font-size:12px;color:var(--text-3);font-style:italic">No labels yet</span>'}
      </div>
      ${availableLabels.length ? `
      <div style="display:flex;gap:.4rem;align-items:center;margin-top:.4rem">
        <select id="detail-add-label-select" style="font-size:11.5px;padding:2px 5px;border:1px solid var(--border);border-radius:4px;flex:1;height:26px;background:#fff">
          <option value="">+ Add label…</option>
          ${addLabelOpts}
        </select>
      </div>` : `<div style="font-size:11.5px;color:var(--text-3);margin-top:.35rem">
        <a href="#" onclick="navigateTo('settings');return false" style="color:var(--accent);text-decoration:none">Create labels</a> in Settings
      </div>`}
    </div>

    <div class="detail-section">
      <div class="detail-section-label">Properties</div>
      <div id="detail-properties"><span style="font-size:12px;color:var(--text-3)">Loading…</span></div>
    </div>

    <div class="detail-section">
      <div class="detail-section-label">Phone</div>
      <div style="font-size:12.5px;color:var(--text-2)">
        ${chat.is_group ? 'Group chat' : (chatSubtitle(chat) || '—')}
      </div>
      ${(() => {
        if (chat.phone_id && State.phones.length) {
          const phone = State.phones.find(p => p.id === chat.phone_id);
          if (phone) return `<div style="font-size:11.5px;color:var(--text-3);margin-top:2px">via ${esc(phone.name||phone.phone_number)}</div>`;
        }
        return '';
      })()}
    </div>

    <div class="detail-section">
      <div class="detail-section-label">Status</div>
      <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
        <span class="${pillClass(chat.status||'open')}" style="font-size:11px">${chat.status||'open'}</span>
        ${chat.ai_active ? '<span class="ai-badge" style="font-size:10px">AI Active</span>' : ''}
        ${chat.is_flagged ? '<span style="font-size:10px;font-weight:600;background:#fffbeb;color:#d97706;border:1px solid #fde68a;border-radius:10px;padding:1px 7px">Flagged</span>' : ''}
      </div>
      <button class="detail-close-chat-btn" id="detail-close-chat-btn" style="margin-top:.6rem">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
        Mark as Resolved
      </button>
    </div>

    <div class="detail-section">
      <div class="detail-section-label">Conversation</div>
      <div style="font-size:12px;color:var(--text-2);display:flex;flex-direction:column;gap:.3rem">
        <div style="display:flex;justify-content:space-between">
          <span style="color:var(--text-3)">Created</span>
          <span>${chat.created_at ? new Date(chat.created_at).toLocaleDateString('en', {month:'short',day:'numeric',year:'numeric'}) : '—'}</span>
        </div>
        <div style="display:flex;justify-content:space-between">
          <span style="color:var(--text-3)">Last message</span>
          <span>${chat.last_message_at ? timeAgo(chat.last_message_at) + ' ago' : '—'}</span>
        </div>
        <div style="display:flex;justify-content:space-between">
          <span style="color:var(--text-3)">Unread</span>
          <span>${chat.unread_count || 0} messages</span>
        </div>
      </div>
    </div>`;

  // Assign agent handler
  document.getElementById('detail-assign-select').addEventListener('change', async e => {
    const val = e.target.value ? parseInt(e.target.value) : null;
    try {
      await Api.inbox.updateChat(chat.id, { assigned_to: val });
      chat.assigned_to = val;
      toast(val ? 'Chat assigned' : 'Unassigned', 'success');
      renderChatList(State.inbox.chats);
    } catch(err) { toast(err.message, 'error'); }
  });

  // Custom properties: render definitions with current values, save on change
  (async () => {
    const wrap = document.getElementById('detail-properties');
    if (!wrap) return;
    try {
      const [defs, valRes] = await Promise.all([
        Api.properties.definitions('chat'),
        Api.properties.chatValues(chat.id),
      ]);
      const values = valRes.custom_properties || {};
      if (!defs.length) {
        wrap.innerHTML = `<div style="font-size:11.5px;color:var(--text-3)">
          No custom properties defined.
          <a href="#" onclick="navigateTo('settings');return false" style="color:var(--accent)">Create in Settings</a></div>`;
        return;
      }
      const sections = {};
      defs.forEach(d => { (sections[d.section] = sections[d.section] || []).push(d); });
      wrap.innerHTML = Object.entries(sections).map(([sec, list]) => `
        ${Object.keys(sections).length > 1 ? `<div class="prop-section-title">${esc(sec)}</div>` : ''}
        ${list.map(d => {
          const v = values[String(d.id)];
          if (d.prop_type === 'single_select') {
            return `<div class="prop-row"><label>${esc(d.name)}</label>
              <select data-prop="${d.id}"><option value="">—</option>
                ${(d.options || []).map(o => `<option ${v === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}
              </select></div>`;
          }
          if (d.prop_type === 'multi_select') {
            const cur = Array.isArray(v) ? v : [];
            return `<div class="prop-row"><label>${esc(d.name)}</label>
              <div class="prop-multi" data-prop-multi="${d.id}">
                ${(d.options || []).map(o => `<label><input type="checkbox" value="${esc(o)}" ${cur.includes(o) ? 'checked' : ''}>${esc(o)}</label>`).join('')}
              </div></div>`;
          }
          const type = d.prop_type === 'date' ? 'date' : d.prop_type === 'number' ? 'number' : 'text';
          return `<div class="prop-row"><label>${esc(d.name)}</label>
            <input type="${type}" data-prop="${d.id}" value="${v != null ? esc(String(v)) : ''}"></div>`;
        }).join('')}`).join('');

      const save = async (id, value) => {
        try { await Api.properties.setChat(chat.id, { [id]: value }); toast('Property saved', 'success'); }
        catch(e) { toast(e.message, 'error'); }
      };
      wrap.querySelectorAll('[data-prop]').forEach(inp =>
        inp.addEventListener('change', () => save(inp.dataset.prop, inp.value)));
      wrap.querySelectorAll('[data-prop-multi]').forEach(group =>
        group.querySelectorAll('input').forEach(cb => cb.addEventListener('change', () => {
          const vals = [...group.querySelectorAll('input:checked')].map(c => c.value);
          save(group.dataset.propMulti, vals);
        })));
    } catch(_) {
      wrap.innerHTML = '<span style="font-size:11.5px;color:var(--text-3)">Could not load properties</span>';
    }
  })();

  // Add label handler
  const addLabelSel = document.getElementById('detail-add-label-select');
  if (addLabelSel) {
    addLabelSel.addEventListener('change', async e => {
      const labelId = e.target.value;
      if (!labelId) return;
      const labelObj = State.labels.find(l => l.id == labelId);
      if (!labelObj) return;
      const newLabels = [...chatLabels, labelObj.name];
      try {
        await Api.inbox.updateChat(chat.id, { labels: newLabels });
        chat.labels = newLabels;
        toast('Label added', 'success');
        renderContactDetail(chat);
        renderChatList(State.inbox.chats);
      } catch(err) { toast(err.message, 'error'); }
    });
  }

  // Remove label handlers
  body.querySelectorAll('[data-remove-label]').forEach(btn => {
    btn.addEventListener('click', async e => {
      e.stopPropagation();
      const lbl = btn.dataset.removeLabel;
      const newLabels = chatLabels.filter(l => l !== lbl && String(l) !== lbl);
      try {
        await Api.inbox.updateChat(chat.id, { labels: newLabels });
        chat.labels = newLabels;
        toast('Label removed', 'success');
        renderContactDetail(chat);
        renderChatList(State.inbox.chats);
      } catch(err) { toast(err.message, 'error'); }
    });
  });

  // Close/resolve chat
  document.getElementById('detail-close-chat-btn').addEventListener('click', async () => {
    try {
      await Api.inbox.updateChat(chat.id, { status: 'resolved' });
      chat.status = 'resolved';
      toast('Chat marked as resolved', 'success');
      renderContactDetail(chat);
      renderChatList(State.inbox.chats);
    } catch(err) { toast(err.message, 'error'); }
  });
}

function renderThread(chat) {
  const panel = document.getElementById('thread-panel');
  if (!panel) return;
  const isAI = chat.ai_active;
  const isDetailOpen = document.getElementById('detail-panel')?.style.display !== 'none';
  panel.innerHTML = `
    <div class="thread-header">
      <div class="thread-contact-info">
        <div class="chat-avatar" style="background:${avatarColor(displayName(chat))};width:34px;height:34px;font-size:12px;flex-shrink:0">${initials(displayName(chat))}</div>
        <div class="thread-contact-text">
          <div class="thread-name">${esc(displayName(chat))}</div>
          <div class="thread-meta">${esc(chatSubtitle(chat))} ${chat.assigned_to ? '· Assigned' : '· Open'}</div>
        </div>
      </div>
      <div class="thread-actions">
        <button id="btn-ai-toggle" class="${isAI ? 'active-ai' : ''}" title="${isAI ? 'Deactivate AI' : 'Activate AI'}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
          ${isAI ? 'AI On' : 'AI Off'}
        </button>
        <button id="btn-suggest" title="AI suggest reply">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          Suggest
        </button>
        <button id="btn-close-chat" class="btn-close-chat" title="Resolve chat">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
          Resolve
        </button>
        <div class="thread-more-wrap">
          <button id="btn-more" title="More actions" class="btn-icon-only">
            <svg viewBox="0 0 24 24" fill="currentColor" stroke="none" style="width:14px;height:14px"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
          </button>
          <div class="thread-more-menu" id="thread-more-menu">
            <button id="btn-summarize">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="21" y1="10" x2="3" y2="10"/><line x1="21" y1="6" x2="3" y2="6"/><line x1="21" y1="14" x2="3" y2="14"/><line x1="21" y1="18" x2="11" y2="18"/></svg>
              Summary
            </button>
            <button id="btn-ticket">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12h6M9 16h6M17 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/></svg>
              Create Ticket
            </button>
            <button id="btn-note">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              Add Note
            </button>
            <button id="btn-flag">
              <svg viewBox="0 0 24 24" fill="${chat.is_flagged ? 'var(--warning)':'none'}" stroke="var(--warning)" stroke-width="2"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>
              ${chat.is_flagged ? 'Unflag' : 'Flag'}
            </button>
          </div>
        </div>
        <button id="btn-details-toggle" class="btn-icon-only${isDetailOpen ? ' btn-details-active' : ''}" title="Toggle contact details">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:15px;height:15px"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
        </button>
      </div>
    </div>
    <div class="messages-area" id="messages-area">
      <div class="loading-center"><div class="spinner"></div></div>
    </div>
    <div class="reply-area" id="reply-area">
      <div class="composer-tabs">
        <span class="composer-tab active" id="tab-whatsapp">WhatsApp</span>
        <span class="composer-tab" id="tab-note">Private Note</span>
      </div>
      <div class="reply-toolbar" id="reply-toolbar">
        <button class="btn btn-ghost btn-sm" id="btn-qr">/ Quick Reply</button>
        <button class="btn btn-ghost btn-sm" id="btn-polish" title="AI polish: fix grammar and tone">✨ Polish</button>
        <button class="btn btn-ghost btn-sm" id="btn-attach" title="Send image or file by URL">📎 Media</button>
        <button class="btn btn-ghost btn-sm" id="btn-schedule" title="Schedule this message">🕐 Schedule</button>
        <select id="phone-select" class="btn btn-secondary btn-sm" style="border:1px solid var(--border);padding:3px 6px">
          ${State.phones.map(p => `<option value="${p.id}">${esc(p.name||p.phone_number)}</option>`).join('')}
        </select>
      </div>
      <div class="reply-bar">
        <textarea id="reply-text" placeholder="Type a message… (Enter to send, Shift+Enter for newline)"></textarea>
        <button class="btn btn-primary" id="send-btn">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:15px;height:15px"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
        </button>
      </div>
    </div>`;

  // AI toggle
  document.getElementById('btn-ai-toggle').addEventListener('click', async () => {
    try {
      if (chat.ai_active) {
        await Api.ai.deactivate(chat.id);
        chat.ai_active = false;
        toast('AI deactivated', 'success');
      } else {
        await Api.ai.activate(chat.id);
        chat.ai_active = true;
        toast('AI activated', 'success');
      }
      renderThread(chat);
      loadMessages(chat.id);
    } catch(e) { toast(e.message, 'error'); }
  });

  // Suggest reply
  document.getElementById('btn-suggest').addEventListener('click', async () => {
    try {
      const res = await Api.ai.suggestReply(chat.id);
      document.getElementById('reply-text').value = res.suggestion || res.reply || JSON.stringify(res);
      toast('Reply suggestion ready', 'success');
    } catch(e) { toast(e.message, 'error'); }
  });

  // Polish draft reply
  document.getElementById('btn-polish').addEventListener('click', async () => {
    const ta = document.getElementById('reply-text');
    const draft = ta.value.trim();
    if (!draft) return toast('Type a draft first', 'error');
    const btn = document.getElementById('btn-polish');
    btn.disabled = true; btn.textContent = '✨ Polishing…';
    try {
      const res = await Api.ai.polish(draft);
      ta.value = res.polished || draft;
      toast('Reply polished', 'success');
    } catch(e) { toast(e.message, 'error'); }
    btn.disabled = false; btn.textContent = '✨ Polish';
  });

  // Send media by URL
  document.getElementById('btn-attach').addEventListener('click', () => {
    showModal('Send Media', `
      <div class="form-group"><label>Type</label><select id="md-type">
        <option value="image">Image</option><option value="file">File / PDF</option>
      </select></div>
      <div class="form-group"><label>Media URL *</label><input type="text" id="md-url" placeholder="https://example.com/photo.jpg"></div>
      <div class="form-group"><label>Caption</label><input type="text" id="md-caption"></div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" id="md-send">Send</button>
      </div>`);
    document.getElementById('md-send').addEventListener('click', async () => {
      const url = document.getElementById('md-url').value.trim();
      if (!url) return toast('Media URL required', 'error');
      try {
        await Api.inbox.send({
          chat_id: chat.id,
          body: document.getElementById('md-caption').value.trim(),
          phone_id: parseInt(document.getElementById('phone-select').value) || null,
          message_type: document.getElementById('md-type').value,
          media_url: url,
        });
        closeModal(); toast('Media sent', 'success'); loadMessages(chat.id);
      } catch(e) { toast(e.message, 'error'); }
    });
  });

  // Schedule current draft
  document.getElementById('btn-schedule').addEventListener('click', () => {
    showScheduleModal(chat.id, document.getElementById('reply-text').value.trim());
  });

  // Summarize
  document.getElementById('btn-summarize').addEventListener('click', async () => {
    try {
      const res = await Api.ai.summarize(chat.id);
      const formattedSummary = esc(res.summary || '')
        .replace(/\n/g, '<br>')
        .replace(/(^|<br>)-\s*/g, '$1• ');
      showModal('Chat Summary', `<div style="font-size:13px;line-height:1.6;color:var(--text-2)">${formattedSummary}</div>`);
    } catch(e) { toast(e.message, 'error'); }
  });

  // Create ticket
  document.getElementById('btn-ticket').addEventListener('click', () => {
    showTicketModal({ chatId: chat.id });
  });

  // Add note
  document.getElementById('btn-note').addEventListener('click', () => {
    showModal('Add Private Note', `
      <div class="form-group"><label>Note (not sent to customer)</label><textarea id="note-content" style="min-height:100px" placeholder="Write a note..."></textarea></div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" id="note-save-btn">Save Note</button>
      </div>`);
    document.getElementById('note-save-btn').addEventListener('click', async () => {
      const content = document.getElementById('note-content').value.trim();
      if (!content) return;
      try {
        await Api.notes.create({ chat_id: chat.id, content });
        closeModal(); toast('Note saved', 'success');
        loadMessages(chat.id);
      } catch(e) { toast(e.message, 'error'); }
    });
  });

  // Flag toggle
  document.getElementById('btn-flag').addEventListener('click', async () => {
    try {
      await Api.inbox.updateChat(chat.id, { is_flagged: !chat.is_flagged });
      chat.is_flagged = !chat.is_flagged;
      renderThread(chat);
      loadChats();
    } catch(e) { toast(e.message, 'error'); }
  });

  // Resolve/close chat
  document.getElementById('btn-close-chat').addEventListener('click', async () => {
    try {
      await Api.inbox.updateChat(chat.id, { status: 'resolved' });
      chat.status = 'resolved';
      toast('Chat resolved', 'success');
      loadChats();
    } catch(e) { toast(e.message, 'error'); }
  });

  // More actions dropdown toggle
  const moreBtn = document.getElementById('btn-more');
  const moreMenu = document.getElementById('thread-more-menu');
  moreBtn.addEventListener('click', e => {
    e.stopPropagation();
    moreMenu.classList.toggle('open');
  });
  document.addEventListener('click', function closeMoreMenu(e) {
    if (!moreMenu.contains(e.target) && e.target !== moreBtn) {
      moreMenu.classList.remove('open');
      document.removeEventListener('click', closeMoreMenu);
    }
  });

  // Details panel toggle
  document.getElementById('btn-details-toggle').addEventListener('click', () => {
    const detailPanel = document.getElementById('detail-panel');
    const layout = document.getElementById('inbox-layout');
    const btn = document.getElementById('btn-details-toggle');
    if (detailPanel.style.display === 'none' || !detailPanel.style.display) {
      renderContactDetail(chat);
      btn.classList.add('btn-details-active');
    } else {
      detailPanel.style.display = 'none';
      layout.classList.remove('detail-open');
      btn.classList.remove('btn-details-active');
    }
  });

  // Quick reply picker
  document.getElementById('btn-qr').addEventListener('click', async () => {
    try {
      const qrs = await Api.quickReplies.list();
      if (!qrs.length) return toast('No quick replies configured', 'error');
      showModal('Quick Replies', `
        <div style="max-height:300px;overflow-y:auto;">
          ${qrs.map(q => `<div class="contact-card" style="cursor:pointer" data-qr="${esc(q.message)}">
            <div style="flex:1">
              <div style="font-weight:600;font-size:13px">/${esc(q.command)}</div>
              <div style="font-size:12px;color:var(--text-3)">${esc(q.message.substring(0,80))}</div>
            </div>
          </div>`).join('')}
        </div>`);
      document.querySelectorAll('[data-qr]').forEach(el => {
        el.addEventListener('click', () => {
          document.getElementById('reply-text').value = el.dataset.qr;
          closeModal();
        });
      });
    } catch(e) { toast(e.message, 'error'); }
  });

  // Composer mode: WhatsApp message vs. private team note
  let composerMode = 'whatsapp';
  const tabWA = document.getElementById('tab-whatsapp');
  const tabNote = document.getElementById('tab-note');
  const replyArea = document.getElementById('reply-area');
  const setComposerMode = mode => {
    composerMode = mode;
    const note = mode === 'note';
    tabWA.classList.toggle('active', !note);
    tabNote.classList.toggle('active', false);
    tabNote.classList.toggle('note-active', note);
    replyArea.classList.toggle('note-mode', note);
    document.getElementById('reply-text').placeholder = note
      ? 'Write a private note — only your team can see this…'
      : 'Type a message… (Enter to send, Shift+Enter for newline)';
  };
  tabWA.addEventListener('click', () => setComposerMode('whatsapp'));
  tabNote.addEventListener('click', () => setComposerMode('note'));

  // Send message (or save private note)
  const sendMsg = async () => {
    const text = document.getElementById('reply-text').value.trim();
    if (!text) return;
    const btn = document.getElementById('send-btn');
    btn.disabled = true;
    try {
      if (composerMode === 'note') {
        await Api.notes.create({ chat_id: chat.id, content: text });
        document.getElementById('reply-text').value = '';
        toast('Private note added — team only', 'success');
        await loadMessages(chat.id);   // show the note in the thread
      } else {
        const phoneId = document.getElementById('phone-select')?.value;
        if (!phoneId) { btn.disabled = false; return toast('Select a phone', 'error'); }
        await Api.inbox.send({ chat_id: chat.id, phone_id: +phoneId, body: text, message_type: 'text' });
        document.getElementById('reply-text').value = '';
        // WS new_message event from backend broadcasts the sent message to all agents in real time.
        // Only fall back to a full reload when WS is disconnected.
        if (!WS.alive) await loadMessages(chat.id);
      }
    } catch(e) { toast(e.message, 'error'); }
    btn.disabled = false;
  };

  document.getElementById('send-btn').addEventListener('click', sendMsg);

  // Quick reply slash suggestions: type "/" and matching replies appear inline
  const replyBar = document.querySelector('.reply-bar');
  if (replyBar) replyBar.style.position = 'relative';
  let qrCache = null, qrBox = null, qrSel = 0;
  const closeQrSuggest = () => { if (qrBox) { qrBox.remove(); qrBox = null; } };

  async function updateQrSuggest() {
    const ta = document.getElementById('reply-text');
    const text = ta.value;
    if (!text.startsWith('/') || text.includes(' ') || composerMode === 'note') { closeQrSuggest(); return; }
    if (!qrCache) {
      try { qrCache = await Api.quickReplies.list(); } catch(_) { qrCache = []; }
    }
    const q = text.slice(1).toLowerCase();
    const matches = qrCache.filter(r => r.command.toLowerCase().includes(q)).slice(0, 6);
    if (!matches.length) { closeQrSuggest(); return; }
    if (!qrBox) {
      qrBox = document.createElement('div');
      qrBox.className = 'qr-suggest';
      replyBar.appendChild(qrBox);
    }
    qrSel = Math.min(qrSel, matches.length - 1);
    qrBox.innerHTML = matches.map((r, i) => `
      <div class="qr-row ${i === qrSel ? 'sel' : ''}" data-i="${i}">
        <span class="qr-cmd">/${esc(r.command)}</span>
        <span class="qr-msg">${esc(r.message)}</span>
      </div>`).join('');
    qrBox.querySelectorAll('.qr-row').forEach(row => row.addEventListener('mousedown', e => {
      e.preventDefault();
      ta.value = matches[+row.dataset.i].message;
      closeQrSuggest();
      ta.focus();
    }));
    qrBox._matches = matches;
  }

  document.getElementById('reply-text').addEventListener('input', updateQrSuggest);
  document.getElementById('reply-text').addEventListener('blur', () => setTimeout(closeQrSuggest, 150));
  document.getElementById('reply-text').addEventListener('keydown', e => {
    if (qrBox && qrBox._matches?.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); qrSel = (qrSel + 1) % qrBox._matches.length; updateQrSuggest(); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); qrSel = (qrSel - 1 + qrBox._matches.length) % qrBox._matches.length; updateQrSuggest(); return; }
      if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        document.getElementById('reply-text').value = qrBox._matches[qrSel].message;
        closeQrSuggest();
        return;
      }
      if (e.key === 'Escape') { closeQrSuggest(); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMsg(); }
  });
}

// Track per-chat scroll-fetch state
let _msgScrollObserver = null;
let _msgLoadingOlder = false;
let _msgNoMoreOlder = false;
let _msgJustSynced = false; // true after loadMessages does a WAHA sync; skip re-sync in _fetchOlderMessages

async function loadMessages(chatId, _alreadySynced) {
  const area = document.getElementById('messages-area');
  if (!area) return;
  const chat = State.inbox.chats?.find(c => c.id == chatId);
  const isGroup = chat?.is_group || false;

  // Reset older-load sentinels for this chat
  _msgLoadingOlder = false;
  _msgNoMoreOlder  = false;
  _msgJustSynced   = false;
  if (_msgScrollObserver) { _msgScrollObserver.disconnect(); _msgScrollObserver = null; }

  // Show a slim loading skeleton immediately
  area.innerHTML = `<div id="msg-loading-bar" style="display:flex;align-items:center;justify-content:center;padding:2rem;gap:.5rem;opacity:.6;font-size:13px;color:var(--text-3)">
    <div class="spinner" style="width:16px;height:16px"></div> Loading messages…
  </div>`;

  try {
    // Always do a live WAHA sync first (200 msgs) unless WS just reconnected
    if (!_alreadySynced) {
      try { await Api.inbox.syncMessages(chatId, 200); _msgJustSynced = true; } catch(_) {}
    }

    let messages = await Api.inbox.messages(chatId, { limit: 100 });
    State.inbox.messages = messages;

    // Interleave private team notes into the thread
    let notes = [];
    try { notes = await Api.notes.list(chatId); } catch(_) {}
    const thread = messages.map(m => ({ kind: 'msg', ts: m.timestamp, item: m }))
      .concat(notes.map(n => ({ kind: 'note', ts: n.created_at, item: n })))
      .sort((a, b) => new Date(a.ts) - new Date(b.ts));

    const msgHtml = thread.map(t => t.kind === 'note' ? renderNoteBubble(t.item) : renderMessage(t.item, isGroup)).join('');

    // Invisible sentinel div at the very top — triggers loading older messages when scrolled into view
    area.innerHTML =
      `<div id="scroll-top-sentinel" style="height:1px;width:100%"></div>
       <div class="msg-spacer"></div>` +
      (msgHtml || `<div style="text-align:center;padding:1rem;font-size:13px;color:var(--text-3)">No messages yet — send the first one!</div>`);

    area.scrollTop = area.scrollHeight;

    // Attach IntersectionObserver for seamless infinite scroll upward
    _attachScrollSentinel(chatId, isGroup, area);
  } catch(e) {
    area.innerHTML = `<div class="loading-center text-muted">Could not load messages. Check your connection.</div>`;
  }
}

function _attachScrollSentinel(chatId, isGroup, area) {
  const sentinel = document.getElementById('scroll-top-sentinel');
  if (!sentinel) return;
  _msgScrollObserver = new IntersectionObserver(async (entries) => {
    if (!entries[0].isIntersecting) return;
    if (_msgLoadingOlder || _msgNoMoreOlder) return;
    _msgLoadingOlder = true;
    await _fetchOlderMessages(chatId, isGroup, area);
    _msgLoadingOlder = false;
  }, { root: area, threshold: 0.1 });
  _msgScrollObserver.observe(sentinel);
}

async function _fetchOlderMessages(chatId, isGroup, area) {
  const sentinel = document.getElementById('scroll-top-sentinel');
  if (!sentinel || !area) return;

  // Show tiny spinner above sentinel
  const spinnerEl = document.createElement('div');
  spinnerEl.id = 'older-spinner';
  spinnerEl.style.cssText = 'display:flex;align-items:center;justify-content:center;padding:.5rem;gap:.4rem;font-size:12px;color:var(--text-3);opacity:.6';
  spinnerEl.innerHTML = '<div class="spinner" style="width:12px;height:12px"></div> Loading older messages…';
  sentinel.insertAdjacentElement('afterend', spinnerEl);

  try {
    const current = State.inbox.messages || [];
    const oldestId = current.length ? current[0].id : null;
    const prevScrollHeight = area.scrollHeight;

    // 1. Try DB first
    let older = oldestId ? await Api.inbox.messages(chatId, { limit: 50, before_id: oldestId }) : [];

    // 2. DB exhausted → pull from WAHA
    // Skip if loadMessages already did a full sync moments ago (avoids double-sync on short chats
    // where the sentinel fires immediately because all messages fit on screen).
    if (!older.length && !_msgNoMoreOlder && !_msgJustSynced) {
      try {
        await Api.inbox.syncMessages(chatId, Math.min((current.length || 0) + 150, 500));
        older = oldestId
          ? await Api.inbox.messages(chatId, { limit: 50, before_id: oldestId })
          : await Api.inbox.messages(chatId, { limit: 50 });
      } catch(_) {}
    }
    _msgJustSynced = false; // consume the guard — subsequent scroll-ups can WAHA sync normally

    document.getElementById('older-spinner')?.remove();

    if (!older.length) {
      _msgNoMoreOlder = true;
      // Show a permanent "no more" tag at the top
      sentinel.insertAdjacentHTML('afterend',
        `<div style="text-align:center;padding:.75rem;font-size:11px;color:var(--text-4);letter-spacing:.03em;opacity:.6">— beginning of conversation —</div>`);
      if (_msgScrollObserver) { _msgScrollObserver.disconnect(); _msgScrollObserver = null; }
      return;
    }

    // Prepend older messages into state
    State.inbox.messages = older.concat(current);
    const html = older.map(m => renderMessage(m, isGroup)).join('');
    sentinel.insertAdjacentHTML('afterend', html);

    // Keep viewport anchored so content doesn't jump
    area.scrollTop = area.scrollHeight - prevScrollHeight;
  } catch(e) {
    document.getElementById('older-spinner')?.remove();
  }
}

function renderNoteBubble(n) {
  const content = esc(n.content || '').replace(/@([\w.]+)/g, '<strong style="color:#a16207">@$1</strong>');
  return `<div class="msg me note-inline">
    <div class="msg-bubble">
      <div class="note-author">📝 Private note · ${esc(n.agent_name || 'Team')}</div>
      ${content}
    </div>
    <div class="msg-info">${fmt(n.created_at)} · team only</div>
  </div>`;
}

function renderMessage(m, isGroup) {
  if (m.body?.startsWith('[NOTE]') || m.message_type === 'note') {
    const content = m.body?.replace('[NOTE] ', '') || m.body;
    return `<div class="note-msg">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
      <span><strong>Note:</strong> ${esc(content)}</span>
    </div>`;
  }
  const cls = m.from_me ? 'me' : 'them';
  let senderDisplay = '';
  if (!m.from_me && (isGroup || m.sender_name)) {
    senderDisplay = (m.sender_name || '').trim();
    if (!senderDisplay && m.sender_number) {
      // never show raw @lid ids as sender
      senderDisplay = /^\d{6,}$/.test(m.sender_number) ? `+${m.sender_number}` : '';
    }
  }

  let bubbleContent = '';
  const mtype = (m.message_type || 'text').toLowerCase();

  if (mtype === 'image' || mtype === 'photo') {
    bubbleContent = `<div class="msg-media-img">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
      <span>Photo</span>
    </div>${m.body ? `<div style="font-size:12px;margin-top:.3rem">${esc(m.body)}</div>` : ''}`;
  } else if (mtype === 'video') {
    bubbleContent = `<div class="msg-media-img">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>
      <span>Video</span>
    </div>${m.body ? `<div style="font-size:12px;margin-top:.3rem">${esc(m.body)}</div>` : ''}`;
  } else if (mtype === 'audio' || mtype === 'voice' || mtype === 'ptt') {
    bubbleContent = `<div class="msg-media-audio">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2M12 19v4M8 23h8"/></svg>
      <div class="msg-audio-bars">${Array(5).fill(0).map(()=>`<span style="height:${8+Math.random()*12|0}px"></span>`).join('')}</div>
      <span style="font-size:11px;color:inherit;opacity:.7">${mtype === 'ptt' ? 'Voice' : 'Audio'}</span>
    </div>`;
  } else if (mtype === 'document' || mtype === 'pdf') {
    bubbleContent = `<div class="msg-media-doc">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
      <span>${esc(m.body || 'Document')}</span>
    </div>`;
  } else if (mtype === 'sticker') {
    bubbleContent = `<span style="font-size:28px">🖼️</span>`;
  } else if (mtype === 'location') {
    bubbleContent = `<div class="msg-media-doc">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>
      <span>${esc(m.body || 'Location')}</span>
    </div>`;
  } else if (mtype === 'contact' || mtype === 'vcard') {
    bubbleContent = `<div class="msg-media-doc">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      <span>${esc(m.body || 'Contact')}</span>
    </div>`;
  } else {
    // Covers text, chat, gif, and any unknown types from WAHA
    if (m.body) {
      bubbleContent = esc(m.body).replace(/\n/g, '<br>');
    } else if (m.has_media) {
      // Media message where type string wasn't specifically matched above
      const _fallbackLabel = {
        gif: '🎞 GIF', image: '📷 Photo', photo: '📷 Photo',
        video: '🎬 Video', audio: '🎤 Voice', ptt: '🎤 Voice',
        document: '📄 Document', sticker: '🖼 Sticker',
      }[mtype] || '📎 Media';
      bubbleContent = `<div class="msg-media-doc">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
        <span>${_fallbackLabel}</span>
      </div>`;
    } else {
      // Completely empty (deleted or system message) — show a dash so bubble is visible
      bubbleContent = `<span style="opacity:.45;font-size:11px;font-style:italic">—</span>`;
    }
  }

  // Skip rendering if somehow bubbleContent is still blank (defensive)
  if (!bubbleContent) bubbleContent = `<span style="opacity:.45;font-size:11px;font-style:italic">—</span>`;

  return `<div class="msg ${cls}" data-mid="${m.id || ''}">
    ${senderDisplay ? `<div class="msg-sender">${esc(senderDisplay)}</div>` : ''}
    <div class="msg-bubble ${m.is_flagged ? 'flagged-msg' : ''}">
      ${m.is_flagged ? '<div class="msg-flag-badge">🚩 AI Flagged</div>' : ''}
      ${bubbleContent}
    </div>
    <div class="msg-info">${fmt(m.timestamp)} ${m.from_me ? (m.is_read ? '<span style="color:#53bdeb">✓✓</span>' : '✓') : ''}</div>
  </div>`;
}

// ── Right-click a message → Create Ticket / Create Task ──────────
let _msgMenuEl = null;
function closeMsgMenu() { if (_msgMenuEl) { _msgMenuEl.remove(); _msgMenuEl = null; } }
document.addEventListener('click', () => closeMsgMenu());
document.addEventListener('contextmenu', e => {
  const msgEl = e.target.closest('.msg[data-mid]');
  if (!msgEl || !msgEl.dataset.mid) return;
  const area = document.getElementById('messages-area');
  if (!area || !area.contains(msgEl)) return;
  e.preventDefault();
  closeMsgMenu();
  const msg = (State.inbox.messages || []).find(m => m.id == msgEl.dataset.mid);
  if (!msg) return;
  const menu = document.createElement('div');
  menu.className = 'msg-context-menu';
  menu.style.left = Math.min(e.clientX, window.innerWidth - 190) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - 110) + 'px';
  menu.innerHTML = `
    <button data-act="ticket">🎫 Create Ticket</button>
    <button data-act="task">✅ Create Task</button>
    <button data-act="copy">📋 Copy text</button>`;
  document.body.appendChild(menu);
  _msgMenuEl = menu;
  menu.querySelector('[data-act="ticket"]').addEventListener('click', () => {
    closeMsgMenu();
    showTicketModal({ chatId: State.inbox.selectedChatId, message: msg });
  });
  menu.querySelector('[data-act="task"]').addEventListener('click', () => {
    closeMsgMenu();
    showTaskModal({ chatId: State.inbox.selectedChatId, message: msg });
  });
  menu.querySelector('[data-act="copy"]').addEventListener('click', () => {
    closeMsgMenu();
    navigator.clipboard?.writeText(msg.body || '').then(() => toast('Copied', 'success'));
  });
});

// ── Full ticket modal: status, assignee, priority presets, labels,
//    ticket custom properties (required enforced) ─────────────────
async function showTicketModal(opts) {
  const { chatId, message } = opts || {};
  let agents = [], defs = [];
  try { agents = await Api.auth.agents(); } catch(_) {}
  try { defs = await Api.properties.definitions('ticket'); } catch(_) {}
  const labelOpts = State.labels.map(l =>
    `<label style="display:flex;align-items:center;gap:.35rem;font-size:12.5px;font-weight:400;padding:.12rem 0">
      <input type="checkbox" class="tk-label" value="${l.id}">
      <span class="lp-dot" style="width:9px;height:9px;border-radius:3px;background:${l.color};display:inline-block"></span>${esc(l.name)}
    </label>`).join('');

  const propFields = defs.map(d => {
    if (d.prop_type === 'single_select') {
      return `<div class="prop-row"><label>${esc(d.name)}${d.required ? ' *' : ''}</label>
        <select class="tk-prop" data-pid="${d.id}" data-required="${d.required}"><option value="">—</option>
          ${(d.options || []).map(o => `<option>${esc(o)}</option>`).join('')}</select></div>`;
    }
    if (d.prop_type === 'multi_select') {
      return `<div class="prop-row"><label>${esc(d.name)}${d.required ? ' *' : ''}</label>
        <div class="prop-multi tk-prop-multi" data-pid="${d.id}" data-required="${d.required}">
          ${(d.options || []).map(o => `<label><input type="checkbox" value="${esc(o)}">${esc(o)}</label>`).join('')}</div></div>`;
    }
    const type = d.prop_type === 'date' ? 'date' : d.prop_type === 'number' ? 'number' : 'text';
    return `<div class="prop-row"><label>${esc(d.name)}${d.required ? ' *' : ''}</label>
      <input type="${type}" class="tk-prop" data-pid="${d.id}" data-required="${d.required}"></div>`;
  }).join('');

  showModal('Create Ticket', `
    ${message ? `<div style="font-size:12px;background:var(--border-light);border-radius:7px;padding:.5rem .7rem;margin-bottom:.8rem;color:var(--text-2)">
      💬 From message: "${esc((message.body || '').slice(0, 120))}"</div>` : ''}
    <div class="form-group"><label>Title *</label><input type="text" id="tkm-title" placeholder="e.g. Billing inquiry — customer overcharged"></div>
    <div class="form-group"><label>Description</label><textarea id="tkm-desc" style="min-height:60px">${esc(message?.body || '')}</textarea></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.7rem">
      <div class="form-group"><label>Status</label><select id="tkm-status">
        <option value="open">Open</option><option value="in_progress">In Progress</option><option value="closed">Closed</option>
      </select></div>
      <div class="form-group"><label>Assignee</label><select id="tkm-assignee">
        <option value="">Unassigned (queue)</option>
        ${agents.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}
      </select></div>
      <div class="form-group"><label>Priority</label><select id="tkm-priority">
        <option value="low">Low</option><option value="medium" selected>Medium</option>
        <option value="high">High</option><option value="urgent">Urgent</option>
      </select></div>
      <div class="form-group"><label>Due Date</label><input type="datetime-local" id="tkm-due"></div>
    </div>
    ${labelOpts ? `<div class="form-group"><label>Labels</label><div style="max-height:110px;overflow-y:auto">${labelOpts}</div></div>` : ''}
    ${propFields ? `<div class="form-group"><label style="font-weight:700">Custom Properties</label>${propFields}</div>` : ''}
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="tkm-save">Create Ticket</button>
    </div>`);

  // Priority → suggested due date (urgent 1h, high 4h, medium 24h, low 3d)
  const prioSel = document.getElementById('tkm-priority');
  const dueInp = document.getElementById('tkm-due');
  const suggestDue = () => {
    const hours = { urgent: 1, high: 4, medium: 24, low: 72 }[prioSel.value] || 24;
    const d = new Date(Date.now() + hours * 3600 * 1000);
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    dueInp.value = d.toISOString().slice(0, 16);
  };
  prioSel.addEventListener('change', suggestDue);
  suggestDue();

  document.getElementById('tkm-save').addEventListener('click', async () => {
    const title = document.getElementById('tkm-title').value.trim();
    if (!title) return toast('Title required', 'error');
    // enforce required custom properties
    for (const el of document.querySelectorAll('.tk-prop[data-required="true"]')) {
      if (!el.value) return toast('Fill all required properties', 'error');
    }
    for (const grp of document.querySelectorAll('.tk-prop-multi[data-required="true"]')) {
      if (![...grp.querySelectorAll('input:checked')].length) return toast('Fill all required properties', 'error');
    }
    try {
      const ticket = await Api.tickets.create({
        chat_id: chatId,
        message_id: message?.id || null,
        title,
        description: document.getElementById('tkm-desc').value,
        status: document.getElementById('tkm-status').value,
        priority: document.getElementById('tkm-priority').value,
        assigned_to: parseInt(document.getElementById('tkm-assignee').value) || null,
        due_date: dueInp.value || null,
      });
      const labelIds = [...document.querySelectorAll('.tk-label:checked')].map(c => +c.value);
      for (const lid of labelIds) await Api.tickets.addLabel(ticket.id, lid).catch(() => {});
      const values = {};
      document.querySelectorAll('.tk-prop').forEach(el => { if (el.value) values[el.dataset.pid] = el.value; });
      document.querySelectorAll('.tk-prop-multi').forEach(grp => {
        const vals = [...grp.querySelectorAll('input:checked')].map(c => c.value);
        if (vals.length) values[grp.dataset.pid] = vals;
      });
      if (Object.keys(values).length) await Api.properties.setTicket(ticket.id, values).catch(() => {});
      closeModal();
      toast(`Ticket #${ticket.id} created — linked to this chat`, 'success');
    } catch(e) { toast(e.message, 'error'); }
  });
}

// ── Full task modal (also used from message right-click) ─────────
async function showTaskModal(opts) {
  const { chatId, message } = opts || {};
  let agents = [];
  try { agents = await Api.auth.agents(); } catch(_) {}
  showModal('Create Task', `
    ${message ? `<div style="font-size:12px;background:var(--border-light);border-radius:7px;padding:.5rem .7rem;margin-bottom:.8rem;color:var(--text-2)">
      💬 From message: "${esc((message.body || '').slice(0, 120))}"</div>` : ''}
    <div class="form-group"><label>Task *</label><input type="text" id="tkt-title" placeholder="Enter your task..."></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.7rem">
      <div class="form-group"><label>Due Date</label><input type="datetime-local" id="tkt-due"></div>
      <div class="form-group"><label>Reminder</label><input type="datetime-local" id="tkt-reminder"></div>
      <div class="form-group"><label>Assignee</label><select id="tkt-assignee">
        <option value="">Unassigned</option>
        ${agents.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}
      </select></div>
      <div class="form-group"><label>Priority</label><select id="tkt-prio">
        <option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option>
      </select></div>
    </div>
    <div class="form-group"><label>Notes</label><textarea id="tkt-notes" style="min-height:60px">${esc(message?.body || '')}</textarea></div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="tkt-save">Save Task</button>
    </div>`);
  document.getElementById('tkt-save').addEventListener('click', async () => {
    const title = document.getElementById('tkt-title').value.trim();
    if (!title) return toast('Task title required', 'error');
    try {
      await Api.tasks.create({
        title,
        chat_id: chatId || null,
        message_id: message?.id || null,
        due_date: document.getElementById('tkt-due').value || null,
        reminder_at: document.getElementById('tkt-reminder').value || null,
        assigned_to: parseInt(document.getElementById('tkt-assignee').value) || null,
        priority: document.getElementById('tkt-prio').value,
        notes: document.getElementById('tkt-notes').value.trim() || null,
      });
      closeModal(); toast('Task created', 'success');
    } catch(e) { toast(e.message, 'error'); }
  });
}

function appendMessage(m) {
  const area = document.getElementById('messages-area');
  if (!area) return;
  const chat = State.inbox.chats?.find(c => c.id == State.inbox.selectedChatId);
  // Keep state in sync so right-click context-menu actions work on real-time messages
  if (!State.inbox.messages) State.inbox.messages = [];
  State.inbox.messages.push(m);
  area.insertAdjacentHTML('beforeend', renderMessage(m, chat?.is_group || false));
  area.scrollTop = area.scrollHeight;
}

// ── TICKETS VIEW ────────────────────────────────────────────────── //
async function renderTickets() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full">
      <div class="section-header">
        <h2>Tickets</h2>
        <div class="header-actions" style="margin-left:auto;display:flex;gap:.5rem;align-items:center">
          <select id="ticket-filter" style="font-size:12.5px;padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:#ffffff;color:var(--text-2);font-weight:500;outline:none;cursor:pointer;box-shadow:0 1px 2px rgba(0,0,0,0.05);transition:border-color 0.15s, box-shadow 0.15s;">
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="in_progress">In Progress</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
          <button class="btn btn-primary btn-sm" id="new-ticket-btn">+ New Ticket</button>
        </div>
      </div>
      <div class="scroll-area">
        <div class="content-card">
          <div class="table-wrap">
            <table class="data-table">
              <thead>
                <tr><th>#</th><th>Title</th><th>Status</th><th>Priority</th><th>Assigned</th><th>Due</th><th>SLA (Service Level Agreement)</th><th style="width:130px;text-align:right">Actions</th></tr>
              </thead>
              <tbody id="tickets-tbody"><tr><td colspan="8" style="text-align:center;padding:2rem"><div class="spinner"></div></td></tr></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>`;

  await loadTickets();

  document.getElementById('ticket-filter').addEventListener('change', e => {
    loadTickets(e.target.value);
  });

  document.getElementById('new-ticket-btn').addEventListener('click', () => showCreateTicketModal());
}

async function loadTickets(status = '') {
  try {
    const q = {};
    if (status) q.status = status;
    const list = await Api.tickets.list(q);
    State.tickets.list = list;
    const tbody = document.getElementById('tickets-tbody');
    if (!tbody) return;
    if (!list.length) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:2rem;color:var(--text-3)">No tickets found</td></tr>`; return;
    }
    tbody.innerHTML = list.map(t => `
      <tr>
        <td style="color:var(--text-3);font-size:12px">#${t.id}</td>
        <td><a href="#" class="ticket-link text-accent" data-tid="${t.id}" style="font-weight:600">${esc(t.title)}</a></td>
        <td><span class="${pillClass(t.status)}">${t.status?.replace('_',' ')}</span></td>
        <td><span class="${pillClass(t.priority)}">${t.priority}</span></td>
        <td style="font-size:12px;color:var(--text-3)">${t.assigned_to ? 'Agent #'+t.assigned_to : '—'}</td>
        <td style="font-size:12px;color:var(--text-3)">${t.due_date ? new Date(t.due_date).toLocaleDateString() : '—'}</td>
        <td>${t.sla_breached ? '<span class="pill" style="background:#FEF2F2;color:#DC2626">Breached</span>' : '<span class="pill" style="background:var(--success-bg);color:var(--success)">OK</span>'}</td>
        <td style="text-align:right">
          <button class="btn btn-ghost btn-sm ticket-edit" data-tid="${t.id}">Edit</button>
          <button class="btn btn-danger btn-sm ticket-del icon-btn" data-tid="${t.id}" title="Delete ticket"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg></button>
        </td>
      </tr>`).join('');

    tbody.querySelectorAll('.ticket-edit').forEach(btn => {
      btn.addEventListener('click', () => showEditTicketModal(list.find(t => t.id == btn.dataset.tid)));
    });
    tbody.querySelectorAll('.ticket-del').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Delete ticket?')) return;
        try { await Api.tickets.del(btn.dataset.tid); toast('Deleted', 'success'); loadTickets(); }
        catch(e) { toast(e.message, 'error'); }
      });
    });
  } catch(e) { toast('Failed to load tickets', 'error'); }
}

async function showCreateTicketModal() {
  let chats = [];
  try {
    chats = await Api.inbox.chats({ limit: 200 }).catch(() => []);
  } catch (_) {}

  const chatOpts = chats.map(c => `<option value="${c.id}">${esc(displayName(c))}</option>`).join('');

  showModal('New Ticket', `
    <div class="form-group"><label>Customer Chat *</label>
      <select id="ntk-chat">${chatOpts || '<option value="">— No active chats —</option>'}</select>
    </div>
    <div class="form-group"><label>Title *</label><input type="text" id="ntk-title"></div>
    <div class="form-group"><label>Description</label><textarea id="ntk-desc"></textarea></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
      <div class="form-group"><label>Priority</label>
        <select id="ntk-priority"><option>low</option><option selected>medium</option><option>high</option><option>urgent</option></select>
      </div>
      <div class="form-group"><label>Due Date</label><input type="date" id="ntk-due"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="ntk-save">Create</button>
    </div>`);

  document.getElementById('ntk-save').addEventListener('click', async () => {
    const chatSelect = document.getElementById('ntk-chat');
    const chat_id = chatSelect ? parseInt(chatSelect.value) : null;
    if (!chat_id) return toast('Chat selection required', 'error');
    const title = document.getElementById('ntk-title').value.trim();
    if (!title) return toast('Title required', 'error');
    try {
      await Api.tickets.create({
        chat_id,
        title,
        description: document.getElementById('ntk-desc').value,
        priority: document.getElementById('ntk-priority').value,
        due_date: document.getElementById('ntk-due').value || null
      });
      closeModal();
      toast('Ticket created', 'success');
      loadTickets();
    } catch(e) {
      toast(e.message, 'error');
    }
  });
}

function showEditTicketModal(ticket) {
  showModal('Edit Ticket #' + ticket.id, `
    <div class="form-group"><label>Title</label><input type="text" id="etk-title" value="${esc(ticket.title)}"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
      <div class="form-group"><label>Status</label>
        <select id="etk-status">
          <option ${ticket.status==='open'?'selected':''}>open</option>
          <option ${ticket.status==='in_progress'?'selected':''} value="in_progress">in_progress</option>
          <option ${ticket.status==='resolved'?'selected':''}>resolved</option>
          <option ${ticket.status==='closed'?'selected':''}>closed</option>
        </select>
      </div>
      <div class="form-group"><label>Priority</label>
        <select id="etk-priority">
          <option ${ticket.priority==='low'?'selected':''}>low</option>
          <option ${ticket.priority==='medium'?'selected':''}>medium</option>
          <option ${ticket.priority==='high'?'selected':''}>high</option>
          <option ${ticket.priority==='urgent'?'selected':''}>urgent</option>
        </select>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="etk-save">Save</button>
    </div>`);
  document.getElementById('etk-save').addEventListener('click', async () => {
    try {
      await Api.tickets.update(ticket.id, { title: document.getElementById('etk-title').value, status: document.getElementById('etk-status').value, priority: document.getElementById('etk-priority').value });
      closeModal(); toast('Updated', 'success'); loadTickets();
    } catch(e) { toast(e.message, 'error'); }
  });
}

// ── CONTACTS VIEW ───────────────────────────────────────────────── //
async function renderContacts() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full">
      <div class="section-header">
        <h2>Contacts</h2>
        <div class="header-actions" style="margin-left:auto;display:flex;gap:.5rem;align-items:center">
          <div class="search-bar"><input type="search" id="contact-search" placeholder="Search…" style="width:200px"></div>
          <button class="btn btn-primary btn-sm" id="new-contact-btn">+ New Contact</button>
        </div>
      </div>
      <div class="list-container" id="contacts-list">
        <div class="loading-center"><div class="spinner"></div></div>
      </div>
    </div>`;

  await loadContacts();
  document.getElementById('contact-search').addEventListener('input', e => {
    State.contacts.search = e.target.value;
    debounce(loadContacts, 300)();
  });
  document.getElementById('new-contact-btn').addEventListener('click', () => showContactModal());
}

function debounce(fn, ms) {
  let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

async function loadContacts() {
  const q = {};
  if (State.contacts.search) q.search = State.contacts.search;
  try {
    const list = await Api.contacts.list(q);
    State.contacts.list = list;
    const el = document.getElementById('contacts-list');
    if (!el) return;
    if (!list.length) { el.innerHTML = `<div class="loading-center text-muted">No contacts</div>`; return; }
    el.innerHTML = list.map(c => `
      <div class="contact-card" data-cid="${c.id}">
        <div class="contact-avatar">${initials(c.name||c.phone_number)}</div>
        <div class="contact-info">
          <div class="contact-name">${esc(c.name||'—')}</div>
          <div class="contact-phone">${esc(c.phone_number)}</div>
          ${c.company ? `<div class="contact-company">${esc(c.company)}</div>` : ''}
        </div>
        <div style="display:flex;gap:.35rem;margin-left:auto">
          <button class="btn btn-ghost btn-sm contact-edit" data-cid="${c.id}">Edit</button>
          <button class="btn btn-danger btn-sm contact-del icon-btn" data-cid="${c.id}" title="Delete contact"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg></button>
        </div>
      </div>`).join('');
    el.querySelectorAll('.contact-edit').forEach(btn => {
      btn.addEventListener('click', e => { e.stopPropagation(); showContactModal(list.find(c => c.id == btn.dataset.cid)); });
    });
    el.querySelectorAll('.contact-del').forEach(btn => {
      btn.addEventListener('click', async e => {
        e.stopPropagation();
        if (!confirm('Delete contact?')) return;
        try { await Api.contacts.del(btn.dataset.cid); toast('Deleted', 'success'); loadContacts(); }
        catch(err) { toast(err.message, 'error'); }
      });
    });
  } catch(_) {}
}

function showContactModal(contact = null) {
  const c = contact || {};
  showModal(contact ? 'Edit Contact' : 'New Contact', `
    <div class="form-group"><label>Name</label><input type="text" id="ct-name" value="${esc(c.name||'')}"></div>
    <div class="form-group"><label>Phone Number *</label><input type="text" id="ct-phone" value="${esc(c.phone_number||'')}" ${contact?'readonly':''}></div>
    <div class="form-group"><label>Email</label><input type="email" id="ct-email" value="${esc(c.email||'')}"></div>
    <div class="form-group"><label>Company</label><input type="text" id="ct-company" value="${esc(c.company||'')}"></div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="ct-save">Save</button>
    </div>`);
  document.getElementById('ct-save').addEventListener('click', async () => {
    const phone = document.getElementById('ct-phone').value.trim();
    if (!phone) return toast('Phone required', 'error');
    try {
      const body = { phone_number: phone, name: document.getElementById('ct-name').value, email: document.getElementById('ct-email').value, company: document.getElementById('ct-company').value };
      if (contact) await Api.contacts.update(contact.id, body);
      else await Api.contacts.create(body);
      closeModal(); toast(contact ? 'Updated' : 'Created', 'success'); loadContacts();
    } catch(e) { toast(e.message, 'error'); }
  });
}

// ── ANALYTICS VIEW ──────────────────────────────────────────────── //
async function renderAnalytics() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header"><h2>Analytics</h2></div>
      <div id="analytics-body">
        <div class="loading-center"><div class="spinner"></div></div>
      </div>
    </div>`;
  try {
    const phones = await Api.phones.list().catch(() => []);
    State.phones = phones;
    const phoneConnected = phones.some(p => p.waha_status === 'WORKING');

    if (!phoneConnected) {
      const body = document.getElementById('analytics-body');
      if (body) {
        body.innerHTML = `<div class="empty-state whatsapp-disconnected-thread" style="padding:4rem 2rem">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:48px;height:48px;opacity:.25;color:var(--text-3)">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            <path d="M2 2l20 20"/>
          </svg>
          <p style="font-size:15px;font-weight:600;color:var(--text-2);opacity:.8;margin:0.5rem 0 0.25rem">WhatsApp Disconnected</p>
          <span style="font-size:13px;color:var(--text-3);max-width:320px;line-height:1.4">Connect your WhatsApp to see analytics data.</span>
          <button class="btn btn-primary btn-sm" style="margin-top:0.75rem" onclick="switchView('settings')">Connect WhatsApp</button>
        </div>`;
      }
      return;
    }

    const [dash, msg, tkt, agents] = await Promise.all([
      Api.analytics.dashboard(),
      Api.analytics.messages(30),
      Api.analytics.tickets(),
      Api.analytics.agents(30),
    ]);
    const body = document.getElementById('analytics-body');
    body.innerHTML = `
      <div class="metrics-grid">
        <div class="metric-card metric-accent">
          <div class="metric-label">Total Chats</div>
          <div class="metric-value">${dash.total_chats ?? 0}</div>
          <div class="metric-sub">All conversations</div>
        </div>
        <div class="metric-card metric-blue">
          <div class="metric-label">Messages (30d)</div>
          <div class="metric-value">${(msg.outgoing_messages ?? 0) + (msg.incoming_messages ?? 0)}</div>
          <div class="metric-sub">${msg.outgoing_messages ?? 0} sent · ${msg.incoming_messages ?? 0} received</div>
        </div>
        <div class="metric-card metric-orange">
          <div class="metric-label">Open Tickets</div>
          <div class="metric-value">${tkt.open ?? 0}</div>
          <div class="metric-sub">${tkt.in_progress ?? 0} in progress</div>
        </div>
        <div class="metric-card metric-green">
          <div class="metric-label">Resolved Tickets</div>
          <div class="metric-value">${tkt.resolved ?? 0}</div>
          <div class="metric-sub">${tkt.sla_breached ?? 0} SLA breached</div>
        </div>
        <div class="metric-card metric-accent">
          <div class="metric-label">Unread Chats</div>
          <div class="metric-value">${dash.unread_chats ?? 0}</div>
        </div>
        <div class="metric-card metric-orange">
          <div class="metric-label">Flagged Chats</div>
          <div class="metric-value">${dash.flagged_chats ?? 0}</div>
        </div>
      </div>
      <div class="analytics-section">
        <div class="content-card">
          <div class="card-header">Agent Performance (30d)</div>
          <div class="table-wrap">
            <table class="data-table">
              <thead><tr><th>Agent</th><th>Messages Sent</th><th>Chats Assigned</th><th>Open Tickets</th></tr></thead>
              <tbody>${(agents||[]).map(a => `
                <tr>
                  <td style="font-weight:600">${esc(a.agent_name||'Agent '+a.agent_id)}</td>
                  <td>${a.messages_sent ?? 0}</td>
                  <td>${a.chats_assigned ?? 0}</td>
                  <td>${a.open_tickets ?? 0}</td>
                </tr>`).join('') || '<tr><td colspan="4" style="text-align:center;color:var(--text-3);padding:1.5rem">No data yet</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </div>`;
  } catch(e) {
    document.getElementById('analytics-body').innerHTML = `<div class="loading-center text-muted">Could not load analytics</div>`;
  }
}

// ── AI AGENT VIEW ───────────────────────────────────────────────── //
async function renderAIAgent() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header"><h2>AI Agent</h2></div>
      <div class="scroll-area">
        <div class="content-card" style="margin-bottom:1rem">
          <div class="card-header">Agent Settings
            <div class="header-actions"><button class="btn btn-primary btn-sm" id="ai-cfg-save">Save Settings</button></div>
          </div>
          <div class="card-body" id="ai-cfg-body"><div class="spinner"></div></div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem">
          <div class="content-card">
            <div class="card-header">Active AI Chats</div>
            <div class="card-body" id="ai-chats-list"><div class="spinner"></div></div>
          </div>
          <div class="content-card">
            <div class="card-header">
              Knowledge Base
              <div class="header-actions"><a href="#" id="goto-kb" style="font-size:12px;color:var(--accent)">Manage →</a></div>
            </div>
            <div class="card-body" id="ai-kb-preview"><div class="spinner"></div></div>
          </div>
        </div>
        <div class="content-card">
          <div class="card-header">Translate Message</div>
          <div class="card-body">
            <div style="display:flex;gap:.75rem;align-items:flex-end">
              <div class="form-group" style="flex:1;margin:0"><label>Text</label><textarea id="tl-text" style="min-height:60px" placeholder="Enter text to translate..."></textarea></div>
              <div class="form-group" style="margin:0"><label>Language</label>
                <select id="tl-lang"><option value="hindi">Hindi</option><option value="spanish">Spanish</option><option value="french">French</option><option value="arabic">Arabic</option><option value="english">English</option></select>
              </div>
              <button class="btn btn-primary btn-sm" id="tl-btn" style="margin-bottom:1rem">Translate</button>
            </div>
            <div id="tl-result" style="display:none;background:var(--bg);padding:.75rem;border-radius:4px;font-size:13px;margin-top:.5rem"></div>
          </div>
        </div>
      </div>
    </div>`;

  document.getElementById('goto-kb').addEventListener('click', e => { e.preventDefault(); navigateTo('knowledge-base'); });

  // Agent Settings form (org-wide personalization + behavior)
  try {
    const cfg = await Api.ai.settings();
    const el = document.getElementById('ai-cfg-body');
    el.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.8rem 1.2rem">
        <div class="form-group"><label style="display:flex;align-items:center;gap:.4rem;font-weight:400">
          <input type="checkbox" id="cfg-enabled" ${cfg.enabled ? 'checked' : ''} style="width:15px;height:15px">
          <strong>AI agent enabled</strong> (master switch)</label></div>
        <div class="form-group"><label style="display:flex;align-items:center;gap:.4rem;font-weight:400">
          <input type="checkbox" id="cfg-autoact" ${cfg.auto_activate_new_chats ? 'checked' : ''} style="width:15px;height:15px">
          Auto-activate on new chats</label></div>
        <div class="form-group"><label>Agent name (shown to customers)</label>
          <input type="text" id="cfg-name" value="${esc(cfg.agent_name)}"></div>
        <div class="form-group"><label>Personality</label><select id="cfg-personality">
          <option value="friendly" ${cfg.personality === 'friendly' ? 'selected' : ''}>Friendly — warm, moderate detail</option>
          <option value="grounded" ${cfg.personality === 'grounded' ? 'selected' : ''}>Grounded — strictly factual</option>
          <option value="spartan" ${cfg.personality === 'spartan' ? 'selected' : ''}>Spartan — ultra-brief</option>
          <option value="sales" ${cfg.personality === 'sales' ? 'selected' : ''}>Sales — benefit-oriented</option>
        </select></div>
        <div class="form-group" style="grid-column:1/-1"><label>Role & business context</label>
          <textarea id="cfg-role" style="min-height:50px" placeholder="e.g. Support agent for Acme Store — we sell electronics, ship India-wide in 3-5 days...">${esc(cfg.role_description)}</textarea></div>
        <div class="form-group" style="grid-column:1/-1"><label>Operational instructions</label>
          <textarea id="cfg-instructions" style="min-height:50px" placeholder="e.g. Technical bugs → say the engineering team will call back. Pricing → share the plans page...">${esc(cfg.custom_instructions)}</textarea></div>
        <div class="form-group" style="grid-column:1/-1"><label>Hard restrictions (the agent must never do these)</label>
          <textarea id="cfg-restrictions" style="min-height:40px" placeholder="e.g. Never promise refunds, never share internal phone numbers, never schedule calls...">${esc(cfg.restrictions)}</textarea></div>
        <div class="form-group" style="grid-column:1/-1"><label>Activation rules (when to reply / ignore)</label>
          <textarea id="cfg-rules" style="min-height:40px" placeholder="e.g. Do not reply to plain greetings or thank-you messages. Only reply to actual questions.">${esc(cfg.activation_rules)}</textarea></div>
        <div class="form-group"><label>Response delay (seconds, lets humans answer first)</label>
          <input type="number" id="cfg-delay" min="0" max="6000" value="${cfg.response_delay_seconds}"></div>
        <div class="form-group"><label>Snooze after human reply (seconds)</label>
          <input type="number" id="cfg-snooze" min="0" max="6000" value="${cfg.snooze_after_human_seconds}"></div>
        <div class="form-group"><label>Operating hours start (HH:MM, empty = always)</label>
          <input type="text" id="cfg-hstart" value="${esc(cfg.hours_start)}" placeholder="09:00"></div>
        <div class="form-group"><label>Operating hours end</label>
          <input type="text" id="cfg-hend" value="${esc(cfg.hours_end)}" placeholder="18:00"></div>
        <div class="form-group"><label style="display:flex;align-items:center;gap:.4rem;font-weight:400">
          <input type="checkbox" id="cfg-flag" ${cfg.flag_enabled ? 'checked' : ''} style="width:15px;height:15px">
          AI auto-flag important messages</label></div>
        <div class="form-group"><label>Flag criteria</label>
          <input type="text" id="cfg-flagcrit" value="${esc(cfg.flag_criteria)}" placeholder="urgent requests, complaints, refunds..."></div>
      </div>`;
    document.getElementById('ai-cfg-save').addEventListener('click', async () => {
      try {
        await Api.ai.saveSettings({
          enabled: document.getElementById('cfg-enabled').checked,
          auto_activate_new_chats: document.getElementById('cfg-autoact').checked,
          agent_name: document.getElementById('cfg-name').value.trim() || 'AI Assistant',
          personality: document.getElementById('cfg-personality').value,
          role_description: document.getElementById('cfg-role').value.trim(),
          custom_instructions: document.getElementById('cfg-instructions').value.trim(),
          restrictions: document.getElementById('cfg-restrictions').value.trim(),
          activation_rules: document.getElementById('cfg-rules').value.trim(),
          response_delay_seconds: parseInt(document.getElementById('cfg-delay').value) || 0,
          snooze_after_human_seconds: parseInt(document.getElementById('cfg-snooze').value) || 0,
          hours_start: document.getElementById('cfg-hstart').value.trim(),
          hours_end: document.getElementById('cfg-hend').value.trim(),
          flag_enabled: document.getElementById('cfg-flag').checked,
          flag_criteria: document.getElementById('cfg-flagcrit').value.trim(),
        });
        toast('AI agent settings saved', 'success');
      } catch(e) { toast(e.message, 'error'); }
    });
  } catch(e) {
    const el = document.getElementById('ai-cfg-body');
    if (el) el.innerHTML = `<p class="text-muted" style="font-size:12.5px">${esc(e.message)}</p>`;
  }

  try {
    const chats = await Api.inbox.chats({ ai_active: true });
    const el = document.getElementById('ai-chats-list');
    if (!chats.length) { el.innerHTML = `<p class="text-muted">No active AI chats</p>`; }
    else {
      el.innerHTML = chats.map(c => `
        <div style="display:flex;align-items:center;gap:.5rem;padding:.4rem 0;border-bottom:1px solid var(--border-light)">
          <div style="flex:1"><div style="font-weight:600;font-size:13px">${esc(displayName(c))}</div>
          <span class="${pillClass(c.ai_state||'ACTIVE')}">${c.ai_state||'ACTIVE'}</span></div>
          <button class="btn btn-ghost btn-sm ai-takeover" data-cid="${c.id}">Takeover</button>
        </div>`).join('');
      el.querySelectorAll('.ai-takeover').forEach(btn => {
        btn.addEventListener('click', async () => {
          try { await Api.ai.takeover(btn.dataset.cid); toast('Human takeover done', 'success'); renderAIAgent(); }
          catch(e) { toast(e.message, 'error'); }
        });
      });
    }
  } catch(_) {}

  try {
    const items = await Api.kb.list({ limit: 5 });
    const el = document.getElementById('ai-kb-preview');
    if (!items.length) { el.innerHTML = `<p class="text-muted">No knowledge items yet</p>`; }
    else { el.innerHTML = items.map(i => `<div style="font-size:13px;padding:.35rem 0;border-bottom:1px solid var(--border-light)">${esc(i.title)}</div>`).join(''); }
  } catch(_) {}

  document.getElementById('tl-btn').addEventListener('click', async () => {
    const text = document.getElementById('tl-text').value.trim();
    if (!text) return;
    try {
      const res = await Api.ai.translate(text, document.getElementById('tl-lang').value);
      const div = document.getElementById('tl-result');
      div.textContent = res.translated || res.translation || JSON.stringify(res);
      div.style.display = 'block';
    } catch(e) { toast(e.message, 'error'); }
  });
}

// ── AUTOMATION VIEW ─────────────────────────────────────────────── //
async function renderAutomation() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header">
        <h2>Automation Rules</h2>
        <div class="header-actions" style="margin-left:auto"><button class="btn btn-primary btn-sm" id="new-rule-btn">+ New Rule</button></div>
      </div>
      <div class="scroll-area" id="rules-list"><div class="loading-center"><div class="spinner"></div></div></div>
    </div>`;

  await loadRules();
  document.getElementById('new-rule-btn').addEventListener('click', () => showRuleModal());
}

async function loadRules() {
  try {
    const [rules, triggers] = await Promise.all([Api.automation.list(), Api.automation.triggers()]);
    const el = document.getElementById('rules-list');
    if (!el) return;
    if (!rules.length) { el.innerHTML = `<div class="loading-center text-muted">No automation rules yet</div>`; return; }
    el.innerHTML = rules.map(r => `
      <div class="rule-card" style="margin-bottom:.75rem;padding:1.25rem;display:flex;align-items:flex-start;justify-content:space-between">
        <div class="rule-info" style="flex:1;min-width:0">
          <div class="rule-name" style="font-size:15px;font-weight:600;color:var(--text);margin-bottom:0.5rem">${esc(r.name)}</div>
          
          <div class="rule-trigger" style="font-size:12.5px;color:var(--text-3);margin-bottom:0.6rem;display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap">
            <span style="font-weight:600;color:var(--text-2)">Trigger:</span>
            <span>${esc(formatTrigger(r.trigger_type))}</span>
            <span style="color:var(--border);padding:0 2px">|</span>
            <span style="font-weight:600;color:var(--text-2)">Runs:</span>
            <span class="pill pill-closed" style="padding:1px 6px;font-size:11px;font-weight:600">${r.runs_count || 0}</span>
          </div>
          
          <div class="rule-actions-list" style="font-size:12.5px;display:flex;align-items:center;gap:0.4rem;flex-wrap:wrap">
            <span style="font-weight:600;color:var(--text-3)">Actions:</span>
            ${(r.actions||[]).map(a => `<span class="pill pill-open" style="font-size:11px;font-weight:600">${esc(formatAction(a))}</span>`).join('') || '<span class="text-muted">—</span>'}
          </div>
        </div>
        
        <div class="rule-controls" style="display:flex;align-items:center;gap:0.5rem;flex-shrink:0;margin-left:1.5rem">
          <span class="${pillClass(r.is_active ? 'active' : 'inactive')}" style="font-size:11.5px;font-weight:600">${r.is_active ? 'Active' : 'Paused'}</span>
          <button class="btn btn-ghost btn-sm rule-toggle" data-rid="${r.id}" data-active="${r.is_active}" style="color:var(--accent);font-weight:600;font-size:12px;padding:4px 8px">${r.is_active ? 'Pause' : 'Resume'}</button>
          <button class="btn btn-ghost btn-sm rule-del" data-rid="${r.id}" style="color:var(--danger);padding:4px 8px;font-size:12px;font-weight:500" title="Delete Rule">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:2px;vertical-align:middle"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
            Delete
          </button>
        </div>
      </div>`).join('');

    el.querySelectorAll('.rule-toggle').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await Api.automation.update(btn.dataset.rid, { is_active: btn.dataset.active === 'true' ? false : true });
          toast('Updated', 'success'); loadRules();
        } catch(e) { toast(e.message, 'error'); }
      });
    });
    el.querySelectorAll('.rule-del').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Delete rule?')) return;
        try { await Api.automation.del(btn.dataset.rid); toast('Deleted', 'success'); loadRules(); }
        catch(e) { toast(e.message, 'error'); }
      });
    });
  } catch(_) {}
}

async function showRuleModal() {
  let agents = [];
  let labels = [];
  try {
    const [agentList, labelList] = await Promise.all([
      Api.auth.agents().catch(() => []),
      Api.labels.list().catch(() => [])
    ]);
    agents = agentList;
    labels = labelList;
  } catch (_) {}

  const agentOptions = agents.map(a => `<option value="${a.id}">${esc(a.name)} (${esc(a.role)})</option>`).join('');
  const labelOptions = labels.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join('');

  showModal('New Automation Rule', `
    <div class="form-group"><label>Rule Name *</label><input type="text" id="rl-name" placeholder="e.g. Auto-assign support"></div>
    <div class="form-group"><label>Trigger</label>
      <select id="rl-trigger">
        <option value="message_received">Message Received</option>
        <option value="message_keyword">Message Keyword Match</option>
        <option value="chat_created">Chat Created</option>
        <option value="ticket_created">Ticket Created</option>
        <option value="no_reply_timeout">No Reply Timeout</option>
      </select>
    </div>

    <!-- Criteria selection -->
    <div class="form-group"><label>Criteria Type</label>
      <select id="rl-criteria-type">
        <option value="always">Always run (no criteria)</option>
        <option value="keyword">If message contains keyword</option>
        <option value="json">Custom JSON Criteria</option>
      </select>
    </div>
    <div class="form-group" id="rl-criteria-keyword-group" style="display:none">
      <label>Keywords (comma-separated)</label>
      <input type="text" id="rl-criteria-keywords" placeholder="e.g. refund, help, price">
    </div>
    <div class="form-group" id="rl-criteria-json-group" style="display:none">
      <label>Criteria (JSON)</label>
      <textarea id="rl-criteria" style="font-family:monospace;font-size:12px">{}</textarea>
    </div>

    <!-- Action selection -->
    <div class="form-group"><label>Action Type</label>
      <select id="rl-action-type">
        <option value="send_message">Send WhatsApp reply</option>
        <option value="flag_chat">Flag Chat</option>
        <option value="activate_ai">Activate AI Auto-responder</option>
        <option value="assign_to_agent">Assign to Agent</option>
        <option value="add_label">Add Label</option>
        <option value="json">Custom JSON Actions</option>
      </select>
    </div>

    <div class="form-group" id="rl-action-message-group">
      <label>Reply Message</label>
      <textarea id="rl-action-message" placeholder="e.g. Hello! We received your message and will get back to you shortly."></textarea>
    </div>
    <div class="form-group" id="rl-action-agent-group" style="display:none">
      <label>Select Agent</label>
      <select id="rl-action-agent">
        <option value="round_robin">Round Robin (Distribute evenly)</option>
        ${agentOptions}
      </select>
    </div>
    <div class="form-group" id="rl-action-label-group" style="display:none">
      <label>Select Label</label>
      <select id="rl-action-label">
        <option value="">-- Choose Label --</option>
        ${labelOptions}
      </select>
    </div>
    <div class="form-group" id="rl-action-json-group" style="display:none">
      <label>Actions (JSON array)</label>
      <textarea id="rl-actions" style="font-family:monospace;font-size:12px">[{"type":"send_message","message":"Hello!"}]</textarea>
    </div>

    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="rl-save">Create Rule</button>
    </div>`);

  const critSelect = document.getElementById('rl-criteria-type');
  critSelect.addEventListener('change', () => {
    const val = critSelect.value;
    document.getElementById('rl-criteria-keyword-group').style.display = val === 'keyword' ? 'block' : 'none';
    document.getElementById('rl-criteria-json-group').style.display = val === 'json' ? 'block' : 'none';
  });

  const actSelect = document.getElementById('rl-action-type');
  actSelect.addEventListener('change', () => {
    const val = actSelect.value;
    document.getElementById('rl-action-message-group').style.display = val === 'send_message' ? 'block' : 'none';
    document.getElementById('rl-action-agent-group').style.display = val === 'assign_to_agent' ? 'block' : 'none';
    document.getElementById('rl-action-label-group').style.display = val === 'add_label' ? 'block' : 'none';
    document.getElementById('rl-action-json-group').style.display = val === 'json' ? 'block' : 'none';
  });

  document.getElementById('rl-save').addEventListener('click', async () => {
    const name = document.getElementById('rl-name').value.trim();
    if (!name) return toast('Name required', 'error');

    let criteria = {};
    const critVal = critSelect.value;
    if (critVal === 'keyword') {
      const keywordsRaw = document.getElementById('rl-criteria-keywords').value.trim();
      if (!keywordsRaw) return toast('Keywords required', 'error');
      const keywords = keywordsRaw.split(',').map(k => k.trim()).filter(Boolean);
      criteria = { keywords };
    } else if (critVal === 'json') {
      try {
        criteria = JSON.parse(document.getElementById('rl-criteria').value || '{}');
      } catch (e) {
        return toast('Invalid Criteria JSON: ' + e.message, 'error');
      }
    }

    let actions = [];
    const actVal = actSelect.value;
    if (actVal === 'send_message') {
      const message = document.getElementById('rl-action-message').value.trim();
      if (!message) return toast('Reply message required', 'error');
      actions = [{ type: 'send_message', message }];
    } else if (actVal === 'flag_chat') {
      actions = [{ type: 'flag_chat' }];
    } else if (actVal === 'activate_ai') {
      actions = [{ type: 'activate_ai' }];
    } else if (actVal === 'assign_to_agent') {
      const agentId = document.getElementById('rl-action-agent').value;
      actions = [{ type: 'assign_to_agent', agent_id: agentId }];
    } else if (actVal === 'add_label') {
      const labelId = document.getElementById('rl-action-label').value;
      if (!labelId) return toast('Please select a label', 'error');
      actions = [{ type: 'add_label', label_id: parseInt(labelId) }];
    } else if (actVal === 'json') {
      try {
        actions = JSON.parse(document.getElementById('rl-actions').value || '[]');
      } catch (e) {
        return toast('Invalid Actions JSON: ' + e.message, 'error');
      }
    }

    try {
      await Api.automation.create({ name, trigger_type: document.getElementById('rl-trigger').value, criteria, actions });
      closeModal(); toast('Rule created', 'success'); loadRules();
    } catch(e) { toast(e.message, 'error'); }
  });
}

// ── KNOWLEDGE BASE ──────────────────────────────────────────────── //
async function renderKnowledgeBase() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header">
        <h2>Knowledge Base</h2>
        <div class="header-actions" style="margin-left:auto;display:flex;gap:.5rem">
          <select id="kb-filter" style="font-size:12px;padding:4px 8px;border:1px solid var(--border);border-radius:4px">
            <option value="">All</option><option value="faq">FAQ</option><option value="document">Document</option>
          </select>
          <button class="btn btn-primary btn-sm" id="new-kb-btn">+ Add Item</button>
        </div>
      </div>
      <div class="scroll-area" id="kb-list"><div class="loading-center"><div class="spinner"></div></div></div>
    </div>`;
  await loadKB();
  document.getElementById('new-kb-btn').addEventListener('click', () => showKBModal());
  document.getElementById('kb-filter').addEventListener('change', e => loadKB(e.target.value));
}

async function loadKB(type = '') {
  const q = {};
  if (type) q.item_type = type;
  try {
    const items = await Api.kb.list(q);
    const el = document.getElementById('kb-list');
    if (!el) return;
    if (!items.length) { el.innerHTML = `<div class="loading-center text-muted">No knowledge items</div>`; return; }
    el.innerHTML = items.map(i => `
      <div class="kb-item">
        <div class="kb-item-title">${esc(i.title)}</div>
        <div class="kb-item-content">${esc((i.content||'').substring(0,200))}${i.content?.length > 200 ? '…' : ''}</div>
        <div class="kb-item-footer">
          <span class="pill ${i.status==='active'?'pill-resolved':'pill-in_progress'}">${i.status}</span>
          <span class="text-muted">${i.item_type}</span>
          ${i.is_self_learned ? '<span class="pill pill-open">AI Learned</span>' : ''}
          <div style="margin-left:auto;display:flex;gap:.35rem">
            ${i.status !== 'active' ? `<button class="btn btn-secondary btn-sm kb-approve" data-id="${i.id}">Approve</button>` : ''}
            <button class="btn btn-ghost btn-sm kb-edit" data-id="${i.id}">Edit</button>
            <button class="btn btn-danger btn-sm kb-del icon-btn" data-id="${i.id}" title="Delete"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg></button>
          </div>
        </div>
      </div>`).join('');
    el.querySelectorAll('.kb-approve').forEach(btn => {
      btn.addEventListener('click', async () => {
        try { await Api.kb.approve(btn.dataset.id); toast('Approved', 'success'); loadKB(); }
        catch(e) { toast(e.message, 'error'); }
      });
    });
    el.querySelectorAll('.kb-del').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Delete?')) return;
        try { await Api.kb.del(btn.dataset.id); toast('Deleted', 'success'); loadKB(); }
        catch(e) { toast(e.message, 'error'); }
      });
    });
    el.querySelectorAll('.kb-edit').forEach(btn => {
      btn.addEventListener('click', () => showKBModal(items.find(i => i.id == btn.dataset.id)));
    });
  } catch(_) {}
}

function showKBModal(item = null) {
  const i = item || {};
  showModal(item ? 'Edit Knowledge Item' : 'New Knowledge Item', `
    <div class="form-group"><label>Title *</label><input type="text" id="kb-title" value="${esc(i.title||'')}"></div>
    <div class="form-group"><label>Type</label>
      <select id="kb-type"><option ${i.item_type==='faq'?'selected':''} value="faq">FAQ</option><option ${i.item_type==='document'?'selected':''} value="document">Document</option></select>
    </div>
    <div class="form-group"><label>Content</label><textarea id="kb-content" style="min-height:120px">${esc(i.content||'')}</textarea></div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="kb-save">Save</button>
    </div>`);
  document.getElementById('kb-save').addEventListener('click', async () => {
    const title = document.getElementById('kb-title').value.trim();
    if (!title) return toast('Title required', 'error');
    try {
      const body = { title, item_type: document.getElementById('kb-type').value, content: document.getElementById('kb-content').value };
      if (item) await Api.kb.update(item.id, body);
      else await Api.kb.create(body);
      closeModal(); toast(item ? 'Updated' : 'Created', 'success'); loadKB();
    } catch(e) { toast(e.message, 'error'); }
  });
}

// ── BULK MESSAGING ──────────────────────────────────────────────── //
async function renderBulk() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header">
        <h2>Bulk Messaging</h2>
        <div class="header-actions" style="margin-left:auto;display:flex;gap:.6rem;align-items:center">
          <button class="btn btn-primary btn-sm" id="new-bulk-btn">+ New Campaign</button>
        </div>
      </div>
      <div class="tab-bar" id="bulk-tabs">
        <div class="tab active" data-btab="campaigns">Campaigns</div>
        <div class="tab" data-btab="templates">Message Templates</div>
        <div class="tab" data-btab="chatlists">Saved Chat Lists</div>
      </div>
      <div class="scroll-area" id="bulk-list"><div class="loading-center"><div class="spinner"></div></div></div>
    </div>`;
  document.querySelectorAll('#bulk-tabs .tab').forEach(t => t.addEventListener('click', () => {
    document.querySelectorAll('#bulk-tabs .tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    loadBulkTab(t.dataset.btab);
  }));
  document.getElementById('new-bulk-btn').addEventListener('click', () => showBulkModal());
  await loadBulkTab('campaigns');
}

function _bulkRepeatSummary(j) {
  if (!j.repeat || j.repeat === 'none') return j.scheduled_at ? 'Once' : 'Immediate';
  const every = (j.interval || 1) > 1 ? `every ${j.interval} ` : '';
  const names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  if (j.repeat === 'daily') {
    const days = (j.days_of_week || []).map(d => names[d]).join(', ');
    return 'Daily' + (days ? ` (${days})` : '') + (every ? ` · ${every}days` : '');
  }
  if (j.repeat === 'weekly') return every ? `Every ${j.interval} weeks` : 'Weekly';
  if (j.repeat === 'monthly') return (every ? `Every ${j.interval} months` : 'Monthly') + (j.day_of_month ? ` on day ${j.day_of_month}` : '');
  return j.repeat;
}

async function loadBulkTab(tab) {
  const el = document.getElementById('bulk-list');
  if (!el) return;
  el.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';

  if (tab === 'campaigns') {
    try {
      const jobs = await Api.bulk.list();
      if (!jobs.length) { el.innerHTML = `<div class="loading-center text-muted">No campaigns yet — create one to get started</div>`; return; }
      el.innerHTML = `<div class="content-card"><div class="table-wrap"><table class="data-table">
        <thead><tr><th>Name</th><th>Status</th><th>Recipients</th><th>Sent</th><th>Failed</th><th>Repeat</th><th>Runs</th><th>Next / Scheduled</th><th></th></tr></thead>
        <tbody>${jobs.map(j => `<tr>
          <td style="font-weight:600">${esc(j.name)}</td>
          <td><span class="${pillClass(j.status==='done'?'resolved':j.status==='running'?'in_progress':j.status==='failed'||j.status==='cancelled'?'urgent':'open')}">${j.status}</span>${j.error_message ? ` <span title="${esc(j.error_message)}">⚠️</span>` : ''}</td>
          <td>${(j.recipient_chat_ids||[]).length}</td>
          <td>${j.sent_count||0}</td>
          <td>${j.failed_count||0}</td>
          <td style="font-size:12px">${esc(_bulkRepeatSummary(j))}${j.end_date ? `<div style="color:var(--text-3);font-size:11px">until ${new Date(j.end_date).toLocaleDateString()}</div>` : ''}</td>
          <td>${j.runs_count||0}</td>
          <td style="font-size:12px;color:var(--text-3)">${j.scheduled_at ? new Date(j.scheduled_at).toLocaleString() : 'Immediate'}</td>
          <td style="white-space:nowrap">
            <button class="btn btn-secondary btn-sm bulk-logs" data-jid="${j.id}">Logs</button>
            ${j.status==='pending' ? `<button class="btn btn-primary btn-sm bulk-send" data-jid="${j.id}">Send Now</button>
            <button class="btn btn-danger btn-sm bulk-stop" data-jid="${j.id}">Stop</button>` : ''}
          </td>
        </tr>`).join('')}</tbody>
      </table></div></div>`;
      el.querySelectorAll('.bulk-send').forEach(btn => btn.addEventListener('click', async () => {
        if (!confirm('Send this campaign now?')) return;
        try { await Api.bulk.send(btn.dataset.jid); toast('Sending…', 'success'); setTimeout(() => loadBulkTab('campaigns'), 1500); }
        catch(e) { toast(e.message, 'error'); }
      }));
      el.querySelectorAll('.bulk-stop').forEach(btn => btn.addEventListener('click', async () => {
        if (!confirm('Stop this campaign (and any repeats)?')) return;
        try { await Api.bulk.stop(btn.dataset.jid); toast('Stopped', 'success'); loadBulkTab('campaigns'); }
        catch(e) { toast(e.message, 'error'); }
      }));
      el.querySelectorAll('.bulk-logs').forEach(btn => btn.addEventListener('click', () => showBulkLogs(btn.dataset.jid)));
    } catch(e) { el.innerHTML = `<div class="loading-center text-muted">${esc(e.message)}</div>`; }
  }

  else if (tab === 'templates') {
    try {
      const templates = await Api.bulk.templates();
      el.innerHTML = `
        <div style="margin-bottom:1rem;display:flex;justify-content:flex-end">
          <button class="btn btn-primary btn-sm" id="new-tpl-btn">+ New Template</button>
        </div>
        ${templates.length ? `<div class="content-card"><div class="table-wrap"><table class="data-table">
          <thead><tr><th>Name</th><th>Message</th><th></th></tr></thead>
          <tbody>${templates.map(t => `<tr>
            <td style="font-weight:600;white-space:nowrap">${esc(t.name)}</td>
            <td style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px;color:var(--text-2)">${esc(t.body)}</td>
            <td style="white-space:nowrap">
              <button class="btn btn-secondary btn-sm tpl-edit" data-tid="${t.id}">Edit</button>
              <button class="btn btn-danger btn-sm tpl-del" data-tid="${t.id}">Delete</button>
            </td></tr>`).join('')}</tbody>
        </table></div></div>`
        : `<div class="empty-state" style="padding:3rem;text-align:center"><p class="text-muted" style="font-size:13px">
            No templates yet. Save frequently used broadcasts once and reuse them —<br>{{name}}, {{phone}} and {{company}} personalize per recipient.</p></div>`}`;
      const openTplModal = (tpl) => {
        showModal(tpl ? 'Edit Template' : 'New Template', `
          <div class="form-group"><label>Template Name *</label><input type="text" id="tpl-name" value="${tpl ? esc(tpl.name) : ''}"></div>
          <div class="form-group"><label>Message *</label>
            <textarea id="tpl-body" style="min-height:110px" placeholder="Hi {{name}}, ...">${tpl ? esc(tpl.body) : ''}</textarea>
            <small class="text-muted">Variables: {{name}}, {{phone}}, {{company}}</small></div>
          <div class="form-group"><label>Preview</label>
            <div id="tpl-preview" style="background:#efeae2;border-radius:8px;padding:.8rem">
              <div style="background:var(--bubble-out);border-radius:8px;padding:.5rem .7rem;font-size:13px;max-width:85%;margin-left:auto;white-space:pre-wrap"></div>
            </div></div>
          <div class="modal-footer">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-primary" id="tpl-save">${tpl ? 'Update Template' : 'Save Template'}</button>
          </div>`);
        const bodyTa = document.getElementById('tpl-body');
        const prevBubble = document.querySelector('#tpl-preview > div');
        const updPrev = () => prevBubble.textContent =
          (bodyTa.value || 'Your message preview…').replace(/{{\s*name\s*}}/g, 'Ravi').replace(/{{\s*phone\s*}}/g, '9198…').replace(/{{\s*company\s*}}/g, 'Acme');
        bodyTa.addEventListener('input', updPrev); updPrev();
        document.getElementById('tpl-save').addEventListener('click', async () => {
          const name = document.getElementById('tpl-name').value.trim();
          const body = bodyTa.value.trim();
          if (!name || !body) return toast('Name and message required', 'error');
          try {
            if (tpl) await Api.bulk.updateTemplate(tpl.id, { name, body });
            else await Api.bulk.createTemplate({ name, body });
            closeModal(); toast('Template saved', 'success'); loadBulkTab('templates');
          } catch(e) { toast(e.message, 'error'); }
        });
      };
      document.getElementById('new-tpl-btn').addEventListener('click', () => openTplModal(null));
      el.querySelectorAll('.tpl-edit').forEach(btn => btn.addEventListener('click', () =>
        openTplModal(templates.find(t => t.id == btn.dataset.tid))));
      el.querySelectorAll('.tpl-del').forEach(btn => btn.addEventListener('click', async () => {
        if (!confirm('Delete template?')) return;
        try { await Api.bulk.delTemplate(btn.dataset.tid); toast('Deleted', 'success'); loadBulkTab('templates'); }
        catch(e) { toast(e.message, 'error'); }
      }));
    } catch(e) { el.innerHTML = `<div class="loading-center text-muted">${esc(e.message)}</div>`; }
  }

  else if (tab === 'chatlists') {
    try {
      const lists = await Api.bulk.chatLists();
      el.innerHTML = `
        <div style="margin-bottom:1rem;display:flex;justify-content:flex-end">
          <button class="btn btn-primary btn-sm" id="new-cl-btn">+ New Chat List</button>
        </div>
        ${lists.length ? `<div class="content-card"><div class="table-wrap"><table class="data-table">
          <thead><tr><th>Name</th><th>Chats</th><th></th></tr></thead>
          <tbody>${lists.map(l => `<tr>
            <td style="font-weight:600">${esc(l.name)}</td>
            <td>${l.count}</td>
            <td style="white-space:nowrap">
              <button class="btn btn-secondary btn-sm cl-edit" data-lid="${l.id}">Edit</button>
              <button class="btn btn-danger btn-sm cl-del" data-lid="${l.id}">Delete</button>
            </td></tr>`).join('')}</tbody>
        </table></div></div>`
        : `<div class="empty-state" style="padding:3rem;text-align:center"><p class="text-muted" style="font-size:13px">
            No saved chat lists yet. Save a recipient selection once and reuse it in every campaign.</p></div>`}`;
      document.getElementById('new-cl-btn').addEventListener('click', () => showChatListModal(null));
      el.querySelectorAll('.cl-edit').forEach(btn => btn.addEventListener('click', () =>
        showChatListModal(lists.find(l => l.id == btn.dataset.lid))));
      el.querySelectorAll('.cl-del').forEach(btn => btn.addEventListener('click', async () => {
        if (!confirm('Delete chat list?')) return;
        try { await Api.bulk.delChatList(btn.dataset.lid); toast('Deleted', 'success'); loadBulkTab('chatlists'); }
        catch(e) { toast(e.message, 'error'); }
      }));
    } catch(e) { el.innerHTML = `<div class="loading-center text-muted">${esc(e.message)}</div>`; }
  }
}

async function showBulkLogs(jobId) {
  showModal('Campaign Logs', '<div class="loading-center"><div class="spinner"></div></div>');
  try {
    const res = await Api.bulk.logs(jobId);
    const html = `
      <div style="display:flex;gap:1rem;margin-bottom:.8rem">
        <div class="stat-mini"><div class="stat-mini-num">${res.job.sent}</div><div class="stat-mini-label">Sent</div></div>
        <div class="stat-mini"><div class="stat-mini-num">${res.job.failed}</div><div class="stat-mini-label">Failed</div></div>
        <div class="stat-mini"><div class="stat-mini-num">${res.job.runs}</div><div class="stat-mini-label">Runs</div></div>
      </div>
      <div class="table-wrap" style="max-height:320px;overflow-y:auto"><table class="data-table">
        <thead><tr><th>Chat</th><th>Status</th><th>Run</th><th>Time</th><th>Remarks</th></tr></thead>
        <tbody>${res.logs.map(r => `<tr>
          <td>${esc(displayName(r.chat_name) || ('#' + (r.chat_id || '?')))}</td>
          <td><span class="${pillClass(r.status === 'sent' ? 'resolved' : 'urgent')}">${r.status}</span></td>
          <td>${r.run}</td>
          <td style="font-size:11.5px;color:var(--text-3)">${r.at ? new Date(r.at).toLocaleString() : ''}</td>
          <td style="font-size:11.5px;color:var(--text-3)">${esc(r.error || '—')}</td>
        </tr>`).join('') || '<tr><td colspan="5" class="text-muted">No delivery logs yet — logs appear after the campaign runs</td></tr>'}</tbody>
      </table></div>
      <div class="modal-footer">
        <button class="btn btn-secondary" id="bl-export">Export CSV</button>
        <button class="btn btn-primary" onclick="closeModal()">Close</button>
      </div>`;
    document.getElementById('modal-body').innerHTML = html;
    document.getElementById('modal-title').textContent = `Logs — ${res.job.name}`;
    document.getElementById('bl-export').addEventListener('click', () => {
      const csv = ['chat,status,run,time,error'].concat(res.logs.map(r =>
        `"${(r.chat_name || '').replace(/"/g, '""')}",${r.status},${r.run},${r.at || ''},"${(r.error || '').replace(/"/g, '""')}"`)).join('\n');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
      a.download = `campaign_${jobId}_logs.csv`;
      document.body.appendChild(a); a.click(); a.remove();
    });
  } catch(e) { toast(e.message, 'error'); closeModal(); }
}

async function showChatListModal(existing) {
  let chats = [];
  try { chats = await Api.inbox.chats({ limit: 200 }); } catch(_) {}
  const selected = new Set(existing ? existing.chat_ids : []);
  showModal(existing ? 'Edit Chat List' : 'New Chat List', `
    <div class="form-group"><label>List Name *</label><input type="text" id="cl-name" value="${existing ? esc(existing.name) : ''}" placeholder="e.g. VIP customers"></div>
    <div class="form-group"><label>Chats (<span id="cl-count">${selected.size}</span> selected)</label>
      <input type="text" id="cl-filter" placeholder="Filter chats..." style="margin-bottom:.4rem">
      <div id="cl-chats" style="max-height:240px;overflow-y:auto;border:1px solid var(--border);border-radius:7px;padding:.3rem"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="cl-save">${existing ? 'Update List' : 'Save List'}</button>
    </div>`);
  const listEl = document.getElementById('cl-chats');
  const render = (q) => {
    const ql = (q || '').toLowerCase();
    listEl.innerHTML = chats
      .filter(c => !ql || displayName(c).toLowerCase().includes(ql))
      .map(c => `<label style="display:flex;align-items:center;gap:.5rem;padding:.25rem .3rem;font-size:12.5px;font-weight:400">
        <input type="checkbox" class="cl-pick" value="${c.id}" ${selected.has(c.id) ? 'checked' : ''}>
        ${esc(displayName(c))}${c.is_group ? ' <span class="pill pill-in_progress" style="font-size:10px">group</span>' : ''}
      </label>`).join('') || '<div class="text-muted" style="padding:.5rem;font-size:12px">No chats</div>';
    listEl.querySelectorAll('.cl-pick').forEach(cb => cb.addEventListener('change', () => {
      cb.checked ? selected.add(+cb.value) : selected.delete(+cb.value);
      document.getElementById('cl-count').textContent = selected.size;
    }));
  };
  document.getElementById('cl-filter').addEventListener('input', e => render(e.target.value));
  render('');
  document.getElementById('cl-save').addEventListener('click', async () => {
    const name = document.getElementById('cl-name').value.trim();
    if (!name) return toast('Name required', 'error');
    if (!selected.size) return toast('Select at least one chat', 'error');
    try {
      if (existing) await Api.bulk.updateChatList(existing.id, { name, chat_ids: [...selected] });
      else await Api.bulk.createChatList({ name, chat_ids: [...selected] });
      closeModal(); toast('Chat list saved', 'success');
      if (document.querySelector('#bulk-tabs .tab.active')?.dataset.btab === 'chatlists') loadBulkTab('chatlists');
    } catch(e) { toast(e.message, 'error'); }
  });
}

async function showBulkModal() {
  const phoneOpts = State.phones.map(p => `<option value="${p.id}">${esc(p.name||p.phone_number)}</option>`).join('');
  let templates = [], chatLists = [], chats = [];
  try { [templates, chatLists, chats] = await Promise.all([
    Api.bulk.templates().catch(() => []),
    Api.bulk.chatLists().catch(() => []),
    Api.inbox.chats({ limit: 200 }).catch(() => []),
  ]); } catch(_) {}
  const selected = new Set();
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  showModal('New Bulk Campaign', `
    <div class="form-group"><label>Campaign Name *</label><input type="text" id="bk-name"></div>
    <div class="form-group"><label>Phone</label><select id="bk-phone">${phoneOpts}</select></div>

    <div class="form-group"><label>Recipients (<span id="bk-count">0</span> selected) *</label>
      ${chatLists.length ? `<select id="bk-savedlist" style="margin-bottom:.4rem">
        <option value="">— Load a saved chat list —</option>
        ${chatLists.map(l => `<option value="${l.id}">${esc(l.name)} (${l.count})</option>`).join('')}
      </select>` : ''}
      <input type="text" id="bk-filter" placeholder="Filter chats..." style="margin-bottom:.4rem">
      <div id="bk-chats" style="max-height:170px;overflow-y:auto;border:1px solid var(--border);border-radius:7px;padding:.3rem"></div>
      <div style="margin-top:.35rem"><a href="#" id="bk-savelist" style="font-size:12px;color:var(--accent)">💾 Save selection as chat list</a></div>
    </div>

    <div class="form-group"><label>Type</label><select id="bk-type">
      <option value="text">Text</option>
      <option value="image">Image + caption</option>
      <option value="file">File / PDF + caption</option>
      <option value="poll">Poll</option>
    </select></div>
    <div class="form-group" id="bk-media-wrap" style="display:none">
      <label>Media URL *</label><input type="text" id="bk-media" placeholder="https://example.com/image.png">
    </div>
    <div class="form-group" id="bk-poll-wrap" style="display:none">
      <label>Poll Options (comma separated) *</label><input type="text" id="bk-poll" placeholder="Yes, No, Maybe">
    </div>

    <div class="form-group"><label id="bk-msg-label">Message *</label>
      ${templates.length ? `<select id="bk-template" style="margin-bottom:.4rem">
        <option value="">— Use a template (optional) —</option>
        ${templates.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('')}
      </select>` : ''}
      <textarea id="bk-msg" style="min-height:80px" placeholder="Hi {{name}}, ..."></textarea>
      <small class="text-muted">Personalize with {{name}}, {{phone}}, {{company}}</small>
    </div>

    <div class="form-group"><label>Delivery</label><select id="bk-delivery">
      <option value="now">Send manually (Send Now button)</option>
      <option value="once">Schedule once</option>
      <option value="repeat">Schedule repeating broadcasts</option>
    </select></div>
    <div class="form-group" id="bk-schedule-wrap" style="display:none">
      <label>First send at *</label><input type="datetime-local" id="bk-schedule">
    </div>
    <div id="bk-repeat-wrap" style="display:none">
      <div class="form-group"><label>Repeat</label><select id="bk-repeat">
        <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
      </select></div>
      <div class="form-group"><label>Repeat every</label>
        <div style="display:flex;align-items:center;gap:.5rem">
          <input type="number" id="bk-interval" min="1" max="30" value="1" style="width:80px">
          <span id="bk-interval-unit" class="text-muted" style="font-size:12.5px">day(s)</span>
        </div></div>
      <div class="form-group" id="bk-days-wrap"><label>On days (unchecked = every day)</label>
        <div style="display:flex;gap:.55rem;flex-wrap:wrap">
          ${DAYS.map((d, i) => `<label style="display:flex;align-items:center;gap:.25rem;font-size:12.5px;font-weight:400">
            <input type="checkbox" class="bk-day" value="${i}">${d}</label>`).join('')}
        </div></div>
      <div class="form-group" id="bk-dom-wrap" style="display:none"><label>Day of month (1–31)</label>
        <input type="number" id="bk-dom" min="1" max="31" placeholder="e.g. 1"></div>
      <div class="form-group"><label>End date (optional)</label><input type="date" id="bk-end"></div>
    </div>
    <div class="form-group"><label>Delay between messages (seconds)</label>
      <input type="number" id="bk-delay" min="1" max="60" value="1" style="width:100px"></div>

    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="bk-save">Create Campaign</button>
    </div>`);

  // Recipient picker
  const chatsEl = document.getElementById('bk-chats');
  const renderChats = (q) => {
    const ql = (q || '').toLowerCase();
    chatsEl.innerHTML = chats
      .filter(c => !ql || displayName(c).toLowerCase().includes(ql))
      .map(c => `<label style="display:flex;align-items:center;gap:.5rem;padding:.22rem .3rem;font-size:12.5px;font-weight:400">
        <input type="checkbox" class="bk-pick" value="${c.id}" ${selected.has(c.id) ? 'checked' : ''}>
        ${esc(displayName(c))}${c.is_group ? ' <span class="pill pill-in_progress" style="font-size:10px">group</span>' : ''}
      </label>`).join('') || '<div class="text-muted" style="padding:.5rem;font-size:12px">No chats</div>';
    chatsEl.querySelectorAll('.bk-pick').forEach(cb => cb.addEventListener('change', () => {
      cb.checked ? selected.add(+cb.value) : selected.delete(+cb.value);
      document.getElementById('bk-count').textContent = selected.size;
    }));
  };
  document.getElementById('bk-filter').addEventListener('input', e => renderChats(e.target.value));
  renderChats('');

  document.getElementById('bk-savedlist')?.addEventListener('change', e => {
    const list = chatLists.find(l => l.id == e.target.value);
    if (!list) return;
    list.chat_ids.forEach(id => selected.add(id));
    document.getElementById('bk-count').textContent = selected.size;
    renderChats(document.getElementById('bk-filter').value);
    toast(`Loaded "${list.name}" (${list.count} chats)`, 'success');
  });
  document.getElementById('bk-savelist').addEventListener('click', async e => {
    e.preventDefault();
    if (!selected.size) return toast('Select chats first', 'error');
    const name = prompt('Chat list name:');
    if (!name) return;
    try { await Api.bulk.createChatList({ name, chat_ids: [...selected] }); toast('Chat list saved', 'success'); }
    catch(err) { toast(err.message, 'error'); }
  });

  // Template picker fills the compose box
  document.getElementById('bk-template')?.addEventListener('change', e => {
    const t = templates.find(x => x.id == e.target.value);
    if (t) document.getElementById('bk-msg').value = t.body;
  });

  // Type toggles
  const typeSel = document.getElementById('bk-type');
  typeSel.addEventListener('change', () => {
    const t = typeSel.value;
    document.getElementById('bk-media-wrap').style.display = (t === 'image' || t === 'file') ? '' : 'none';
    document.getElementById('bk-poll-wrap').style.display = t === 'poll' ? '' : 'none';
    document.getElementById('bk-msg-label').textContent = t === 'poll' ? 'Poll Question *' : 'Message *';
  });

  // Delivery mode toggles
  const deliverySel = document.getElementById('bk-delivery');
  const repeatSel = document.getElementById('bk-repeat');
  deliverySel.addEventListener('change', () => {
    const mode = deliverySel.value;
    document.getElementById('bk-schedule-wrap').style.display = mode === 'now' ? 'none' : 'block';
    document.getElementById('bk-repeat-wrap').style.display = mode === 'repeat' ? 'block' : 'none';
  });
  repeatSel.addEventListener('change', () => {
    document.getElementById('bk-days-wrap').style.display = repeatSel.value === 'daily' ? 'block' : 'none';
    document.getElementById('bk-dom-wrap').style.display = repeatSel.value === 'monthly' ? 'block' : 'none';
    document.getElementById('bk-interval-unit').textContent =
      repeatSel.value === 'weekly' ? 'week(s)' : repeatSel.value === 'monthly' ? 'month(s)' : 'day(s)';
  });

  document.getElementById('bk-save').addEventListener('click', async () => {
    const name = document.getElementById('bk-name').value.trim();
    const msg = document.getElementById('bk-msg').value.trim();
    if (!name || !msg) return toast('Name and message required', 'error');
    if (!selected.size) return toast('Select at least one recipient', 'error');
    const mode = deliverySel.value;
    const schedule = document.getElementById('bk-schedule').value;
    if (mode !== 'now' && !schedule) return toast('Pick the first send time', 'error');
    const type = typeSel.value;
    try {
      await Api.bulk.create({
        name, message: msg,
        phone_id: parseInt(document.getElementById('bk-phone').value),
        recipient_chat_ids: [...selected].map(String),
        scheduled_at: mode === 'now' ? null : schedule,
        message_type: type,
        media_url: document.getElementById('bk-media').value.trim() || null,
        poll_options: type === 'poll'
          ? document.getElementById('bk-poll').value.split(',').map(s => s.trim()).filter(Boolean)
          : null,
        delay_seconds: parseInt(document.getElementById('bk-delay').value) || 1,
        repeat: mode === 'repeat' ? repeatSel.value : 'none',
        interval: parseInt(document.getElementById('bk-interval')?.value) || 1,
        days_of_week: mode === 'repeat' && repeatSel.value === 'daily'
          ? [...document.querySelectorAll('.bk-day:checked')].map(c => +c.value) : null,
        day_of_month: mode === 'repeat' && repeatSel.value === 'monthly'
          ? (parseInt(document.getElementById('bk-dom')?.value) || null) : null,
        end_date: mode === 'repeat' ? (document.getElementById('bk-end').value || null) : null,
      });
      closeModal(); toast('Campaign created', 'success'); loadBulkTab('campaigns');
    } catch(e) { toast(e.message, 'error'); }
  });
}

// ── SETTINGS VIEW ───────────────────────────────────────────────── //
async function renderSettings() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header"><h2>Settings</h2></div>
      <div class="tab-bar" id="settings-tabs">
        <div class="tab active" data-tab="phones">WhatsApp</div>
        <div class="tab" data-tab="labels">Labels</div>
        <div class="tab" data-tab="quickreplies">Quick Replies</div>
        <div class="tab" data-tab="agents">Agents</div>
        <div class="tab" data-tab="properties">Custom Properties</div>
        <div class="tab" data-tab="exports">Data Exports</div>
      </div>
      <div class="scroll-area" id="settings-content"></div>
    </div>`;

  document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      loadSettingsTab(t.dataset.tab);
    });
  });

  loadSettingsTab('phones');
}

function showAddPhoneModal() {
  showModal('Add WhatsApp Number', `
    <div class="form-group">
      <label>Display Name *</label>
      <input type="text" id="add-ph-name" placeholder="e.g. Sales Support">
    </div>
    <div class="form-group">
      <label>WAHA Session Name *</label>
      <input type="text" id="add-ph-session" placeholder="e.g. sales_support (lowercase, alphanumeric, no spaces)" style="font-family:monospace">
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="add-ph-save">Save & Connect</button>
    </div>
  `);

  document.getElementById('add-ph-save').addEventListener('click', async () => {
    const name = document.getElementById('add-ph-name').value.trim();
    const session = document.getElementById('add-ph-session').value.trim();
    if (!name || !session) return toast('All fields are required', 'error');
    if (!/^[a-z0-9_-]+$/.test(session)) {
      return toast('Session name must be lowercase alphanumeric and may contain dashes or underscores only.', 'error');
    }
    
    try {
      const res = await Api.phones.create({
        name,
        session_name: session,
        phone_number: 'pending',
        is_default: false
      });
      closeModal();
      toast('WhatsApp session created! Initializing connection...', 'success');
      loadSettingsTab('phones'); loadPhones();
      
      // Auto-connect after brief timeout so WAHA is ready
      setTimeout(async () => {
        try {
          await Api.phones.start(res.id);
          // Highlight/show QR immediately by opening the QR flow
          loadSettingsTab('phones').then(() => {
            const reconnectBtn = document.querySelector(`.phone-btn-reconnect[data-pid="${res.id}"]`) 
                              || document.querySelector(`.phone-btn-connect[data-pid="${res.id}"]`);
            if (reconnectBtn) reconnectBtn.click();
          });
        } catch(_) {}
      }, 1000);
      
    } catch(e) {
      toast(e.message, 'error');
    }
  });
}

async function loadSettingsTab(tab) {
  const el = document.getElementById('settings-content');
  if (!el) return;
  el.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';

  if (tab === 'phones') {
    try {
      const phones = await Api.phones.list();
      
      let html = `
        <div class="flex-col gap-4">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem;padding-bottom:1rem;border-bottom:1px solid var(--border-light)">
            <div>
              <h3 style="margin:0 0 .25rem;font-size:16px;font-weight:600">WhatsApp Sessions</h3>
              <p style="margin:0;font-size:12.5px;color:var(--text-3)">Configure and connect multiple WhatsApp numbers to Hyperscope</p>
            </div>
            <button class="btn btn-primary btn-sm" id="btn-add-phone">+ Add WhatsApp Number</button>
          </div>
          
          <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(320px, 1fr));gap:1rem">
      `;
      
      if (!phones.length) {
        html += `
          <div class="content-card" style="grid-column:1/-1;padding:3rem 1.5rem;text-align:center;color:var(--text-3)">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin:0 auto 1rem;opacity:0.5"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.93 3.35 2 2 0 0 1 3.98 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 8.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
            <div style="font-weight:600;font-size:14px;color:var(--text-2)">No WhatsApp Sessions Configured</div>
            <p style="font-size:12px;margin:0.25rem 0 1.25rem">Get started by adding your first WhatsApp number connection.</p>
          </div>
        `;
      } else {
        html += phones.map(p => {
          const connected = p.waha_status === 'WORKING';
          const statusText = p.waha_status || 'STOPPED';
          let statusColor = '#ef4444'; // Red
          let statusBg = '#fef2f2';
          if (connected) {
            statusColor = '#10b981'; // Green
            statusBg = '#f0fdf4';
          } else if (p.waha_status === 'SCAN_QR_CODE') {
            statusColor = '#f59e0b'; // Orange
            statusBg = '#fffbeb';
          }
          
          return `
            <div class="content-card" style="padding:1.25rem;display:flex;flex-direction:column;justify-content:space-between;border:1px solid ${connected ? '#bbf7d0' : 'var(--border)'}">
              <div>
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.75rem">
                  <div>
                    <h4 style="margin:0;font-size:14px;font-weight:600">${esc(p.name)}</h4>
                    <span style="font-size:11px;color:var(--text-3);font-family:monospace">session: ${esc(p.session_name)}</span>
                  </div>
                  <span class="pill" style="background:${statusBg};color:${statusColor};border:1px solid ${statusColor}33;padding:1px 6px;font-size:10px">${statusText}</span>
                </div>
                
                <div style="margin-bottom:1.25rem">
                  <div style="font-size:12px;color:var(--text-3)">Phone Number:</div>
                  <div style="font-size:14px;font-weight:500;color:var(--text)">
                    ${p.phone_number && p.phone_number !== 'pending' ? '+' + p.phone_number : '<span style="color:#d97706;font-size:12px">⚠️ Pending connection</span>'}
                  </div>
                </div>
                
                <div id="phone-qr-area-${p.id}" style="margin-bottom:1rem"></div>
              </div>
              
              <div style="display:flex;gap:0.4rem;flex-wrap:wrap;align-items:center">
                ${connected
                  ? `<button class="btn btn-secondary btn-sm phone-btn-reconnect" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px">Reconnect / QR</button>
                     <button class="btn btn-danger btn-sm phone-btn-disconnect" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px">Disconnect</button>
                     <button class="btn btn-ghost btn-sm phone-btn-clear" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px;color:#be123c" title="Delete all chats/messages for this phone from DB">Clear Data</button>`
                  : `<button class="btn btn-primary btn-sm phone-btn-connect" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px">Connect</button>`
                }
                <button class="btn btn-ghost btn-sm phone-btn-delete" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px;margin-left:auto;color:var(--danger)" title="Remove phone session from Hyperscope">Delete</button>
              </div>
            </div>
          `;
        }).join('');
      }
      
      html += `
          </div>
        </div>
      `;
      
      el.innerHTML = html;

      async function startQrFlow(phoneId) {
        const area = document.getElementById(`phone-qr-area-${phoneId}`);
        if (!area) return;
        area.innerHTML = `<div class="spinner" style="margin:.5rem auto"></div>`;
        let _syncTimer = null;
        let _pollTimer = null;
        let attempt = 0;
        async function pollQr() {
          if (!document.getElementById(`phone-qr-area-${phoneId}`)) { clearInterval(_pollTimer); clearInterval(_syncTimer); return; }
          try {
            const r = await Api.phones.qr(phoneId);
            if (r && r.qr) {
              area.innerHTML = `
                <img src="${r.qr}" style="max-width:200px;border-radius:8px;border:1px solid var(--border);display:block;margin:0 auto">
                <p style="font-size:11px;color:var(--text-2);margin:.6rem 0 0;text-align:center">Open WhatsApp → Linked Devices → Link a Device → Scan</p>`;
              if (!_syncTimer) {
                _syncTimer = setInterval(async () => {
                  try {
                    const s = await Api.phones.status(phoneId);
                    if (s.status === 'WORKING') {
                      clearInterval(_syncTimer); clearInterval(_pollTimer);
                      await Api.phones.syncNumber(phoneId).catch(() => {});
                      toast('WhatsApp connected! Syncing chats…', 'success');
                      loadSettingsTab('phones'); loadPhones();
                      _chatAutoSynced = false;
                      try { await Api.inbox.sync(phoneId); } catch(_) {}
                      loadChats();
                    }
                  } catch(_) {}
                }, 4000);
              }
            } else {
              area.innerHTML = `<p style="font-size:12px;color:var(--text-2);text-align:center">Waiting for QR…</p>`;
            }
          } catch(e) { area.innerHTML = `<p style="font-size:12px;color:var(--danger);text-align:center">${esc(e.message)}</p>`; }
          attempt++;
        }
        _pollTimer = setInterval(pollQr, 7000);
        await pollQr();
      }

      async function logoutAndShowQR(phoneId, btn, originalLabel) {
        if (btn) { btn.disabled = true; btn.textContent = 'Clearing session…'; }
        try {
          await Api.phones.logout(phoneId).catch(() => {});
          await new Promise(r => setTimeout(r, 1500));
          await Api.phones.start(phoneId).catch(() => {});
          await new Promise(r => setTimeout(r, 1500));
          if (btn) btn.textContent = 'Loading QR…';
          await startQrFlow(phoneId);
        } catch(err) {
          toast(err.message, 'error');
          if (btn) { btn.disabled = false; btn.textContent = originalLabel; }
        }
      }

      document.getElementById('btn-add-phone')?.addEventListener('click', () => {
        showAddPhoneModal();
      });

      el.querySelectorAll('.phone-btn-connect').forEach(btn => {
        btn.addEventListener('click', async () => {
          const pid = parseInt(btn.dataset.pid);
          await logoutAndShowQR(pid, btn, 'Connect');
        });
      });

      el.querySelectorAll('.phone-btn-reconnect').forEach(btn => {
        btn.addEventListener('click', async () => {
          const pid = parseInt(btn.dataset.pid);
          await logoutAndShowQR(pid, btn, 'Reconnect / QR');
        });
      });

      el.querySelectorAll('.phone-btn-disconnect').forEach(btn => {
        btn.addEventListener('click', async () => {
          const pid = parseInt(btn.dataset.pid);
          if (!confirm('Disconnect WhatsApp? You will need to scan QR again to reconnect.')) return;
          btn.disabled = true;
          try {
            await Api.phones.logout(pid);
            toast('Disconnected — scan QR to reconnect', 'success');
            loadSettingsTab('phones'); loadPhones();
          } catch(e) { toast(e.message, 'error'); btn.disabled = false; }
        });
      });

      el.querySelectorAll('.phone-btn-clear').forEach(btn => {
        btn.addEventListener('click', async () => {
          const pid = parseInt(btn.dataset.pid);
          if (!confirm('WARNING: This will permanently delete all synced chats, messages, and associated tasks/tickets for this phone from the database. Proceed?')) return;
          btn.disabled = true;
          try {
            await Api.phones.clearData(pid);
            toast('Data cleared successfully!', 'success');
            loadSettingsTab('phones'); loadPhones();
          } catch(e) { toast(e.message, 'error'); btn.disabled = false; }
        });
      });

      el.querySelectorAll('.phone-btn-delete').forEach(btn => {
        btn.addEventListener('click', async () => {
          const pid = parseInt(btn.dataset.pid);
          if (!confirm('Remove this phone session from Hyperscope? This will deactivate the session.')) return;
          btn.disabled = true;
          try {
            await Api.phones.del(pid);
            toast('Phone session removed', 'success');
            loadSettingsTab('phones'); loadPhones();
          } catch(e) { toast(e.message, 'error'); btn.disabled = false; }
        });
      });

    } catch(_) { el.innerHTML = '<div class="loading-center text-muted">Could not load WhatsApp status</div>'; }
  }

  else if (tab === 'labels') {
    try {
      const lbls = await Api.labels.list();
      el.innerHTML = `
        <div style="margin-bottom:1.5rem;display:flex;justify-content:space-between;align-items:center;padding-bottom:1rem;border-bottom:1px solid var(--border-light)">
          <div>
            <h3 style="margin:0 0 .25rem;font-size:16px;font-weight:600">Labels</h3>
            <p style="margin:0;font-size:12.5px;color:var(--text-3)">Manage labels to categorize chats and organize your inbox</p>
          </div>
          <button class="btn btn-primary btn-sm" id="add-label-btn">+ New Label</button>
        </div>
        <div class="content-card">
          <div class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th style="width: 60px;">Color</th>
                  <th>Label Name</th>
                  <th style="text-align: right; width: 120px;">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${lbls.map(l => `
                  <tr>
                    <td>
                      <div style="width:18px;height:18px;border-radius:4px;background:${esc(l.color)};border:1px solid rgba(0,0,0,0.15)"></div>
                    </td>
                    <td style="font-weight:600;font-size:13.5px;color:var(--text)">${esc(l.name)}</td>
                    <td style="text-align: right;">
                      <button class="btn btn-ghost btn-sm lbl-del" data-id="${l.id}" style="color:var(--danger);padding:4px 8px;font-size:12px;font-weight:500" title="Delete Label">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:3px;vertical-align:middle"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                        Delete
                      </button>
                    </td>
                  </tr>`).join('') || `<tr><td colspan="3" class="text-muted" style="text-align:center;padding:2rem">No labels yet. Click "+ New Label" to create one.</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>`;

      document.getElementById('add-label-btn').addEventListener('click', () => {
        showModal('New Label', `
          <div class="form-group"><label>Name *</label><input type="text" id="lbl-name" placeholder="e.g. VIP, Support, Sales"></div>
          <div class="form-group"><label>Color</label><input type="color" id="lbl-color" value="#0D8C7C"></div>
          <div class="modal-footer">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-primary" id="lbl-save">Create</button>
          </div>`);
        document.getElementById('lbl-save').addEventListener('click', async () => {
          const name = document.getElementById('lbl-name').value.trim();
          if (!name) return toast('Name required', 'error');
          try { await Api.labels.create({ name, color: document.getElementById('lbl-color').value }); closeModal(); toast('Label created', 'success'); loadSettingsTab('labels'); loadLabels(); }
          catch(e) { toast(e.message, 'error'); }
        });
      });
      el.querySelectorAll('.lbl-del').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm('Delete label?')) return;
          try { await Api.labels.del(btn.dataset.id); toast('Deleted', 'success'); loadSettingsTab('labels'); loadLabels(); }
          catch(e) { toast(e.message, 'error'); }
        });
      });
    } catch(_) {}
  }

  else if (tab === 'quickreplies') {
    try {
      const qrs = await Api.quickReplies.list();
      el.innerHTML = `
        <div style="margin-bottom:1.5rem;display:flex;justify-content:space-between;align-items:center;padding-bottom:1rem;border-bottom:1px solid var(--border-light)">
          <div>
            <h3 style="margin:0 0 .25rem;font-size:16px;font-weight:600">Quick Replies</h3>
            <p style="margin:0;font-size:12.5px;color:var(--text-3)">Create shortcuts (starting with /) to quickly insert templates into the composer</p>
          </div>
          <button class="btn btn-primary btn-sm" id="add-qr-btn">+ New Quick Reply</button>
        </div>
        <div class="content-card">
          <div class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th style="width: 150px;">Shortcut</th>
                  <th>Message Template</th>
                  <th style="text-align: right; width: 120px;">Actions</th>
                </tr>
              </thead>
              <tbody>
                ${qrs.map(q => `
                  <tr>
                    <td style="font-weight:700;font-size:13.5px;color:var(--accent);font-family:monospace">/${esc(q.command)}</td>
                    <td style="font-size:13px;color:var(--text-2);word-break:break-all">${esc(q.message)}</td>
                    <td style="text-align: right;">
                      <button class="btn btn-ghost btn-sm qr-del" data-id="${q.id}" style="color:var(--danger);padding:4px 8px;font-size:12px;font-weight:500" title="Delete Quick Reply">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:3px;vertical-align:middle"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                        Delete
                      </button>
                    </td>
                  </tr>`).join('') || `<tr><td colspan="3" class="text-muted" style="text-align:center;padding:2rem">No quick replies yet. Click "+ New Quick Reply" to create one.</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>`;

      document.getElementById('add-qr-btn').addEventListener('click', () => {
        showModal('New Quick Reply', `
          <div class="form-group"><label>Command *</label><input type="text" id="qr-cmd" placeholder="e.g. hello (no slash)"></div>
          <div class="form-group"><label>Message *</label><textarea id="qr-msg" placeholder="Message text to send..."></textarea></div>
          <div class="modal-footer">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-primary" id="qr-save">Create</button>
          </div>`);
        document.getElementById('qr-save').addEventListener('click', async () => {
          const cmd = document.getElementById('qr-cmd').value.trim();
          const msg = document.getElementById('qr-msg').value.trim();
          if (!cmd || !msg) return toast('Command and message required', 'error');
          try { await Api.quickReplies.create({ command: cmd, message: msg }); closeModal(); toast('Created', 'success'); loadSettingsTab('quickreplies'); }
          catch(e) { toast(e.message, 'error'); }
        });
      });
      el.querySelectorAll('.qr-del').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm('Delete?')) return;
          try { await Api.quickReplies.del(btn.dataset.id); toast('Deleted', 'success'); loadSettingsTab('quickreplies'); }
          catch(e) { toast(e.message, 'error'); }
        });
      });
    } catch(_) {}
  }

  else if (tab === 'agents') {
    try {
      const agents = await Api.auth.agents();
      el.innerHTML = `
        <div style="margin-bottom:1rem;display:flex;justify-content:flex-end">
          <button class="btn btn-primary btn-sm" id="invite-agent-btn">+ Invite Agent</button>
        </div>
        ${agents.map(a => `
          <div style="display:flex;align-items:center;gap:.75rem;padding:.65rem .85rem;border-bottom:1px solid var(--border-light)">
            <div class="agent-avatar" style="background:${avatarColor(a.name)};width:32px;height:32px;font-size:12px">${initials(a.name)}</div>
            <div style="flex:1">
              <div style="font-weight:600;font-size:13px">${esc(a.name)}</div>
              <div style="font-size:11px;color:var(--text-3)">${esc(a.email)} · ${a.role}</div>
            </div>
            <span class="pill ${a.is_active ? 'pill-resolved' : 'pill-closed'}">${a.is_active ? 'Active' : 'Inactive'}</span>
            <button class="btn btn-secondary btn-sm agent-numbers" data-aid="${a.id}" data-name="${esc(a.name)}">Numbers</button>
          </div>`).join('')}`;

      el.querySelectorAll('.agent-numbers').forEach(btn => {
        btn.addEventListener('click', async () => {
          try {
            const [perm, phones] = await Promise.all([
              Api.auth.agentPhones(btn.dataset.aid), Api.phones.list(),
            ]);
            const allowed = new Set(perm.phone_ids);
            showModal(`Number Access — ${btn.dataset.name}`, `
              <p class="text-muted" style="font-size:12.5px;margin-bottom:.75rem">
                Select which WhatsApp numbers this agent can access. No selection = access to all numbers.
              </p>
              ${phones.map(p => `
                <label style="display:flex;align-items:center;gap:.5rem;padding:.4rem 0;font-size:13px">
                  <input type="checkbox" class="perm-phone" value="${p.id}" ${allowed.has(p.id) ? 'checked' : ''}>
                  ${esc(p.name || p.phone_number)} (${esc(p.phone_number)})
                </label>`).join('') || '<p class="text-muted">No phones connected</p>'}
              <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                <button class="btn btn-primary" id="perm-save">Save</button>
              </div>`);
            document.getElementById('perm-save').addEventListener('click', async () => {
              const ids = [...document.querySelectorAll('.perm-phone:checked')].map(c => parseInt(c.value));
              try {
                await Api.auth.setAgentPhones(btn.dataset.aid, ids);
                closeModal(); toast('Number permissions saved', 'success');
              } catch(e) { toast(e.message, 'error'); }
            });
          } catch(e) { toast(e.message, 'error'); }
        });
      });

      document.getElementById('invite-agent-btn').addEventListener('click', () => {
        showModal('Invite Team Member', `
          <div class="form-group"><label>Full Name *</label><input type="text" id="inv-name"></div>
          <div class="form-group"><label>Email *</label><input type="email" id="inv-email"></div>
          <div class="form-group"><label>Password *</label><input type="password" id="inv-pass"></div>
          <div class="form-group"><label>Role</label>
            <select id="inv-role"><option value="agent">Agent</option><option value="admin">Admin</option><option value="viewer">Viewer</option></select>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-primary" id="inv-save">Invite</button>
          </div>`);
        document.getElementById('inv-save').addEventListener('click', async () => {
          const name = document.getElementById('inv-name').value.trim();
          const email = document.getElementById('inv-email').value.trim();
          const pass = document.getElementById('inv-pass').value;
          if (!name || !email || !pass) return toast('All fields required', 'error');
          try {
            await Api.auth.register({ name, email, password: pass, role: document.getElementById('inv-role').value });
            closeModal(); toast('Agent created', 'success'); loadSettingsTab('agents');
          } catch(e) { toast(e.message, 'error'); }
        });
      });
    } catch(_) {}
  }

  else if (tab === 'properties') {
    const entity = window._propEntity || 'chat';
    try {
      const defs = await Api.properties.definitions(entity);
      const sections = {};
      defs.forEach(d => { (sections[d.section] = sections[d.section] || []).push(d); });
      el.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
          <div class="tab-bar" style="border:none">
            <div class="tab ${entity === 'chat' ? 'active' : ''}" data-ent="chat">Chat properties</div>
            <div class="tab ${entity === 'ticket' ? 'active' : ''}" data-ent="ticket">Ticket properties</div>
          </div>
          <button class="btn btn-primary btn-sm" id="new-prop-btn">+ New Property</button>
        </div>
        ${Object.keys(sections).length ? Object.entries(sections).map(([sec, list]) => `
          <div class="content-card" style="margin-bottom:.8rem">
            <div class="card-header">${esc(sec)}</div>
            <div class="table-wrap"><table class="data-table">
              <thead><tr><th>Name</th><th>Type</th><th>Options</th><th>Required</th><th></th></tr></thead>
              <tbody>${list.map(d => `<tr>
                <td style="font-weight:600">${esc(d.name)}</td>
                <td><span class="pill pill-open" style="font-size:11px">${esc(d.prop_type)}</span></td>
                <td style="font-size:12px;color:var(--text-3)">${(d.options || []).map(esc).join(', ') || '—'}</td>
                <td>${d.required ? 'Yes' : 'No'}</td>
                <td>
                  <button class="btn btn-ghost btn-sm prop-del" data-pid="${d.id}" style="color:var(--danger);padding:4px 8px;font-size:12px;font-weight:500" title="Delete Property">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:2px;vertical-align:middle"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                    Delete
                  </button>
                </td>
              </tr>`).join('')}</tbody>
            </table></div>
          </div>`).join('')
        : `<div class="empty-state" style="padding:3rem;text-align:center">
            <p class="text-muted" style="font-size:13px">No custom ${entity} properties yet.<br>
            Create fields like "Plan", "Renewal date" or "Account owner" — they appear in the ${entity === 'chat' ? 'chat detail panel' : 'ticket view'}.</p>
          </div>`}`;
      el.querySelectorAll('.tab[data-ent]').forEach(t => t.addEventListener('click', () => {
        window._propEntity = t.dataset.ent; loadSettingsTab('properties');
      }));
      el.querySelectorAll('.prop-del').forEach(btn => btn.addEventListener('click', async () => {
        if (!confirm('Delete this property? Its values will stay stored but hidden.')) return;
        try { await Api.properties.deleteDef(btn.dataset.pid); toast('Deleted', 'success'); loadSettingsTab('properties'); }
        catch(e) { toast(e.message, 'error'); }
      }));
      document.getElementById('new-prop-btn').addEventListener('click', () => {
        showModal('New Custom Property', `
          <div class="form-group"><label>Entity</label><select id="pr-entity">
            <option value="chat" ${entity === 'chat' ? 'selected' : ''}>Chat</option>
            <option value="ticket" ${entity === 'ticket' ? 'selected' : ''}>Ticket</option>
          </select></div>
          <div class="form-group"><label>Section</label><input type="text" id="pr-section" value="General" placeholder="e.g. Account details"></div>
          <div class="form-group"><label>Name *</label><input type="text" id="pr-name" placeholder="e.g. Plan"></div>
          <div class="form-group"><label>Type</label><select id="pr-type">
            <option value="text">Text</option>
            <option value="number">Number</option>
            <option value="date">Date</option>
            <option value="single_select">Single-select dropdown</option>
            <option value="multi_select">Multi-select dropdown</option>
          </select></div>
          <div class="form-group" id="pr-options-wrap" style="display:none">
            <label>Options (comma separated) *</label>
            <input type="text" id="pr-options" placeholder="Free, Pro, Enterprise">
          </div>
          <div class="form-group"><label style="display:flex;align-items:center;gap:.4rem;font-weight:400">
            <input type="checkbox" id="pr-required" style="width:15px;height:15px"> Required (tickets)</label></div>
          <div class="modal-footer">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-primary" id="pr-save">Create</button>
          </div>`);
        const typeSel = document.getElementById('pr-type');
        typeSel.addEventListener('change', () => {
          document.getElementById('pr-options-wrap').style.display =
            typeSel.value.endsWith('_select') ? 'block' : 'none';
        });
        document.getElementById('pr-save').addEventListener('click', async () => {
          const name = document.getElementById('pr-name').value.trim();
          if (!name) return toast('Name required', 'error');
          try {
            await Api.properties.createDef({
              entity: document.getElementById('pr-entity').value,
              section: document.getElementById('pr-section').value.trim() || 'General',
              name,
              prop_type: typeSel.value,
              options: typeSel.value.endsWith('_select')
                ? document.getElementById('pr-options').value.split(',').map(s => s.trim()).filter(Boolean)
                : null,
              required: document.getElementById('pr-required').checked,
            });
            closeModal(); toast('Property created', 'success'); loadSettingsTab('properties');
          } catch(e) { toast(e.message, 'error'); }
        });
      });
    } catch(e) { el.innerHTML = `<div class="loading-center text-muted">${esc(e.message)}</div>`; }
  }



  else if (tab === 'exports') {
    el.innerHTML = `
      <div class="settings-grid">
        ${[
          ['Chats', 'chats', 'All chats with status, labels and assignment'],
          ['Messages (30d)', 'messages', 'Message history for the last 30 days'],
          ['Tickets', 'tickets', 'Tickets with status, priority and SLA info'],
          ['Contacts', 'contacts', 'Contact book (masked numbers stay masked)'],
          ['Audit Logs (30d)', 'logs', 'Full audit trail — admin only'],
        ].map(([label, key, desc]) => `
          <div class="content-card">
            <div class="card-header">${label}</div>
            <div class="card-body">
              <p class="text-muted" style="font-size:12.5px;margin-bottom:.7rem">${desc}</p>
              <button class="btn btn-secondary btn-sm export-btn" data-key="${key}">Download CSV</button>
            </div>
          </div>`).join('')}
      </div>`;
    el.querySelectorAll('.export-btn').forEach(btn => btn.addEventListener('click', async () => {
      try { await Api.exports[btn.dataset.key](); toast('Export downloaded', 'success'); }
      catch(e) { toast(e.message, 'error'); }
    }));
  }
}


// ── DASHBOARD VIEW ──────────────────────────────────────────────── //
function _stopDashWahaPoller() {
  if (_dashWahaTimer) { clearInterval(_dashWahaTimer); _dashWahaTimer = null; }
}

async function _updateDashWaha(phoneId) {
  if (_dashWahaUpdating) return;
  _dashWahaUpdating = true;
  try {
    const box = document.getElementById('dash-waha-box');
    const label = document.getElementById('dash-waha-label');
    const actions = document.getElementById('dash-waha-actions');
    if (!box || !label || !actions) return;

    let status = 'UNKNOWN';
    try {
      const r = await Api.phones.status(phoneId);
      status = (r.status || 'UNKNOWN').toUpperCase();
    } catch(_) { status = 'UNKNOWN'; }

    if (status === 'WORKING') {
      if (_dashWahaPrevStatus !== 'WORKING') {
        box.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:.5rem;padding:1.5rem 0">
          <div style="width:64px;height:64px;border-radius:50%;background:#dcfce7;display:flex;align-items:center;justify-content:center">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#15803d" stroke-width="2.2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
          </div>
          <span style="font-size:12px;font-weight:600;color:#15803d;background:#dcfce7;padding:.25rem .75rem;border-radius:20px">Connected</span>
        </div>`;
        label.innerHTML = `<strong style="font-size:13px">WhatsApp</strong><br><span style="font-size:11px;color:var(--text-3)">Session active</span>`;
        actions.innerHTML = `
          <button class="btn btn-danger btn-sm" id="dash-btn-stop">Disconnect</button>
          <button class="btn btn-primary btn-sm" id="dash-btn-restart">Restart</button>`;
        _bindDashWahaButtons(phoneId);
      }

    } else if (status === 'SCAN_QR_CODE') {
      if (_dashWahaPrevStatus !== 'SCAN_QR_CODE') {
        box.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%">
          <div style="width:28px;height:28px;border:3px solid #e5e7eb;border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite"></div>
        </div>`;
        label.innerHTML = `Loading QR code…`;
        actions.innerHTML = `<button class="btn btn-secondary btn-sm" id="dash-btn-restart">Reconnect</button>`;
        _bindDashWahaButtons(phoneId);
      }
      try {
        const qrData = await Api.phones.qr(phoneId);
        const boxNow = document.getElementById('dash-waha-box');
        const lblNow = document.getElementById('dash-waha-label');
        if (qrData && qrData.qr && boxNow) {
          boxNow.innerHTML = `<img src="${qrData.qr}" style="width:100%;height:100%;display:block;object-fit:contain;" alt="WhatsApp QR">`;
          if (lblNow) lblNow.innerHTML = `Scan to connect WhatsApp<br><span style="font-size:11px;color:var(--text-3)">Settings → Linked Devices → Link a Device</span>`;
        }
      } catch(_) {}

    } else if (status === 'STARTING') {
      if (_dashWahaPrevStatus !== 'STARTING') {
        box.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:.75rem;padding:1.5rem 0">
          <div style="width:40px;height:40px;border:3px solid #e5e7eb;border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite"></div>
          <span style="font-size:12px;color:var(--text-3)">Connecting…</span>
        </div>`;
        label.innerHTML = `Starting WhatsApp session`;
        actions.innerHTML = ``;
      }

    } else if (status === 'STOPPED') {
      if (_dashWahaPrevStatus !== 'STOPPED') {
        box.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:.5rem;padding:1.5rem 0">
          <div style="width:64px;height:64px;border-radius:50%;background:#fee2e2;display:flex;align-items:center;justify-content:center">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
          </div>
          <span style="font-size:12px;font-weight:600;color:#dc2626;background:#fee2e2;padding:.25rem .75rem;border-radius:20px">Disconnected</span>
        </div>`;
        label.innerHTML = `Session is stopped`;
        actions.innerHTML = `<button class="btn btn-primary btn-sm" id="dash-btn-start">Scan QR to Connect</button>`;
        _bindDashWahaButtons(phoneId);
      }

    } else {
      if (_dashWahaPrevStatus !== status) {
        box.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:.5rem;padding:1.5rem 0">
          <div style="width:64px;height:64px;border-radius:50%;background:#f3f4f6;display:flex;align-items:center;justify-content:center">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#9ca3af" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          </div>
          <span style="font-size:12px;color:var(--text-3)">${status}</span>
        </div>`;
        label.innerHTML = `Unknown state`;
        actions.innerHTML = `<button class="btn btn-secondary btn-sm" id="dash-btn-start">Start Session</button>`;
        _bindDashWahaButtons(phoneId);
      }
    }
    _dashWahaPrevStatus = status;
  } finally {
    _dashWahaUpdating = false;
  }
}

async function _dashShowQR(phoneId) {
  // Logout clears WAHA auth → next start forces fresh QR
  const box = document.getElementById('dash-waha-box');
  const lbl = document.getElementById('dash-waha-label');
  const act = document.getElementById('dash-waha-actions');
  if (box) box.innerHTML = `<div style="width:32px;height:32px;border:3px solid #e5e7eb;border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite"></div>`;
  if (lbl) lbl.innerHTML = 'Clearing session…';
  if (act) act.innerHTML = '';

  await Api.phones.logout(phoneId).catch(() => {});
  await new Promise(r => setTimeout(r, 1500));
  await Api.phones.start(phoneId).catch(() => {});
  await new Promise(r => setTimeout(r, 1500));

  if (lbl) lbl.innerHTML = 'Loading QR…';

  let attempts = 0;
  async function pollDashQR() {
    if (!document.getElementById('dash-waha-box')) return; // navigated away
    try {
      const s = await Api.phones.status(phoneId);
      const status = (s.status || '').toUpperCase();
      if (status === 'WORKING') {
        await Api.phones.syncNumber(phoneId).catch(() => {});
        toast('WhatsApp connected! Syncing chats…', 'success');
        // Sync chats from WAHA then refresh chat list
        _chatAutoSynced = false;
        try { await Api.inbox.sync(phoneId); } catch(_) {}
        await loadPhones();
        if (State.currentView === 'inbox') loadChats();
        _dashWahaPrevStatus = '';
        _startDashWahaPoller(phoneId);
        return;
      }
      const r = await Api.phones.qr(phoneId);
      if (r && r.qr) {
        const b = document.getElementById('dash-waha-box');
        const l = document.getElementById('dash-waha-label');
        const a = document.getElementById('dash-waha-actions');
        if (b) b.innerHTML = `<img src="${r.qr}" style="width:100%;height:100%;object-fit:contain;display:block" alt="QR">`;
        if (l) l.innerHTML = `Scan with WhatsApp<br><span style="font-size:11px;color:var(--text-3)">Settings → Linked Devices → Link a Device</span>`;
        if (a) {
          a.innerHTML = `<button class="btn btn-danger btn-sm" id="dash-btn-cancel-qr">Cancel</button>`;
          document.getElementById('dash-btn-cancel-qr')?.addEventListener('click', () => {
            Api.phones.stop(phoneId).catch(()=>{});
            _dashWahaPrevStatus = '';
            _startDashWahaPoller(phoneId);
          });
        }
      } else if (attempts < 5) {
        if (lbl) lbl.innerHTML = `Waiting for QR… (${attempts+1})`;
      }
    } catch(_) {}
    attempts++;
    if (attempts < 30) setTimeout(pollDashQR, 5000);
  }
  pollDashQR();
}

function _bindDashWahaButtons(phoneId) {
  document.getElementById('dash-btn-stop')?.addEventListener('click', async () => {
    if (!confirm('Disconnect WhatsApp? You will need to scan QR again to reconnect.')) return;
    _stopDashWahaPoller();
    const btn = document.getElementById('dash-btn-stop');
    if (btn) { btn.disabled = true; btn.textContent = 'Disconnecting…'; }
    await Api.phones.logout(phoneId).catch(() => {});
    _dashWahaPrevStatus = '';
    setTimeout(() => _startDashWahaPoller(phoneId), 1500);
  });
  document.getElementById('dash-btn-restart')?.addEventListener('click', async () => {
    _stopDashWahaPoller();
    const btn = document.getElementById('dash-btn-restart');
    if (btn) { btn.disabled = true; btn.textContent = 'Restarting…'; }
    await Api.phones.restart(phoneId).catch(() => {});
    _dashWahaPrevStatus = '';
    setTimeout(() => _startDashWahaPoller(phoneId), 2000);
  });
  document.getElementById('dash-btn-start')?.addEventListener('click', async () => {
    _stopDashWahaPoller();
    await _dashShowQR(phoneId);
  });
}

function _startDashWahaPoller(phoneId) {
  _dashWahaPrevStatus = '';
  _dashWahaUpdating = false;
  _updateDashWaha(phoneId);
  _dashWahaTimer = setInterval(() => {
    if (!document.getElementById('dash-waha-box')) { _stopDashWahaPoller(); return; }
    _updateDashWaha(phoneId);
  }, 5000);
}

async function renderDashboard() {
  _stopDashWahaPoller();
  const main = document.getElementById('main-content');

  const cards = [
    { icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`, title: 'Bulk Messages', desc: 'Send personalized broadcast messages to multiple contacts at once.', action: 'bulk', label: 'Send Now' },
    { icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>`, title: 'Manage Team', desc: 'Invite agents and assign roles to manage customer conversations.', action: 'settings', label: 'Invite Agents' },
    { icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.93 3.35 2 2 0 0 1 3.98 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 8.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>`, title: 'WhatsApp', desc: 'Connect your WhatsApp via QR code to start receiving messages.', action: 'settings', label: 'Connect' },
    { icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 12h6M9 16h6M17 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z"/></svg>`, title: 'Manage Tickets', desc: 'Track and resolve customer support tickets from your inbox.', action: 'tickets', label: 'View Tickets' },
    { icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>`, title: 'AI Agent', desc: 'Set up your Gemini AI agent to auto-handle conversations.', action: 'ai-agent', label: 'Configure AI' },
    { icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`, title: 'Automation Rules', desc: 'Create rules to auto-assign, label and respond to messages.', action: 'automation', label: 'Create Rule' },
  ];

  const wsName = 'Hyperscope';
  main.innerHTML = `
  <div class="dashboard-wrap" style="overflow-y:auto">
    <div class="dashboard-inner" style="max-width:1240px">

      <div class="dash-workspace">
        <div class="ws-logo">H</div>
        <div>
          <h2>${wsName}</h2>
          <div class="ws-sub">${esc(State.agent?.email || 'workspace')}</div>
        </div>
      </div>

      <div class="dash-grid">
        <div class="dash-main">

          <div class="stat-cards">
            <div class="stat-card">
              <div class="sc-label"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>All chats</div>
              <div class="sc-num" id="ds-total">—</div>
            </div>
            <div class="stat-card">
              <div class="sc-label"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>Unread chats</div>
              <div class="sc-num" id="ds-unread">—</div>
            </div>
            <div class="stat-card flagged">
              <div class="sc-label"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>Flagged chats</div>
              <div class="sc-num" id="ds-flagged">—</div>
            </div>
          </div>

          <div class="dash-duo">
            <div class="dash-panel">
              <div class="dp-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>Team</div>
              <div class="dp-body">
                <div style="font-size:12.5px;color:var(--text-2);margin-bottom:.5rem"><span id="ds-online">—</span> online</div>
                <div id="ds-team-avatars" style="display:flex;gap:.35rem"></div>
              </div>
            </div>
            <div class="dash-panel">
              <div class="dp-head"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 0 0-2 2v3a2 2 0 1 1 0 4v3a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3a2 2 0 1 1 0-4V7a2 2 0 0 0-2-2z"/></svg>Tickets</div>
              <div class="dp-body" style="display:flex;gap:2.5rem">
                <div>
                  <div style="font-size:12.5px;color:var(--text-3);display:flex;align-items:center;gap:.35rem"><span style="width:8px;height:8px;border-radius:50%;border:2px solid var(--danger);display:inline-block"></span>Open</div>
                  <div style="font-size:19px;font-weight:700;margin-top:.25rem" id="ds-tickets">—</div>
                </div>
                <div>
                  <div style="font-size:12.5px;color:var(--text-3)">Assigned to me</div>
                  <div style="font-size:19px;font-weight:700;margin-top:.25rem" id="ds-tickets-mine">—</div>
                </div>
                <div>
                  <div style="font-size:12.5px;color:var(--text-3)">In progress</div>
                  <div style="font-size:19px;font-weight:700;margin-top:.25rem" id="ds-tickets-prog">—</div>
                </div>
              </div>
            </div>
          </div>

          <div class="dash-panel" style="margin:1rem 0">
            <div class="dp-head">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
              Recent Conversations
              <button class="btn btn-ghost btn-sm" style="margin-left:auto;font-size:12px" onclick="switchView('inbox')">View all →</button>
            </div>
            <div class="dp-body" id="ds-recent-chats" style="padding:0">
              <div style="padding:1.5rem;text-align:center;color:var(--text-3);font-size:13px">Loading…</div>
            </div>
          </div>

          <div class="quick-links-title">Quick links</div>
          <div class="quick-links">
            ${cards.map(c => `<div class="ql-card">
              <h3>${c.icon} ${c.title}</h3>
              <p>${c.desc}</p>
              <div class="ql-actions">
                <button class="btn btn-secondary btn-sm" onclick="switchView('${c.action}')">${c.label}</button>
              </div>
            </div>`).join('')}
          </div>

        </div>

        <div class="dash-side">
          <div class="phone-status-head">
            <h3>WhatsApp</h3>
          </div>
          <div id="dash-phone-cards"></div>
          <div class="dash-panel" style="margin-top:.4rem">
            <div class="dp-body" style="text-align:center">
              <div class="gs-qr-box" id="dash-waha-box" style="display:flex;align-items:center;justify-content:center;min-height:120px">
                <div class="waha-spinner" style="width:32px;height:32px;border:3px solid #e5e7eb;border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite"></div>
              </div>
              <div class="gs-qr-label" id="dash-waha-label" style="font-size:12px;color:var(--text-3);margin-top:.5rem">Checking connection…</div>
              <div class="gs-qr-actions" id="dash-waha-actions" style="margin-top:.5rem"></div>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>`;

  // Load quick stats asynchronously
  try {
    const phones = State.phones.length ? State.phones : await Api.phones.list().catch(() => []);
    const phoneConnected = phones.some(p => p.waha_status === 'WORKING');

    const [dash, tkt, agents] = await Promise.all([
      Api.analytics.dashboard(),
      Api.analytics.tickets(),
      Api.auth.agents().catch(() => []),
    ]);
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    // Only show real chat counts when WhatsApp is connected
    set('ds-total', phoneConnected ? (dash.total_chats ?? 0) : 0);
    set('ds-unread', phoneConnected ? (dash.unread_chats ?? 0) : 0);
    set('ds-flagged', phoneConnected ? (dash.flagged_chats ?? 0) : 0);
    set('ds-tickets', tkt.open ?? 0);
    set('ds-tickets-prog', tkt.in_progress ?? 0);
    set('ds-tickets-mine', '-');
    set('ds-online', `${dash.online_agents ?? agents.length} of ${agents.length || (dash.online_agents ?? 0)}`);
    const avEl = document.getElementById('ds-team-avatars');
    if (avEl) avEl.innerHTML = agents.slice(0, 8).map(a =>
      `<div class="agent-avatar" title="${esc(a.name)}" style="background:${avatarColor(a.name)};width:28px;height:28px;font-size:11px">${initials(a.name)}</div>`
    ).join('');
    try {
      const mine = await Api.tickets.list({ assigned_to: State.agent?.id, status: 'open' });
      set('ds-tickets-mine', mine.length);
    } catch(_) {}

    // Recent conversations panel
    try {
      const recentEl = document.getElementById('ds-recent-chats');
      if (recentEl && phoneConnected) {
        const recent = await Api.inbox.chats({ limit: 8 });
        if (!recent.length) {
          recentEl.innerHTML = `<div style="padding:1.5rem;text-align:center;color:var(--text-3);font-size:13px">No conversations yet</div>`;
        } else {
          recentEl.innerHTML = recent.map(c => {
            const unread = c.unread_count > 0;
            const avatar = initials(c.name || c.chat_wid);
            const color = avatarColor(c.name || c.chat_wid);
            const preview = c.last_message
              ? (c.last_message.length > 55 ? c.last_message.slice(0, 55) + '…' : c.last_message)
              : '<span style="opacity:.45;font-style:italic">No messages</span>';
            const time = c.last_message_at ? timeAgo(c.last_message_at) : '';
            return `<div class="ds-chat-row" onclick="switchView('inbox');setTimeout(()=>openChat(${c.id}),300)" style="display:flex;align-items:center;gap:.65rem;padding:.55rem 1rem;cursor:pointer;border-bottom:1px solid var(--border-light);transition:background .1s" onmouseenter="this.style.background='var(--bg)'" onmouseleave="this.style.background=''">
              <div class="agent-avatar" style="background:${color};width:34px;height:34px;font-size:12px;flex-shrink:0">${esc(avatar)}</div>
              <div style="flex:1;min-width:0">
                <div style="display:flex;align-items:center;justify-content:space-between;gap:.5rem">
                  <span style="font-size:13px;font-weight:${unread ? 700 : 500};color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(displayName(c))}</span>
                  <span style="font-size:11px;color:var(--text-4);flex-shrink:0">${time}</span>
                </div>
                <div style="font-size:12px;color:var(--text-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px">${preview}</div>
              </div>
              ${unread ? `<span style="background:var(--accent);color:#fff;font-size:11px;font-weight:600;min-width:18px;height:18px;border-radius:99px;display:flex;align-items:center;justify-content:center;padding:0 5px;flex-shrink:0">${c.unread_count > 99 ? '99+' : c.unread_count}</span>` : ''}
            </div>`;
          }).join('');
        }
      } else if (recentEl) {
        recentEl.innerHTML = `<div style="padding:1.5rem;text-align:center;color:var(--text-3);font-size:13px">Connect WhatsApp to see conversations</div>`;
      }
    } catch(_) {}
  } catch(_) {}

  // Phone status cards + live WAHA poller
  try {
    const phones = await Api.phones.list();
    const cardsEl = document.getElementById('dash-phone-cards');
    if (cardsEl) cardsEl.innerHTML = phones.map(p => {
      const ok = p.waha_status === 'WORKING';
      return `<div class="phone-card">
        <div class="pc-avatar" style="background:${ok?'#dcfce7':'#f3f4f6'};color:${ok?'#15803d':'#6b7280'}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.93 3.35 2 2 0 0 1 3.98 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 8.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
        </div>
        <div class="pc-meta">
          <div class="pc-number">WhatsApp</div>
        </div>
        <div class="pc-status ${ok ? 'connected' : 'offline'}"><span class="dot"></span>${ok ? 'Connected' : esc(p.waha_status || 'Offline')}</div>
        <button class="pc-menu-btn" title="Manage in Settings" onclick="switchView('settings')">⋯</button>
      </div>`;
    }).join('') || '';

    if (phones && phones.length > 0) {
      _startDashWahaPoller(phones[0].id);
    } else {
      const box = document.getElementById('dash-waha-box');
      const lbl = document.getElementById('dash-waha-label');
      const act = document.getElementById('dash-waha-actions');
      if (box) box.innerHTML = `<svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#d1d5db" stroke-width="1"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="7" y="7" width="3" height="3" fill="#d1d5db" stroke="none"/><rect x="14" y="14" width="3" height="3" fill="#d1d5db" stroke="none"/></svg>`;
      if (lbl) lbl.innerHTML = `Scan QR to connect WhatsApp`;
      if (act) act.innerHTML = `<button class="btn btn-primary btn-sm" onclick="switchView('settings')">Connect WhatsApp</button>`;
    }
  } catch(_) {}
}

// ── COMMUNITIES VIEW ────────────────────────────────────────────── //
async function renderCommunities() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header">
        <h2>Groups</h2>
        <div class="header-actions" style="margin-left:auto;display:flex;gap:.5rem">
          <input type="text" id="grp-search" class="search-input" placeholder="Search groups..." style="max-width:220px">
          <button class="btn btn-secondary btn-sm" id="grp-refresh">Refresh</button>
        </div>
      </div>
      <div class="scroll-area" id="groups-list"><div class="loading-center"><div class="spinner"></div></div></div>
    </div>`;
  document.getElementById('grp-refresh').addEventListener('click', () => loadGroups());
  let t; document.getElementById('grp-search').addEventListener('input', e => {
    clearTimeout(t); t = setTimeout(() => loadGroups(e.target.value.trim()), 300);
  });
  await loadGroups();
}

async function loadGroups(search) {
  const el = document.getElementById('groups-list');
  if (!el) return;
  try {
    const phones = await Api.phones.list().catch(() => []);
    State.phones = phones;
    const phoneConnected = phones.some(p => p.waha_status === 'WORKING');

    if (!phoneConnected) {
      el.innerHTML = `<div class="empty-state whatsapp-disconnected-thread" style="padding:4rem 2rem">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:48px;height:48px;opacity:.25;color:var(--text-3)">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          <path d="M2 2l20 20"/>
        </svg>
        <p style="font-size:15px;font-weight:600;color:var(--text-2);opacity:.8;margin:0.5rem 0 0.25rem">WhatsApp Disconnected</p>
        <span style="font-size:13px;color:var(--text-3);max-width:320px;line-height:1.4">Connect your WhatsApp to view groups.</span>
        <button class="btn btn-primary btn-sm" style="margin-top:0.75rem" onclick="switchView('settings')">Connect WhatsApp</button>
      </div>`;
      return;
    }

    const groups = await Api.groups.list(search ? { search } : undefined);
    if (!groups.length) {
      el.innerHTML = `<div class="empty-state" style="padding:4rem 2rem">
        <p style="font-size:14px;color:var(--text-3);max-width:340px;text-align:center;line-height:1.6">
          No WhatsApp groups synced yet. Connect a phone and sync chats — group chats will appear here automatically.
        </p>
        <button class="btn btn-primary btn-sm" onclick="switchView('settings')">Connect a Phone</button>
      </div>`;
      return;
    }
    el.innerHTML = `<div class="content-card"><div class="table-wrap"><table class="data-table">
      <thead><tr><th>Group</th><th>Messages (7d)</th><th>Unread</th><th>Last Activity</th><th></th></tr></thead>
      <tbody>${groups.map(g => `<tr>
        <td style="font-weight:600">${esc(displayName(g))}${g.is_flagged ? ' 🚩' : ''}</td>
        <td>${g.messages_7d}</td>
        <td>${g.unread_count || 0}</td>
        <td style="font-size:12px;color:var(--text-3)">${g.last_message_at ? timeAgo(g.last_message_at) : '—'}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-secondary btn-sm grp-members" data-gid="${g.id}">Members</button>
          <button class="btn btn-secondary btn-sm grp-stats" data-gid="${g.id}" data-name="${esc(displayName(g))}">Analytics</button>
        </td>
      </tr>`).join('')}</tbody>
    </table></div></div>`;
    el.querySelectorAll('.grp-members').forEach(btn => btn.addEventListener('click', () => showGroupMembers(btn.dataset.gid)));
    el.querySelectorAll('.grp-stats').forEach(btn => btn.addEventListener('click', () => showGroupAnalytics(btn.dataset.gid, btn.dataset.name)));
  } catch(e) { el.innerHTML = `<div class="loading-center text-muted">${esc(e.message)}</div>`; }
}

async function showGroupMembers(gid) {
  showModal('Group Members', '<div class="loading-center"><div class="spinner"></div></div>');
  try {
    const res = await Api.groups.participants(gid);
    const body = document.querySelector('#modal .modal-body') || document.querySelector('#modal-body');
    let membersHtml;
    if (!res.api_available) {
      membersHtml = `<tr><td colspan="2" style="padding:1rem;text-align:center">
        <div style="color:var(--text-3);font-size:13px;line-height:1.6">
          <div style="font-size:20px;margin-bottom:.4rem">⚠️</div>
          WAHA could not fetch members for this group.<br>
          <span style="font-size:12px">This may require a WAHA Plus plan or the group may no longer be accessible.</span>
        </div>
      </td></tr>`;
    } else if (!res.participants.length) {
      membersHtml = `<tr><td colspan="2" class="text-muted" style="text-align:center;padding:1rem">No members found</td></tr>`;
    } else {
      membersHtml = res.participants.map(p => `<tr>
        <td>+${esc(p.number)}</td>
        <td>${p.is_admin ? '<span class="pill pill-resolved">Admin</span>' : 'Member'}</td>
      </tr>`).join('');
    }
    const html = `
      <p style="font-size:13px;color:var(--text-2);margin-bottom:.75rem">
        <strong>${esc(res.group)}</strong>${res.count ? ` — ${res.count} members` : ''}
      </p>
      <div class="table-wrap" style="max-height:320px;overflow-y:auto"><table class="data-table">
        <thead><tr><th>Number</th><th>Role</th></tr></thead>
        <tbody>${membersHtml}</tbody>
      </table></div>`;
    if (body) body.innerHTML = html; else showModal('Group Members', html);
  } catch(e) { toast(e.message, 'error'); closeModal(); }
}

async function showGroupAnalytics(gid, name) {
  showModal(`Analytics — ${name}`, '<div class="loading-center"><div class="spinner"></div></div>');
  try {
    const a = await Api.groups.analytics(gid, 30);
    const maxDay = Math.max(1, ...a.daily_volume.map(d => d.count));
    const html = `
      <div style="display:flex;gap:1rem;margin-bottom:1rem">
        <div class="stat-mini"><div class="stat-mini-num">${a.total_messages}</div><div class="stat-mini-label">Messages (30d)</div></div>
        <div class="stat-mini"><div class="stat-mini-num">${a.incoming}</div><div class="stat-mini-label">Incoming</div></div>
        <div class="stat-mini"><div class="stat-mini-num">${a.outgoing}</div><div class="stat-mini-label">Outgoing</div></div>
      </div>
      <div style="display:flex;align-items:flex-end;gap:2px;height:60px;margin-bottom:1rem">
        ${a.daily_volume.map(d => `<div title="${d.date}: ${d.count}" style="flex:1;background:var(--accent);opacity:.75;border-radius:2px 2px 0 0;height:${Math.max(4, Math.round(d.count / maxDay * 60))}px"></div>`).join('') || '<span class="text-muted">No activity</span>'}
      </div>
      <p style="font-size:12px;font-weight:600;margin-bottom:.35rem">Top senders</p>
      <div class="table-wrap" style="max-height:200px;overflow-y:auto"><table class="data-table">
        <tbody>${a.top_senders.map(s => `<tr><td>${esc(s.name)}</td><td style="text-align:right">${s.messages}</td></tr>`).join('') || '<tr><td class="text-muted">No senders yet</td></tr>'}</tbody>
      </table></div>`;
    const body = document.querySelector('#modal .modal-body') || document.querySelector('#modal-body');
    if (body) body.innerHTML = html; else showModal(`Analytics — ${name}`, html);
  } catch(e) { toast(e.message, 'error'); closeModal(); }
}

// ── SCHEDULED MESSAGES VIEW ─────────────────────────────────────── //
async function renderScheduled() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header">
        <h2>Scheduled Messages</h2>
        <div class="header-actions" style="margin-left:auto">
          <button class="btn btn-primary btn-sm" id="new-sched-btn">+ Schedule Message</button>
        </div>
      </div>
      <div class="scroll-area" id="sched-list"><div class="loading-center"><div class="spinner"></div></div></div>
    </div>`;
  document.getElementById('new-sched-btn').addEventListener('click', () => showScheduleModal());
  await loadScheduled();
}

async function loadScheduled() {
  const el = document.getElementById('sched-list');
  if (!el) return;
  try {
    const items = await Api.scheduled.list();
    if (!items.length) { el.innerHTML = `<div class="loading-center text-muted">Nothing scheduled yet</div>`; return; }
    el.innerHTML = `<div class="content-card"><div class="table-wrap"><table class="data-table">
      <thead><tr><th>Chat</th><th>Message</th><th>Next Send</th><th>Repeat</th><th>Ends</th><th>Status</th><th>Sent</th><th style="width:140px;text-align:right">Actions</th></tr></thead>
      <tbody>${items.map(m => `<tr>
        <td style="font-weight:600">${esc(displayName(m.chat_name) || ('#' + m.chat_id))}</td>
        <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(m.body)}</td>
        <td style="font-size:12px">${new Date(m.send_at).toLocaleString()}</td>
        <td style="font-size:12px">${esc(m.repeat_summary || (m.repeat === 'none' ? 'Once' : m.repeat))}</td>
        <td style="font-size:12px;color:var(--text-3)">${m.end_date ? new Date(m.end_date).toLocaleDateString() : (m.repeat !== 'none' ? 'Open-ended' : '—')}</td>
        <td><span class="${pillClass(m.status==='sent'?'resolved':m.status==='failed'?'urgent':'open')}">${m.status}</span>${m.last_error ? ` <span title="${esc(m.last_error)}">⚠️</span>` : ''}</td>
        <td>${m.sent_count}</td>
        <td style="white-space:nowrap;text-align:right">${m.status === 'pending' ? `
          <button class="btn btn-secondary btn-sm sched-edit" data-sid="${m.id}">Edit</button>
          <button class="btn btn-danger btn-sm sched-cancel" data-sid="${m.id}">Cancel</button>` : ''}</td>
      </tr>`).join('')}</tbody>
    </table></div></div>`;
    el.querySelectorAll('.sched-cancel').forEach(btn => btn.addEventListener('click', async () => {
      try { await Api.scheduled.cancel(btn.dataset.sid); toast('Cancelled', 'success'); loadScheduled(); }
      catch(e) { toast(e.message, 'error'); }
    }));
    el.querySelectorAll('.sched-edit').forEach(btn => btn.addEventListener('click', () => {
      const item = items.find(x => x.id == btn.dataset.sid);
      if (item) showScheduleModal(null, null, item);
    }));
  } catch(e) { el.innerHTML = `<div class="loading-center text-muted">${esc(e.message)}</div>`; }
}

async function showScheduleModal(prefillChatId, prefillBody, editItem) {
  let chats = [];
  try { chats = await Api.inbox.chats({ limit: 200 }); } catch(_) {}
  const selChat = editItem ? editItem.chat_id : prefillChatId;
  const opts = chats.map(c => `<option value="${c.id}" ${selChat == c.id ? 'selected' : ''}>${esc(displayName(c))}</option>`).join('');
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const ed = editItem || {};
  const toLocalDt = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0, 16);
  };
  const toLocalDateOnly = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  };

  showModal(editItem ? 'Edit Scheduled Message' : 'Schedule Message', `
    <div class="form-group"><label>Chat *</label><select id="sc-chat" ${editItem ? 'disabled' : ''}>${opts}</select></div>
    <div class="form-group"><label>Message *</label><textarea id="sc-body" style="min-height:70px">${esc(ed.body || prefillBody || '')}</textarea></div>
    <div class="form-group"><label>Send At *</label><input type="datetime-local" id="sc-at" value="${toLocalDt(ed.send_at)}"></div>
    <div class="form-group"><label>Repeat</label><select id="sc-repeat">
      <option value="none">Once</option>
      <option value="daily" ${ed.repeat === 'daily' ? 'selected' : ''}>Daily</option>
      <option value="weekly" ${ed.repeat === 'weekly' ? 'selected' : ''}>Weekly</option>
      <option value="monthly" ${ed.repeat === 'monthly' ? 'selected' : ''}>Monthly</option>
    </select></div>
    <div id="sc-recur-opts" style="display:${ed.repeat && ed.repeat !== 'none' ? 'block' : 'none'}">
      <div class="form-group"><label>Repeat every</label>
        <div style="display:flex;align-items:center;gap:.5rem">
          <input type="number" id="sc-interval" min="1" max="30" value="${ed.interval || 1}" style="width:80px">
          <span id="sc-interval-unit" class="text-muted" style="font-size:12.5px">day(s)</span>
        </div>
      </div>
      <div class="form-group" id="sc-days-wrap" style="display:${ed.repeat === 'daily' ? 'block' : 'none'}">
        <label>On days (leave all unchecked = every day)</label>
        <div style="display:flex;gap:.55rem;flex-wrap:wrap">
          ${DAYS.map((d, i) => `<label style="display:flex;align-items:center;gap:.25rem;font-size:12.5px;font-weight:400">
            <input type="checkbox" class="sc-day" value="${i}" ${(ed.days_of_week || []).includes(i) ? 'checked' : ''}>${d}</label>`).join('')}
        </div>
      </div>
      <div class="form-group" id="sc-dom-wrap" style="display:${ed.repeat === 'monthly' ? 'block' : 'none'}">
        <label>Day of month (1–31)</label>
        <input type="number" id="sc-dom" min="1" max="31" value="${ed.day_of_month || ''}" placeholder="e.g. 1">
      </div>
      <div class="form-group"><label>End date (optional — leave empty for open-ended)</label>
        <input type="date" id="sc-end" value="${toLocalDateOnly(ed.end_date)}"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="sc-save">${editItem ? 'Save Changes' : 'Schedule'}</button>
    </div>`);

  const repeatSel = document.getElementById('sc-repeat');
  repeatSel.addEventListener('change', () => {
    const r = repeatSel.value;
    document.getElementById('sc-recur-opts').style.display = r === 'none' ? 'none' : 'block';
    document.getElementById('sc-days-wrap').style.display = r === 'daily' ? 'block' : 'none';
    document.getElementById('sc-dom-wrap').style.display = r === 'monthly' ? 'block' : 'none';
    document.getElementById('sc-interval-unit').textContent =
      r === 'weekly' ? 'week(s)' : r === 'monthly' ? 'month(s)' : 'day(s)';
  });

  document.getElementById('sc-save').addEventListener('click', async () => {
    const body = document.getElementById('sc-body').value.trim();
    const at = document.getElementById('sc-at').value;
    if (!body || !at) return toast('Message and time required', 'error');
    const repeat = repeatSel.value;
    const sendAtUtc = new Date(at).toISOString();
    const endVal = document.getElementById('sc-end')?.value;
    const endDateUtc = endVal ? new Date(endVal).toISOString() : (editItem ? '' : null);

    const payload = {
      body, send_at: sendAtUtc, repeat,
      interval: parseInt(document.getElementById('sc-interval')?.value) || 1,
      days_of_week: repeat === 'daily'
        ? [...document.querySelectorAll('.sc-day:checked')].map(c => +c.value)
        : null,
      day_of_month: repeat === 'monthly'
        ? (parseInt(document.getElementById('sc-dom')?.value) || null)
        : null,
      end_date: endDateUtc,
    };
    try {
      if (editItem) {
        await Api.scheduled.update(editItem.id, payload);
        toast('Schedule updated', 'success');
      } else {
        payload.chat_id = parseInt(document.getElementById('sc-chat').value);
        await Api.scheduled.create(payload);
        toast('Message scheduled', 'success');
      }
      closeModal();
      if (State.currentView === 'scheduled') loadScheduled();
    } catch(e) { toast(e.message, 'error'); }
  });
}

// ── LOGS VIEW ───────────────────────────────────────────────────── //
async function renderLogs() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header">
        <h2>Audit Logs</h2>
        <div class="header-actions" style="margin-left:auto;display:flex;align-items:center;gap:.6rem;flex-wrap:wrap">
          <div style="display:flex;align-items:center;gap:.4rem">
            <span style="font-size:12px;color:var(--text-3);font-weight:500">From:</span>
            <input type="date" id="log-start-date" class="search-input" style="padding:4px 8px;font-size:12.5px;max-width:130px;height:30px">
          </div>
          <div style="display:flex;align-items:center;gap:.4rem">
            <span style="font-size:12px;color:var(--text-3);font-weight:500">To:</span>
            <input type="date" id="log-end-date" class="search-input" style="padding:4px 8px;font-size:12.5px;max-width:130px;height:30px">
          </div>
          <select id="log-action-filter" style="max-width:160px;height:30px;padding:4px 8px;font-size:12.5px;border-radius:6px;border:1px solid var(--border)"><option value="">All events</option></select>
          <button class="btn btn-secondary btn-sm" id="log-export" style="height:30px;padding:4px 12px;font-size:12.5px">Export CSV</button>
        </div>
      </div>
      <div class="scroll-area">
        <div class="content-card">
          <div class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th style="width: 170px;">Time</th>
                  <th style="width: 150px;">Event</th>
                  <th style="width: 150px;">Agent</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody id="logs-tbody">
                <tr><td colspan="4" style="text-align:center;padding:3rem"><div class="spinner"></div></td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>`;

  const startInput = document.getElementById('log-start-date');
  const endInput = document.getElementById('log-end-date');
  const actionSel = document.getElementById('log-action-filter');

  function reloadWithFilters() {
    let startVal = startInput.value;
    let endVal = endInput.value;
    let start_date = startVal ? `${startVal}T00:00:00.000Z` : undefined;
    let end_date = endVal ? `${endVal}T23:59:59.999Z` : undefined;
    loadLogsTable(actionSel.value, start_date, end_date);
  }

  startInput.addEventListener('change', reloadWithFilters);
  endInput.addEventListener('change', reloadWithFilters);
  actionSel.addEventListener('change', reloadWithFilters);

  document.getElementById('log-export').addEventListener('click', async () => {
    try { await Api.exports.logs(30); toast('Export downloaded', 'success'); }
    catch(e) { toast(e.message, 'error'); }
  });

  try {
    const actions = await Api.logs.actions();
    actions.forEach(a => {
      const o = document.createElement('option');
      o.value = a;
      o.textContent = a.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
      actionSel.appendChild(o);
    });
  } catch(_) {}
  await loadLogsTable('');
}

function formatLogEvent(action) {
  const map = {
    ticket_created: { text: 'Ticket Created', class: 'pill-open' },
    ticket_updated: { text: 'Ticket Updated', class: 'pill-in_progress' },
    task_created: { text: 'Task Created', class: 'pill-open' },
    task_updated: { text: 'Task Updated', class: 'pill-in_progress' },
    bulk_job_created: { text: 'Bulk Job Created', class: 'pill-open' },
    bulk_job_completed: { text: 'Bulk Job Completed', class: 'pill-resolved' },
    automation_rule_created: { text: 'Rule Created', class: 'pill-open' },
    automation_rule_updated: { text: 'Rule Updated', class: 'pill-in_progress' },
    automation_rule_deleted: { text: 'Rule Deleted', class: 'pill-urgent' },
    automation_rule_executed: { text: 'Rule Executed', class: 'pill-resolved' },
    sla_breached: { text: 'SLA Breached', class: 'pill-urgent' },
    task_reminder_sent: { text: 'Reminder Sent', class: 'pill-resolved' },
    private_note_added: { text: 'Note Added', class: 'pill-open' },
    label_created: { text: 'Label Created', class: 'pill-open' },
    label_deleted: { text: 'Label Deleted', class: 'pill-urgent' },
  };
  const item = map[action];
  if (item) {
    return `<span class="pill ${item.class}">${esc(item.text)}</span>`;
  }
  const titleText = action.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  return `<span class="pill pill-open">${esc(titleText)}</span>`;
}

async function loadLogsTable(action, start_date, end_date) {
  const tbody = document.getElementById('logs-tbody');
  if (!tbody) return;
  try {
    const params = {};
    if (action) params.action = action;
    if (start_date) params.start_date = start_date;
    if (end_date) params.end_date = end_date;

    const logs = await Api.logs.list(Object.keys(params).length ? params : undefined);
    if (!logs.length) {
      tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:3rem;color:var(--text-3)">
        No activity logs match the filter criteria.</td></tr>`;
      return;
    }
    tbody.innerHTML = logs.map(l => {
      let detailsHtml = esc(l.description || '');
      if (l.metadata && Object.keys(l.metadata).length) {
        detailsHtml += `
          <div style="margin-top:0.35rem;font-size:11.5px;font-family:monospace;color:var(--text-3);background:var(--bg-light);padding:5px 8px;border-radius:4px;border:1px solid var(--border-light);max-width:650px;word-break:break-all">
            ${esc(JSON.stringify(l.metadata))}
          </div>`;
      }
      return `<tr>
        <td style="font-size:12.5px;color:var(--text-3);white-space:nowrap;vertical-align:top;padding-top:10px">${new Date(l.created_at).toLocaleString()}</td>
        <td style="vertical-align:top;padding-top:8px">${formatLogEvent(l.action)}</td>
        <td style="font-size:13px;color:var(--text-2);vertical-align:top;padding-top:10px">${esc(l.agent_name || 'System')}</td>
        <td style="font-size:13px;vertical-align:top;padding-top:10px">${detailsHtml}</td>
      </tr>`;
    }).join('');
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="4" class="text-muted" style="text-align:center;padding:2rem">${esc(e.message)}</td></tr>`;
  }
}

// Alias so dashboard card buttons can call switchView(...)
function switchView(view) { navigateTo(view); }

// ══ CHAT LIST (table view with bulk actions) ══════════════════════ //
async function renderChatListView() {
  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="flex-col h-full" style="overflow-y:auto">
      <div class="section-header">
        <h2>Chat List</h2>
        <div class="header-actions" style="margin-left:auto;display:flex;gap:.5rem">
          <input type="text" id="cl-search" class="search-input" placeholder="Search chats..." style="max-width:220px">
          <button class="btn btn-secondary btn-sm" id="cl-refresh">Refresh</button>
        </div>
      </div>
      <div class="scroll-area" id="cl-table-wrap"><div class="loading-center"><div class="spinner"></div></div></div>
    </div>
    <div class="bulk-toolbar" id="bulk-toolbar" style="display:none">
      <span class="bt-count" id="bt-count">0 selected</span>
      <button id="bt-update">✏️ Update Chats</button>
      <button id="bt-group">👥 Group Actions</button>
      <button id="bt-export">⬇ Export</button>
      <button id="bt-clear" title="Clear selection">×</button>
    </div>`;

  const selected = new Set();
  let rows = [];

  function refreshToolbar() {
    const tb = document.getElementById('bulk-toolbar');
    const count = document.getElementById('bt-count');
    if (!tb) return;
    tb.style.display = selected.size ? 'flex' : 'none';
    if (count) count.textContent = `${selected.size} chat${selected.size === 1 ? '' : 's'} selected`;
  }

  async function loadTable(search) {
    const wrap = document.getElementById('cl-table-wrap');
    try {
      const phones = await Api.phones.list().catch(() => []);
      State.phones = phones;
      const phoneConnected = phones.some(p => p.waha_status === 'WORKING');

      if (!phoneConnected) {
        if (wrap) {
          wrap.innerHTML = `<div class="empty-state whatsapp-disconnected-thread" style="padding:4rem 2rem">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:48px;height:48px;opacity:.25;color:var(--text-3)">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              <path d="M2 2l20 20"/>
            </svg>
            <p style="font-size:15px;font-weight:600;color:var(--text-2);opacity:.8;margin:0.5rem 0 0.25rem">WhatsApp Disconnected</p>
            <span style="font-size:13px;color:var(--text-3);max-width:320px;line-height:1.4">Connect your WhatsApp to view the chat list.</span>
            <button class="btn btn-primary btn-sm" style="margin-top:0.75rem" onclick="switchView('settings')">Connect WhatsApp</button>
          </div>`;
        }
        return;
      }

      rows = await Api.inbox.chats({ limit: 200, ...(search ? { search } : {}) });
      const agentNames = {};
      try { (await Api.auth.agents()).forEach(a => agentNames[a.id] = a.name); } catch(_) {}
      wrap.innerHTML = `<div class="content-card"><div class="table-wrap"><table class="data-table chatlist-table">
        <thead><tr>
          <th style="width:34px"><input type="checkbox" id="cl-all"></th>
          <th>Chat Name</th><th>Labels</th><th>Assigned To</th><th>Last Active</th><th>Type</th>
        </tr></thead>
        <tbody>${rows.map(c => `<tr data-cid="${c.id}">
          <td><input type="checkbox" class="cl-check" data-cid="${c.id}" ${selected.has(c.id) ? 'checked' : ''}></td>
          <td style="font-weight:600">${esc(displayName(c))}</td>
          <td>${(c.labels || []).map(id => {
            const l = State.labels.find(x => x.id === id);
            return l ? `<span class="chat-label-mini" style="background:${l.color}22;color:${l.color};border:1px solid ${l.color}44">${esc(l.name)}</span>` : '';
          }).join(' ') || '<span class="text-muted" style="font-size:11px">—</span>'}</td>
          <td style="font-size:12.5px">${agentNames[c.assigned_to] ? esc(agentNames[c.assigned_to]) : '<span class="text-muted">Unassigned</span>'}</td>
          <td style="font-size:12px;color:var(--text-3)">${c.last_message_at ? timeAgo(c.last_message_at) : '—'}</td>
          <td><span class="pill ${c.is_group ? 'pill-in_progress' : 'pill-open'}" style="font-size:11px">${c.is_group ? 'Group' : 'User'}</span></td>
        </tr>`).join('')}</tbody>
      </table></div></div>`;

      document.getElementById('cl-all').addEventListener('change', e => {
        rows.forEach(c => e.target.checked ? selected.add(c.id) : selected.delete(c.id));
        wrap.querySelectorAll('.cl-check').forEach(cb => cb.checked = e.target.checked);
        refreshToolbar();
      });
      wrap.querySelectorAll('.cl-check').forEach(cb => cb.addEventListener('change', () => {
        cb.checked ? selected.add(+cb.dataset.cid) : selected.delete(+cb.dataset.cid);
        refreshToolbar();
      }));
    } catch(e) { wrap.innerHTML = `<div class="loading-center text-muted">${esc(e.message)}</div>`; }
  }

  document.getElementById('cl-refresh').addEventListener('click', () => loadTable());
  let t; document.getElementById('cl-search').addEventListener('input', e => {
    clearTimeout(t); t = setTimeout(() => loadTable(e.target.value.trim()), 300);
  });
  document.getElementById('bt-clear').addEventListener('click', () => {
    selected.clear();
    document.querySelectorAll('.cl-check, #cl-all').forEach(cb => cb.checked = false);
    refreshToolbar();
  });

  // Update Chats: labels, read state, pin, archive, AI — applied to selection
  document.getElementById('bt-update').addEventListener('click', () => {
    const labelOpts = State.labels.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join('');
    showModal(`Update ${selected.size} Chats`, `
      <div class="form-group"><label>Add label</label><select id="bu-addlabel"><option value="">— none —</option>${labelOpts}</select></div>
      <div class="form-group"><label>Remove label</label><select id="bu-removelabel"><option value="">— none —</option>${labelOpts}</select></div>
      <div class="form-group"><label>Mark as</label><select id="bu-read">
        <option value="">— no change —</option><option value="read">Read</option><option value="unread">Unread</option>
      </select></div>
      ${[['bu-pin', 'Pin chats'], ['bu-archive', 'Archive chats'], ['bu-ai', 'Activate AI Agent'], ['bu-flag', 'Flag chats']].map(([id, label]) => `
        <div class="form-group"><label style="display:flex;align-items:center;gap:.4rem;font-weight:400">
          <input type="checkbox" id="${id}" style="width:15px;height:15px"> ${label}</label></div>`).join('')}
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" id="bu-apply">Apply to ${selected.size} chats</button>
      </div>`);
    document.getElementById('bu-apply').addEventListener('click', async () => {
      const updates = {};
      if (document.getElementById('bu-pin').checked) updates.is_pinned = true;
      if (document.getElementById('bu-archive').checked) updates.is_archived = true;
      if (document.getElementById('bu-ai').checked) updates.ai_active = true;
      if (document.getElementById('bu-flag').checked) updates.is_flagged = true;
      const read = document.getElementById('bu-read').value;
      try {
        await Api.inbox.bulkUpdate({
          chat_ids: [...selected],
          updates: Object.keys(updates).length ? updates : null,
          mark_read: read === 'read' ? true : read === 'unread' ? false : null,
          add_label_id: parseInt(document.getElementById('bu-addlabel').value) || null,
          remove_label_id: parseInt(document.getElementById('bu-removelabel').value) || null,
        });
        closeModal(); toast(`Updated ${selected.size} chats`, 'success');
        selected.clear(); refreshToolbar(); loadTable();
      } catch(e) { toast(e.message, 'error'); }
    });
  });

  // Group Actions: add participants to all selected groups
  document.getElementById('bt-group').addEventListener('click', () => {
    const groups = rows.filter(c => selected.has(c.id) && c.is_group);
    if (!groups.length) return toast('Select at least one group chat', 'error');
    showModal(`Group Actions — ${groups.length} group(s)`, `
      <p class="text-muted" style="font-size:12.5px;margin-bottom:.6rem">
        Add contacts to all selected groups at once:<br>
        ${groups.slice(0, 5).map(g => esc(displayName(g))).join(', ')}${groups.length > 5 ? '…' : ''}
      </p>
      <div class="form-group"><label>Phone numbers (comma separated, with country code) *</label>
        <input type="text" id="ga-numbers" placeholder="919876543210, 918765432109"></div>
      <div class="modal-footer">
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" id="ga-apply">Add to groups</button>
      </div>`);
    document.getElementById('ga-apply').addEventListener('click', async () => {
      const numbers = document.getElementById('ga-numbers').value.split(',').map(s => s.trim()).filter(Boolean);
      if (!numbers.length) return toast('Enter at least one number', 'error');
      try {
        const res = await Api.groups.addParticipants({
          chat_ids: groups.map(g => g.id), phone_numbers: numbers,
        });
        const ok = res.results.filter(r => r.ok).length;
        closeModal(); toast(`Added to ${ok}/${res.results.length} groups`, ok ? 'success' : 'error');
      } catch(e) { toast(e.message, 'error'); }
    });
  });

  // Export: CSV of the selected rows
  document.getElementById('bt-export').addEventListener('click', () => {
    const picked = rows.filter(c => selected.has(c.id));
    const header = ['id', 'name', 'type', 'labels', 'unread', 'flagged', 'last_active'];
    const csv = [header.join(',')].concat(picked.map(c => [
      c.id,
      '"' + displayName(c).replace(/"/g, '""') + '"',
      c.is_group ? 'group' : 'user',
      '"' + (c.labels || []).map(id => State.labels.find(l => l.id === id)?.name || id).join('; ') + '"',
      c.unread_count || 0,
      c.is_flagged ? 'yes' : 'no',
      c.last_message_at || '',
    ].join(','))).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = 'chat_list.csv';
    document.body.appendChild(a); a.click(); a.remove();
    toast(`Exported ${picked.length} chats`, 'success');
  });

  await loadTable();
}

// ══ TASKS PANEL (topbar) ══════════════════════════════════════════ //
(() => {
  const panel = document.getElementById('tasks-panel');
  const openBtn = document.getElementById('topbar-tasks');
  if (!panel || !openBtn) return;

  const body = document.getElementById('tasks-panel-body');
  const viewSel = document.getElementById('tasks-view');

  async function loadTasks() {
    body.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
    try {
      const tasks = await Api.tasks.list({ view: viewSel.value });
      if (!tasks.length) {
        body.innerHTML = `<div class="empty-state" style="padding:3rem 1rem;text-align:center">
          <p class="text-muted" style="font-size:13px">No tasks here yet</p>
          <button class="btn btn-primary btn-sm" style="margin-top:.6rem" onclick="document.getElementById('tasks-create-btn').click()">Create Task +</button>
        </div>`;
        return;
      }
      body.innerHTML = tasks.map(t => `
        <div class="task-row ${t.status === 'done' ? 'done' : ''}" data-tid="${t.id}">
          <input type="checkbox" class="task-check" ${t.status === 'done' ? 'checked' : ''}>
          <div style="flex:1;min-width:0">
            <div class="task-title">${esc(t.title)}</div>
            <div class="task-sub">
              ${t.assignee_name ? esc(t.assignee_name) : 'Unassigned'}
              ${t.due_date ? ' · due ' + new Date(t.due_date).toLocaleDateString() : ''}
              ${t.notes ? ' · ' + esc(t.notes.slice(0, 40)) : ''}
            </div>
          </div>
          <span class="task-prio ${esc(t.priority)}">${esc(t.priority)}</span>
          <button class="modal-close task-del" title="Delete" style="font-size:14px">×</button>
        </div>`).join('');
      body.querySelectorAll('.task-check').forEach(cb => cb.addEventListener('change', async e => {
        const id = e.target.closest('.task-row').dataset.tid;
        try { await Api.tasks.update(id, { status: e.target.checked ? 'done' : 'open' }); loadTasks(); }
        catch(err) { toast(err.message, 'error'); }
      }));
      body.querySelectorAll('.task-del').forEach(btn => btn.addEventListener('click', async e => {
        const id = e.target.closest('.task-row').dataset.tid;
        if (!confirm('Delete task?')) return;
        try { await Api.tasks.del(id); loadTasks(); } catch(err) { toast(err.message, 'error'); }
      }));
    } catch(e) { body.innerHTML = `<div class="loading-center text-muted">${esc(e.message)}</div>`; }
  }

  openBtn.addEventListener('click', () => {
    panel.style.display = panel.style.display === 'none' ? 'flex' : 'none';
    if (panel.style.display !== 'none') loadTasks();
  });
  document.getElementById('tasks-close').addEventListener('click', () => panel.style.display = 'none');
  viewSel.addEventListener('change', loadTasks);

  document.getElementById('tasks-create-btn').addEventListener('click', () => {
    // Full task modal (due date, reminder, assignee, priority, notes)
    showTaskModal({});
    // refresh the panel after the modal closes
    const overlay = document.getElementById('modal-overlay');
    const watcher = setInterval(() => {
      if (overlay.style.display === 'none') { clearInterval(watcher); loadTasks(); }
    }, 400);
  });
})();

// ══ NOTIFICATION SETTINGS (topbar bell) ═══════════════════════════ //
const NotifPrefs = {
  get() {
    try { return JSON.parse(localStorage.getItem('notif_prefs')) || {}; } catch(_) { return {}; }
  },
  save(p) { localStorage.setItem('notif_prefs', JSON.stringify(p)); },
};

(() => {
  const bell = document.getElementById('topbar-bell');
  const pop = document.getElementById('notif-popover');
  if (!bell || !pop) return;

  const SETTINGS = [
    ['inapp', 'In-App Notifications'],
    ['desktop', 'Desktop Notifications'],
    ['sound', 'Sound'],
  ];
  const TYPES = [
    ['new_messages', 'New Messages'],
    ['new_note', 'New Private Note'],
    ['ticket_assign', 'Ticket Assignment'],
    ['task_assign', 'Task Assignment'],
  ];

  function render() {
    const p = NotifPrefs.get();
    pop.innerHTML = `
      <div class="np-title">Notification Settings</div>
      ${SETTINGS.map(([k, label]) => `
        <div class="notif-row">${label}
          <label class="np-switch"><input type="checkbox" data-k="${k}" ${p[k] ? 'checked' : ''}><span class="np-slider"></span></label>
        </div>`).join('')}
      <div class="np-title">Notification Types</div>
      ${TYPES.map(([k, label]) => `
        <div class="notif-row">${label}
          <input type="checkbox" class="np-check" data-k="${k}" ${p[k] !== false ? 'checked' : ''}>
        </div>`).join('')}`;
    pop.querySelectorAll('input[data-k]').forEach(inp => inp.addEventListener('change', () => {
      const prefs = NotifPrefs.get();
      prefs[inp.dataset.k] = inp.checked;
      NotifPrefs.save(prefs);
      if (inp.dataset.k === 'desktop' && inp.checked && 'Notification' in window) {
        Notification.requestPermission();
      }
    }));
  }

  bell.addEventListener('click', e => {
    e.stopPropagation();
    const open = pop.style.display !== 'none';
    pop.style.display = open ? 'none' : 'block';
    if (!open) render();
  });
  document.addEventListener('click', e => {
    if (!pop.contains(e.target) && e.target !== bell) pop.style.display = 'none';
  });
})();

// Called from the WS handler on incoming events
function notifyUser(type, title, bodyText) {
  const p = NotifPrefs.get();
  if (p[type] === false) return;
  if (p.inapp) toast(`${title}: ${bodyText}`.slice(0, 120), 'default');
  if (p.desktop && 'Notification' in window && Notification.permission === 'granted') {
    try { new Notification(title, { body: bodyText.slice(0, 140) }); } catch(_) {}
  }
  if (p.sound) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator(); const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.value = 880; gain.gain.value = 0.04;
      osc.start(); osc.stop(ctx.currentTime + 0.12);
    } catch(_) {}
  }
}

// ══ ASK AI (Org & Chat Assistant) ═════════════════════════════════ //
(() => {
  const fab = document.getElementById('topbar-ask-ai');
  const panel = document.getElementById('ai-panel');
  if (!fab || !panel) return;
  const body = document.getElementById('ai-panel-body');
  const input = document.getElementById('ai-panel-q');
  const scopeChip = document.getElementById('ai-scope');

  // Show the button once logged in
  const bootWatch = setInterval(() => {
    if (State.agent) { fab.style.display = 'flex'; clearInterval(bootWatch); }
  }, 800);

  const ORG_RECIPES = [
    ['summarize_24h', '📋 Summarize last 24 hours'],
    ['find_followups', '💬 Find chats needing follow-up'],
    ['triage_unassigned', '👥 Triage unassigned'],
    ['stale_tickets', '🎫 Find stale tickets'],
  ];
  const CHAT_RECIPES = [
    ['summarize_chat', '📋 Summarize this chat'],
    ['sentiment', '🙂 Sentiment scan'],
    ['draft_reply', '✍️ Draft a reply'],
  ];

  function currentChatId() {
    return State.currentView === 'inbox' ? State.inbox.selectedChatId : null;
  }

  function renderIntro() {
    const chatId = currentChatId();
    const chat = chatId ? State.inbox.chats?.find(c => c.id == chatId) : null;
    scopeChip.textContent = chat ? `Chat: ${displayName(chat).slice(0, 22)}` : 'Org Assistant';
    const recipes = chat ? CHAT_RECIPES.concat(ORG_RECIPES.slice(0, 2)) : ORG_RECIPES;
    body.innerHTML = `
      <div class="ai-greeting">Hi ${esc((State.agent?.name || '').split(' ')[0] || 'there')} 👋</div>
      <div class="ai-sub">I can answer questions about your workspace${chat ? ' and this conversation' : ''} — powered by Gemini. I analyze and draft; I never send anything myself.</div>
      <div class="ai-chips">${recipes.map(([k, label]) =>
        `<button class="ai-chip" data-recipe="${k}">${label}</button>`).join('')}</div>
      <div id="ai-thread"></div>`;
    body.querySelectorAll('.ai-chip').forEach(chip =>
      chip.addEventListener('click', () => ask('', chip.dataset.recipe, chip.textContent)));
  }

  async function ask(prompt, recipe, label) {
    const thread = document.getElementById('ai-thread');
    if (!thread) return;
    const chatId = currentChatId();
    thread.insertAdjacentHTML('beforeend',
      `<div class="ai-msg q">${esc(label || prompt)}</div>
       <div class="ai-msg a ai-pending">Thinking…</div>`);
    body.scrollTop = body.scrollHeight;
    try {
      const isChatRecipe = recipe && CHAT_RECIPES.some(([k]) => k === recipe);
      const res = await Api.ai.assistant({
        prompt: prompt || '',
        recipe: recipe || null,
        chat_id: isChatRecipe ? chatId : (!recipe && chatId ? chatId : null),
      });
      const pending = thread.querySelector('.ai-pending');
      if (pending) { pending.classList.remove('ai-pending'); pending.textContent = res.answer; }
    } catch(e) {
      const pending = thread.querySelector('.ai-pending');
      if (pending) { pending.classList.remove('ai-pending'); pending.textContent = '⚠️ ' + e.message; }
    }
    body.scrollTop = body.scrollHeight;
  }

  fab.addEventListener('click', () => {
    const open = panel.style.display !== 'none';
    panel.style.display = open ? 'none' : 'flex';
    if (!open) { renderIntro(); setTimeout(() => input.focus(), 50); }
  });
  document.getElementById('ai-panel-close').addEventListener('click', () => panel.style.display = 'none');

  const send = () => {
    const q = input.value.trim();
    if (!q) return;
    input.value = '';
    ask(q, null, null);
  };
  document.getElementById('ai-panel-send').addEventListener('click', send);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });

  // Ctrl+K opens the assistant
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      fab.click();
    }
  });
})();

// ── Boot ────────────────────────────────────────────────────────── //
window.onerror = (msg, src, line, col, err) => {
  console.error('[uncaught]', msg, 'at', src, line + ':' + col, err);
};
window.addEventListener('unhandledrejection', e => {
  console.error('[unhandled promise]', e.reason);
});
checkAuth();
