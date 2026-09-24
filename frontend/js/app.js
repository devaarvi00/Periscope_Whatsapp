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
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// Only allow hex colors into style attributes (label colors are user-controlled)
const SAFE_COLOR_RE = /^#[0-9a-fA-F]{3,8}$/;
function safeColor(c, fallback = '#9ca3af') {
  return esc(typeof c === 'string' && SAFE_COLOR_RE.test(c) ? c : fallback);
}

// Only allow inline image data URLs (WAHA QR codes) into img src
function safeImgSrc(src) {
  return typeof src === 'string' && /^data:image\/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=\s]+$/.test(src) ? src : '';
}

// Server datetimes are naive UTC (no Z / offset) — parse them as UTC
function parseServerDate(ts) {
  if (ts == null || ts === '') return null;
  if (ts instanceof Date) return ts;
  if (typeof ts === 'number') return new Date(ts * 1000);
  let s = String(ts);
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) s = s.replace(' ', 'T') + 'Z';
  return new Date(s);
}

// Local datetime-local input value → ISO UTC string (or null when empty)
function localInputToIso(v) {
  if (!v) return null;
  const d = new Date(v);
  return isNaN(d.getTime()) ? null : d.toISOString();
}

// CSV cell: neutralize spreadsheet formulas and quote properly
function csvCell(v) {
  let s = v == null ? '' : String(v);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return '"' + s.replace(/"/g, '""') + '"';
}
function csvRow(cells) { return cells.map(csvCell).join(','); }

function timeAgo(ts) {
  if (!ts) return '';
  const d = parseServerDate(ts);
  const diff = Date.now() - d.getTime();
  if (diff < 60000) return 'now';
  if (diff < 3600000) return Math.floor(diff/60000) + 'm';
  if (diff < 86400000) return Math.floor(diff/3600000) + 'h';
  return d.toLocaleDateString('en', {month:'short', day:'numeric'});
}

function fmt(ts) {
  if (!ts) return '';
  const d = parseServerDate(ts);
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

// Admin-only controls (phone management, exports, automation rules) are hidden for other roles
function isAdmin() {
  return String(State.agent?.role || '').toLowerCase() === 'admin';
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
          <span class="lp-dot" style="background:${safeColor(l.color)}"></span>
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
  const hashRoute = decodeURIComponent(location.hash.replace('#', ''));
  navigateTo(_parseRoute(hashRoute) ? hashRoute : 'dashboard');
  loadOrg();
  refreshUnreadBadge();
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

  // Workspace menu user row
  const mua = document.getElementById('ws-menu-user-avatar');
  const mue = document.getElementById('ws-menu-user-email');
  if (mua) { mua.textContent = initials(a.name); mua.style.background = avatarColor(a.name); }
  if (mue) mue.textContent = a.email || a.name;
  const disp = document.getElementById('agent-display');
  if (disp) disp.title = `${a.name} · ${a.role}`;
}

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
              const cid = +row.dataset.id;
              navigateTo('inbox');
              // renderInbox awaits loadChats(); wait for it before opening the chat
              Promise.resolve(_inboxReady).then(() => {
                if (State.currentView !== 'inbox') return;
                if (State.inbox.chats?.some(x => x.id === cid)) openChat(cid);
                else toast('Chat not found in the current list', 'error');
              });
            } else if (go) navigateTo(go);
          });
        });
      } catch(e) { closeResults(); toast(e.message || 'Search failed', 'error'); }
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

function logoutFn() {
  disconnectWS();
  _stopDashWahaPoller();
  _stopDashQrPoll();
  _stopAllPhoneQrFlows();
  clearTimeout(_chatDebounce);
  closeLabelPicker();
  closeWsMenu();
  closeModal();
  Api.clearToken();
  // Reset in-memory state so the next login starts clean
  Object.assign(State, {
    agent: null,
    currentView: 'inbox',
    inbox: { chats: [], selectedChatId: null, messages: [], filter: 'all', search: '' },
    tickets: { list: [], filter: 'all' },
    contacts: { list: [], search: '' },
    labels: [],
    phones: [],
    ws: null,
  });
  _chatAutoSynced = false;
  ['tasks-panel', 'ai-panel', 'notif-popover', 'topbar-dropdown'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  const main = document.getElementById('main-content');
  if (main) main.innerHTML = '';
  history.replaceState(null, '', location.pathname + location.search);
  showLogin();
}
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

// ── Sidebar: workspace menu, theme, collapse ──────────────────────
function _store(key, val) {
  try { val == null ? localStorage.removeItem(key) : localStorage.setItem(key, val); } catch (_) {}
}

function wsInitial(name) {
  return (String(name || '').trim()[0] || 'h').toLowerCase();
}

function renderOrg() {
  const org = State.org || { name: 'Hyperscope', uid: '' };
  const letter = wsInitial(org.name);
  for (const id of ['ws-avatar', 'ws-menu-avatar', 'ws-menu-current-avatar']) {
    const el = document.getElementById(id);
    if (el) el.textContent = letter;
  }
  for (const id of ['ws-name', 'ws-menu-name', 'ws-menu-current-name']) {
    const el = document.getElementById(id);
    if (el) el.textContent = org.name;
  }
  const uid = document.getElementById('ws-menu-uid');
  if (uid) { uid.textContent = org.uid || ''; uid.style.display = org.uid ? '' : 'none'; }
}

async function loadOrg() {
  try { State.org = await Api.org.get(); }
  catch (_) { State.org = State.org || null; }  // keep the default label if the call fails
  renderOrg();
}

const wsSwitch = document.getElementById('ws-switch');
const wsMenu = document.getElementById('ws-menu');

function closeWsMenu(focusButton) {
  if (!wsMenu || wsMenu.hidden) return;
  wsMenu.hidden = true;
  wsSwitch?.setAttribute('aria-expanded', 'false');
  if (focusButton) wsSwitch?.focus();
}

function openWsMenu() {
  if (!wsMenu || !wsSwitch) return;
  // Fixed positioning so the menu isn't clipped by the sidebar (esp. when collapsed)
  const r = wsSwitch.getBoundingClientRect();
  const collapsed = document.documentElement.getAttribute('data-sidebar') === 'collapsed'
    || window.matchMedia('(max-width:760px)').matches;
  wsMenu.style.top = (collapsed ? r.top : r.bottom + 4) + 'px';
  wsMenu.style.left = (collapsed ? r.right + 8 : Math.max(8, r.left)) + 'px';
  wsMenu.hidden = false;
  wsSwitch.setAttribute('aria-expanded', 'true');
  // Keep the menu on screen on short viewports
  const mr = wsMenu.getBoundingClientRect();
  if (mr.bottom > window.innerHeight - 8) {
    wsMenu.style.top = Math.max(8, window.innerHeight - 8 - mr.height) + 'px';
  }
  wsMenu.querySelector('.ws-menu-item')?.focus();
}

wsSwitch?.addEventListener('click', e => {
  e.stopPropagation();
  wsMenu.hidden ? openWsMenu() : closeWsMenu();
});
document.addEventListener('click', e => {
  if (wsMenu && !wsMenu.hidden && !wsMenu.contains(e.target)) closeWsMenu();
});
window.addEventListener('resize', () => closeWsMenu());
wsMenu?.addEventListener('keydown', e => {
  const items = [...wsMenu.querySelectorAll('.ws-menu-item')];
  const i = items.indexOf(document.activeElement);
  if (e.key === 'Escape') { e.preventDefault(); closeWsMenu(true); }
  else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
  else if (e.key === 'Tab') closeWsMenu();
});

document.getElementById('ws-menu-uid')?.addEventListener('click', async e => {
  e.stopPropagation();
  const uid = State.org?.uid;
  if (!uid) return;
  try { await navigator.clipboard.writeText(uid); toast('Workspace ID copied', 'success'); }
  catch (_) { toast(uid); }
});

wsMenu?.addEventListener('click', e => {
  const btn = e.target.closest('[data-ws-action]');
  if (!btn) return;
  const action = btn.dataset.wsAction;
  closeWsMenu();
  ({
    'org-settings': showOrgSettingsModal,
    invite:         openInviteTeam,
    help:           showHelpModal,
    current:        () => {},
    create:         showCreateWorkspaceModal,
    password:       showChangePasswordModal,
    logout:         logoutFn,
  }[action] || (() => {}))();
});

function showOrgSettingsModal() {
  const org = State.org || {};
  const admin = isAdmin();
  const ro = admin ? '' : 'disabled';
  showModal('Organization settings', `
    <div class="form-group">
      <label for="org-name">Workspace name</label>
      <input type="text" id="org-name" maxlength="120" value="${esc(org.name || '')}" ${ro}>
    </div>
    <div class="form-group">
      <label for="org-support-email">Support email</label>
      <input type="email" id="org-support-email" maxlength="255" placeholder="support@yourcompany.com" value="${esc(org.support_email || '')}" ${ro}>
      <small class="text-muted">Shown to your team under Help &amp; Support</small>
    </div>
    <div class="form-group">
      <label for="org-support-url">Help centre URL</label>
      <input type="url" id="org-support-url" maxlength="500" placeholder="https://" value="${esc(org.support_url || '')}" ${ro}>
    </div>
    <div class="form-group">
      <label>Workspace ID</label>
      <div class="text-muted" style="font-family:ui-monospace,monospace;font-size:12.5px;word-break:break-all">${esc(org.uid || '—')}</div>
    </div>
    ${admin ? '' : '<p class="text-muted" style="font-size:12.5px;margin-bottom:.75rem">Only admins can change organization settings.</p>'}
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">${admin ? 'Cancel' : 'Close'}</button>
      ${admin ? '<button class="btn btn-primary" id="org-save">Save</button>' : ''}
    </div>
  `);
  document.getElementById('org-save')?.addEventListener('click', async ev => {
    const name = document.getElementById('org-name').value.trim();
    if (!name) return toast('Workspace name is required', 'error');
    const url = document.getElementById('org-support-url').value.trim();
    if (url && !/^https?:\/\//i.test(url)) return toast('Help centre URL must start with http:// or https://', 'error');
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      State.org = await Api.org.update({
        name,
        support_email: document.getElementById('org-support-email').value.trim(),
        support_url: url,
      });
      renderOrg();
      closeModal();
      toast('Organization settings saved', 'success');
    } catch (err) {
      toast(err.message, 'error');
      btn.disabled = false;
    }
  });
}

function openInviteTeam() {
  if (!isAdmin()) return toast('Only admins can invite team members', 'error');
  navigateTo('settings');
  document.querySelector('#settings-tabs .tab[data-tab="agents"]')?.click();
  // The agents tab renders asynchronously; open the invite form once its button exists
  const started = Date.now();
  (function waitForInvite() {
    const b = document.getElementById('invite-agent-btn');
    if (b) return b.click();
    if (State.currentView === 'settings' && Date.now() - started < 5000) setTimeout(waitForInvite, 100);
  })();
}

function showHelpModal() {
  const org = State.org || {};
  const safeUrl = /^https?:\/\//i.test(org.support_url || '') ? org.support_url : '';
  const contact = [
    org.support_email ? `<a class="btn btn-secondary btn-sm" href="mailto:${esc(org.support_email)}">✉️ ${esc(org.support_email)}</a>` : '',
    safeUrl ? `<a class="btn btn-secondary btn-sm" href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer">Open help centre ↗</a>` : '',
  ].filter(Boolean).join(' ');
  showModal('Help & Support', `
    <div class="form-group">
      <label>Contact support</label>
      ${contact
        ? `<div style="display:flex;gap:.5rem;flex-wrap:wrap">${contact}</div>`
        : `<p class="text-muted" style="font-size:13px">No support contact is set yet.${isAdmin() ? ' Add one in Organization settings.' : ' Ask your admin to add one.'}</p>`}
    </div>
    <div class="form-group">
      <label>Keyboard shortcuts</label>
      <div style="font-size:13px;line-height:1.9">
        <div><kbd>Ctrl</kbd> + <kbd>K</kbd> &nbsp;Ask AI</div>
        <div><kbd>Esc</kbd> &nbsp;Close menus and dialogs</div>
      </div>
    </div>
    <div class="form-group" style="margin-bottom:.5rem">
      <label>Workspace ID</label>
      <div class="text-muted" style="font-family:ui-monospace,monospace;font-size:12.5px;word-break:break-all">${esc(org.uid || '—')}</div>
      <small class="text-muted">Include this when you contact support.</small>
    </div>
    <div class="modal-footer"><button class="btn btn-primary" onclick="closeModal()">Done</button></div>
  `);
}

function showCreateWorkspaceModal() {
  showModal('Create workspace', `
    <p style="font-size:13.5px;line-height:1.6;margin-bottom:.75rem">
      This installation runs a single workspace. Each Hyperscope server hosts one workspace with its own
      WhatsApp numbers, team and data.
    </p>
    <p class="text-muted" style="font-size:13px;line-height:1.6;margin-bottom:1rem">
      To run another workspace, deploy a separate Hyperscope instance${State.org?.support_email ? ` or contact <a href="mailto:${esc(State.org.support_email)}">${esc(State.org.support_email)}</a>` : ''}.
    </p>
    <div class="modal-footer"><button class="btn btn-primary" onclick="closeModal()">Got it</button></div>
  `);
}

function showChangePasswordModal() {
  showModal('Update password', `
    <div class="form-group">
      <label for="pw-current">Current password</label>
      <input type="password" id="pw-current" autocomplete="current-password" maxlength="256">
    </div>
    <div class="form-group">
      <label for="pw-new">New password</label>
      <input type="password" id="pw-new" autocomplete="new-password" minlength="8" maxlength="72">
      <small class="text-muted">8–72 characters</small>
    </div>
    <div class="form-group">
      <label for="pw-confirm">Confirm new password</label>
      <input type="password" id="pw-confirm" autocomplete="new-password" maxlength="72">
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="pw-save">Update password</button>
    </div>
  `);
  document.getElementById('pw-current')?.focus();
  document.getElementById('pw-save')?.addEventListener('click', async ev => {
    const cur = document.getElementById('pw-current').value;
    const nw = document.getElementById('pw-new').value;
    const cf = document.getElementById('pw-confirm').value;
    if (!cur) return toast('Enter your current password', 'error');
    if (nw.length < 8 || nw.length > 72) return toast('New password must be 8–72 characters', 'error');
    if (new TextEncoder().encode(nw).length > 72) return toast('New password is too long', 'error');
    if (nw !== cf) return toast('New passwords do not match', 'error');
    if (nw === cur) return toast('New password must be different', 'error');
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      await Api.auth.changePassword(cur, nw);
      closeModal();
      toast('Password updated', 'success');
    } catch (err) {
      toast(err.message, 'error');
      btn.disabled = false;
    }
  });
}

// Theme (light/dark) — stored per browser
function applyTheme(theme) {
  const dark = theme === 'dark';
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  const label = document.getElementById('theme-label');
  const icon = document.getElementById('theme-icon');
  const btn = document.getElementById('theme-toggle');
  if (label) label.textContent = dark ? 'Dark' : 'Light';
  if (btn) btn.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
  if (icon) {
    // Font Awesome Free sun / moon (CC BY 4.0)
    icon.setAttribute('viewBox', dark ? '0 0 512 512' : '0 0 384 512');
    icon.innerHTML = dark
      ? '<path d="M361.5 1.2c5 2.1 8.6 6.6 9.6 11.9L391 121l107.9 19.8c5.3 1 9.8 4.6 11.9 9.6s1.5 10.7-1.6 15.2L446.9 256l62.3 90.3c3.1 4.5 3.7 10.2 1.6 15.2s-6.6 8.6-11.9 9.6L391 391 371.1 498.9c-1 5.3-4.6 9.8-9.6 11.9s-10.7 1.5-15.2-1.6L256 446.9l-90.3 62.3c-4.5 3.1-10.2 3.7-15.2 1.6s-8.6-6.6-9.6-11.9L121 391 13.1 371.1c-5.3-1-9.8-4.6-11.9-9.6s-1.5-10.7 1.6-15.2L65.1 256 2.8 165.7c-3.1-4.5-3.7-10.2-1.6-15.2s6.6-8.6 11.9-9.6L121 121 140.9 13.1c1-5.3 4.6-9.8 9.6-11.9s10.7-1.5 15.2 1.6L256 65.1 346.3 2.8c4.5-3.1 10.2-3.7 15.2-1.6zM160 256a96 96 0 1 1 192 0 96 96 0 1 1 -192 0zm224 0a128 128 0 1 0 -256 0 128 128 0 1 0 256 0z"/>'
      : '<path d="M223.5 32C100 32 0 132.3 0 256S100 480 223.5 480c60.6 0 115.5-24.2 155.8-63.4c5-4.9 6.3-12.5 3.1-18.7s-10.1-9.7-17-8.5c-9.8 1.7-19.8 2.6-30.1 2.6c-96.9 0-175.5-78.8-175.5-176c0-65.8 36-123.1 89.3-153.3c6.1-3.5 9.2-10.5 7.7-17.3s-7.3-11.9-14.3-12.5c-6.3-.5-12.6-.8-19-.8z"/>';
  }
}
applyTheme(document.documentElement.getAttribute('data-theme'));
document.getElementById('theme-toggle')?.addEventListener('click', () => {
  const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  _store('theme', next);
});

// Collapse to an icon rail — stored per browser
function applySidebarCollapsed(collapsed) {
  const root = document.documentElement;
  collapsed ? root.setAttribute('data-sidebar', 'collapsed') : root.removeAttribute('data-sidebar');
  const btn = document.getElementById('sidebar-collapse');
  if (btn) {
    btn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    btn.setAttribute('aria-expanded', String(!collapsed));
    const lbl = btn.querySelector('.nav-label');
    if (lbl) lbl.textContent = collapsed ? 'Expand' : 'Collapse';
  }
}
applySidebarCollapsed(document.documentElement.getAttribute('data-sidebar') === 'collapsed');
document.getElementById('sidebar-collapse')?.addEventListener('click', () => {
  const collapsed = document.documentElement.getAttribute('data-sidebar') !== 'collapsed';
  closeWsMenu();
  applySidebarCollapsed(collapsed);
  _store('sidebar', collapsed ? 'collapsed' : null);
});

// ── Navigation ─────────────────────────────────────────────────── //
const VIEW_LABELS = {
  dashboard: 'Dashboard', inbox: 'Chats', tickets: 'Tickets',
  contacts: 'Contacts', 'chat-list': 'Chat List',
  analytics: 'Analytics', 'ai-agent': 'AI',
  automation: 'Automation Rules',
  bulk: 'Bulk Messages', settings: 'Settings',
  communities: 'Groups', logs: 'Logs', scheduled: 'Scheduled Messages',
};

// Routes are "view" or "view/sub" (only analytics has sub-pages, e.g. #analytics/team)
function _parseRoute(route) {
  const [view, sub] = String(route || '').split('/');
  if (!VIEW_LABELS[view]) return null;
  return { view, sub: view === 'analytics' && AN_PAGES[sub] ? sub : null };
}

function navigateTo(route) {
  const r = _parseRoute(route) || { view: 'dashboard', sub: null };
  const view = r.view;
  const full = r.sub ? `${view}/${r.sub}` : view;
  if (location.hash !== '#' + full) history.pushState(null, '', '#' + full);
  _stopDashWahaPoller();
  _stopDashQrPoll();
  _stopAllPhoneQrFlows();
  // Switching analytics sub-pages keeps the analytics shell (sub-nav) in place
  const keepShell = view === 'analytics' && State.currentView === 'analytics' && document.getElementById('an-shell');
  if (view !== 'analytics' && State.currentView === 'analytics') _anDestroyCharts();
  State.currentView = view;
  State.currentRoute = full;
  document.querySelectorAll('.nav-item[data-view]').forEach(el => {
    el.classList.toggle('active', el.dataset.view === view);
  });
  const bc = document.getElementById('app-breadcrumb');
  if (bc) bc.innerHTML = `<strong>${esc(VIEW_LABELS[view] || view)}</strong>`;
  const main = document.getElementById('main-content');
  if (!keepShell) main.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
  if (view === 'analytics') return renderAnalytics(r.sub);
  ({
    dashboard:        renderDashboard,
    inbox:            renderInbox,
    tickets:          renderTickets,
    contacts:         renderContacts,
    'chat-list':      renderChatListView,
    analytics:        renderAnalytics,
    'ai-agent':       renderAIAgent,
    automation:       renderAutomation,

    bulk:             renderBulk,
    settings:         renderSettings,
    communities:      renderCommunities,
    logs:             renderLogs,
    scheduled:        renderScheduled,
  }[view] || (() => { main.innerHTML = `<div class="loading-center">View not found</div>`; }))();
}

document.querySelectorAll('.nav-item[data-view]').forEach(el => {
  el.addEventListener('click', e => { e.preventDefault(); navigateTo(el.dataset.view); });
});

// Back/forward between views (hash is set by navigateTo)
window.addEventListener('popstate', () => {
  if (!State.agent) return;
  const v = decodeURIComponent(location.hash.replace('#', ''));
  if (_parseRoute(v) && v !== (State.currentRoute || State.currentView)) navigateTo(v);
});

// ── WebSocket ──────────────────────────────────────────────────── //
const WS = {
  socket: null,
  retryDelay: 1000,    // ms — doubles on each failure, capped at 30s
  maxDelay: 30000,
  pongTimeout: null,
  alive: false,
  retryTimer: null,
};

// Close the socket (connecting or open) and cancel any pending reconnect
function disconnectWS() {
  clearTimeout(WS.retryTimer);
  WS.retryTimer = null;
  if (WS.socket) {
    WS.socket.onopen = WS.socket.onmessage = WS.socket.onerror = WS.socket.onclose = null;
    try { WS.socket.close(); } catch(_) {}
    WS.socket = null;
  }
  State.ws = null;
  WS.alive = false;
  WS.retryDelay = 1000;
}

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

  // Close any existing socket cleanly (keeps the current backoff delay)
  clearTimeout(WS.retryTimer);
  WS.retryTimer = null;
  if (WS.socket) {
    WS.socket.onopen = WS.socket.onmessage = WS.socket.onerror = WS.socket.onclose = null;
    try { WS.socket.close(); } catch(_) {}
    WS.socket = null;
  }

  wsSetStatus('reconnecting', 'Connecting…');

  // The JWT is sent as the first message, never in the URL (keeps it out of logs)
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  WS.socket = ws;
  WS.alive = false;

  ws.onopen = () => {
    const token = Api.getToken();
    if (!token) { ws.close(); return; }
    ws.send(JSON.stringify({ type: 'auth', token }));
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

  ws.onclose = e => {
    if (WS.socket !== ws) return;   // superseded by a newer socket
    WS.socket = null;
    State.ws = null;
    WS.alive = false;

    // 4001 = token rejected/expired by the server → sign out, don't retry forever
    if (e && e.code === 4001) {
      wsSetStatus('disconnected', 'Signed out');
      toast('Your session has expired — please sign in again', 'error');
      logoutFn();
      return;
    }
    if (!State.agent || !Api.getToken()) return;

    wsSetStatus('reconnecting', 'Reconnecting…');

    // Exponential backoff
    const delay = Math.min(WS.retryDelay, WS.maxDelay);
    WS.retryDelay = Math.min(WS.retryDelay * 2, WS.maxDelay);
    WS.retryTimer = setTimeout(connectWS, delay);
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

    // Show toast/notify for inbound messages (toast only when user is on another view)
    if (!d.from_me && typeof notifyUser === 'function') {
      notifyUser('new_messages', displayName(d.sender_name || d.chat_name || d.chat_wid || 'New message'), d.body || 'Media message', {
        toast: State.currentView !== 'inbox',
        toastText: `💬 New message: ${(d.body || 'Media message').substring(0, 60)}`,
        tag: `chat-${d.chat_id}`,
      });
    }
    return;
  }

  // ── Notifications (each respects the agent's prefs via notifyUser) ──
  if (event === 'note_mention' || event === 'note_added') {
    const chat = displayName(d.chat_name);
    const verb = event === 'note_mention' ? 'mentioned you' : 'added a private note';
    notifyUser('new_note', `${d.by} ${verb}`, d.content || '', {
      toastText: `📝 ${d.by} ${verb} in ${chat}`,
      tag: `note-${d.note_id}`,
    });
    return;
  }

  if (event === 'ticket_assigned') {
    notifyUser('ticket_assign', 'Ticket assigned to you', d.title || '', {
      toastText: `🎫 ${d.by} assigned you ticket #${d.ticket_id}: ${d.title}`,
      tag: `ticket-${d.ticket_id}`,
    });
    return;
  }

  if (event === 'task_assigned') {
    notifyUser('task_assign', 'Task assigned to you', d.title || '', {
      toastText: `✅ ${d.by} assigned you a task: ${d.title}`,
      tag: `task-${d.task_id}`,
    });
    return;
  }

  if (event === 'task_reminder') {
    notifyUser('task_assign', '⏰ Task reminder', d.title || '', {
      toastText: `⏰ Task reminder: ${d.title}`,
      tag: `task-reminder-${d.task_id}`,
    });
    return;
  }

  if (event === 'chat_assigned') {
    const chat = displayName(d.chat_name || `Chat #${d.chat_id}`);
    notifyUser('chat_assign', 'Chat assigned to you', chat, {
      toastText: `💬 ${d.by || 'Someone'} assigned you ${chat}`,
      tag: `chat-assign-${d.chat_id}`,
    });
    return;
  }

  if (event === 'ticket_overdue') {
    notifyUser('ticket_overdue', 'Ticket overdue', d.title || '', {
      toastText: `⚠️ Ticket #${d.ticket_id} is overdue: ${d.title}`,
      tag: `ticket-overdue-${d.ticket_id}`,
    });
    return;
  }

  if (event === 'task_overdue') {
    notifyUser('task_overdue', 'Task overdue', d.title || '', {
      toastText: `⚠️ Task overdue: ${d.title}`,
      tag: `task-overdue-${d.task_id}`,
    });
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
    if (State.currentView === 'dashboard') _dashSetPhoneStatus(d.phone_id, d.status);
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
  if (!badge || !num) return;
  const totalCount = State.phones.length;
  const working = State.phones.filter(p => p.waha_status === 'WORKING').length;
  num.textContent = working;
  if (total) total.textContent = totalCount;
  badge.style.display = State.agent ? 'flex' : 'none';
  // Colour lives in CSS (dashboard.css) so the dark theme applies
  badge.classList.toggle('state-none', working === 0);
  badge.classList.toggle('state-partial', working > 0 && working < totalCount);
  badge.classList.toggle('state-ok', totalCount > 0 && working === totalCount);
  badge.title = `${working} of ${totalCount} phone${totalCount === 1 ? '' : 's'} connected — manage in Settings`;
}

// ── Topbar actions (home, refresh, help, phones badge) ────────── //
document.getElementById('topbar-home')?.addEventListener('click', () => navigateTo('dashboard'));
document.getElementById('topbar-refresh')?.addEventListener('click', () => {
  loadPhones();
  navigateTo(State.currentRoute || State.currentView || 'dashboard');
});
document.getElementById('topbar-help')?.addEventListener('click', () => showHelpModal());
document.getElementById('topbar-phone-count')?.addEventListener('click', () => navigateTo('settings'));

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

  _inboxReady = loadChats();
  await _inboxReady;

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
let _inboxReady = null;   // promise of the initial loadChats() in renderInbox
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

  const phoneConnected = State.phones.some(p => p.waha_status === 'WORKING');
  const phone = State.phones.find(p => p.waha_status === 'WORKING') || State.phones[0];

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
  if (f === 'awaiting') return chats.filter(c => c.last_message_from_me === false);
  return chats;
}

// Sidebar "Chats" badge = number of unread chats across the agent's numbers.
// Comes from the server so it's right on every page, not just the loaded list page.
let _unreadBadgeTimer = null;
function refreshUnreadBadge(delay = 0) {
  clearTimeout(_unreadBadgeTimer);
  _unreadBadgeTimer = setTimeout(async () => {
    if (!State.agent) return;
    try {
      const s = await Api.analytics.summary();
      const n = s?.chats?.unread || 0;
      const badge = document.getElementById('unread-badge');
      if (badge) { badge.textContent = n > 99 ? '99+' : n; badge.style.display = n ? 'inline-flex' : 'none'; }
    } catch (_) { /* keep the last value */ }
  }, delay);
}
function _updateUnreadBadge() { refreshUnreadBadge(1500); }

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
        const labelObj = State.labels.find(l => l.id == lbl);
        const color2 = safeColor(labelObj?.color);
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
  // chat.labels holds label IDs (server: label_ids)
  const chatLabels = (chat.labels || []).map(Number).filter(Number.isFinite);

  // Build label chips
  const labelsMarkup = chatLabels.map(lbl => {
    const labelObj = State.labels.find(l => l.id === lbl);
    const lColor = safeColor(labelObj?.color);
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
  const availableLabels = State.labels.filter(l => !chatLabels.includes(l.id));
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
        <select id="detail-add-label-select" style="font-size:11.5px;padding:2px 5px;border:1px solid var(--border);border-radius:4px;flex:1;height:26px;background:var(--surface)">
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
        <span class="${pillClass(chat.status||'open')}" style="font-size:11px">${esc(chat.status||'open')}</span>
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
          <span>${chat.created_at ? parseServerDate(chat.created_at).toLocaleDateString('en', {month:'short',day:'numeric',year:'numeric'}) : '—'}</span>
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
      try {
        await Api.inbox.addLabel(chat.id, labelObj.id);
        chat.labels = [...chatLabels, labelObj.id];
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
      const lid = parseInt(btn.dataset.removeLabel);
      try {
        await Api.inbox.removeLabel(chat.id, lid);
        chat.labels = chatLabels.filter(l => l !== lid);
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
let _msgLastSyncedChatId = null; // chat ID that was most recently synced; prevents double-sync in _fetchOlderMessages
let _msgLoadSeq = 0;             // bumps on every loadMessages(); stale responses compare against it

async function loadMessages(chatId, _alreadySynced) {
  const area = document.getElementById('messages-area');
  if (!area) return;
  const seq = ++_msgLoadSeq;
  // True when the user switched chats (or a newer load started) while we were awaiting
  const stale = () => seq !== _msgLoadSeq || State.inbox.selectedChatId != chatId || !area.isConnected;
  const chat = State.inbox.chats?.find(c => c.id == chatId);
  const isGroup = chat?.is_group || false;

  // Reset older-load sentinels for this chat
  _msgLoadingOlder = false;
  _msgNoMoreOlder  = false;
  _msgLastSyncedChatId = null;
  if (_msgScrollObserver) { _msgScrollObserver.disconnect(); _msgScrollObserver = null; }

  // Show a slim loading skeleton immediately
  area.innerHTML = `<div id="msg-loading-bar" style="display:flex;align-items:center;justify-content:center;padding:2rem;gap:.5rem;opacity:.6;font-size:13px;color:var(--text-3)">
    <div class="spinner" style="width:16px;height:16px"></div> Loading messages…
  </div>`;

  try {
    // Always do a live WAHA sync first (200 msgs) unless WS just reconnected
    if (!_alreadySynced) {
      try { await Api.inbox.syncMessages(chatId, 200); _msgLastSyncedChatId = chatId; } catch(_) {}
      if (stale()) return;
    }

    let messages = await Api.inbox.messages(chatId, { limit: 100 });
    if (stale()) return;

    // Interleave private team notes into the thread
    let notes = [];
    try { notes = await Api.notes.list(chatId); } catch(_) {}
    if (stale()) return;
    State.inbox.messages = messages;
    const thread = messages.map(m => ({ kind: 'msg', ts: m.timestamp, item: m }))
      .concat(notes.map(n => ({ kind: 'note', ts: n.created_at, item: n })))
      .sort((a, b) => parseServerDate(a.ts) - parseServerDate(b.ts));

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
    if (stale()) return;
    area.innerHTML = `<div class="loading-center text-muted">Could not load messages. Check your connection.</div>`;
  }
}

function _attachScrollSentinel(chatId, isGroup, area) {
  const sentinel = document.getElementById('scroll-top-sentinel');
  if (!sentinel) return;
  if (_msgScrollObserver) _msgScrollObserver.disconnect();
  const seq = _msgLoadSeq;
  const observer = new IntersectionObserver(async (entries) => {
    // Observer belongs to one chat load; retire it once another chat/load takes over
    if (seq !== _msgLoadSeq || State.inbox.selectedChatId != chatId || !area.isConnected) {
      observer.disconnect();
      if (_msgScrollObserver === observer) _msgScrollObserver = null;
      return;
    }
    if (!entries[0].isIntersecting) return;
    if (_msgLoadingOlder || _msgNoMoreOlder) return;
    _msgLoadingOlder = true;
    try { await _fetchOlderMessages(chatId, isGroup, area, seq); }
    finally { if (seq === _msgLoadSeq) _msgLoadingOlder = false; }
  }, { root: area, threshold: 0.1 });
  _msgScrollObserver = observer;
  observer.observe(sentinel);
}

async function _fetchOlderMessages(chatId, isGroup, area, seq) {
  const sentinel = document.getElementById('scroll-top-sentinel');
  if (!sentinel || !area) return;
  const stale = () => seq !== _msgLoadSeq || State.inbox.selectedChatId != chatId || !area.isConnected;

  // Show tiny spinner above sentinel
  const spinnerEl = document.createElement('div');
  spinnerEl.id = 'older-spinner';
  spinnerEl.style.cssText = 'display:flex;align-items:center;justify-content:center;padding:.5rem;gap:.4rem;font-size:12px;color:var(--text-3);opacity:.6';
  spinnerEl.innerHTML = '<div class="spinner" style="width:12px;height:12px"></div> Loading older messages…';
  sentinel.insertAdjacentElement('afterend', spinnerEl);

  try {
    const current = State.inbox.messages || [];
    const oldestTs = current.length ? current[0].timestamp : null;
    const prevScrollHeight = area.scrollHeight;

    // 1. Try DB first using timestamp cursor (correct for historically-synced messages
    //    that arrive with high IDs but early timestamps — id-based cursor misses them)
    let older = oldestTs ? await Api.inbox.messages(chatId, { limit: 50, before_ts: oldestTs }) : [];
    if (stale()) return;

    // 2. DB exhausted → pull from WAHA
    // Skip if loadMessages already synced this exact chat moments ago (avoids double-sync
    // on short chats where the sentinel fires immediately because all msgs fit on screen).
    if (!older.length && !_msgNoMoreOlder && _msgLastSyncedChatId !== chatId) {
      try {
        await Api.inbox.syncMessages(chatId, Math.min((current.length || 0) + 150, 500));
        older = oldestTs
          ? await Api.inbox.messages(chatId, { limit: 50, before_ts: oldestTs })
          : await Api.inbox.messages(chatId, { limit: 50 });
      } catch(_) {}
      if (stale()) return;
    }
    _msgLastSyncedChatId = null; // consume the guard — subsequent scroll-ups can WAHA sync normally

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
    State.inbox.messages = older.concat(State.inbox.messages || []);
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
      <span class="lp-dot" style="width:9px;height:9px;border-radius:3px;background:${safeColor(l.color)};display:inline-block"></span>${esc(l.name)}
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
        message_wid: message?.message_wid || null,
        title,
        description: document.getElementById('tkm-desc').value,
        status: document.getElementById('tkm-status').value,
        priority: document.getElementById('tkm-priority').value,
        assigned_to: parseInt(document.getElementById('tkm-assignee').value) || null,
        due_date: localInputToIso(dueInp.value),
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
  const { chatId, message, onSaved } = opts || {};
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
        message_wid: message?.message_wid || null,
        due_date: localInputToIso(document.getElementById('tkt-due').value),
        reminder_at: localInputToIso(document.getElementById('tkt-reminder').value),
        assigned_to: parseInt(document.getElementById('tkt-assignee').value) || null,
        priority: document.getElementById('tkt-prio').value,
        notes: document.getElementById('tkt-notes').value.trim() || null,
      });
      closeModal(); toast('Task created', 'success');
      if (typeof onSaved === 'function') onSaved();
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
          <select id="ticket-filter" style="font-size:12.5px;padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--text-2);font-weight:500;outline:none;cursor:pointer;box-shadow:0 1px 2px rgba(0,0,0,0.05);transition:border-color 0.15s, box-shadow 0.15s;">
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
          <div id="tickets-more-wrap" style="display:none;text-align:center;padding:.75rem 1rem">
            <button class="btn btn-secondary btn-sm" id="tickets-more-btn">Load more</button>
          </div>
        </div>
      </div>
    </div>`;

  State.tickets.filter = '';
  await loadTickets();

  document.getElementById('ticket-filter').addEventListener('change', e => {
    loadTickets(e.target.value);
  });
  document.getElementById('tickets-more-btn').addEventListener('click', () => loadTickets(undefined, true));

  document.getElementById('new-ticket-btn').addEventListener('click', () => showCreateTicketModal());
}

const TICKET_PAGE = 50;
let _ticketsSeq = 0;

// loadTickets(status?, append?) — status undefined keeps the current filter;
// append=true fetches the next page (limit/offset) and adds it to the table
async function loadTickets(status, append = false) {
  if (status !== undefined) State.tickets.filter = status;
  const filter = State.tickets.filter && State.tickets.filter !== 'all' ? State.tickets.filter : '';
  const seq = ++_ticketsSeq;
  const moreBtn = document.getElementById('tickets-more-btn');
  if (moreBtn) { moreBtn.disabled = true; moreBtn.textContent = 'Loading…'; }
  try {
    const q = { limit: TICKET_PAGE, offset: append ? State.tickets.list.length : 0 };
    if (filter) q.status = filter;
    const [page, agents] = await Promise.all([
      Api.tickets.list(q),
      Api.auth.agents().catch(() => []),
    ]);
    if (seq !== _ticketsSeq) return;   // superseded by a newer load
    const agentMap = {};
    agents.forEach(a => { agentMap[a.id] = a.name; });
    const list = append ? State.tickets.list.concat(page) : page;
    State.tickets.list = list;
    const moreWrap = document.getElementById('tickets-more-wrap');
    if (moreWrap) moreWrap.style.display = page.length === TICKET_PAGE ? 'block' : 'none';
    if (moreBtn) { moreBtn.disabled = false; moreBtn.textContent = 'Load more'; }
    const tbody = document.getElementById('tickets-tbody');
    if (!tbody) return;
    if (!list.length) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:2rem;color:var(--text-3)">No tickets found</td></tr>`; return;
    }
    tbody.innerHTML = list.map(t => `
      <tr>
        <td style="color:var(--text-3);font-size:12px">#${t.id}</td>
        <td><a href="#" class="ticket-link text-accent" data-tid="${t.id}" style="font-weight:600">${esc(t.title)}</a></td>
        <td><span class="${pillClass(t.status)}">${esc(t.status?.replace('_',' '))}</span></td>
        <td><span class="${pillClass(t.priority)}">${esc(t.priority)}</span></td>
        <td style="font-size:12px;color:var(--text-3)">${t.assigned_to ? esc(agentMap[t.assigned_to] || 'Agent #'+t.assigned_to) : '—'}</td>
        <td style="font-size:12px;color:var(--text-3)">${t.due_date ? parseServerDate(t.due_date).toLocaleDateString() : '—'}</td>
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
  } catch(e) {
    if (seq !== _ticketsSeq) return;
    if (moreBtn) { moreBtn.disabled = false; moreBtn.textContent = 'Load more'; }
    const tbody = document.getElementById('tickets-tbody');
    if (tbody && !append) tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:2rem;color:var(--text-3)">Could not load tickets</td></tr>`;
    toast(e.message || 'Failed to load tickets', 'error');
  }
}

async function showCreateTicketModal() {
  let chats = [], agents = [];
  try { chats = await Api.inbox.chats({ limit: 200 }).catch(() => []); } catch (_) {}
  try { agents = await Api.auth.agents(); } catch (_) {}

  const chatOpts = chats.map(c => `<option value="${c.id}">${esc(displayName(c))}</option>`).join('');
  const agentOpts = agents.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('');

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
      <div class="form-group"><label>Assignee</label>
        <select id="ntk-assignee">
          <option value="">Unassigned</option>
          ${agentOpts}
        </select>
      </div>
      <div class="form-group"><label>Due Date</label><input type="date" id="ntk-due"></div>
      <div class="form-group"><label>Status</label>
        <select id="ntk-status">
          <option value="open" selected>Open</option>
          <option value="in_progress">In Progress</option>
          <option value="resolved">Resolved</option>
          <option value="closed">Closed</option>
        </select>
      </div>
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
        status: document.getElementById('ntk-status').value,
        assigned_to: parseInt(document.getElementById('ntk-assignee').value) || null,
        due_date: document.getElementById('ntk-due').value || null,
      });
      closeModal();
      toast('Ticket created', 'success');
      loadTickets();
    } catch(e) {
      toast(e.message, 'error');
    }
  });
}

async function showEditTicketModal(ticket) {
  let agents = [];
  try { agents = await Api.auth.agents(); } catch (_) {}
  const agentOpts = agents.map(a =>
    `<option value="${a.id}" ${ticket.assigned_to == a.id ? 'selected' : ''}>${esc(a.name)}</option>`
  ).join('');

  showModal('Edit Ticket #' + ticket.id, `
    <div class="form-group"><label>Title</label><input type="text" id="etk-title" value="${esc(ticket.title)}"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
      <div class="form-group"><label>Status</label>
        <select id="etk-status">
          <option ${ticket.status==='open'?'selected':''} value="open">Open</option>
          <option ${ticket.status==='in_progress'?'selected':''} value="in_progress">In Progress</option>
          <option ${ticket.status==='resolved'?'selected':''} value="resolved">Resolved</option>
          <option ${ticket.status==='closed'?'selected':''} value="closed">Closed</option>
        </select>
      </div>
      <div class="form-group"><label>Priority</label>
        <select id="etk-priority">
          <option ${ticket.priority==='low'?'selected':''} value="low">Low</option>
          <option ${ticket.priority==='medium'?'selected':''} value="medium">Medium</option>
          <option ${ticket.priority==='high'?'selected':''} value="high">High</option>
          <option ${ticket.priority==='urgent'?'selected':''} value="urgent">Urgent</option>
        </select>
      </div>
      <div class="form-group"><label>Assignee</label>
        <select id="etk-assignee">
          <option value="" ${!ticket.assigned_to ? 'selected' : ''}>Unassigned</option>
          ${agentOpts}
        </select>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="etk-save">Save</button>
    </div>`);
  document.getElementById('etk-save').addEventListener('click', async () => {
    try {
      await Api.tickets.update(ticket.id, {
        title: document.getElementById('etk-title').value,
        status: document.getElementById('etk-status').value,
        priority: document.getElementById('etk-priority').value,
        assigned_to: parseInt(document.getElementById('etk-assignee').value) || null,
      });
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
  const debouncedLoad = debounce(loadContacts, 300);
  document.getElementById('contact-search').addEventListener('input', e => {
    State.contacts.search = e.target.value;
    debouncedLoad();
  });
  document.getElementById('new-contact-btn').addEventListener('click', () => showContactModal());
}

function debounce(fn, ms) {
  let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

let _contactsSeq = 0;
async function loadContacts() {
  const q = {};
  if (State.contacts.search) q.search = State.contacts.search;
  const seq = ++_contactsSeq;
  try {
    const list = await Api.contacts.list(q);
    if (seq !== _contactsSeq) return;   // a newer search already started
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
  } catch(e) {
    if (seq !== _contactsSeq) return;
    const el = document.getElementById('contacts-list');
    if (el) el.innerHTML = `<div class="loading-center text-muted">Could not load contacts</div>`;
    toast(e.message || 'Failed to load contacts', 'error');
  }
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
// Sub-routes: #analytics/<page>. Range / chat / filters persist across pages (memory only).
// Analytics icons: Font Awesome Free 6.7.2 solid (CC BY 4.0) — [viewBox width, path]
const AN_ICONS = {
  chart: [512, 'M64 64c0-17.7-14.3-32-32-32S0 46.3 0 64L0 400c0 44.2 35.8 80 80 80l400 0c17.7 0 32-14.3 32-32s-14.3-32-32-32L80 416c-8.8 0-16-7.2-16-16L64 64zm406.6 86.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L320 210.7l-57.4-57.4c-12.5-12.5-32.8-12.5-45.3 0l-112 112c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L240 221.3l57.4 57.4c12.5 12.5 32.8 12.5 45.3 0l128-128z'],
  team: [640, 'M144 0a80 80 0 1 1 0 160A80 80 0 1 1 144 0zM512 0a80 80 0 1 1 0 160A80 80 0 1 1 512 0zM0 298.7C0 239.8 47.8 192 106.7 192l42.7 0c15.9 0 31 3.5 44.6 9.7c-1.3 7.2-1.9 14.7-1.9 22.3c0 38.2 16.8 72.5 43.3 96c-.2 0-.4 0-.7 0L21.3 320C9.6 320 0 310.4 0 298.7zM405.3 320c-.2 0-.4 0-.7 0c26.6-23.5 43.3-57.8 43.3-96c0-7.6-.7-15-1.9-22.3c13.6-6.3 28.7-9.7 44.6-9.7l42.7 0C592.2 192 640 239.8 640 298.7c0 11.8-9.6 21.3-21.3 21.3l-213.3 0zM224 224a96 96 0 1 1 192 0 96 96 0 1 1 -192 0zM128 485.3C128 411.7 187.7 352 261.3 352l117.3 0C452.3 352 512 411.7 512 485.3c0 14.7-11.9 26.7-26.7 26.7l-330.7 0c-14.7 0-26.7-11.9-26.7-26.7z'],
  phone: [384, 'M16 64C16 28.7 44.7 0 80 0L304 0c35.3 0 64 28.7 64 64l0 384c0 35.3-28.7 64-64 64L80 512c-35.3 0-64-28.7-64-64L16 64zM224 448a32 32 0 1 0 -64 0 32 32 0 1 0 64 0zM304 64L80 64l0 320 224 0 0-320z'],
  chats: [640, 'M208 352c114.9 0 208-78.8 208-176S322.9 0 208 0S0 78.8 0 176c0 38.6 14.7 74.3 39.6 103.4c-3.5 9.4-8.7 17.7-14.2 24.7c-4.8 6.2-9.7 11-13.3 14.3c-1.8 1.6-3.3 2.9-4.3 3.7c-.5 .4-.9 .7-1.1 .8l-.2 .2s0 0 0 0s0 0 0 0C1 327.2-1.4 334.4 .8 340.9S9.1 352 16 352c21.8 0 43.8-5.6 62.1-12.5c9.2-3.5 17.8-7.4 25.2-11.4C134.1 343.3 169.8 352 208 352zM448 176c0 112.3-99.1 196.9-216.5 207C255.8 457.4 336.4 512 432 512c38.2 0 73.9-8.7 104.7-23.9c7.5 4 16 7.9 25.2 11.4c18.3 6.9 40.3 12.5 62.1 12.5c6.9 0 13.1-4.5 15.2-11.1c2.1-6.6-.2-13.8-5.8-17.9c0 0 0 0 0 0s0 0 0 0l-.2-.2c-.2-.2-.6-.4-1.1-.8c-1-.8-2.5-2-4.3-3.7c-3.6-3.3-8.5-8.1-13.3-14.3c-5.5-7-10.7-15.4-14.2-24.7c24.9-29 39.6-64.7 39.6-103.4c0-92.8-84.9-168.9-192.6-175.5c.4 5.1 .6 10.3 .6 15.5z'],
  ticket: [576, 'M64 64C28.7 64 0 92.7 0 128l0 64c0 8.8 7.4 15.7 15.7 18.6C34.5 217.1 48 235 48 256s-13.5 38.9-32.3 45.4C7.4 304.3 0 311.2 0 320l0 64c0 35.3 28.7 64 64 64l448 0c35.3 0 64-28.7 64-64l0-64c0-8.8-7.4-15.7-15.7-18.6C541.5 294.9 528 277 528 256s13.5-38.9 32.3-45.4c8.3-2.9 15.7-9.8 15.7-18.6l0-64c0-35.3-28.7-64-64-64L64 64zm64 112l0 160c0 8.8 7.2 16 16 16l288 0c8.8 0 16-7.2 16-16l0-160c0-8.8-7.2-16-16-16l-288 0c-8.8 0-16 7.2-16 16zM96 160c0-17.7 14.3-32 32-32l320 0c17.7 0 32 14.3 32 32l0 192c0 17.7-14.3 32-32 32l-320 0c-17.7 0-32-14.3-32-32l0-192z'],
  message: [512, 'M64 0C28.7 0 0 28.7 0 64L0 352c0 35.3 28.7 64 64 64l96 0 0 80c0 6.1 3.4 11.6 8.8 14.3s11.9 2.1 16.8-1.5L309.3 416 448 416c35.3 0 64-28.7 64-64l0-288c0-35.3-28.7-64-64-64L64 0z'],
  members: [640, 'M96 128a128 128 0 1 1 256 0A128 128 0 1 1 96 128zM0 482.3C0 383.8 79.8 304 178.3 304l91.4 0C368.2 304 448 383.8 448 482.3c0 16.4-13.3 29.7-29.7 29.7L29.7 512C13.3 512 0 498.7 0 482.3zM609.3 512l-137.8 0c5.4-9.4 8.6-20.3 8.6-32l0-8c0-60.7-27.1-115.2-69.8-151.8c2.4-.1 4.7-.2 7.1-.2l61.4 0C567.8 320 640 392.2 640 481.3c0 17-13.8 30.7-30.7 30.7zM432 256c-31 0-59-12.6-79.3-32.9C372.4 196.5 384 163.6 384 128c0-26.8-6.6-52.1-18.3-74.3C384.3 40.1 407.2 32 432 32c61.9 0 112 50.1 112 112s-50.1 112-112 112z'],
  export: [576, 'M0 64C0 28.7 28.7 0 64 0L224 0l0 128c0 17.7 14.3 32 32 32l128 0 0 128-168 0c-13.3 0-24 10.7-24 24s10.7 24 24 24l168 0 0 112c0 35.3-28.7 64-64 64L64 512c-35.3 0-64-28.7-64-64L0 64zM384 336l0-48 110.1 0-39-39c-9.4-9.4-9.4-24.6 0-33.9s24.6-9.4 33.9 0l80 80c9.4 9.4 9.4 24.6 0 33.9l-80 80c-9.4 9.4-24.6 9.4-33.9 0s-9.4-24.6 0-33.9l39-39L384 336zm0-208l-128 0L256 0 384 128z'],
  info: [512, 'M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512zM216 336l24 0 0-64-24 0c-13.3 0-24-10.7-24-24s10.7-24 24-24l48 0c13.3 0 24 10.7 24 24l0 88 8 0c13.3 0 24 10.7 24 24s-10.7 24-24 24l-80 0c-13.3 0-24-10.7-24-24s10.7-24 24-24zm40-208a32 32 0 1 1 0 64 32 32 0 1 1 0-64z'],
  refresh: [512, 'M463.5 224l8.5 0c13.3 0 24-10.7 24-24l0-128c0-9.7-5.8-18.5-14.8-22.2s-19.3-1.7-26.2 5.2L413.4 96.6c-87.6-86.5-228.7-86.2-315.8 1c-87.5 87.5-87.5 229.3 0 316.8s229.3 87.5 316.8 0c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0c-62.5 62.5-163.8 62.5-226.3 0s-62.5-163.8 0-226.3c62.2-62.2 162.7-62.5 225.3-1L327 183c-6.9 6.9-8.9 17.2-5.2 26.2s12.5 14.8 22.2 14.8l119.5 0z'],
  upload: [448, 'M246.6 9.4c-12.5-12.5-32.8-12.5-45.3 0l-128 128c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L192 109.3 192 320c0 17.7 14.3 32 32 32s32-14.3 32-32l0-210.7 73.4 73.4c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3l-128-128zM64 352c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 64c0 53 43 96 96 96l256 0c53 0 96-43 96-96l0-64c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 64c0 17.7-14.3 32-32 32L96 448c-17.7 0-32-14.3-32-32l0-64z'],
  filter: [512, 'M3.9 54.9C10.5 40.9 24.5 32 40 32l432 0c15.5 0 29.5 8.9 36.1 22.9s4.6 30.5-5.2 42.5L320 320.9 320 448c0 12.1-6.8 23.2-17.7 28.6s-23.8 4.3-33.5-3l-64-48c-8.1-6-12.8-15.5-12.8-25.6l0-79.1L9 97.3C-.7 85.4-2.8 68.8 3.9 54.9z'],
  down: [512, 'M233.4 406.6c12.5 12.5 32.8 12.5 45.3 0l192-192c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L256 338.7 86.6 169.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3l192 192z'],
  right: [320, 'M310.6 233.4c12.5 12.5 12.5 32.8 0 45.3l-192 192c-12.5 12.5-32.8 12.5-45.3 0s-12.5-32.8 0-45.3L242.7 256 73.4 86.6c-12.5-12.5-12.5-32.8 0-45.3s32.8-12.5 45.3 0l192 192z'],
  calendar: [448, 'M96 32l0 32L48 64C21.5 64 0 85.5 0 112l0 48 448 0 0-48c0-26.5-21.5-48-48-48l-48 0 0-32c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 32L160 64l0-32c0-17.7-14.3-32-32-32S96 14.3 96 32zM448 192L0 192 0 464c0 26.5 21.5 48 48 48l352 0c26.5 0 48-21.5 48-48l0-272z'],
  search: [512, 'M416 208c0 45.9-14.9 88.3-40 122.7L502.6 457.4c12.5 12.5 12.5 32.8 0 45.3s-32.8 12.5-45.3 0L330.7 376c-34.4 25.2-76.8 40-122.7 40C93.1 416 0 322.9 0 208S93.1 0 208 0S416 93.1 416 208zM208 352a144 144 0 1 0 0-288 144 144 0 1 0 0 288z'],
  clock: [512, 'M256 0a256 256 0 1 1 0 512A256 256 0 1 1 256 0zM232 120l0 136c0 8 4 15.5 10.7 20l96 64c11 7.4 25.9 4.4 33.3-6.7s4.4-25.9-6.7-33.3L280 243.2 280 120c0-13.3-10.7-24-24-24s-24 10.7-24 24z'],
  check: [512, 'M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512zM369 209L241 337c-9.4 9.4-24.6 9.4-33.9 0l-64-64c-9.4-9.4-9.4-24.6 0-33.9s24.6-9.4 33.9 0l47 47L335 175c9.4-9.4 24.6-9.4 33.9 0s9.4 24.6 0 33.9z'],
  unassigned: [640, 'M38.8 5.1C28.4-3.1 13.3-1.2 5.1 9.2S-1.2 34.7 9.2 42.9l592 464c10.4 8.2 25.5 6.3 33.7-4.1s6.3-25.5-4.1-33.7L353.3 251.6C407.9 237 448 187.2 448 128C448 57.3 390.7 0 320 0C250.2 0 193.5 55.8 192 125.2L38.8 5.1zM264.3 304.3C170.5 309.4 96 387.2 96 482.3c0 16.4 13.3 29.7 29.7 29.7l388.6 0c3.9 0 7.6-.7 11-2.1l-261-205.6z'],
  hourglass: [384, 'M32 0C14.3 0 0 14.3 0 32S14.3 64 32 64l0 11c0 42.4 16.9 83.1 46.9 113.1L146.7 256 78.9 323.9C48.9 353.9 32 394.6 32 437l0 11c-17.7 0-32 14.3-32 32s14.3 32 32 32l32 0 256 0 32 0c17.7 0 32-14.3 32-32s-14.3-32-32-32l0-11c0-42.4-16.9-83.1-46.9-113.1L237.3 256l67.9-67.9c30-30 46.9-70.7 46.9-113.1l0-11c17.7 0 32-14.3 32-32s-14.3-32-32-32L320 0 64 0 32 0zM96 75l0-11 192 0 0 11c0 19-5.6 37.4-16 53L112 128c-10.3-15.6-16-34-16-53zm16 309c3.5-5.3 7.6-10.3 12.1-14.9L192 301.3l67.9 67.9c4.6 4.6 8.6 9.6 12.1 14.9L112 384z'],
  userPlus: [640, 'M96 128a128 128 0 1 1 256 0A128 128 0 1 1 96 128zM0 482.3C0 383.8 79.8 304 178.3 304l91.4 0C368.2 304 448 383.8 448 482.3c0 16.4-13.3 29.7-29.7 29.7L29.7 512C13.3 512 0 498.7 0 482.3zM504 312l0-64-64 0c-13.3 0-24-10.7-24-24s10.7-24 24-24l64 0 0-64c0-13.3 10.7-24 24-24s24 10.7 24 24l0 64 64 0c13.3 0 24 10.7 24 24s-10.7 24-24 24l-64 0 0 64c0 13.3-10.7 24-24 24s-24-10.7-24-24z'],
  userMinus: [640, 'M96 128a128 128 0 1 1 256 0A128 128 0 1 1 96 128zM0 482.3C0 383.8 79.8 304 178.3 304l91.4 0C368.2 304 448 383.8 448 482.3c0 16.4-13.3 29.7-29.7 29.7L29.7 512C13.3 512 0 498.7 0 482.3zM472 200l144 0c13.3 0 24 10.7 24 24s-10.7 24-24 24l-144 0c-13.3 0-24-10.7-24-24s10.7-24 24-24z'],
  userX: [640, 'M96 128a128 128 0 1 1 256 0A128 128 0 1 1 96 128zM0 482.3C0 383.8 79.8 304 178.3 304l91.4 0C368.2 304 448 383.8 448 482.3c0 16.4-13.3 29.7-29.7 29.7L29.7 512C13.3 512 0 498.7 0 482.3zM471 143c9.4-9.4 24.6-9.4 33.9 0l47 47 47-47c9.4-9.4 24.6-9.4 33.9 0s9.4 24.6 0 33.9l-47 47 47 47c9.4 9.4 9.4 24.6 0 33.9s-24.6 9.4-33.9 0l-47-47-47 47c-9.4 9.4-24.6 9.4-33.9 0s-9.4-24.6 0-33.9l47-47-47-47c-9.4-9.4-9.4-24.6 0-33.9z'],
  out: [384, 'M214.6 41.4c-12.5-12.5-32.8-12.5-45.3 0l-160 160c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L160 141.2 160 448c0 17.7 14.3 32 32 32s32-14.3 32-32l0-306.7L329.4 246.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3l-160-160z'],
  in: [384, 'M169.4 470.6c12.5 12.5 32.8 12.5 45.3 0l160-160c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L224 370.8 224 64c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 306.7L54.6 265.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3l160 160z'],
  flag: [448, 'M64 32C64 14.3 49.7 0 32 0S0 14.3 0 32L0 64 0 368 0 480c0 17.7 14.3 32 32 32s32-14.3 32-32l0-128 64.3-16.1c41.1-10.3 84.6-5.5 122.5 13.4c44.2 22.1 95.5 24.8 141.7 7.4l34.7-13c12.5-4.7 20.8-16.6 20.8-30l0-247.7c0-23-24.2-38-44.8-27.7l-9.6 4.8c-46.3 23.2-100.8 23.2-147.1 0c-35.1-17.6-75.4-22-113.5-12.5L64 48l0-16z'],
  stopwatch: [448, 'M176 0c-17.7 0-32 14.3-32 32s14.3 32 32 32l16 0 0 34.4C92.3 113.8 16 200 16 304c0 114.9 93.1 208 208 208s208-93.1 208-208c0-41.8-12.3-80.7-33.5-113.2l24.1-24.1c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L355.7 143c-28.1-23-62.2-38.8-99.7-44.6L256 64l16 0c17.7 0 32-14.3 32-32s-14.3-32-32-32L224 0 176 0zm72 192l0 128c0 13.3-10.7 24-24 24s-24-10.7-24-24l0-128c0-13.3 10.7-24 24-24s24 10.7 24 24z'],
  note: [448, 'M64 32C28.7 32 0 60.7 0 96L0 416c0 35.3 28.7 64 64 64l224 0 0-112c0-26.5 21.5-48 48-48l112 0 0-224c0-35.3-28.7-64-64-64L64 32zM448 352l-45.3 0L336 352c-8.8 0-16 7.2-16 16l0 66.7 0 45.3 32-32 64-64 32-32z'],
  logs: [512, 'M75 75L41 41C25.9 25.9 0 36.6 0 57.9L0 168c0 13.3 10.7 24 24 24l110.1 0c21.4 0 32.1-25.9 17-41l-30.8-30.8C155 85.5 203 64 256 64c106 0 192 86 192 192s-86 192-192 192c-40.8 0-78.6-12.7-109.7-34.4c-14.5-10.1-34.4-6.6-44.6 7.9s-6.6 34.4 7.9 44.6C151.2 495 201.7 512 256 512c141.4 0 256-114.6 256-256S397.4 0 256 0C185.3 0 121.3 28.7 75 75zm181 53c-13.3 0-24 10.7-24 24l0 104c0 6.4 2.5 12.5 7 17l72 72c9.4 9.4 24.6 9.4 33.9 0s9.4-24.6 0-33.9l-65-65 0-94.1c0-13.3-10.7-24-24-24z'],
  contacts: [512, 'M96 0C60.7 0 32 28.7 32 64l0 384c0 35.3 28.7 64 64 64l288 0c35.3 0 64-28.7 64-64l0-384c0-35.3-28.7-64-64-64L96 0zM208 288l64 0c44.2 0 80 35.8 80 80c0 8.8-7.2 16-16 16l-192 0c-8.8 0-16-7.2-16-16c0-44.2 35.8-80 80-80zm-32-96a64 64 0 1 1 128 0 64 64 0 1 1 -128 0zM512 80c0-8.8-7.2-16-16-16s-16 7.2-16 16l0 64c0 8.8 7.2 16 16 16s16-7.2 16-16l0-64zM496 192c-8.8 0-16 7.2-16 16l0 64c0 8.8 7.2 16 16 16s16-7.2 16-16l0-64c0-8.8-7.2-16-16-16zm16 144c0-8.8-7.2-16-16-16s-16 7.2-16 16l0 64c0 8.8 7.2 16 16 16s16-7.2 16-16l0-64z'],
  actions: [512, 'M32 96l320 0 0-64c0-12.9 7.8-24.6 19.8-29.6s25.7-2.2 34.9 6.9l96 96c6 6 9.4 14.1 9.4 22.6s-3.4 16.6-9.4 22.6l-96 96c-9.2 9.2-22.9 11.9-34.9 6.9s-19.8-16.6-19.8-29.6l0-64L32 160c-17.7 0-32-14.3-32-32s14.3-32 32-32zM480 352c17.7 0 32 14.3 32 32s-14.3 32-32 32l-320 0 0 64c0 12.9-7.8 24.6-19.8 29.6s-25.7 2.2-34.9-6.9l-96-96c-6-6-9.4-14.1-9.4-22.6s3.4-16.6 9.4-22.6l96-96c9.2-9.2 22.9-11.9 34.9-6.9s19.8 16.6 19.8 29.6l0 64 320 0z'],
  close: [384, 'M342.6 150.6c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L192 210.7 86.6 105.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L146.7 256 41.4 361.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0L192 301.3 297.4 406.6c12.5 12.5 32.8 12.5 45.3 0s12.5-32.8 0-45.3L237.3 256 342.6 150.6z'],
  comment: [512, 'M512 240c0 114.9-114.6 208-256 208c-37.1 0-72.3-6.4-104.1-17.9c-11.9 8.7-31.3 20.6-54.3 30.6C73.6 471.1 44.7 480 16 480c-6.5 0-12.3-3.9-14.8-9.9c-2.5-6-1.1-12.8 3.4-17.4c0 0 0 0 0 0s0 0 0 0s0 0 0 0c0 0 0 0 0 0l.3-.3c.3-.3 .7-.7 1.3-1.4c1.1-1.2 2.8-3.1 4.9-5.7c4.1-5 9.6-12.4 15.2-21.6c10-16.6 19.5-38.4 21.4-62.9C17.7 326.8 0 285.1 0 240C0 125.1 114.6 32 256 32s256 93.1 256 208z'],
  user: [448, 'M224 256A128 128 0 1 0 224 0a128 128 0 1 0 0 256zm-45.7 48C79.8 304 0 383.8 0 482.3C0 498.7 13.3 512 29.7 512l388.6 0c16.4 0 29.7-13.3 29.7-29.7C448 383.8 368.2 304 269.7 304l-91.4 0z'],
  group: [640, 'M72 88a56 56 0 1 1 112 0A56 56 0 1 1 72 88zM64 245.7C54 256.9 48 271.8 48 288s6 31.1 16 42.3l0-84.7zm144.4-49.3C178.7 222.7 160 261.2 160 304c0 34.3 12 65.8 32 90.5l0 21.5c0 17.7-14.3 32-32 32l-64 0c-17.7 0-32-14.3-32-32l0-26.8C26.2 371.2 0 332.7 0 288c0-61.9 50.1-112 112-112l32 0c24 0 46.2 7.5 64.4 20.3zM448 416l0-21.5c20-24.7 32-56.2 32-90.5c0-42.8-18.7-81.3-48.4-107.7C449.8 183.5 472 176 496 176l32 0c61.9 0 112 50.1 112 112c0 44.7-26.2 83.2-64 101.2l0 26.8c0 17.7-14.3 32-32 32l-64 0c-17.7 0-32-14.3-32-32zm8-328a56 56 0 1 1 112 0A56 56 0 1 1 456 88zM576 245.7l0 84.7c10-11.3 16-26.1 16-42.3s-6-31.1-16-42.3zM320 32a64 64 0 1 1 0 128 64 64 0 1 1 0-128zM240 304c0 16.2 6 31 16 42.3l0-84.7c-10 11.3-16 26.1-16 42.3zm144-42.3l0 84.7c10-11.3 16-26.1 16-42.3s-6-31.1-16-42.3zM448 304c0 44.7-26.2 83.2-64 101.2l0 42.8c0 17.7-14.3 32-32 32l-64 0c-17.7 0-32-14.3-32-32l0-42.8c-37.8-18-64-56.5-64-101.2c0-61.9 50.1-112 112-112l32 0c61.9 0 112 50.1 112 112z'],
  download: [512, 'M288 32c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 242.7-73.4-73.4c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3l128 128c12.5 12.5 32.8 12.5 45.3 0l128-128c12.5-12.5 12.5-32.8 0-45.3s-32.8-12.5-45.3 0L288 274.7 288 32zM64 352c-35.3 0-64 28.7-64 64l0 32c0 35.3 28.7 64 64 64l384 0c35.3 0 64-28.7 64-64l0-32c0-35.3-28.7-64-64-64l-101.5 0-45.3 45.3c-25 25-65.5 25-90.5 0L165.5 352 64 352zm368 56a24 24 0 1 1 0 48 24 24 0 1 1 0-48z'],
};

function anIcon(name, cls = '') {
  const ic = AN_ICONS[name];
  if (!ic) return '';
  return `<svg class="an-ic ${cls}" viewBox="0 0 ${ic[0]} 512" fill="currentColor" aria-hidden="true"><path d="${ic[1]}"/></svg>`;
}

const AN_PAGES = {
  team:     { nav: 'Team analytics',  title: 'Team analytics',  icon: 'team',    agentFilter: true,
              info: 'Activity per team member for the selected range. Only messages sent from Hyperscope are attributed to a member; messages sent from the phone itself, bulk jobs, automations or the AI agent count in Total only.' },
  phones:   { nav: 'Phone metrics',   title: 'Phone analytics', icon: 'phone',
              info: 'Activity per connected WhatsApp number for the selected range.' },
  chats:    { nav: 'Chat metrics',    title: 'Chat metrics',    icon: 'chats',
              info: 'New chats are chats that first reached Hyperscope in the selected range (including chats imported when a phone is connected).' },
  tickets:  { nav: 'Ticket metrics',  title: 'Ticket metrics',  icon: 'ticket',
              info: 'Tickets created in the selected range, grouped by assignee. Unresolved age buckets count open tickets by how long ago they were created.' },
  messages: { nav: 'Message metrics', title: 'Message metrics', icon: 'message', agentFilter: true,
              info: 'Incoming and outgoing messages in the selected range. With a member filter, outgoing counts only messages that member sent from Hyperscope.' },
  members:  { nav: 'Member metrics',  title: 'Member metrics',  icon: 'members',
              info: 'Group membership changes: joins (including members added by an admin), leaves and removals.' },
  exports:  { nav: 'Data exports',    title: 'Data exports',    icon: 'export', group: 'exports' },
};

const AN = {
  sub: 'team',
  range: null,            // { preset, from: 'YYYY-MM-DD', to: 'YYYY-MM-DD' } (local dates, inclusive)
  chat: null,             // { id, name }
  phoneIds: [], agentIds: [],
  data: null, charts: [], seq: 0,
  agents: null,
};

const _AN_MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function _anYmd(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function _anParseYmd(s) { const [y, m, d] = String(s).split('-').map(Number); return new Date(y, m - 1, d); }
function _anAddDays(d, n) { const x = new Date(d); x.setDate(x.getDate() + n); return x; }
function _anShortDate(d) { return `${String(d.getDate()).padStart(2, '0')}-${_AN_MON[d.getMonth()]}-${String(d.getFullYear()).slice(-2)}`; }

function _anPresetRange(preset) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const r = { preset };
  if (preset === 'today') { r.from = r.to = _anYmd(today); }
  else if (preset === 'yesterday') { r.from = r.to = _anYmd(_anAddDays(today, -1)); }
  else if (preset === '7d') { r.from = _anYmd(_anAddDays(today, -6)); r.to = _anYmd(today); }
  else if (preset === '30d') { r.from = _anYmd(_anAddDays(today, -29)); r.to = _anYmd(today); }
  else if (preset === 'month') { r.from = _anYmd(new Date(today.getFullYear(), today.getMonth(), 1)); r.to = _anYmd(today); }
  else { r.preset = 'default'; r.from = _anYmd(_anAddDays(today, -1)); r.to = _anYmd(today); }
  return r;
}

function _anQuery() {
  if (!AN.range) AN.range = _anPresetRange('default');
  const from = _anParseYmd(AN.range.from);
  const end = _anAddDays(_anParseYmd(AN.range.to), 1);
  const to = end > new Date() ? new Date() : end;
  const q = { from: from.toISOString(), to: to.toISOString() };
  try { q.tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch (_) { q.tz = 'UTC'; }
  q.tz_offset = -from.getTimezoneOffset();  // fallback when the server lacks that zone name
  if (AN.chat) q.chat_id = AN.chat.id;
  if (AN.phoneIds.length) q.phone_ids = AN.phoneIds.join(',');
  if (AN.agentIds.length && AN_PAGES[AN.sub]?.agentFilter) q.agent_ids = AN.agentIds.join(',');
  return q;
}

function _anRangeLabel() {
  if (!AN.range) AN.range = _anPresetRange('default');
  return `${_anShortDate(_anParseYmd(AN.range.from))} to ${_anShortDate(_anParseYmd(AN.range.to))}`;
}

// Numbers / durations. null / undefined → "--" (not computable), with an optional tooltip.
function _anNum(v, tip) {
  if (v == null) return `<span class="an-na"${tip ? ` data-tip="${esc(tip)}"` : ''}>--</span>`;
  return Number(v).toLocaleString('en-IN');
}
function _anDurText(sec) {
  if (sec == null) return '--';
  sec = Math.round(sec);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d) return `${d}d ${h % 24}h`;
  if (h) return `${h}h ${m % 60}m`;
  return `${m}m ${sec % 60}s`;
}
function _anDur(sec, tip) {
  if (sec == null) return _anNum(null, tip);
  return _anDurText(sec);
}

function _anDestroyCharts() {
  AN.charts.forEach(c => { try { c.destroy(); } catch (_) {} });
  AN.charts = [];
}

function _anShellHTML() {
  const item = (key) => {
    const p = AN_PAGES[key];
    return `<a href="#analytics/${key}" class="an-nav-item" data-an="${key}">${anIcon(p.icon)}<span>${esc(p.nav)}</span></a>`;
  };
  return `
    <div class="an-shell" id="an-shell">
      <nav class="an-nav" aria-label="Analytics">
        <div class="an-nav-group">${anIcon('chart')}<span>Analytics</span></div>
        ${['team', 'phones', 'chats', 'tickets', 'messages', 'members'].map(item).join('')}
        <div class="an-nav-group an-nav-group-2">${anIcon('export')}<span>Exports</span></div>
        ${item('exports')}
      </nav>
      <div class="an-main" id="an-main"></div>
    </div>
    <div class="an-tooltip" id="an-tooltip" role="tooltip"></div>`;
}

async function renderAnalytics(sub) {
  sub = AN_PAGES[sub] ? sub : (AN.sub || 'team');
  AN.sub = sub;
  _anSyncRoute(sub);
  const main = document.getElementById('main-content');
  if (!document.getElementById('an-shell')) {
    main.innerHTML = _anShellHTML();
    main.querySelectorAll('.an-nav-item').forEach(a => a.addEventListener('click', e => {
      e.preventDefault();
      navigateTo('analytics/' + a.dataset.an);
    }));
    _anBindTooltips(main);
  }
  main.querySelectorAll('.an-nav-item').forEach(a => {
    const on = a.dataset.an === sub;
    a.classList.toggle('active', on);
    on ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current');
  });
  _anDestroyCharts();
  AN.data = null;
  const host = document.getElementById('an-main');
  host.scrollTop = 0;
  if (sub === 'exports') { _anRenderExports(host); return; }
  host.innerHTML = _anPageHTML(sub);
  _anBindToolbar(host);
  _anLoad();
}

// Keep the hash / breadcrumb on the concrete sub-page (e.g. after clicking "Analytics" in the sidebar)
function _anSyncRoute(sub) {
  const route = 'analytics/' + sub;
  State.currentRoute = route;
  if (location.hash !== '#' + route) history.replaceState(null, '', '#' + route);
  const bc = document.getElementById('app-breadcrumb');
  if (bc) bc.innerHTML = `<strong>${esc(VIEW_LABELS.analytics)}</strong><span class="bc-sep" aria-hidden="true">&gt;</span><strong>${esc(sub)}</strong>`;
}

function _anPageHTML(sub) {
  const p = AN_PAGES[sub];
  const nFilters = AN.phoneIds.length + (p.agentFilter ? AN.agentIds.length : 0);
  return `
    <div class="an-page">
      <div class="an-title">${anIcon(p.icon, 'an-title-ic')}<h1>${esc(p.title)}</h1></div>
      <div class="an-toolbar">
        <div class="an-pop-wrap">
          <button class="an-btn" id="an-range-btn" aria-haspopup="true" aria-expanded="false">
            ${anIcon('calendar', 'an-btn-lead')}<span id="an-range-label">${esc(_anRangeLabel())}</span>${anIcon('down', 'an-caret')}
          </button>
        </div>
        <div class="an-chatpick${AN.chat ? ' has-chat' : ''}" id="an-chatpick">
          ${anIcon('search', 'an-chatpick-ic')}
          <input id="an-chat-input" type="text" autocomplete="off" placeholder="Select a chat..."
                 aria-label="Filter by chat" value="${esc(AN.chat ? AN.chat.name : '')}">
          <button class="an-chat-clear" id="an-chat-clear" title="Clear chat" aria-label="Clear chat">${anIcon('close')}</button>
        </div>
        <div class="an-pop-wrap">
          <button class="an-btn" id="an-filter-btn" aria-haspopup="true" aria-expanded="false">
            ${anIcon('filter', 'an-btn-lead')}<span>Filter</span>${nFilters ? `<span class="an-count">${nFilters}</span>` : ''}
          </button>
        </div>
        <div class="an-spacer"></div>
        <span class="an-info" tabindex="0" data-tip="${esc(p.info || '')}" aria-label="About this page">${anIcon('info')}</span>
        <button class="an-btn" id="an-refresh">${anIcon('refresh', 'an-btn-lead')}<span>Refresh</span></button>
        <button class="an-btn" id="an-export">${anIcon('upload', 'an-btn-lead')}<span>Export</span></button>
      </div>
      <div id="an-body" class="an-body"><div class="loading-center"><div class="spinner"></div></div></div>
    </div>`;
}

function _anClosePops() {
  document.querySelectorAll('.an-pop').forEach(p => p.remove());
  document.querySelectorAll('.an-btn[aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
}
document.addEventListener('click', e => {
  if (!e.target.closest('.an-pop, .an-pop-wrap, .an-chatpick')) {
    _anClosePops();
    document.getElementById('an-chat-list')?.remove();
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && document.querySelector('.an-pop, #an-chat-list')) {
    _anClosePops();
    document.getElementById('an-chat-list')?.remove();
  }
});

function _anBindToolbar(host) {
  host.querySelector('#an-range-btn').addEventListener('click', e => _anOpenRange(e.currentTarget));
  host.querySelector('#an-filter-btn').addEventListener('click', e => _anOpenFilter(e.currentTarget));
  host.querySelector('#an-refresh').addEventListener('click', () => _anLoad());
  host.querySelector('#an-export').addEventListener('click', () => _anExportCsv());
  _anBindChatPicker(host);
}

function _anOpenRange(btn) {
  const open = btn.getAttribute('aria-expanded') === 'true';
  _anClosePops();
  if (open) return;
  btn.setAttribute('aria-expanded', 'true');
  const presets = [['today', 'Today'], ['yesterday', 'Yesterday'], ['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['month', 'This month']];
  const pop = document.createElement('div');
  pop.className = 'an-pop an-range-pop';
  pop.innerHTML = `
    ${presets.map(([k, l]) => `<button class="an-pop-item${AN.range?.preset === k ? ' active' : ''}" data-preset="${k}">${l}${AN.range?.preset === k ? anIcon('check', 'an-pop-check') : ''}</button>`).join('')}
    <div class="an-pop-sep"></div>
    <div class="an-pop-label">Custom</div>
    <div class="an-custom">
      <label>From<input type="date" id="an-from" value="${esc(AN.range.from)}" max="${_anYmd(new Date())}"></label>
      <label>To<input type="date" id="an-to" value="${esc(AN.range.to)}" max="${_anYmd(new Date())}"></label>
    </div>
    <button class="btn btn-primary btn-sm an-apply" id="an-range-apply">Apply</button>`;
  btn.parentElement.appendChild(pop);
  pop.querySelectorAll('[data-preset]').forEach(b => b.addEventListener('click', () => {
    AN.range = _anPresetRange(b.dataset.preset);
    _anClosePops(); _anRangeChanged();
  }));
  pop.querySelector('#an-range-apply').addEventListener('click', () => {
    const f = pop.querySelector('#an-from').value, t = pop.querySelector('#an-to').value;
    if (!f || !t) return toast('Pick both dates', 'error');
    if (f > t) return toast('"From" must be on or before "To"', 'error');
    if ((_anParseYmd(t) - _anParseYmd(f)) / 86400000 > 365) return toast('Pick at most 366 days', 'error');
    AN.range = { preset: 'custom', from: f, to: t };
    _anClosePops(); _anRangeChanged();
  });
}

function _anRangeChanged() {
  const l = document.getElementById('an-range-label');
  if (l) l.textContent = _anRangeLabel();
  _anLoad();
}

async function _anOpenFilter(btn) {
  const open = btn.getAttribute('aria-expanded') === 'true';
  _anClosePops();
  if (open) return;
  btn.setAttribute('aria-expanded', 'true');
  const withAgents = !!AN_PAGES[AN.sub]?.agentFilter;
  const pop = document.createElement('div');
  pop.className = 'an-pop an-filter-pop';
  pop.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
  btn.parentElement.appendChild(pop);
  let phones = State.phones || [];
  try { if (!phones.length) phones = State.phones = await Api.phones.list(); } catch (_) {}
  if (withAgents && !AN.agents) { try { AN.agents = await Api.auth.agents(); } catch (_) { AN.agents = []; } }
  if (!pop.isConnected) return;
  const opt = (kind, id, label, sub, checked) => `
    <label class="an-check"><input type="checkbox" data-kind="${kind}" value="${id}"${checked ? ' checked' : ''}>
      <span class="an-check-text"><span>${esc(label)}</span>${sub ? `<small>${esc(sub)}</small>` : ''}</span></label>`;
  pop.innerHTML = `
    <div class="an-pop-label">Phones</div>
    <div class="an-check-list">${phones.map(p => opt('phone', p.id, p.name || 'Phone', _dashFmtPhone(p.phone_number), AN.phoneIds.includes(p.id))).join('') || '<div class="an-pop-empty">No phones connected</div>'}</div>
    ${withAgents ? `<div class="an-pop-sep"></div><div class="an-pop-label">Members</div>
      <div class="an-check-list">${(AN.agents || []).map(a => opt('agent', a.id, a.name, a.email, AN.agentIds.includes(a.id))).join('') || '<div class="an-pop-empty">No members</div>'}</div>` : ''}
    <div class="an-pop-foot">
      <button class="btn btn-secondary btn-sm" id="an-filter-clear">Clear</button>
      <button class="btn btn-primary btn-sm" id="an-filter-apply">Apply</button>
    </div>`;
  pop.querySelector('#an-filter-clear').addEventListener('click', () => {
    AN.phoneIds = []; if (withAgents) AN.agentIds = [];
    _anClosePops(); _anFiltersChanged();
  });
  pop.querySelector('#an-filter-apply').addEventListener('click', () => {
    const vals = kind => [...pop.querySelectorAll(`input[data-kind="${kind}"]:checked`)].map(i => Number(i.value));
    AN.phoneIds = vals('phone');
    if (withAgents) AN.agentIds = vals('agent');
    _anClosePops(); _anFiltersChanged();
  });
}

function _anFiltersChanged() {
  const btn = document.getElementById('an-filter-btn');
  if (btn) {
    btn.querySelector('.an-count')?.remove();
    const n = AN.phoneIds.length + (AN_PAGES[AN.sub]?.agentFilter ? AN.agentIds.length : 0);
    if (n) btn.insertAdjacentHTML('beforeend', `<span class="an-count">${n}</span>`);
  }
  _anLoad();
}

function _anBindChatPicker(host) {
  const wrap = host.querySelector('#an-chatpick');
  const input = host.querySelector('#an-chat-input');
  let timer = null, seq = 0;
  const close = () => document.getElementById('an-chat-list')?.remove();
  const show = async () => {
    const my = ++seq;
    let list = [];
    try { list = await Api.analytics.chatOptions(input.value.trim()); } catch (_) {}
    if (my !== seq || !input.isConnected) return;  // superseded, or blurred (blur bumps seq)
    close();
    const box = document.createElement('div');
    box.id = 'an-chat-list';
    box.className = 'an-pop an-chat-list';
    box.setAttribute('role', 'listbox');
    box.innerHTML = list.length ? list.map(c => `
      <button class="an-pop-item an-chat-opt" role="option" data-id="${c.id}" data-name="${esc(displayName(c.name))}">
        <span class="agent-avatar xs" style="background:${safeColor(avatarColor(c.name))}">${esc(initials(displayName(c.name)))}</span>
        <span class="an-chat-name">${esc(displayName(c.name))}</span>${c.is_group ? '<small>Group</small>' : ''}
      </button>`).join('') : '<div class="an-pop-empty">No chats found</div>';
    wrap.appendChild(box);
    box.querySelectorAll('.an-chat-opt').forEach(b => b.addEventListener('mousedown', e => {
      e.preventDefault();
      AN.chat = { id: Number(b.dataset.id), name: b.dataset.name };
      input.value = AN.chat.name;
      wrap.classList.add('has-chat');
      close(); input.blur(); _anLoad();
    }));
  };
  input.addEventListener('focus', () => { input.select(); show(); });
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(show, 200); });
  input.addEventListener('blur', () => setTimeout(() => {
    seq++;
    close();
    input.value = AN.chat ? AN.chat.name : '';
  }, 150));
  host.querySelector('#an-chat-clear').addEventListener('click', () => {
    AN.chat = null; input.value = ''; wrap.classList.remove('has-chat'); _anLoad();
  });
}

// Floating tooltip for any [data-tip] inside the analytics shell
function _anBindTooltips(root) {
  const tip = () => document.getElementById('an-tooltip');
  const showTip = el => {
    const t = tip(); const text = el.getAttribute('data-tip');
    if (!t || !text) return;
    t.textContent = text;
    t.classList.add('show');
    const r = el.getBoundingClientRect(), w = t.offsetWidth, h = t.offsetHeight;
    let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
    let top = r.bottom + 8;
    if (top + h > window.innerHeight - 8) top = r.top - h - 8;
    t.style.left = left + 'px'; t.style.top = top + 'px';
  };
  const hide = () => tip()?.classList.remove('show');
  root.addEventListener('mouseover', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el); });
  root.addEventListener('mouseout', e => { if (e.target.closest('[data-tip]')) hide(); });
  root.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el); });
  root.addEventListener('focusout', hide);
}

async function _anLoad() {
  const sub = AN.sub;
  const body = document.getElementById('an-body');
  if (!body || sub === 'exports') return;
  const my = ++AN.seq;
  body.classList.add('is-loading');
  if (!AN.data) body.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
  try {
    const data = await Api.analytics[sub](_anQuery());
    if (my !== AN.seq || AN.sub !== sub || !document.getElementById('an-body')) return;
    AN.data = data;
    _anDestroyCharts();
    body.innerHTML = _AN_RENDER[sub].html(data);
    _AN_RENDER[sub].after?.(data, body);
  } catch (e) {
    if (my !== AN.seq) return;
    if (e.message === 'Chat not found' && AN.chat) {
      AN.chat = null;
      const inp = document.getElementById('an-chat-input'); if (inp) inp.value = '';
      document.getElementById('an-chatpick')?.classList.remove('has-chat');
    }
    body.innerHTML = `<div class="an-card an-error">Could not load analytics — ${esc(e.message || 'request failed')}</div>`;
  } finally {
    if (my === AN.seq) body.classList.remove('is-loading');
  }
}

// ── Shared pieces ── //
function _anStat(icon, tone, label, value, tip) {
  return `
    <div class="an-card an-stat">
      <div class="an-stat-head">
        <span class="an-stat-ic tone-${tone}">${anIcon(icon)}</span>
        <span class="an-stat-label">${esc(label)}</span>
        ${tip ? `<span class="an-i" tabindex="0" data-tip="${esc(tip)}">${anIcon('info')}</span>` : ''}
      </div>
      <div class="an-stat-val">${value}</div>
    </div>`;
}

function _anAvatar(row, total) {
  if (total) return `<span class="an-avatar an-avatar-total">${anIcon('team')}</span>`;
  const bg = row.avatar_color ? safeColor(row.avatar_color) : safeColor(avatarColor(row.name));
  return `<span class="an-avatar" style="background:${bg}">${esc(initials(row.name))}${row.online ? '<i class="an-dot" title="Online"></i>' : ''}</span>`;
}

function _anUserCell(row, total) {
  if (total) return `<div class="an-user">${_anAvatar(null, true)}<div><div class="an-user-name">Total</div><div class="an-user-sub">All members</div></div></div>`;
  return `<div class="an-user">${_anAvatar(row)}<div class="an-user-meta"><div class="an-user-name">${esc(row.name)}</div>${row.email ? `<div class="an-user-sub">${esc(row.email)}</div>` : ''}</div></div>`;
}

function _anTh(label, tip, cls = '') {
  return `<th class="${cls}">${esc(label)}${tip ? ` <span class="an-i" tabindex="0" data-tip="${esc(tip)}">${anIcon('info')}</span>` : ''}</th>`;
}

function _anEmptyRow(cols) {
  return `<tr class="an-empty-row"><td colspan="${cols}">No data available</td></tr>`;
}

function _anFrtTip(d) {
  return d.frt_basis === 'all_inbound'
    ? 'No incoming message in this range was flagged, so this is measured from any incoming message to the next reply.'
    : 'Median time from a flagged incoming message to the next reply in that chat.';
}
const _AN_NO_FRT = 'No replies to measure in this range.';

// ── Chart (Chart.js; colours re-read from CSS variables on every build) ── //
function _anCss(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function _anBucketLabel(iso, bucket) {
  const d = new Date(iso);
  const day = `${String(d.getDate()).padStart(2, '0')}-${_AN_MON[d.getMonth()]}`;
  return bucket === 'hour' ? [day, `${String(d.getHours()).padStart(2, '0')}:00`] : [day];
}

function _anChartCard(id, title, series, opts = {}) {
  return `
    <div class="an-card an-chart-card${opts.cls ? ' ' + opts.cls : ''}">
      <div class="an-card-title">${esc(title)}${opts.tip ? ` <span class="an-i" tabindex="0" data-tip="${esc(opts.tip)}">${anIcon('info')}</span>` : ''}</div>
      <div class="an-chart-box"><canvas id="${id}" role="img" aria-label="${esc(title)} chart"></canvas></div>
      <div class="an-legend" data-for="${id}">
        ${series.map((s, i) => `
          <button class="an-legend-item" data-i="${i}" aria-pressed="true">
            <i class="an-legend-sq" style="background:var(${s.color})"></i>
            <span class="an-legend-text"><span>${esc(s.label)}</span>${s.desc ? `<small>${esc(s.desc)}</small>` : ''}</span>
          </button>`).join('')}
      </div>
    </div>`;
}

function _anExternalTooltip(context) {
  const { chart, tooltip } = context;
  let el = chart.canvas.parentNode.querySelector('.an-chart-tip');
  if (!el) {
    el = document.createElement('div');
    el.className = 'an-chart-tip';
    chart.canvas.parentNode.appendChild(el);
  }
  if (tooltip.opacity === 0) { el.style.opacity = 0; return; }
  const title = (tooltip.title || []).join(' ');
  el.innerHTML = `<div class="an-chart-tip-title">${esc(title)}</div>` + (tooltip.dataPoints || []).map(p => `
    <div class="an-chart-tip-row"><i style="background:${esc(p.dataset.borderColor)}"></i>
      <span>${esc(p.dataset.label)}</span><b>${Number(p.raw).toLocaleString('en-IN')}</b></div>`).join('');
  el.style.opacity = 1;
  const box = chart.canvas.parentNode;
  const w = el.offsetWidth;
  let left = tooltip.caretX + 14;
  if (left + w > box.clientWidth) left = tooltip.caretX - w - 14;
  el.style.left = Math.max(0, left) + 'px';
  el.style.top = Math.max(0, Math.min(tooltip.caretY - el.offsetHeight / 2, box.clientHeight - el.offsetHeight)) + 'px';
}

function _anBuildChart(id, series, labels, bucket) {
  const canvas = document.getElementById(id);
  if (!canvas || typeof Chart === 'undefined') return;
  const grid = _anCss('--border', '#e5e7eb');
  const tick = _anCss('--text-3', '#9ca3af');
  const labelsFmt = labels.map(l => _anBucketLabel(l, bucket));
  const chart = new Chart(canvas, {
    type: 'line',
    data: {
      labels: labelsFmt,
      datasets: series.map(s => {
        const c = _anCss(s.color, '#15803d');
        return {
          label: s.label, data: s.data, borderColor: c, backgroundColor: c,
          borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 3.5, pointHitRadius: 8, tension: 0.3, cubicInterpolationMode: 'monotone', fill: false,
        };
      }),
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false, external: _anExternalTooltip,
                   callbacks: { title: items => (items[0]?.label || []).toString().replace(',', ' ') } },
      },
      scales: {
        x: {
          grid: { display: false }, border: { color: grid },
          ticks: { color: tick, font: { family: 'Inter', size: 11 }, maxRotation: 0, autoSkip: true, autoSkipPadding: 18 },
        },
        y: {
          beginAtZero: true, grid: { color: grid }, border: { display: false, dash: [4, 4] },
          ticks: { color: tick, font: { family: 'Inter', size: 11 }, precision: 0, padding: 8 },
        },
      },
    },
  });
  AN.charts.push(chart);
  const legend = document.querySelector(`.an-legend[data-for="${id}"]`);
  legend?.querySelectorAll('.an-legend-item').forEach(b => b.addEventListener('click', () => {
    const i = Number(b.dataset.i);
    const vis = chart.isDatasetVisible(i);
    chart.setDatasetVisibility(i, !vis);
    b.setAttribute('aria-pressed', String(!vis));
    b.classList.toggle('off', vis);
    chart.update();
  }));
}

// Rebuild charts when the theme flips so they pick up the new CSS variable colours
new MutationObserver(() => {
  if (State.currentView === 'analytics' && AN.data && AN.charts.length) {
    const body = document.getElementById('an-body');
    if (body) { _anDestroyCharts(); body.innerHTML = _AN_RENDER[AN.sub].html(AN.data); _AN_RENDER[AN.sub].after?.(AN.data, body); }
  }
}).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

// ── Page renderers ── //
const _AN_UPTIME_TIP = 'Presence tracking has no data for this range (it records time with Hyperscope open).';

const _AN_RENDER = {
  team: {
    html(d) {
      const t = d.total || {};
      const frtTip = _anFrtTip(d);
      const unattr = t.unattributed_messages
        ? `${t.unattributed_messages.toLocaleString('en-IN')} message(s) were sent from the phone, bulk jobs, automations or the AI agent — they count in Total but not for a member.`
        : 'Outgoing messages. Only messages sent from Hyperscope are attributed to a member.';
      const row = (r, total) => `
        <tr class="${total ? 'an-total-row' : ''}">
          <td>${_anUserCell(r, total)}</td>
          <td>${_anNum(r.active_chats)}</td>
          <td>${_anNum(r.messages_sent)}</td>
          <td>${_anNum(r.chats_initiated)}</td>
          <td>${_anNum(r.tickets_closed)}</td>
          <td>${_anNum(r.responses_flagged)}</td>
          <td>${_anDur(r.median_frt_seconds, _AN_NO_FRT)}</td>
          <td>${_anDur(r.uptime_seconds, _AN_UPTIME_TIP)}</td>
        </tr>`;
      return `
        <div class="an-card an-table-card">
          <div class="an-table-wrap"><table class="an-table">
            <thead><tr>
              ${_anTh('User')}
              ${_anTh('Active chats', 'Chats with at least one message in range (for a member: chats they sent a message in).')}
              ${_anTh('Messages sent', unattr)}
              ${_anTh('Chats initiated', 'Chats whose first message in range was outgoing.')}
              ${_anTh('Tickets closed', 'Tickets moved to resolved or closed in range (per member: who closed them).')}
              ${_anTh('Responses (to flagged messages)', 'Replies to flagged incoming messages.')}
              ${_anTh('Median first response time', frtTip)}
              ${_anTh('User uptime', 'Time with Hyperscope open (at least one live connection) in range.')}
            </tr></thead>
            <tbody>${row(t, true)}${(d.rows || []).map(r => row(r)).join('') || _anEmptyRow(8)}</tbody>
          </table></div>
        </div>`;
    },
  },

  phones: {
    html(d) {
      const t = d.total || {};
      const row = (r, total) => `
        <tr class="${total ? 'an-total-row' : ''}">
          <td>${total
            ? `<div class="an-user"><span class="an-avatar an-avatar-total">${anIcon('phone')}</span><div><div class="an-user-name">Total</div><div class="an-user-sub">All phones</div></div></div>`
            : `<div class="an-user"><span class="an-avatar an-avatar-phone">${anIcon('phone')}</span><div class="an-user-meta"><div class="an-user-name">${esc(r.name || 'Phone')}</div><div class="an-user-sub">${esc(_dashFmtPhone(r.phone_number) || r.phone_number || '')}</div></div></div>`}</td>
          <td>${_anNum(r.new_chats)}</td>
          <td>${_anNum(r.messages_sent)}</td>
          <td>${_anNum(r.responses_flagged)}</td>
          <td>${_anDur(r.median_frt_seconds, _AN_NO_FRT)}</td>
        </tr>`;
      return `
        <div class="an-card an-table-card">
          <div class="an-table-wrap"><table class="an-table">
            <thead><tr>
              ${_anTh('Phone')}
              ${_anTh('New chats created', 'Chats that first reached Hyperscope in range.')}
              ${_anTh('Messages sent', 'All outgoing messages from this number.')}
              ${_anTh('Responses (to flagged messages)', 'Replies to flagged incoming messages.')}
              ${_anTh('Median first response time', _anFrtTip(d))}
            </tr></thead>
            <tbody>${row(t, true)}${(d.rows || []).map(r => row(r)).join('') || _anEmptyRow(5)}</tbody>
          </table></div>
        </div>`;
    },
  },

  chats: {
    html(d) {
      const t = d.total || {};
      return `
        <div class="an-split">
          <div class="an-stack">
            ${_anStat('chats', 'green', 'Total new chats', _anNum(t.new_chats), 'One-to-one chats and groups that first reached Hyperscope in range.')}
            ${_anStat('comment', 'blue', 'New one-to-one chats', _anNum(t.new_individual), 'New chats with a single contact.')}
            ${_anStat('group', 'navy', 'New groups', _anNum(t.new_groups), 'New group chats.')}
          </div>
          ${_anChartCard('an-chart', 'New chats created', [
            { label: 'Individual chats', color: '--an-green' }, { label: 'Group chats', color: '--an-navy' }])}
        </div>
        <div class="an-card an-table-card">
          <div class="an-card-title">Most active chats</div>
          <div class="an-table-wrap"><table class="an-table an-table-compact">
            <thead><tr>${_anTh('Chat name')}${_anTh('Total messages', '', 'num')}</tr></thead>
            <tbody>${(d.most_active || []).map(c => `
              <tr><td><div class="an-user"><span class="an-avatar sm" style="background:${safeColor(avatarColor(c.name))}">${esc(initials(displayName(c.name)))}</span>
                <div class="an-user-name">${esc(displayName(c.name))}${c.is_group ? ' <small class="an-tag">Group</small>' : ''}</div></div></td>
                <td class="num">${_anNum(c.messages)}</td></tr>`).join('') || _anEmptyRow(2)}</tbody>
          </table></div>
        </div>`;
    },
    after(d) {
      const s = d.series || {};
      _anBuildChart('an-chart', [
        { label: 'Individual chats', color: '--an-green', data: s.individual || [] },
        { label: 'Group chats', color: '--an-navy', data: s.group || [] },
      ], s.buckets || [], d.range?.bucket);
    },
  },

  tickets: {
    html(d) {
      const t = d.total || {};
      const age = r => r.unresolved_age || {};
      const row = (r, total) => `
        <tr class="${total ? 'an-total-row' : ''}">
          <td>${total ? _anUserCell(null, true) : _anUserCell(r)}</td>
          <td>${_anNum(r.total)}</td>
          <td>${_anNum(r.open)}</td>
          <td>${_anNum(r.closed)}</td>
          <td>${_anDur(r.avg_resolution_seconds, 'No tickets resolved yet.')}</td>
          <td class="an-sep-l">${_anNum(age(r).lt_1h)}</td>
          <td>${_anNum(age(r).lt_24h)}</td>
          <td>${_anNum(age(r).lt_7d)}</td>
          <td>${_anNum(age(r).gt_7d)}</td>
        </tr>`;
      const totalRow = { total: t.total, open: t.unresolved, closed: t.resolved,
                         avg_resolution_seconds: t.avg_resolution_seconds, unresolved_age: t.unresolved_age };
      return `
        <div class="an-stats an-stats-5">
          ${_anStat('ticket', 'navy', 'Total tickets', _anNum(t.total), 'Tickets created in range.')}
          ${_anStat('clock', 'amber', 'Unresolved tickets', _anNum(t.unresolved), 'Tickets created in range that are still open or in progress.')}
          ${_anStat('check', 'green', 'Resolved tickets', _anNum(t.resolved), 'Tickets created in range that are resolved or closed.')}
          ${_anStat('unassigned', 'grey', 'Unassigned', _anNum(t.unassigned), 'Unresolved tickets created in range with no assignee.')}
          ${_anStat('hourglass', 'blue', 'Average resolution time', _anDur(t.avg_resolution_seconds, 'No tickets resolved in this range.'), 'Average time from creation to resolution for tickets resolved in range.')}
        </div>
        ${_anChartCard('an-chart', 'Ticket history', [
          { label: 'Unresolved', color: '--an-peach', desc: 'Open or in-progress tickets at the end of each period' },
          { label: 'Created', color: '--an-red', desc: 'Tickets created in each period' },
          { label: 'Closed', color: '--an-green', desc: 'Tickets resolved or closed in each period' }], { cls: 'an-chart-wide' })}
        <div class="an-card an-table-card">
          <div class="an-table-wrap"><table class="an-table">
            <thead>
              <tr class="an-group-head"><th colspan="5"></th><th colspan="4" class="an-sep-l">Unresolved tickets by age</th></tr>
              <tr>
                ${_anTh('User')}
                ${_anTh('Total', 'Tickets created in range, by assignee.')}
                ${_anTh('Open / In progress', '', 'h-amber')}
                ${_anTh('Closed', '', 'h-green')}
                ${_anTh('Avg. resolution time', '', 'h-blue')}
                ${_anTh('< 1 hour', '', 'an-sep-l')}${_anTh('< 24 hours')}${_anTh('< 7 days')}${_anTh('> 7 days')}
              </tr>
            </thead>
            <tbody>${row(totalRow, true)}${(d.rows || []).map(r => row(r)).join('') || _anEmptyRow(9)}</tbody>
          </table></div>
        </div>`;
    },
    after(d) {
      const s = d.series || {};
      _anBuildChart('an-chart', [
        { label: 'Unresolved', color: '--an-peach', data: s.unresolved || [] },
        { label: 'Created', color: '--an-red', data: s.created || [] },
        { label: 'Closed', color: '--an-green', data: s.closed || [] },
      ], s.buckets || [], d.range?.bucket);
    },
  },

  messages: {
    html(d) {
      const t = d.total || {};
      const frtTip = _anFrtTip(d);
      const row = r => `
        <tr>
          <td>${_anUserCell(r)}</td>
          <td>${_anNum(r.active_chats)}</td>
          <td>${_anNum(r.messages_sent)}</td>
          <td>${_anNum(r.responses_flagged)}</td>
          <td>${_anDur(r.median_frt_seconds, _AN_NO_FRT)}</td>
        </tr>`;
      return `
        <div class="an-stats an-stats-5">
          ${_anStat('chats', 'green', 'Active chats', _anNum(t.active_chats), 'Chats with at least one message in range.')}
          ${_anStat('out', 'blue', 'Outgoing messages', _anNum(t.outgoing), 'Messages sent from your numbers.')}
          ${_anStat('in', 'navy', 'Incoming messages', _anNum(t.incoming), 'Messages received on your numbers.')}
          ${_anStat('flag', 'red', 'Responses to flagged messages', _anNum(t.responses_flagged), 'Replies to flagged incoming messages.')}
          ${_anStat('stopwatch', 'amber', 'Median first response time', _anDur(t.median_frt_seconds, _AN_NO_FRT), frtTip)}
        </div>
        ${_anChartCard('an-chart', 'Message metrics overview', [
          { label: 'Active chats', color: '--an-green' }, { label: 'Outgoing messages', color: '--an-blue' },
          { label: 'Incoming messages', color: '--an-navy' }, { label: 'Responses to flagged messages', color: '--an-red' }], { cls: 'an-chart-wide' })}
        <div class="an-card an-table-card">
          <div class="an-table-wrap"><table class="an-table">
            <thead><tr>
              ${_anTh('User')}
              ${_anTh('# Active chats', 'Chats the member sent a message in.')}
              ${_anTh('# Messages sent', 'Messages the member sent from Hyperscope.')}
              ${_anTh('# Responses (to flagged messages)')}
              ${_anTh('Median first response time (to flagged messages)', frtTip)}
            </tr></thead>
            <tbody>${(d.rows || []).map(row).join('') || _anEmptyRow(5)}</tbody>
          </table></div>
        </div>`;
    },
    after(d) {
      const s = d.series || {};
      _anBuildChart('an-chart', [
        { label: 'Active chats', color: '--an-green', data: s.active_chats || [] },
        { label: 'Outgoing messages', color: '--an-blue', data: s.outgoing || [] },
        { label: 'Incoming messages', color: '--an-navy', data: s.incoming || [] },
        { label: 'Responses to flagged messages', color: '--an-red', data: s.responses_flagged || [] },
      ], s.buckets || [], d.range?.bucket);
    },
  },

  members: {
    html(d) {
      const t = d.total || {};
      return `
        <div class="an-stats an-stats-3">
          ${_anStat('userPlus', 'green', 'Members joined', _anNum(t.joined), 'Members who joined a group or were added to one.')}
          ${_anStat('userMinus', 'navy', 'Members left', _anNum(t.left), 'Members who left a group.')}
          ${_anStat('userX', 'red', 'Members removed', _anNum(t.removed), 'Members removed from a group by an admin.')}
        </div>
        ${_anChartCard('an-chart', 'Member activity', [
          { label: 'Joins', color: '--an-green' }, { label: 'Leaves', color: '--an-navy' }, { label: 'Removes', color: '--an-red' }], { cls: 'an-chart-wide' })}`;
    },
    after(d) {
      const s = d.series || {};
      _anBuildChart('an-chart', [
        { label: 'Joins', color: '--an-green', data: s.joined || [] },
        { label: 'Leaves', color: '--an-navy', data: s.left || [] },
        { label: 'Removes', color: '--an-red', data: s.removed || [] },
      ], s.buckets || [], d.range?.bucket);
    },
  },
};

// ── Page CSV export (what the page shows) ── //
function _anExportCsv() {
  const d = AN.data;
  if (!d) return toast('Nothing to export yet', 'error');
  const sec = v => (v == null ? '' : Math.round(v));
  const series = (s, cols) => [['Period start', ...cols.map(c => c[1])],
    ...(s.buckets || []).map((b, i) => [new Date(b).toLocaleString('en-GB'), ...cols.map(c => (s[c[0]] || [])[i] ?? 0)])];
  let rows;
  if (AN.sub === 'team') {
    const h = ['User', 'Email', 'Active chats', 'Messages sent', 'Chats initiated', 'Tickets closed', 'Responses (to flagged messages)', 'Median first response time (s)', 'User uptime (s)'];
    const r = (x, name, email) => [name, email, x.active_chats, x.messages_sent, x.chats_initiated, x.tickets_closed, x.responses_flagged, sec(x.median_frt_seconds), sec(x.uptime_seconds)];
    rows = [h, r(d.total, 'Total', 'All members'), ...d.rows.map(x => r(x, x.name, x.email))];
  } else if (AN.sub === 'phones') {
    const r = (x, name, num) => [name, num, x.new_chats, x.messages_sent, x.responses_flagged, sec(x.median_frt_seconds)];
    rows = [['Phone', 'Number', 'New chats created', 'Messages sent', 'Responses (to flagged messages)', 'Median first response time (s)'],
            r(d.total, 'Total', ''), ...d.rows.map(x => r(x, x.name, x.phone_number))];
  } else if (AN.sub === 'chats') {
    rows = [...series(d.series, [['individual', 'Individual chats'], ['group', 'Group chats']]), [],
            ['Most active chats', 'Total messages'], ...d.most_active.map(c => [displayName(c.name), c.messages])];
  } else if (AN.sub === 'tickets') {
    const a = x => x.unresolved_age || {};
    const r = (x, name) => [name, x.total, x.open, x.closed, sec(x.avg_resolution_seconds), a(x).lt_1h, a(x).lt_24h, a(x).lt_7d, a(x).gt_7d];
    const t = d.total;
    rows = [['User', 'Total', 'Open / In progress', 'Closed', 'Avg. resolution time (s)', 'Unresolved < 1 hour', '< 24 hours', '< 7 days', '> 7 days'],
            r({ total: t.total, open: t.unresolved, closed: t.resolved, avg_resolution_seconds: t.avg_resolution_seconds, unresolved_age: t.unresolved_age }, 'Total'),
            ...d.rows.map(x => r(x, x.name)), [],
            ...series(d.series, [['unresolved', 'Unresolved'], ['created', 'Created'], ['closed', 'Closed']])];
  } else if (AN.sub === 'messages') {
    rows = [['User', 'Email', '# Active chats', '# Messages sent', '# Responses (to flagged messages)', 'Median first response time (s)'],
            ...d.rows.map(x => [x.name, x.email, x.active_chats, x.messages_sent, x.responses_flagged, sec(x.median_frt_seconds)]), [],
            ...series(d.series, [['active_chats', 'Active chats'], ['outgoing', 'Outgoing messages'], ['incoming', 'Incoming messages'], ['responses_flagged', 'Responses to flagged messages']])];
  } else if (AN.sub === 'members') {
    rows = series(d.series, [['joined', 'Joins'], ['left', 'Leaves'], ['removed', 'Removes']]);
  } else return;
  const csv = rows.map(csvRow).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url; a.download = `${AN.sub}-analytics_${AN.range.from}_to_${AN.range.to}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Data exports ── //
const AN_EXPORTS = [
  { key: 'chats',       icon: 'chats',    title: 'Chats export',          desc: 'All chats with type, phone, assignee, labels and custom properties.', range: 'Last activity', rangeOptional: true },
  { key: 'tickets',     icon: 'ticket',   title: 'Tickets export',        desc: 'Tickets with status, priority, assignee, labels and SLA details.', range: 'Created', rangeOptional: true },
  { key: 'messages',    icon: 'message',  title: 'Messages export',       desc: 'Message history with direction, sender and the member who sent it.', range: 'Sent', flagged: true },
  { key: 'notes',       icon: 'note',     title: 'Private notes export',  desc: 'Internal team notes left on chats, with author and time.', range: 'Created', rangeOptional: true },
  { key: 'phones',      icon: 'phone',    title: 'Phones export',         desc: 'Connected WhatsApp numbers with their session and status.' },
  { key: 'chatActions', icon: 'actions',  title: 'Chat actions export',   desc: 'Group membership events — joins, adds, leaves, removals and admin changes.', range: 'Happened' },
  { key: 'contacts',    icon: 'contacts', title: 'Contacts export',       desc: 'Your contact book with labels and custom properties (masked numbers stay masked).' },
  { key: 'logs',        icon: 'logs',     title: 'Activity logs export',  desc: 'Full audit trail of actions taken in the workspace.', range: 'Happened' },
];

function _anRenderExports(host) {
  const admin = isAdmin();
  host.innerHTML = `
    <div class="an-page an-exports">
      <div class="an-title">${anIcon('export', 'an-title-ic')}<h1>Data exports</h1></div>
      <p class="an-exports-sub">Download your workspace data as CSV files.${admin ? '' : ' Only admins can export data.'}</p>
      <div class="an-export-list">
        ${AN_EXPORTS.map(x => `
          <button class="an-card an-export-card" data-key="${x.key}"${admin ? '' : ' disabled aria-disabled="true"'}>
            <span class="an-export-ic">${anIcon(x.icon)}</span>
            <span class="an-export-text"><span class="an-export-title">${esc(x.title)}</span><span class="an-export-desc">${esc(x.desc)}</span></span>
            ${anIcon('right', 'an-export-chev')}
          </button>`).join('')}
      </div>
    </div>`;
  if (!admin) return;
  host.querySelectorAll('.an-export-card').forEach(b => b.addEventListener('click', () =>
    _anExportModal(AN_EXPORTS.find(x => x.key === b.dataset.key))));
}

function _anExportModal(x) {
  const today = new Date();
  const from = _anYmd(_anAddDays(today, -29)), to = _anYmd(today);
  const rangeHTML = x.range ? `
    ${x.rangeOptional ? `<label class="an-check an-check-inline"><input type="checkbox" id="anx-all" checked><span>All time</span></label>` : ''}
    <div class="an-custom an-modal-range" id="anx-range"${x.rangeOptional ? ' hidden' : ''}>
      <label>${esc(x.range)} from<input type="date" id="anx-from" value="${from}" max="${to}"></label>
      <label>To<input type="date" id="anx-to" value="${to}" max="${to}"></label>
    </div>` : '<p class="an-modal-note">Exports every record — no date range needed.</p>';
  showModal(x.title, `
    <div class="an-modal">
      <p class="an-modal-desc">${esc(x.desc)}</p>
      ${rangeHTML}
      ${x.flagged ? '<label class="an-check an-check-inline"><input type="checkbox" id="anx-flagged"><span>Flagged messages only</span></label>' : ''}
      <div class="an-modal-foot">
        <button class="btn btn-secondary btn-sm" id="anx-cancel">Cancel</button>
        <button class="btn btn-primary btn-sm" id="anx-go">${anIcon('download')} Download CSV</button>
      </div>
    </div>`);
  const all = document.getElementById('anx-all');
  all?.addEventListener('change', () => { document.getElementById('anx-range').hidden = all.checked; });
  document.getElementById('anx-cancel').addEventListener('click', () => closeModal());
  document.getElementById('anx-go').addEventListener('click', async e => {
    const btn = e.currentTarget;
    const q = {};
    if (x.range && !(all && all.checked)) {
      const f = document.getElementById('anx-from').value, t = document.getElementById('anx-to').value;
      if (!f || !t || f > t) return toast('Pick a valid date range', 'error');
      q.from = _anParseYmd(f).toISOString();
      q.to = _anAddDays(_anParseYmd(t), 1).toISOString();
    }
    if (x.flagged && document.getElementById('anx-flagged').checked) q.flagged_only = 'true';
    btn.disabled = true;
    try { await Api.exports[x.key](q); toast('Export downloaded', 'success'); closeModal(); }
    catch (err) { toast(err.message, 'error'); }
    finally { btn.disabled = false; }
  });
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
        ${isAdmin() ? `<div class="header-actions" style="margin-left:auto"><button class="btn btn-primary btn-sm" id="new-rule-btn">+ New Rule</button></div>` : ''}
      </div>
      <div class="scroll-area" id="rules-list"><div class="loading-center"><div class="spinner"></div></div></div>
    </div>`;

  await loadRules();
  document.getElementById('new-rule-btn')?.addEventListener('click', () => showRuleModal());
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
          ${isAdmin() ? `<button class="btn btn-ghost btn-sm rule-toggle" data-rid="${r.id}" data-active="${r.is_active}" style="color:var(--accent);font-weight:600;font-size:12px;padding:4px 8px">${r.is_active ? 'Pause' : 'Resume'}</button>
          <button class="btn btn-ghost btn-sm rule-del" data-rid="${r.id}" style="color:var(--danger);padding:4px 8px;font-size:12px;font-weight:500" title="Delete Rule">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:2px;vertical-align:middle"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
            Delete
          </button>` : ''}
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
  } catch(e) {
    const el = document.getElementById('rules-list');
    if (el) el.innerHTML = `<div class="loading-center text-muted">Could not load automation rules</div>`;
    toast(e.message || 'Failed to load automation rules', 'error');
  }
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
          <td><span class="${pillClass(j.status==='done'?'resolved':j.status==='running'?'in_progress':j.status==='failed'||j.status==='cancelled'?'urgent':'open')}">${esc(j.status)}</span>${j.error_message ? ` <span title="${esc(j.error_message)}">⚠️</span>` : ''}</td>
          <td>${(j.recipient_chat_ids||[]).length}</td>
          <td>${j.sent_count||0}</td>
          <td>${j.failed_count||0}</td>
          <td style="font-size:12px">${esc(_bulkRepeatSummary(j))}${j.end_date ? `<div style="color:var(--text-3);font-size:11px">until ${parseServerDate(j.end_date).toLocaleDateString()}</div>` : ''}</td>
          <td>${j.runs_count||0}</td>
          <td style="font-size:12px;color:var(--text-3)">${j.scheduled_at ? parseServerDate(j.scheduled_at).toLocaleString() : 'Immediate'}</td>
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
          <td><span class="${pillClass(r.status === 'sent' ? 'resolved' : 'urgent')}">${esc(r.status)}</span></td>
          <td>${r.run}</td>
          <td style="font-size:11.5px;color:var(--text-3)">${r.at ? parseServerDate(r.at).toLocaleString() : ''}</td>
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
        csvRow([r.chat_name || '', r.status, r.run, r.at || '', r.error || '']))).join('\n');
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
        scheduled_at: mode === 'now' ? null : new Date(schedule).toISOString(),
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
        ${isAdmin() ? '<a class="tab tab-link" href="#analytics/exports" id="settings-exports-link">Data exports ↗</a>' : ''}
      </div>
      <div class="scroll-area" id="settings-content"></div>
    </div>`;

  const tabs = document.querySelectorAll('#settings-tabs .tab[data-tab]');
  tabs.forEach(t => {
    t.addEventListener('click', () => {
      tabs.forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      loadSettingsTab(t.dataset.tab);
    });
  });
  document.getElementById('settings-exports-link')?.addEventListener('click', e => {
    e.preventDefault();
    navigateTo('analytics/exports');
  });

  loadSettingsTab('phones');
}

function showAddPhoneModal() {
  showModal('Connect WhatsApp', `
    <div class="form-group">
      <label>Display Name *</label>
      <input type="text" id="add-ph-name" placeholder="e.g. Sales, Support" autofocus>
      <small class="text-muted">Uses the WAHA session configured in your server environment</small>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" id="add-ph-save">Connect</button>
    </div>
  `);

  document.getElementById('add-ph-save').addEventListener('click', async () => {
    const name = document.getElementById('add-ph-name').value.trim();
    if (!name) return toast('Display name is required', 'error');

    const btn = document.getElementById('add-ph-save');
    btn.disabled = true;
    btn.textContent = 'Connecting…';

    try {
      const res = await Api.phones.connect(name);
      closeModal();
      toast(`Connecting — scan the QR code to link WhatsApp`, 'success');
      await loadSettingsTab('phones');
      loadPhones();
      const connectBtn = document.querySelector(`.phone-btn-connect[data-pid="${res.phone_id}"]`);
      if (connectBtn) connectBtn.click();
    } catch(e) {
      toast(e.message, 'error');
      btn.disabled = false;
      btn.textContent = 'Connect';
    }
  });
}

function _settingsLoadFailed(el, what, e) {
  if (el) el.innerHTML = `<div class="loading-center text-muted">Could not load ${esc(what)}</div>`;
  toast(e?.message || `Failed to load ${what}`, 'error');
}

// Settings → WhatsApp QR flows: interval handles keyed by phone id
const _phoneQrFlows = {};
function _stopPhoneQrFlow(phoneId) {
  const f = _phoneQrFlows[phoneId];
  if (!f) return;
  clearInterval(f.poll); clearInterval(f.sync);
  delete _phoneQrFlows[phoneId];
}
function _stopAllPhoneQrFlows() {
  Object.keys(_phoneQrFlows).forEach(_stopPhoneQrFlow);
}

async function loadSettingsTab(tab) {
  const el = document.getElementById('settings-content');
  if (!el) return;
  _stopAllPhoneQrFlows();
  el.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';

  if (tab === 'phones') {
    try {
      const phones = await Api.phones.list();
      // Silently resolve any WORKING phone still showing "pending" number —
      // happens when app restarted before sync-number completed after QR scan
      phones.filter(p => p.waha_status === 'WORKING' && (p.phone_number || '').startsWith('pending'))
            .forEach(p => Api.phones.syncNumber(p.id).catch(() => {}));
      
      let html = `
        <div class="flex-col gap-4">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1.5rem;padding-bottom:1rem;border-bottom:1px solid var(--border-light)">
            <div>
              <h3 style="margin:0 0 .25rem;font-size:16px;font-weight:600">WhatsApp Session</h3>
              <p style="margin:0;font-size:12.5px;color:var(--text-3)">Connect your WhatsApp number to Hyperscope</p>
            </div>
            ${!phones.length && isAdmin() ? `<button class="btn btn-primary btn-sm" id="btn-add-phone">+ Connect WhatsApp</button>` : ''}
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
                  <span class="pill" style="background:${statusBg};color:${statusColor};border:1px solid ${statusColor}33;padding:1px 6px;font-size:10px">${esc(statusText)}</span>
                </div>
                
                <div style="margin-bottom:0.75rem">
                  <div style="font-size:12px;color:var(--text-3)">Phone Number:</div>
                  <div style="font-size:14px;font-weight:500;color:var(--text)">
                    ${p.phone_number && !p.phone_number.startsWith('pending') ? '+' + p.phone_number : '<span style="color:#d97706;font-size:12px">⚠️ Pending connection</span>'}
                  </div>
                </div>

                <div id="phone-qr-area-${p.id}" style="margin-bottom:1rem"></div>
              </div>

              <div style="display:flex;gap:0.4rem;flex-wrap:wrap;align-items:center">
                ${!isAdmin()
                  ? `<span style="font-size:11.5px;color:var(--text-3)">Only admins can manage WhatsApp sessions</span>`
                  : connected
                  ? `<button class="btn btn-secondary btn-sm phone-btn-reconnect" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px">Reconnect / QR</button>
                     <button class="btn btn-danger btn-sm phone-btn-disconnect" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px">Disconnect</button>
                     <button class="btn btn-ghost btn-sm phone-btn-clear" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px;color:#be123c" title="Delete all chats/messages for this phone from DB">Clear Data</button>`
                  : `<button class="btn btn-primary btn-sm phone-btn-connect" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px">Connect</button>`
                }
                ${isAdmin() ? `<button class="btn btn-ghost btn-sm phone-btn-delete" data-pid="${p.id}" style="font-size:11.5px;padding:5px 9px;margin-left:auto;color:var(--danger)" title="Remove phone session from Hyperscope">Delete</button>` : ''}
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
        // One flow per phone: clear timers from an earlier Connect click first
        _stopPhoneQrFlow(phoneId);
        const flow = { poll: null, sync: null };
        _phoneQrFlows[phoneId] = flow;
        let _syncTimer = null;
        let _pollTimer = null;
        async function pollQr() {
          if (_phoneQrFlows[phoneId] !== flow) return;
          if (!area.isConnected) { _stopPhoneQrFlow(phoneId); return; }
          try {
            const r = await Api.phones.qr(phoneId);
            if (r && r.qr) {
              area.innerHTML = `
                <img src="${safeImgSrc(r.qr)}" style="max-width:200px;border-radius:8px;border:1px solid var(--border);display:block;margin:0 auto">
                <p style="font-size:11px;color:var(--text-2);margin:.6rem 0 0;text-align:center">Open WhatsApp → Linked Devices → Link a Device → Scan</p>`;
              if (!_syncTimer) {
                _syncTimer = flow.sync = setInterval(async () => {
                  try {
                    const s = await Api.phones.status(phoneId);
                    if (s.status === 'WORKING') {
                      _stopPhoneQrFlow(phoneId);
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
              // No QR — session may already be connected; check status
              try {
                const s = await Api.phones.status(phoneId);
                if (s.status === 'WORKING') {
                  _stopPhoneQrFlow(phoneId);
                  await Api.phones.syncNumber(phoneId).catch(() => {});
                  toast('WhatsApp connected! Syncing chats…', 'success');
                  loadSettingsTab('phones'); loadPhones();
                  _chatAutoSynced = false;
                  try { await Api.inbox.sync(phoneId); } catch(_) {}
                  loadChats();
                  return;
                }
              } catch(_) {}
              area.innerHTML = `<p style="font-size:12px;color:var(--text-2);text-align:center">Waiting for QR…</p>`;
            }
          } catch(e) { area.innerHTML = `<p style="font-size:12px;color:var(--danger);text-align:center">${esc(e.message)}</p>`; }
        }
        _pollTimer = flow.poll = setInterval(pollQr, 7000);
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


    } catch(e) { _settingsLoadFailed(el, 'WhatsApp status', e); }
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
                      <div style="width:18px;height:18px;border-radius:4px;background:${safeColor(l.color)};border:1px solid rgba(0,0,0,0.15)"></div>
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
    } catch(e) { _settingsLoadFailed(el, 'labels', e); }
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
    } catch(e) { _settingsLoadFailed(el, 'quick replies', e); }
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
              <div style="font-size:11px;color:var(--text-3)">${esc(a.email)} · ${esc(a.role)}</div>
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
          <div class="form-group"><label>Password * <small class="text-muted">(8–72 characters)</small></label><input type="password" id="inv-pass" minlength="8" maxlength="72" autocomplete="new-password"></div>
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
          if (pass.length < 8 || pass.length > 72) return toast('Password must be 8–72 characters', 'error');
          try {
            await Api.auth.register({ name, email, password: pass, role: document.getElementById('inv-role').value });
            closeModal(); toast('Agent created', 'success'); loadSettingsTab('agents');
          } catch(e) { toast(e.message, 'error'); }
        });
      });
    } catch(e) { _settingsLoadFailed(el, 'agents', e); }
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
}


// ── DASHBOARD VIEW ──────────────────────────────────────────────── //
// Show/hide the dashboard connection panel (QR / status) for one phone
function _dashShowPanel(phoneId, show) {
  const panel = document.getElementById('dash-waha-panel');
  if (!panel) return;
  panel.hidden = !show;
  const title = document.getElementById('dash-waha-title');
  const ph = State.phones.find(p => p.id === phoneId);
  if (title) title.textContent = ph ? ph.name : 'WhatsApp';
}

function _stopDashWahaPoller() {
  if (_dashWahaTimer) { clearInterval(_dashWahaTimer); _dashWahaTimer = null; }
}

// pollDashQR() setTimeout chain: a token lets Cancel/navigation stop it
let _dashQrTimer = null;
let _dashQrToken = 0;
function _stopDashQrPoll() {
  _dashQrToken++;
  clearTimeout(_dashQrTimer);
  _dashQrTimer = null;
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

    _dashSetPhoneStatus(phoneId, status);
    _dashShowPanel(phoneId, status !== 'WORKING');

    if (status === 'WORKING') {
      // Connected: the phone card already shows it, so the panel is hidden
      if (_dashWahaPrevStatus !== 'WORKING') { box.innerHTML = ''; label.innerHTML = ''; actions.innerHTML = ''; }

    } else if (status === 'SCAN_QR_CODE') {
      if (_dashWahaPrevStatus !== 'SCAN_QR_CODE') {
        box.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%">
          <div style="width:28px;height:28px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite"></div>
        </div>`;
        label.innerHTML = `Loading QR code…`;
        actions.innerHTML = isAdmin() ? `<button class="btn btn-secondary btn-sm" id="dash-btn-restart">Reconnect</button>` : '';
        _bindDashWahaButtons(phoneId);
      }
      try {
        const qrData = await Api.phones.qr(phoneId);
        const boxNow = document.getElementById('dash-waha-box');
        const lblNow = document.getElementById('dash-waha-label');
        if (qrData && qrData.qr && boxNow) {
          boxNow.innerHTML = `<img src="${safeImgSrc(qrData.qr)}" style="width:100%;height:100%;display:block;object-fit:contain;" alt="WhatsApp QR">`;
          if (lblNow) lblNow.innerHTML = `Scan to connect WhatsApp<br><span style="font-size:11px;color:var(--text-3)">Settings → Linked Devices → Link a Device</span>`;
        }
      } catch(_) {}

    } else if (status === 'STARTING') {
      if (_dashWahaPrevStatus !== 'STARTING') {
        box.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:.75rem;padding:1.5rem 0">
          <div style="width:40px;height:40px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite"></div>
          <span style="font-size:12px;color:var(--text-3)">Connecting…</span>
        </div>`;
        label.innerHTML = `Starting WhatsApp session`;
        actions.innerHTML = ``;
      }

    } else if (status === 'STOPPED') {
      if (_dashWahaPrevStatus !== 'STOPPED') {
        box.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:.5rem;padding:1.5rem 0">
          <div style="width:64px;height:64px;border-radius:50%;background:var(--danger-bg);display:flex;align-items:center;justify-content:center">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
          </div>
          <span style="font-size:12px;font-weight:600;color:#dc2626;background:var(--danger-bg);padding:.25rem .75rem;border-radius:20px">Disconnected</span>
        </div>`;
        label.innerHTML = `Session is stopped`;
        actions.innerHTML = isAdmin() ? `<button class="btn btn-primary btn-sm" id="dash-btn-start">Scan QR to Connect</button>` : '';
        _bindDashWahaButtons(phoneId);
      }

    } else {
      if (_dashWahaPrevStatus !== status) {
        box.innerHTML = `<div style="display:flex;flex-direction:column;align-items:center;gap:.5rem;padding:1.5rem 0">
          <div style="width:64px;height:64px;border-radius:50%;background:var(--surface-3);display:flex;align-items:center;justify-content:center">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          </div>
          <span style="font-size:12px;color:var(--text-3)">${esc(status)}</span>
        </div>`;
        label.innerHTML = `Unknown state`;
        actions.innerHTML = isAdmin() ? `<button class="btn btn-secondary btn-sm" id="dash-btn-start">Start Session</button>` : '';
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
  _dashShowPanel(phoneId, true);
  if (box) box.innerHTML = `<div style="width:32px;height:32px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite"></div>`;
  if (lbl) lbl.innerHTML = 'Clearing session…';
  if (act) act.innerHTML = '';

  _stopDashQrPoll();
  const token = _dashQrToken;
  const cancelled = () => token !== _dashQrToken || !document.getElementById('dash-waha-box');

  await Api.phones.logout(phoneId).catch(() => {});
  await new Promise(r => setTimeout(r, 1500));
  if (cancelled()) return;
  await Api.phones.start(phoneId).catch(() => {});
  await new Promise(r => setTimeout(r, 1500));
  if (cancelled()) return;

  if (lbl) lbl.innerHTML = 'Loading QR…';

  let attempts = 0;
  async function pollDashQR() {
    if (cancelled()) return; // cancelled or navigated away
    try {
      const s = await Api.phones.status(phoneId);
      if (cancelled()) return;
      const status = (s.status || '').toUpperCase();
      if (status === 'WORKING') {
        _stopDashQrPoll();
        await Api.phones.syncNumber(phoneId).catch(() => {});
        toast('WhatsApp connected! Syncing chats…', 'success');
        // Sync chats from WAHA then refresh chat list
        _chatAutoSynced = false;
        try { await Api.inbox.sync(phoneId); } catch(_) {}
        await loadPhones();
        if (State.currentView === 'inbox') loadChats();
        _dashRenderPhoneCards(State.phones);
        _dashWahaPrevStatus = '';
        _startDashWahaPoller(phoneId);
        return;
      }
      const r = await Api.phones.qr(phoneId);
      if (cancelled()) return;
      if (r && r.qr) {
        const b = document.getElementById('dash-waha-box');
        const l = document.getElementById('dash-waha-label');
        const a = document.getElementById('dash-waha-actions');
        if (b) b.innerHTML = `<img src="${safeImgSrc(r.qr)}" style="width:100%;height:100%;object-fit:contain;display:block" alt="QR">`;
        if (l) l.innerHTML = `Scan with WhatsApp<br><span style="font-size:11px;color:var(--text-3)">Settings → Linked Devices → Link a Device</span>`;
        if (a) {
          a.innerHTML = `<button class="btn btn-danger btn-sm" id="dash-btn-cancel-qr">Cancel</button>`;
          document.getElementById('dash-btn-cancel-qr')?.addEventListener('click', () => {
            _stopDashQrPoll();
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
    if (attempts < 30 && !cancelled()) _dashQrTimer = setTimeout(pollDashQR, 5000);
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
  _stopDashWahaPoller();
  _stopDashQrPoll();
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
  _stopDashQrPoll();
  const main = document.getElementById('main-content');
  if (!State.org) await loadOrg();
  const org = State.org || { name: 'Hyperscope', uid: '' };
  const admin = isAdmin();

  main.innerHTML = `
  <div class="dsh-wrap">
    <div class="dsh-inner">
      <header class="dsh-head">
        <div class="dsh-ws-avatar" aria-hidden="true">${esc(wsInitial(org.name))}</div>
        <div class="dsh-ws-meta">
          <h1 class="dsh-ws-name">${esc(org.name)}</h1>
          ${org.uid ? `<button class="dsh-ws-uid" id="dsh-ws-uid" title="Copy workspace ID">${esc(org.uid)}</button>` : ''}
        </div>
      </header>

      <div class="dsh-grid">
        <div class="dsh-main">
          <div class="dsh-stats">
            <div class="dsh-card dsh-stat">
              <div class="dsh-stat-label">${_DSH_ICONS.chat}All chats</div>
              <div class="dsh-stat-num" id="ds-total">—</div>
            </div>
            <div class="dsh-card dsh-stat">
              <div class="dsh-stat-label">${_DSH_ICONS.unread}Unread chats</div>
              <div class="dsh-stat-num" id="ds-unread">—</div>
            </div>
            <div class="dsh-card dsh-stat dsh-stat-flagged">
              <div class="dsh-stat-label">${_DSH_ICONS.flag}Flagged chats</div>
              <div class="dsh-stat-num" id="ds-flagged">—</div>
            </div>
          </div>

          <div class="dsh-duo">
            <section class="dsh-card dsh-panel">
              <div class="dsh-panel-head">${_DSH_ICONS.team}Team</div>
              <div class="dsh-panel-body">
                <div class="dsh-muted" id="ds-online">—</div>
                <div class="dsh-avatars" id="ds-team-avatars"></div>
              </div>
            </section>
            <section class="dsh-card dsh-panel">
              <div class="dsh-panel-head">${_DSH_ICONS.ticket}Tickets</div>
              <div class="dsh-panel-body dsh-ticket-cols">
                <button class="dsh-ticket-col" data-dsh-go="tickets">
                  <span class="dsh-ticket-label"><span class="dsh-ring" aria-hidden="true"></span>Open</span>
                  <span class="dsh-ticket-num" id="ds-tickets">—</span>
                </button>
                <button class="dsh-ticket-col" data-dsh-go="tickets">
                  <span class="dsh-ticket-label">${_DSH_ICONS.userCircle}Assigned to me</span>
                  <span class="dsh-ticket-num" id="ds-tickets-mine">—</span>
                </button>
              </div>
            </section>
          </div>

          <h2 class="dsh-section-title">Quick links</h2>
          <div class="dsh-links">
            ${_dashQuickLinks().map((l, i) => `
              <div class="dsh-link">
                <button class="dsh-link-title" data-dsh-link="${i}" data-dsh-act="0" ${l.actions[0].disabled ? 'disabled' : ''}>
                  ${l.icon}<span>${esc(l.title)}</span>${_DSH_ICONS.arrow}
                </button>
                <p>${esc(l.desc)}</p>
                <div class="dsh-link-actions">
                  ${l.actions.map((a, j) => `<button class="dsh-btn" data-dsh-link="${i}" data-dsh-act="${j}"
                      ${a.disabled ? `disabled title="${esc(a.why || 'Not available')}"` : ''}>${esc(a.label)}</button>`).join('')}
                </div>
              </div>`).join('')}
          </div>
        </div>

        <aside class="dsh-side">
          <div class="dsh-side-head">
            <h2 class="dsh-section-title">Phone status</h2>
            ${admin ? `<button class="dsh-btn" id="dsh-add-phone">Add phone ${_DSH_ICONS.phone}</button>` : ''}
          </div>
          <div class="dsh-phones" id="dash-phone-cards">
            <div class="dsh-card dsh-phone dsh-muted">Loading phones…</div>
          </div>
          <div class="dsh-card dsh-connect" id="dash-waha-panel" hidden>
            <div class="dsh-connect-title" id="dash-waha-title"></div>
            <div class="gs-qr-box dsh-qr" id="dash-waha-box"></div>
            <div class="dsh-connect-label" id="dash-waha-label"></div>
            <div class="dsh-connect-actions" id="dash-waha-actions"></div>
          </div>
        </aside>
      </div>
    </div>
  </div>`;

  _bindDashboard();

  let sum;
  try { sum = await Api.analytics.summary(); }
  catch (e) { toast(e.message || 'Could not load dashboard', 'error'); return; }
  if (State.currentView !== 'dashboard' || !document.getElementById('ds-total')) return;

  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  const phones = sum.phones || [];
  // Match the inbox: chats are hidden while no number is connected
  const phoneConnected = phones.some(p => p.waha_status === 'WORKING');
  set('ds-total', phoneConnected ? (sum.chats?.total ?? 0) : 0);
  set('ds-unread', phoneConnected ? (sum.chats?.unread ?? 0) : 0);
  set('ds-flagged', phoneConnected ? (sum.chats?.flagged ?? 0) : 0);
  set('ds-tickets', sum.tickets?.open ?? 0);
  set('ds-tickets-mine', sum.tickets?.assigned_to_me ?? 0);

  const online = sum.team?.online || [];
  set('ds-online', `${online.length} of ${sum.team?.total ?? 0} online`);
  const avEl = document.getElementById('ds-team-avatars');
  if (avEl) avEl.innerHTML = online.slice(0, 12).map(a => `
    <span class="dsh-avatar" title="${esc(a.name)}" style="background:${esc(a.avatar_color || avatarColor(a.name))}">
      ${esc(initials(a.name))}<span class="dsh-online-dot" aria-label="online"></span>
    </span>`).join('') + (online.length > 12 ? `<span class="dsh-muted">+${online.length - 12}</span>` : '');

  State.phones = phones;
  updatePhoneBadge();
  _dashRenderPhoneCards(phones);

  // Live connection panel for the first number that needs attention
  const target = phones.find(p => p.waha_status !== 'WORKING') || phones[0];
  if (target) _startDashWahaPoller(target.id);
}

const _DSH_ICONS = {
  chat:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
  unread: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12v3a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h9"/><circle cx="19" cy="5" r="3"/></svg>`,
  flag:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></svg>`,
  team:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
  ticket: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M15 5v2m0 4v2m0 4v2M5 5a2 2 0 0 0-2 2v3a2 2 0 1 1 0 4v3a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3a2 2 0 1 1 0-4V7a2 2 0 0 0-2-2z"/></svg>`,
  userCircle: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="10" r="3"/><path d="M6.17 18.34a7 7 0 0 1 11.66 0"/></svg>`,
  arrow:  `<svg class="dsh-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></svg>`,
  phone:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 12 19.79 19.79 0 0 1 1.93 3.35 2 2 0 0 1 3.98 1h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 8.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg>`,
  send:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>`,
  plug:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22v-5"/><path d="M9 8V2M15 8V2"/><path d="M18 8v5a6 6 0 0 1-12 0V8z"/></svg>`,
  code:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>`,
  more:   `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>`,
};

// Navigate to a view and, once it has rendered, click one of its buttons
function _dashGo(view, clickId) {
  navigateTo(view);
  if (!clickId) return;
  const started = Date.now();
  (function waitFor() {
    const b = document.getElementById(clickId);
    if (b) return b.click();
    if (State.currentView === view && Date.now() - started < 5000) setTimeout(waitFor, 100);
  })();
}

function _dashGoSettings(tab) {
  navigateTo('settings');
  if (tab && tab !== 'phones') document.querySelector(`#settings-tabs .tab[data-tab="${tab}"]`)?.click();
}

function _dashAddPhone() {
  if (!isAdmin()) return toast('Only admins can add phones', 'error');
  // The add-phone modal continues into Settings → WhatsApp to show the QR
  navigateTo('settings');
  showAddPhoneModal();
}

function _dashQuickLinks() {
  const admin = isAdmin();
  const adminOnly = 'Only admins can do this';
  return [
    { icon: _DSH_ICONS.send, title: 'Bulk messages',
      desc: 'Send personalised broadcasts to many contacts at once and track delivery.',
      actions: [{ label: 'Open', fn: () => _dashGo('bulk') },
                { label: 'New campaign', fn: () => _dashGo('bulk', 'new-bulk-btn') }] },
    { icon: _DSH_ICONS.team, title: 'Manage team',
      desc: 'Invite agents, set roles and choose which numbers each agent can use.',
      actions: [{ label: 'Open', fn: () => _dashGoSettings('agents') },
                { label: 'Invite', fn: openInviteTeam, disabled: !admin, why: adminOnly }] },
    { icon: _DSH_ICONS.phone, title: 'Add phones',
      desc: 'Connect more WhatsApp numbers and manage their sessions.',
      actions: [{ label: 'Open', fn: () => _dashGoSettings('phones') },
                { label: 'Add phone', fn: _dashAddPhone, disabled: !admin, why: adminOnly }] },
    { icon: _DSH_ICONS.ticket, title: 'Manage tickets',
      desc: 'Track customer issues raised from chats through to resolution.',
      actions: [{ label: 'Open', fn: () => _dashGo('tickets') },
                { label: 'New ticket', fn: () => _dashGo('tickets', 'new-ticket-btn') }] },
    { icon: _DSH_ICONS.plug, title: 'Integrate your tools',
      desc: 'Let the AI agent answer chats and automate routing with rules.',
      actions: [{ label: 'AI agent', fn: () => _dashGo('ai-agent') },
                { label: 'Automation', fn: () => _dashGo('automation') }] },
    { icon: _DSH_ICONS.code, title: 'APIs & Webhooks',
      desc: 'Programmatic access with API keys and outbound webhooks via the developer API.',
      actions: [{ label: 'API keys', disabled: true, why: 'Not available in the app yet' },
                { label: 'Webhooks', disabled: true, why: 'Not available in the app yet' }] },
  ];
}

function _bindDashboard() {
  const root = document.querySelector('.dsh-wrap');
  if (!root) return;
  const links = _dashQuickLinks();
  root.addEventListener('click', e => {
    const linkBtn = e.target.closest('[data-dsh-link]');
    if (linkBtn && !linkBtn.disabled) {
      const a = links[+linkBtn.dataset.dshLink]?.actions[+linkBtn.dataset.dshAct];
      if (a && !a.disabled && a.fn) a.fn();
      return;
    }
    const go = e.target.closest('[data-dsh-go]');
    if (go) return navigateTo(go.dataset.dshGo);
  });
  document.getElementById('dsh-add-phone')?.addEventListener('click', _dashAddPhone);
  document.getElementById('dsh-ws-uid')?.addEventListener('click', async () => {
    const uid = State.org?.uid;
    if (!uid) return;
    try { await navigator.clipboard.writeText(uid); toast('Workspace ID copied', 'success'); }
    catch (_) { toast(uid); }
  });
}

// "919510715498" → "+91 95107 15498"
function _dashFmtPhone(num) {
  const d = String(num || '').replace(/\D/g, '');
  if (!d || String(num).startsWith('pending')) return '';
  if (d.length === 12 && d.startsWith('91')) return `+91 ${d.slice(2, 7)} ${d.slice(7)}`;
  if (d.length === 11 && d.startsWith('1')) return `+1 ${d.slice(1, 4)} ${d.slice(4, 7)} ${d.slice(7)}`;
  return '+' + d;
}

function _dashStatusInfo(status) {
  const s = String(status || '').toUpperCase();
  if (s === 'WORKING') return { cls: 'ok', label: 'Connected' };
  if (s === 'STARTING') return { cls: 'wait', label: 'Connecting' };
  if (s === 'SCAN_QR_CODE') return { cls: 'wait', label: 'Waiting for QR scan' };
  return { cls: 'off', label: 'Disconnected' };
}

function _dashRenderPhoneCards(phones) {
  const el = document.getElementById('dash-phone-cards');
  if (!el) return;
  const admin = isAdmin();
  if (!phones.length) {
    el.innerHTML = `<div class="dsh-card dsh-phone dsh-phone-empty">
      <span class="dsh-muted">No WhatsApp number connected yet.</span>
      ${admin ? `<button class="dsh-btn" id="dsh-empty-add">Add phone</button>` : ''}
    </div>`;
    document.getElementById('dsh-empty-add')?.addEventListener('click', _dashAddPhone);
    return;
  }
  el.innerHTML = phones.map(p => {
    const st = _dashStatusInfo(p.waha_status);
    const number = _dashFmtPhone(p.phone_number);
    return `<div class="dsh-card dsh-phone" data-pid="${p.id}">
      <div class="dsh-phone-avatar" style="background:${avatarColor(p.name)}">${esc(initials(p.name))}</div>
      <div class="dsh-phone-meta">
        <div class="dsh-phone-num">${number ? esc(number) : '<span class="dsh-muted">Pending connection</span>'}</div>
        <div class="dsh-phone-name">${esc(p.name)}</div>
      </div>
      <div class="dsh-phone-status">
        <span class="dsh-status-dot ${st.cls}" title="${esc(st.label)}" aria-label="${esc(st.label)}"></span>
        ${admin ? `<button class="dsh-restart" data-dsh-restart="${p.id}">Restart</button>` : ''}
      </div>
      ${admin ? `<div class="dsh-menu-wrap">
        <button class="dsh-menu-btn" data-dsh-menu="${p.id}" aria-haspopup="menu" aria-expanded="false" title="More actions">${_DSH_ICONS.more}</button>
        <div class="dsh-menu" role="menu" hidden>
          <button role="menuitem" data-dsh-phone-act="qr">${p.waha_status === 'WORKING' ? 'Reconnect / QR' : 'Scan QR to connect'}</button>
          <button role="menuitem" data-dsh-phone-act="logout">Log out</button>
          <button role="menuitem" class="danger" data-dsh-phone-act="clear">Clear data</button>
          <button role="menuitem" class="danger" data-dsh-phone-act="delete">Delete</button>
        </div>
      </div>` : ''}
    </div>`;
  }).join('');

  el.querySelectorAll('[data-dsh-restart]').forEach(btn => btn.addEventListener('click', async () => {
    const pid = +btn.dataset.dshRestart;
    btn.disabled = true; btn.textContent = 'Restarting…';
    try { await Api.phones.restart(pid); toast('Session restarting…', 'success'); }
    catch (e) { toast(e.message, 'error'); }
    _dashSetPhoneStatus(pid, 'STARTING');
    setTimeout(() => { if (State.currentView === 'dashboard') _startDashWahaPoller(pid); }, 2000);
  }));

  el.querySelectorAll('[data-dsh-menu]').forEach(btn => btn.addEventListener('click', e => {
    e.stopPropagation();
    const menu = btn.nextElementSibling;
    const open = menu.hidden;
    _dashCloseMenus();
    menu.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (open) menu.querySelector('button')?.focus();
  }));

  el.querySelectorAll('[data-dsh-phone-act]').forEach(item => item.addEventListener('click', () => {
    const pid = +item.closest('[data-pid]').dataset.pid;
    _dashCloseMenus();
    _dashPhoneAction(pid, item.dataset.dshPhoneAct);
  }));
}

function _dashCloseMenus() {
  document.querySelectorAll('.dsh-menu').forEach(m => { m.hidden = true; });
  document.querySelectorAll('[data-dsh-menu]').forEach(b => b.setAttribute('aria-expanded', 'false'));
}
document.addEventListener('click', e => { if (!e.target.closest?.('.dsh-menu-wrap')) _dashCloseMenus(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') _dashCloseMenus(); });

async function _dashPhoneAction(pid, act) {
  const phone = State.phones.find(p => p.id === pid);
  const reload = () => (State.currentView === 'dashboard' ? renderDashboard() : loadPhones());
  try {
    if (act === 'qr') {
      if (phone?.waha_status === 'WORKING' &&
          !confirm('Reconnect this number? The current session will be logged out and a new QR code shown.')) return;
      _stopDashWahaPoller();
      await _dashShowQR(pid);
    } else if (act === 'logout') {
      if (!confirm('Disconnect WhatsApp? You will need to scan QR again to reconnect.')) return;
      await Api.phones.logout(pid);
      toast('Disconnected — scan QR to reconnect', 'success');
      await reload();
    } else if (act === 'clear') {
      if (!confirm('WARNING: This will permanently delete all synced chats, messages, and associated tasks/tickets for this phone from the database. Proceed?')) return;
      await Api.phones.clearData(pid);
      toast('Data cleared successfully!', 'success');
      await reload();
    } else if (act === 'delete') {
      if (!confirm('Remove this phone session from Hyperscope? This will deactivate the session.')) return;
      await Api.phones.del(pid);
      toast('Phone session removed', 'success');
      await reload();
    }
  } catch (e) { toast(e.message, 'error'); }
}

// Keep a phone card's status dot in sync with the live poller
function _dashSetPhoneStatus(pid, status) {
  const ph = State.phones.find(p => p.id === pid);
  if (ph && ph.waha_status !== status && status !== 'UNKNOWN') { ph.waha_status = status; updatePhoneBadge(); }
  const dot = document.querySelector(`.dsh-phone[data-pid="${pid}"] .dsh-status-dot`);
  if (!dot) return;
  const st = _dashStatusInfo(status);
  dot.className = `dsh-status-dot ${st.cls}`;
  dot.title = st.label;
  dot.setAttribute('aria-label', st.label);
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
        ${a.daily_volume.map(d => `<div title="${esc(d.date)}: ${+d.count || 0}" style="flex:1;background:var(--accent);opacity:.75;border-radius:2px 2px 0 0;height:${Math.max(4, Math.round(d.count / maxDay * 60))}px"></div>`).join('') || '<span class="text-muted">No activity</span>'}
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
        <td style="font-size:12px">${parseServerDate(m.send_at).toLocaleString()}</td>
        <td style="font-size:12px">${esc(m.repeat_summary || (m.repeat === 'none' ? 'Once' : m.repeat))}</td>
        <td style="font-size:12px;color:var(--text-3)">${m.end_date ? parseServerDate(m.end_date).toLocaleDateString() : (m.repeat !== 'none' ? 'Open-ended' : '—')}</td>
        <td><span class="${pillClass(m.status==='sent'?'resolved':m.status==='failed'?'urgent':'open')}">${esc(m.status)}</span>${m.last_error ? ` <span title="${esc(m.last_error)}">⚠️</span>` : ''}</td>
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
    const d = parseServerDate(iso);
    if (isNaN(d.getTime())) return '';
    return new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0, 16);
  };
  const toLocalDateOnly = (iso) => {
    if (!iso) return '';
    const d = parseServerDate(iso);
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
          ${isAdmin() ? '<button class="btn btn-secondary btn-sm" id="log-export" style="height:30px;padding:4px 12px;font-size:12.5px">Export CSV</button>' : ''}
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

  document.getElementById('log-export')?.addEventListener('click', async () => {
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
        <td style="font-size:12.5px;color:var(--text-3);white-space:nowrap;vertical-align:top;padding-top:10px">${parseServerDate(l.created_at).toLocaleString()}</td>
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
            if (!l) return '';
            const lc = safeColor(l.color);
            return `<span class="chat-label-mini" style="background:${lc}22;color:${lc};border:1px solid ${lc}44">${esc(l.name)}</span>`;
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
    const csv = [header.join(',')].concat(picked.map(c => csvRow([
      c.id,
      displayName(c),
      c.is_group ? 'group' : 'user',
      (c.labels || []).map(id => State.labels.find(l => l.id === id)?.name || id).join('; '),
      c.unread_count || 0,
      c.is_flagged ? 'yes' : 'no',
      c.last_message_at || '',
    ]))).join('\n');
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
              ${t.due_date ? ' · due ' + parseServerDate(t.due_date).toLocaleDateString() : ''}
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
    // Full task modal (due date, reminder, assignee, priority, notes); refresh the panel once saved
    showTaskModal({ onSaved: () => { if (panel.style.display !== 'none') loadTasks(); } });
  });
})();

// ══ NOTIFICATION SETTINGS (topbar bell) ═══════════════════════════ //
// Per-agent prefs live on the server (GET/PUT /auth/me/notification-prefs);
// localStorage only caches them so the popover and early WS events don't wait.
const NotifPrefs = {
  DEFAULTS: {
    in_app: true, desktop: false, sound: false,
    types: {
      new_messages: true, new_note: true, ticket_assign: true, task_assign: true,
      chat_assign: true, ticket_overdue: true, task_overdue: true,
    },
  },
  LEGACY_KEY: 'notif_prefs',   // pre-server flat format: {desktop, sound, new_messages, ...}
  _prefs: null,
  _prefsFor: null,             // agent id _prefs belongs to
  _loadedFor: null,            // agent id the server prefs were fetched for
  _loading: null,

  _cacheKey() { return `notif_prefs_v2:${State.agent?.id || 0}`; },
  _normalize(p) {
    const d = this.DEFAULTS;
    const out = { ...d, types: { ...d.types } };
    if (!p || typeof p !== 'object') return out;
    ['in_app', 'desktop', 'sound'].forEach(k => { if (typeof p[k] === 'boolean') out[k] = p[k]; });
    const t = p.types && typeof p.types === 'object' ? p.types : {};
    Object.keys(d.types).forEach(k => { if (typeof t[k] === 'boolean') out.types[k] = t[k]; });
    return out;
  },
  _writeCache(p) {
    try { localStorage.setItem(this._cacheKey(), JSON.stringify(p)); } catch (_) {}
  },
  // Old flat localStorage prefs → new shape (null when there are none)
  _readLegacy() {
    let old = null;
    try { old = JSON.parse(localStorage.getItem(this.LEGACY_KEY)); } catch (_) {}
    if (!old || typeof old !== 'object') return null;
    const patch = { types: {} };
    ['desktop', 'sound'].forEach(k => { if (typeof old[k] === 'boolean') patch[k] = old[k]; });
    Object.keys(this.DEFAULTS.types).forEach(k => { if (typeof old[k] === 'boolean') patch.types[k] = old[k]; });
    return patch;
  },

  get() {
    const id = State.agent?.id || 0;
    if (!this._prefs || this._prefsFor !== id) {   // first use, or a different agent logged in
      let cached = null;
      try { cached = JSON.parse(localStorage.getItem(this._cacheKey())); } catch (_) {}
      this._prefs = this._normalize(cached || this._readLegacy());
      this._prefsFor = id;
    }
    return this._prefs;
  },
  isLoaded() { return !!State.agent && this._loadedFor === State.agent.id; },

  // Fetch from the server (once per logged-in agent); migrates legacy local prefs.
  load() {
    if (!State.agent || !Api.getToken?.()) return Promise.resolve(this.get());
    if (this.isLoaded()) return Promise.resolve(this._prefs);
    if (this._loading) return this._loading;
    const agentId = State.agent.id;
    this._loading = (async () => {
      try {
        let server = await Api.auth.notificationPrefs();
        const legacy = this._readLegacy();
        if (legacy) {
          server = await Api.auth.saveNotificationPrefs(legacy);
          try { localStorage.removeItem(this.LEGACY_KEY); } catch (_) {}
        }
        if (State.agent?.id === agentId) {
          this._prefs = this._normalize(server);
          this._prefsFor = this._loadedFor = agentId;
          this._writeCache(this._prefs);
        }
      } catch (_) {
        // Offline / server error: keep the cached copy, retry on next call
      } finally {
        this._loading = null;
      }
      return this.get();
    })();
    return this._loading;
  },

  // Optimistic update; reverts (and rethrows) if the server rejects it.
  async save(patch) {
    const before = this.get();
    const next = this._normalize({ ...before, ...patch, types: { ...before.types, ...(patch.types || {}) } });
    this._prefs = next;
    this._writeCache(next);
    try {
      const saved = await Api.auth.saveNotificationPrefs(patch);
      this._prefs = this._normalize(saved);
      this._writeCache(this._prefs);
      return this._prefs;
    } catch (e) {
      this._prefs = before;
      this._writeCache(before);
      throw e;
    }
  },
};

function loadNotifPrefs() { return NotifPrefs.load(); }

// One shared AudioContext (browsers cap how many a page may create)
const NotifSound = {
  _ctx: null,
  play() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      if (!this._ctx) this._ctx = new Ctx();
      const ctx = this._ctx;
      if (ctx.state === 'suspended') ctx.resume().catch(() => {});
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.setValueAtTime(1175, ctx.currentTime + 0.09);
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.06, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.24);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(); osc.stop(ctx.currentTime + 0.25);
    } catch (_) {}
  },
};

(() => {
  const bell = document.getElementById('topbar-bell');
  const pop = document.getElementById('notif-popover');
  if (!bell || !pop) return;

  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Notification settings');
  bell.setAttribute('aria-haspopup', 'dialog');
  bell.setAttribute('aria-controls', 'notif-popover');
  bell.setAttribute('aria-expanded', 'false');

  const SETTINGS = [
    ['in_app', 'In-App Notifications'],
    ['desktop', 'Desktop Notifications'],
    ['sound', 'Sound'],
  ];
  const TYPES = [
    ['new_messages', 'New Messages'],
    ['new_note', 'New Private Note'],
    ['ticket_assign', 'Ticket Assignment'],
    ['task_assign', 'Task Assignment'],
    ['chat_assign', 'Chat Assignment'],
    ['ticket_overdue', 'Ticket Overdue'],
    ['task_overdue', 'Task Overdue'],
  ];

  const isOpen = () => pop.style.display !== 'none';

  function render() {
    const p = NotifPrefs.get();
    pop.innerHTML = `
      <div class="np-section" role="group" aria-labelledby="np-h-settings">
        <div class="np-heading" id="np-h-settings">Notification Settings</div>
        ${SETTINGS.map(([k, label]) => `
          <label class="np-row">
            <span class="np-label">${label}</span>
            <span class="np-toggle">
              <input type="checkbox" role="switch" data-setting="${k}" ${p[k] ? 'checked' : ''}>
              <span class="np-track" aria-hidden="true"></span>
            </span>
          </label>`).join('')}
      </div>
      <div class="np-divider" role="separator"></div>
      <div class="np-section" role="group" aria-labelledby="np-h-types">
        <div class="np-heading" id="np-h-types">Notification Types</div>
        ${TYPES.map(([k, label]) => `
          <label class="np-row">
            <span class="np-label">${label}</span>
            <input type="checkbox" class="np-checkbox" data-type="${k}" ${p.types[k] ? 'checked' : ''}>
          </label>`).join('')}
      </div>`;

    pop.querySelectorAll('input[data-setting]').forEach(inp =>
      inp.addEventListener('change', () => onSettingChange(inp)));
    pop.querySelectorAll('input[data-type]').forEach(inp =>
      inp.addEventListener('change', () => persist({ types: { [inp.dataset.type]: inp.checked } }, inp)));
  }

  async function persist(patch, inp) {
    try {
      await NotifPrefs.save(patch);
    } catch (e) {
      if (inp) inp.checked = !inp.checked;
      toast(`Couldn't save notification settings: ${e.message || e}`, 'error');
    }
  }

  async function onSettingChange(inp) {
    const key = inp.dataset.setting;
    if (key === 'desktop' && inp.checked) {
      const reason = await ensureDesktopPermission();
      if (reason) {
        inp.checked = false;
        toast(reason, 'error');
        return;
      }
    }
    await persist({ [key]: inp.checked }, inp);
    if (key === 'sound' && inp.checked) NotifSound.play();   // preview + unlocks audio via this gesture
  }

  // Returns an error message, or '' when desktop notifications may be shown.
  async function ensureDesktopPermission() {
    if (!('Notification' in window)) return 'This browser does not support desktop notifications';
    if (Notification.permission === 'granted') return '';
    if (Notification.permission === 'denied') {
      return 'Desktop notifications are blocked. Allow them for this site in your browser settings.';
    }
    try {
      const result = await Notification.requestPermission();
      return result === 'granted' ? '' : 'Desktop notification permission was not granted';
    } catch (_) {
      return 'Could not request desktop notification permission';
    }
  }

  function open() {
    pop.style.display = 'block';
    bell.setAttribute('aria-expanded', 'true');
    render();
    pop.querySelector('input')?.focus();
    // Refresh from the server; re-render only if it changed while open
    const shown = JSON.stringify(NotifPrefs.get());
    loadNotifPrefs().then(p => {
      if (isOpen() && JSON.stringify(p) !== shown) render();
    });
  }
  function close(returnFocus) {
    if (!isOpen()) return;
    pop.style.display = 'none';
    bell.setAttribute('aria-expanded', 'false');
    if (returnFocus) bell.focus();
  }

  bell.addEventListener('click', e => {
    e.stopPropagation();
    isOpen() ? close(false) : open();
  });
  pop.addEventListener('click', e => e.stopPropagation());
  document.addEventListener('click', e => {
    if (isOpen() && !pop.contains(e.target) && !bell.contains(e.target)) close(false);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && isOpen()) { e.stopPropagation(); close(true); }
  });
})();

// Single entry point for every notification-worthy WS event.
// type: a NotifPrefs.DEFAULTS.types key. opts.toast=false skips the in-app
// toast (e.g. new message in the chat already on screen).
function notifyUser(type, title, bodyText = '', opts = {}) {
  if (!NotifPrefs.isLoaded()) loadNotifPrefs();   // lazy; this event uses the cached copy
  const p = NotifPrefs.get();
  if (p.types[type] === false) return;
  const body = String(bodyText || '');

  if (p.in_app && opts.toast !== false) {
    toast(opts.toastText || (body ? `${title}: ${body.slice(0, 80)}` : title));
  }
  if (p.desktop && 'Notification' in window && Notification.permission === 'granted'
      && (document.hidden || !document.hasFocus())) {
    try {
      const n = new Notification(title, { body: body.slice(0, 140), tag: opts.tag || undefined });
      n.onclick = () => { window.focus(); n.close(); };
    } catch (_) {}
  }
  if (p.sound) NotifSound.play();
}

// ══ ASK AI (Org & Chat Assistant) ═════════════════════════════════ //
(() => {
  const fab = document.getElementById('topbar-ask-ai');
  const panel = document.getElementById('ai-panel');
  if (!fab || !panel) return;
  const body = document.getElementById('ai-panel-body');
  const input = document.getElementById('ai-panel-q');
  const scopeChip = document.getElementById('ai-scope');

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
