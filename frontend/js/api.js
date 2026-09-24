/* ── Hyperscope API Client ──────────────────────────────────────── */
const BASE = '/api/v1';
const FORBIDDEN_MSG = "You don't have permission to do this — ask an admin.";

const Api = (() => {
  let _token = localStorage.getItem('token') || null;

  function setToken(t) { _token = t; localStorage.setItem('token', t); }
  function clearToken() { _token = null; localStorage.removeItem('token'); }
  function getToken() { return _token; }

  function headers(extra = {}) {
    const h = { 'Content-Type': 'application/json', ...extra };
    if (_token) h['Authorization'] = 'Bearer ' + _token;
    return h;
  }

  async function req(method, path, body, opts = {}) {
    const r = await fetch(BASE + path, {
      method,
      headers: headers(opts.headers || {}),
      body: body != null ? JSON.stringify(body) : undefined,
    });
    if (r.status === 401 && _token) { clearToken(); window.location.reload(); return; }
    if (r.status === 403) {
      const err = new Error(FORBIDDEN_MSG);
      err.status = 403;
      throw err;
    }
    if (!r.ok) {
      let msg = 'Request failed';
      try {
        const e = await r.json();
        if (Array.isArray(e.detail))
          msg = e.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
        else if (e.detail && typeof e.detail === 'string')
          msg = e.detail;
        else if (e.message)
          msg = e.message;
        else
          msg = JSON.stringify(e);
      } catch(_) {}
      throw new Error(msg);
    }
    if (r.status === 204) return null;
    return r.json();
  }

  const get  = (p, q)    => req('GET', p + (q ? '?' + new URLSearchParams(q) : ''));
  const post = (p, b)    => req('POST', p, b);
  const patch = (p, b)   => req('PATCH', p, b);
  const del  = (p)       => req('DELETE', p);

  // Auth
  const auth = {
    login:    (email, password) => post('/auth/login', { email, password }),
    me:       ()                => get('/auth/me'),
    agents:   ()                => get('/auth/agents'),
    register: (data)            => post('/auth/register', data),
    agentPhones:    (id)        => get(`/auth/agents/${id}/phones`),
    setAgentPhones: (id, ids)   => req('PUT', `/auth/agents/${id}/phones`, ids),
    changePassword: (current_password, new_password) =>
      post('/auth/change-password', { current_password, new_password }),
    notificationPrefs:     ()      => get('/auth/me/notification-prefs'),
    saveNotificationPrefs: (prefs) => req('PUT', '/auth/me/notification-prefs', prefs),
  };

  // Organization (workspace identity for the sidebar switcher)
  const org = {
    get:    ()  => get('/org'),
    update: (b) => patch('/org', b),
  };

  // Inbox
  const inbox = {
    chats:      (q)     => get('/inbox/chats', q),
    updateChat: (id, b) => patch(`/inbox/chats/${id}`, b),
    markRead:   (id)    => post(`/inbox/chats/${id}/read`),
    messages:   (id, q) => get(`/inbox/chats/${id}/messages`, q),
    send:       (b)     => post('/inbox/send', b),
    addLabel:   (cid, lid)    => post(`/inbox/chats/${cid}/labels/${lid}`),
    removeLabel:(cid, lid)    => del(`/inbox/chats/${cid}/labels/${lid}`),
    sync:          (pid)  => post(`/inbox/sync/${pid}`),
    syncMessages:  (cid, limit) => post(`/inbox/chats/${cid}/sync-messages${limit ? '?limit=' + limit : ''}`),
    bulkUpdate:    (b)    => post('/inbox/bulk-update', b),
    getChat:       (id)   => get(`/inbox/chats/${id}`),
    activity:      (id)   => get(`/inbox/chats/${id}/activity`),
    picture:       (id)   => get(`/inbox/chats/${id}/picture`),
  };

  // Tickets
  const tickets = {
    list:   (q)     => get('/tickets', q),
    create: (b)     => post('/tickets', b),
    update: (id, b) => patch(`/tickets/${id}`, b),
    del:    (id)    => del(`/tickets/${id}`),
    addLabel:    (id, lid) => post(`/tickets/${id}/labels/${lid}`),
  };

  // Contacts
  const contacts = {
    list:   (q)     => get('/contacts', q),
    get:    (id)    => get(`/contacts/${id}`),
    create: (b)     => post('/contacts', b),
    update: (id, b) => patch(`/contacts/${id}`, b),
    del:    (id)    => del(`/contacts/${id}`),
  };

  // Labels
  const labels = {
    list:   ()      => get('/labels'),
    create: (b)     => post('/labels', b),
    update: (id, b) => patch(`/labels/${id}`, b),
    del:    (id)    => del(`/labels/${id}`),
  };

  // Notes
  const notes = {
    list:   (chatId) => get(`/notes/chat/${chatId}`),
    create: (b)      => post('/notes', b),
    del:    (id)     => del(`/notes/${id}`),
  };

  // Quick Replies
  const quickReplies = {
    list:   ()      => get('/quick-replies'),
    create: (b)     => post('/quick-replies', b),
    del:    (id)    => del(`/quick-replies/${id}`),
  };

  // Phones
  const phones = {
    list:       ()       => get('/phones'),
    update:     (id, b)  => req('PATCH', `/phones/${id}`, b),
    connect:    (name)   => post('/phones/connect', name ? { name } : {}),
    syncNumber: (id)     => post(`/phones/${id}/sync-number`, {}),
    status:     (id)     => get(`/phones/${id}/status`),
    qr:         (id)     => get(`/phones/${id}/qr`),
    start:      (id)     => post(`/phones/${id}/start`),
    stop:       (id)     => post(`/phones/${id}/stop`),
    restart:    (id)     => post(`/phones/${id}/restart`),
    logout:     (id)     => post(`/phones/${id}/logout`, {}),
    clearData:  (id)     => post(`/phones/${id}/clear-data`),
    del:        (id)     => del(`/phones/${id}`),
  };

  // Analytics — every page takes { from, to, tz, chat_id, phone_ids, agent_ids }
  const _clean = q => Object.fromEntries(Object.entries(q || {}).filter(([, v]) => v != null && v !== ''));
  const analytics = {
    team:     (q) => get('/analytics/team', _clean(q)),
    phones:   (q) => get('/analytics/phones', _clean(q)),
    chats:    (q) => get('/analytics/chats', _clean(q)),
    tickets:  (q) => get('/analytics/tickets', _clean(q)),
    messages: (q) => get('/analytics/messages', _clean(q)),
    members:  (q) => get('/analytics/members', _clean(q)),
    chatOptions: (search) => get('/analytics/chat-options', { q: search || '' }),
    // Dashboard home: chats/team/tickets/phones in one call (phone-scoped)
    summary:   ()     => get('/dashboard/summary'),
  };

  // Automation
  const automation = {
    triggers: ()      => get('/automation/trigger-types'),
    actions:  ()      => get('/automation/action-types'),
    list:     ()      => get('/automation/rules'),
    create:   (b)     => post('/automation/rules', b),
    update:   (id, b) => patch(`/automation/rules/${id}`, b),
    del:      (id)    => del(`/automation/rules/${id}`),
  };

  // Bulk
  const bulk = {
    list:    ()      => get('/bulk/jobs'),
    create:  (b)     => post('/bulk/jobs', b),
    send:    (id)    => post(`/bulk/jobs/${id}/send`),
    stop:    (id)    => post(`/bulk/jobs/${id}/stop`),
    logs:    (id)    => get(`/bulk/jobs/${id}/logs`),
    credits: ()      => get('/bulk/credits'),
    templates:      ()      => get('/bulk/templates'),
    createTemplate: (b)     => post('/bulk/templates', b),
    updateTemplate: (id, b) => patch(`/bulk/templates/${id}`, b),
    delTemplate:    (id)    => del(`/bulk/templates/${id}`),
    chatLists:      ()      => get('/bulk/chat-lists'),
    createChatList: (b)     => post('/bulk/chat-lists', b),
    updateChatList: (id, b) => patch(`/bulk/chat-lists/${id}`, b),
    delChatList:    (id)    => del(`/bulk/chat-lists/${id}`),
  };

  // AI
  const ai = {
    activate:      (chatId) => post(`/ai/chat/${chatId}/activate`),
    deactivate:    (chatId) => post(`/ai/chat/${chatId}/deactivate`),
    takeover:      (chatId) => post(`/ai/chat/${chatId}/takeover`),
    summarize:     (chatId) => post(`/ai/chat/${chatId}/summarize`),
    suggestReply:  (chatId) => post(`/ai/chat/${chatId}/suggest-reply`),
    translate:     (text, lang) => post('/ai/translate', { text, target_language: lang }),
    polish:        (text, tone) => post('/ai/polish', { text, tone: tone || 'professional' }),
    settings:      ()   => get('/ai/settings'),
    saveSettings:  (b)  => req('PUT', '/ai/settings', b),
    assistant:     (b)  => post('/ai/assistant', b),
  };

  // Activity logs
  const logs = {
    list:    (q) => get('/logs', q),
    actions: ()  => get('/logs/actions'),
  };

  // Groups
  const groups = {
    list:            (q)  => get('/groups', q),
    participants:    (id) => get(`/groups/${id}/participants`),
    analytics:       (id, days) => get(`/groups/${id}/analytics`, { days: days || 30 }),
    addParticipants: (b)  => post('/groups/add-participants', b),
    analyticsRange:  (id, r) => get(`/groups/${id}/analytics`, { from: r.from, to: r.to }),
  };

  // Media library (files are fetched with auth, so they come back as blobs)
  async function mediaBlob(id) {
    const r = await fetch(`${BASE}/media/${id}/file`, { headers: headers() });
    if (r.status === 401 && _token) { clearToken(); window.location.reload(); return; }
    if (r.status === 403) throw new Error(FORBIDDEN_MSG);
    if (!r.ok) throw new Error(r.status === 404 ? 'Media not available' : 'Could not load media');
    return r.blob();
  }
  const media = {
    list:     (q)  => get('/media', Object.fromEntries(Object.entries(q || {}).filter(([, v]) => v !== '' && v != null))),
    blob:     mediaBlob,
    download: async (id, filename) => {
      const blob = await mediaBlob(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename || 'file';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  };

  // Scheduled messages
  const scheduled = {
    list:   (q)     => get('/scheduled', q),
    create: (b)     => post('/scheduled', b),
    update: (id, b) => patch(`/scheduled/${id}`, b),
    cancel: (id)    => del(`/scheduled/${id}`),
  };

  // Tasks
  const tasks = {
    list:   (q)     => get('/tasks', q),
    create: (b)     => post('/tasks', b),
    update: (id, b) => patch(`/tasks/${id}`, b),
    del:    (id)    => del(`/tasks/${id}`),
  };

  // Custom properties
  const properties = {
    definitions:  (entity) => get('/properties/definitions', entity ? { entity } : undefined),
    createDef:    (b)      => post('/properties/definitions', b),
    updateDef:    (id, b)  => patch(`/properties/definitions/${id}`, b),
    deleteDef:    (id)     => del(`/properties/definitions/${id}`),
    chatValues:   (id)     => get(`/properties/chat/${id}`),
    setChat:      (id, values)   => req('PUT', `/properties/chat/${id}`, { values }),
    ticketValues: (id)     => get(`/properties/ticket/${id}`),
    setTicket:    (id, values)   => req('PUT', `/properties/ticket/${id}`, { values }),
  };

  // Exports: authenticated file downloads
  async function download(path, filename) {
    const r = await fetch(BASE + path, { headers: headers() });
    if (r.status === 401 && _token) { clearToken(); window.location.reload(); return; }
    if (r.status === 403) throw new Error(FORBIDDEN_MSG);
    if (!r.ok) throw new Error('Export failed');
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }
  // Each export takes optional { from, to } (ISO); a number is the legacy "last N days"
  const _exportQs = (q, extra) => {
    const p = typeof q === 'number' ? { days: q } : _clean(q);
    const qs = new URLSearchParams({ ...p, ...(extra || {}) }).toString();
    return qs ? '?' + qs : '';
  };
  const exportsApi = {
    chats:       (q) => download('/exports/chats.csv' + _exportQs(q), 'chats.csv'),
    messages:    (q) => download('/exports/messages.csv' + _exportQs(q ?? 30), 'messages.csv'),
    tickets:     (q) => download('/exports/tickets.csv' + _exportQs(q), 'tickets.csv'),
    contacts:    ()  => download('/exports/contacts.csv', 'contacts.csv'),
    logs:        (q) => download('/exports/logs.csv' + _exportQs(q ?? 30), 'audit_logs.csv'),
    notes:       (q) => download('/exports/notes.csv' + _exportQs(q), 'private_notes.csv'),
    phones:      ()  => download('/exports/phones.csv', 'phones.csv'),
    chatActions: (q) => download('/exports/chat_actions.csv' + _exportQs(q ?? 30), 'chat_actions.csv'),
  };

  // Search
  const search = (q) => get('/search', { q });

  return {
    setToken, clearToken, getToken,
    auth, inbox, tickets, contacts, labels, notes, quickReplies,
    phones, analytics, automation, bulk, ai, search,
    logs, groups, scheduled, exports: exportsApi,
    tasks, properties, org, media,
  };
})();
