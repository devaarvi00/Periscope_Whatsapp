/* ── Hyperscope API Client ──────────────────────────────────────── */
const BASE = '/api/v1';

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
  };

  // Inbox
  const inbox = {
    chats:      (q)     => get('/inbox/chats', q),
    chat:       (id)    => get(`/inbox/chats/${id}`),
    updateChat: (id, b) => patch(`/inbox/chats/${id}`, b),
    markRead:   (id)    => post(`/inbox/chats/${id}/read`),
    messages:   (id, q) => get(`/inbox/chats/${id}/messages`, q),
    send:       (b)     => post('/inbox/send', b),
    addLabel:   (cid, lid)    => post(`/inbox/chats/${cid}/labels/${lid}`),
    removeLabel:(cid, lid)    => del(`/inbox/chats/${cid}/labels/${lid}`),
    sync:          (pid)  => post(`/inbox/sync/${pid}`),
    syncMessages:  (cid)  => post(`/inbox/chats/${cid}/sync-messages`),
  };

  // Tickets
  const tickets = {
    list:   (q)     => get('/tickets', q),
    get:    (id)    => get(`/tickets/${id}`),
    create: (b)     => post('/tickets', b),
    update: (id, b) => patch(`/tickets/${id}`, b),
    del:    (id)    => del(`/tickets/${id}`),
    labels:      (id)      => get(`/tickets/${id}/labels`),
    addLabel:    (id, lid) => post(`/tickets/${id}/labels/${lid}`),
    removeLabel: (id, lid) => del(`/tickets/${id}/labels/${lid}`),
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
    list:      ()    => get('/phones'),
    create:    (b)   => post('/phones', b),
    status:    (id)  => get(`/phones/${id}/status`),
    qr:        (id)  => get(`/phones/${id}/qr`),
    start:     (id)  => post(`/phones/${id}/start`),
    stop:      (id)  => post(`/phones/${id}/stop`),
    restart:   (id)  => post(`/phones/${id}/restart`),
    clearData: (id)  => post(`/phones/${id}/clear-data`),
    del:       (id)  => del(`/phones/${id}`),
  };

  // Analytics
  const analytics = {
    dashboard: ()     => get('/analytics/dashboard'),
    messages:  (d)    => get('/analytics/messages', { days: d }),
    tickets:   ()     => get('/analytics/tickets'),
    agents:    (d)    => get('/analytics/agents', { days: d }),
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

  // Knowledge Base
  const kb = {
    list:    (q)     => get('/knowledge-base', q),
    create:  (b)     => post('/knowledge-base', b),
    update:  (id, b) => patch(`/knowledge-base/${id}`, b),
    approve: (id)    => patch(`/knowledge-base/${id}/approve`),
    del:     (id)    => del(`/knowledge-base/${id}`),
  };

  // Bulk
  const bulk = {
    list:    ()      => get('/bulk/jobs'),
    create:  (b)     => post('/bulk/jobs', b),
    send:    (id)    => post(`/bulk/jobs/${id}/send`),
    credits: ()      => get('/bulk/credits'),
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
  };

  // Activity logs
  const logs = {
    list:    (q) => get('/logs', q),
    actions: ()  => get('/logs/actions'),
  };

  // Groups
  const groups = {
    list:         (q)  => get('/groups', q),
    participants: (id) => get(`/groups/${id}/participants`),
    analytics:    (id, days) => get(`/groups/${id}/analytics`, { days: days || 30 }),
  };

  // Scheduled messages
  const scheduled = {
    list:   (q)  => get('/scheduled', q),
    create: (b)  => post('/scheduled', b),
    cancel: (id) => del(`/scheduled/${id}`),
  };

  // Developer platform
  const developer = {
    apiKeys:       ()   => get('/developer/api-keys'),
    createApiKey:  (b)  => post('/developer/api-keys', b),
    revokeApiKey:  (id) => del(`/developer/api-keys/${id}`),
    webhooks:      ()   => get('/developer/webhooks'),
    webhookEvents: ()   => get('/developer/webhook-events'),
    createWebhook: (b)  => post('/developer/webhooks', b),
    testWebhook:   (id) => post(`/developer/webhooks/${id}/test`),
    delWebhook:    (id) => del(`/developer/webhooks/${id}`),
  };

  // Exports: authenticated file downloads
  async function download(path, filename) {
    const r = await fetch(BASE + path, { headers: headers() });
    if (!r.ok) throw new Error('Export failed');
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }
  const exportsApi = {
    chats:    ()     => download('/exports/chats.csv', 'chats.csv'),
    messages: (days) => download(`/exports/messages.csv?days=${days || 30}`, 'messages.csv'),
    tickets:  ()     => download('/exports/tickets.csv', 'tickets.csv'),
    contacts: ()     => download('/exports/contacts.csv', 'contacts.csv'),
    logs:     (days) => download(`/exports/logs.csv?days=${days || 30}`, 'audit_logs.csv'),
  };

  // Search
  const search = (q) => get('/search', { q });

  return {
    setToken, clearToken, getToken,
    auth, inbox, tickets, contacts, labels, notes, quickReplies,
    phones, analytics, automation, kb, bulk, ai, search,
    logs, groups, scheduled, developer, exports: exportsApi,
  };
})();
