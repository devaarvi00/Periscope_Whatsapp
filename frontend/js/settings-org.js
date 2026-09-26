/* Organization settings pages (config, permissions, tickets, media library, group settings).
 * Registers page renderers into window.SettingsPages, which the settings shell in app.js reads:
 *   window.SettingsPages[key] = { title, subtitle, render: async (el) => {...} }
 * Each render() draws its own title + subtitle into `el` (skip with el.dataset.noHeader = "1").
 *
 * Also provides, for the rest of the app:
 *   window.OrgConfig                      last GET /org/config payload (null until loaded)
 *   window.OrgPermissions                 last GET /org/permissions payload
 *   window.soLoadOrgConfig()              (re)load both; returns a promise
 *   window.soMsgOnRight(m) / soMsgExtras(m)   chat bubble hooks used by renderMessage() in app.js
 *   window.openMediaLibraryPicker(onPick, { kind: 'media' | 'doc' })
 *       Opens a modal listing the media library; calls onPick(item) with
 *       { id, kind, name, mimetype, size, url } and closes. `url` needs the
 *       Authorization header (use soFetchBlob(item.url) for previews). */
window.SettingsPages = window.SettingsPages || {};

(() => {
  'use strict';

  const API_BASE = '/api/v1';
  const FORBIDDEN = "You don't have permission to do this — ask an admin.";
  const ico = (name, size = 16) => (typeof cxIcon === 'function' ? cxIcon(name, size) : '');
  const h = s => (typeof esc === 'function' ? esc(s) : String(s ?? ''));
  const say = (msg, type) => (typeof toast === 'function' ? toast(msg, type) : console.log(msg));
  // Solid icons for these pages — Font Awesome Free 6.7.2 (CC BY 4.0)
  const FA = {
    cloudUp: ['0 0 640 512', 'M144 480C64.5 480 0 415.5 0 336c0-62.8 40.2-116.2 96.2-135.9c-.1-2.7-.2-5.4-.2-8.1c0-88.4 71.6-160 160-160c59.3 0 111 32.2 138.7 80.2C409.9 102 428.3 96 448 96c53 0 96 43 96 96c0 12.2-2.3 23.8-6.4 34.6C596 238.4 640 290.1 640 352c0 70.7-57.3 128-128 128l-368 0zm79-217c-9.4 9.4-9.4 24.6 0 33.9s24.6 9.4 33.9 0l39-39L296 392c0 13.3 10.7 24 24 24s24-10.7 24-24l0-134.1 39 39c9.4 9.4 24.6 9.4 33.9 0s9.4-24.6 0-33.9l-80-80c-9.4-9.4-24.6-9.4-33.9 0l-80 80z'],
    pen: ['0 0 512 512', 'M362.7 19.3L314.3 67.7 444.3 197.7l48.4-48.4c25-25 25-65.5 0-90.5L453.3 19.3c-25-25-65.5-25-90.5 0zm-71 71L58.6 323.5c-10.4 10.4-18 23.3-22.2 37.4L1 481.2C-1.5 489.7 .8 498.8 7 505s15.3 8.5 23.7 6.1l120.3-35.4c14.1-4.2 27-11.8 37.4-22.2L421.7 220.3 291.7 90.3z'],
    usersSolid: ['0 0 640 512', 'M96 128a128 128 0 1 1 256 0A128 128 0 1 1 96 128zM0 482.3C0 383.8 79.8 304 178.3 304l91.4 0C368.2 304 448 383.8 448 482.3c0 16.4-13.3 29.7-29.7 29.7L29.7 512C13.3 512 0 498.7 0 482.3zM609.3 512l-137.8 0c5.4-9.4 8.6-20.3 8.6-32l0-8c0-60.7-27.1-115.2-69.8-151.8c2.4-.1 4.7-.2 7.1-.2l61.4 0C567.8 320 640 392.2 640 481.3c0 17-13.8 30.7-30.7 30.7zM432 256c-31 0-59-12.6-79.3-32.9C372.4 196.5 384 163.6 384 128c0-26.8-6.6-52.1-18.3-74.3C384.3 40.1 407.2 32 432 32c61.9 0 112 50.1 112 112s-50.1 112-112 112z'],
    imageReg: ['0 0 512 512', 'M448 80c8.8 0 16 7.2 16 16l0 319.8-5-6.5-136-176c-4.5-5.9-11.6-9.3-19-9.3s-14.4 3.4-19 9.3L202 340.7l-30.5-42.7C167 291.7 159.8 288 152 288s-15 3.7-19.5 10.1l-80 112L48 416.3l0-.3L48 96c0-8.8 7.2-16 16-16l384 0zM64 32C28.7 32 0 60.7 0 96L0 416c0 35.3 28.7 64 64 64l384 0c35.3 0 64-28.7 64-64l0-320c0-35.3-28.7-64-64-64L64 32zm80 192a48 48 0 1 0 0-96 48 48 0 1 0 0 96z'],
    fileReg: ['0 0 384 512', 'M64 464c-8.8 0-16-7.2-16-16L48 64c0-8.8 7.2-16 16-16l160 0 0 80c0 17.7 14.3 32 32 32l80 0 0 288c0 8.8-7.2 16-16 16L64 464zM64 0C28.7 0 0 28.7 0 64L0 448c0 35.3 28.7 64 64 64l256 0c35.3 0 64-28.7 64-64l0-293.5c0-17-6.7-33.3-18.7-45.3L274.7 18.7C262.7 6.7 246.5 0 229.5 0L64 0zm56 256c-13.3 0-24 10.7-24 24s10.7 24 24 24l144 0c13.3 0 24-10.7 24-24s-10.7-24-24-24l-144 0zm0 96c-13.3 0-24 10.7-24 24s10.7 24 24 24l144 0c13.3 0 24-10.7 24-24s-10.7-24-24-24l-144 0z'],
  };
  const fa = (name, size = 16) => FA[name]
    ? `<svg class="so-fa" width="${size}" height="${size}" viewBox="${FA[name][0]}" fill="currentColor" aria-hidden="true"><path d="${FA[name][1]}"/></svg>` : '';
  const isAdmin = () => (typeof State !== 'undefined' && State.agent?.role === 'admin');

  // ── Local request helper (same error handling as api.js) ─────────── //
  async function req(method, path, body, opts = {}) {
    const headers = {};
    const token = Api.getToken();
    if (token) headers.Authorization = 'Bearer ' + token;
    let payload;
    if (body instanceof FormData) payload = body;
    else if (body != null) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
    const r = await fetch(API_BASE + path, { method, headers, body: payload });
    if (r.status === 401 && token) { Api.clearToken(); window.location.reload(); return; }
    if (!r.ok) {
      let msg = r.status === 403 ? FORBIDDEN : 'Request failed';
      try {
        const e = await r.json();
        if (Array.isArray(e.detail)) msg = e.detail.map(d => (d.msg || JSON.stringify(d)).replace(/^Value error, /, '')).join('; ');
        else if (typeof e.detail === 'string' && e.detail) msg = e.detail;
        else if (e.message) msg = e.message;
      } catch (_) {}
      if (r.status === 413 && msg === 'Request failed') msg = 'File is larger than 16 MB';
      const err = new Error(msg); err.status = r.status; throw err;
    }
    if (r.status === 204) return null;
    if (opts.blob) return r.blob();
    return r.json();
  }
  const get = p => req('GET', p);

  // Authenticated file → object URL (media library previews)
  const _blobCache = new Map();
  async function fetchBlobUrl(url) {
    if (_blobCache.has(url)) return _blobCache.get(url);
    const p = req('GET', url.replace(/^\/api\/v1/, ''), null, { blob: true }).then(b => URL.createObjectURL(b));
    _blobCache.set(url, p);
    p.catch(() => _blobCache.delete(url));
    return p;
  }
  window.soFetchBlob = fetchBlobUrl;

  // ── Org config cache (read by the chat bubble hooks) ─────────────── //
  window.OrgConfig = null;
  window.OrgPermissions = null;
  let _loading = null;
  function loadOrgConfig() {
    if (!Api.getToken()) return Promise.resolve(null);
    _loading = Promise.all([get('/org/config'), get('/org/permissions')])
      .then(([cfg, perms]) => { window.OrgConfig = cfg; window.OrgPermissions = perms; return cfg; })
      .catch(() => null)
      .finally(() => { _loading = null; });
    return _loading;
  }
  window.soLoadOrgConfig = loadOrgConfig;
  // Load once a session token exists (login happens after this script runs), then every 5 min
  (function waitForToken() {
    if (Api.getToken()) { loadOrgConfig(); setInterval(loadOrgConfig, 5 * 60 * 1000); return; }
    setTimeout(waitForToken, 1500);
  })();
  const cfg = () => window.OrgConfig?.config || {};

  // ── Chat bubble hooks (called from renderMessage in app.js) ──────── //
  const LANG_NAME = code => (window.OrgConfig?.languages || []).find(l => l.code === code)?.name || code;
  const TEXT_TYPES = new Set(['text', 'chat', '']);

  window.soMsgOnRight = function (m) {
    if (cfg().active_phone_right === false) return !!(m.from_me || m.from_org_phone);
    return !!m.from_me;
  };

  window.soMsgExtras = function (m) {
    const c = cfg();
    if (m.is_revoked) {
      if (!m.revoked_body) return '';
      return `<div class="so-del"><button type="button" class="so-link so-view-del" data-text="${h(m.revoked_body)}">View message</button></div>`;
    }
    if (m.from_me || !TEXT_TYPES.has((m.message_type || 'text').toLowerCase()) || !m.body) return '';
    if (m.translation?.text) return translationBlock(m.translation);
    if (!c.translation_enabled) return '';
    return `<div class="so-tr-act"><button type="button" class="so-link so-tr-btn" data-text="${h(m.body)}">${ico('translate', 12)} Translate</button></div>`;
  };

  function translationBlock(tr) {
    return `<div class="so-tr"><div class="so-tr-l">${ico('translate', 11)} Translated to ${h(LANG_NAME(tr.lang))}${tr.auto ? ' · auto' : ''}</div>`
      + `<div class="so-tr-t">${h(tr.text).replace(/\n/g, '<br>')}</div></div>`;
  }

  document.addEventListener('click', async e => {
    const tbtn = e.target.closest('.so-tr-btn');
    if (tbtn) {
      e.stopPropagation();
      const lang = cfg().translation_language || 'en';
      tbtn.disabled = true; tbtn.textContent = 'Translating…';
      try {
        const res = await Api.ai.translate(tbtn.dataset.text || '', LANG_NAME(lang));
        const text = (res && (res.translated || res.translation)) || '';
        if (!text) throw new Error('No translation returned');
        tbtn.closest('.so-tr-act').outerHTML = translationBlock({ lang, text });
      } catch (err) {
        say(err.message || 'Translation failed', 'error');
        tbtn.disabled = false; tbtn.innerHTML = `${ico('translate', 12)} Translate`;
      }
      return;
    }
    const vbtn = e.target.closest('.so-view-del');
    if (vbtn) {
      e.stopPropagation();
      const box = vbtn.closest('.so-del');
      const shown = box.querySelector('.so-del-body');
      if (shown) { shown.remove(); vbtn.textContent = 'View message'; return; }
      box.insertAdjacentHTML('beforeend', `<div class="so-del-body">${h(vbtn.dataset.text).replace(/\n/g, '<br>')}</div>`);
      vbtn.textContent = 'Hide message';
    }
  });

  // Live updates from the webhook hooks: auto-translations and deletions
  if (typeof window.handleWSEvent === 'function') {
    const base = window.handleWSEvent;
    window.handleWSEvent = function (msg) {
      const ev = msg && msg.event, d = (msg && msg.data) || {};
      if (ev === 'message_translated' || ev === 'message_revoked') {
        const openId = typeof State !== 'undefined' ? State.inbox?.selectedChatId : null;
        if (openId == null || openId != d.chat_id) return;
        const bubble = document.querySelector(`#messages-area .msg[data-mid="${Number(d.message_id)}"] .msg-bubble`);
        if (ev === 'message_translated' && bubble && d.translation?.text) {
          bubble.querySelector('.so-tr, .so-tr-act')?.remove();
          const time = bubble.querySelector('.cx-btime');
          time ? time.insertAdjacentHTML('beforebegin', translationBlock(d.translation)) : bubble.insertAdjacentHTML('beforeend', translationBlock(d.translation));
        } else if (ev === 'message_revoked' && typeof loadMessages === 'function') {
          loadMessages(d.chat_id, true);
        }
        return;
      }
      return base.apply(this, arguments);
    };
  }

  // ── Shared page building blocks ──────────────────────────────────── //
  function pageHead(el, title, subtitle) {
    return el.dataset.noHeader === '1' ? '' : `<div class="so-head"><h1>${h(title)}</h1><p>${h(subtitle)}</p></div>`;
  }
  function card(title, icon, body, extraHead = '') {
    return `<section class="so-card"><header class="so-card-h"><h2>${h(title)}</h2>${extraHead}<span class="so-ibox">${ico(icon, 15)}</span></header>${body}</section>`;
  }
  // Card whose header has a grey subtitle under the title (reference layout)
  function cardSub(title, subtitle, iconHtml, body) {
    return `<section class="so-card"><header class="so-card-h so-card-h2"><div class="so-card-ht"><h2>${h(title)}</h2><p>${h(subtitle)}</p></div><span class="so-ibox">${iconHtml}</span></header>${body}</section>`;
  }
  function toggle(id, on, disabled = false, label = '') {
    return `<label class="so-switch${disabled ? ' is-disabled' : ''}"><input type="checkbox" id="${id}" ${on ? 'checked' : ''} ${disabled ? 'disabled' : ''} aria-label="${h(label)}"><span></span></label>`;
  }
  function row(label, desc, control, opts = {}) {
    return `<div class="so-row${opts.cls ? ' ' + opts.cls : ''}"${opts.id ? ` id="${opts.id}"` : ''}>
      ${opts.icon ? `<span class="so-row-ico">${ico(opts.icon, 15)}</span>` : ''}
      <div class="so-row-t"><div class="so-label">${label}</div>${desc ? `<div class="so-desc">${desc}</div>` : ''}</div>
      <div class="so-ctl">${control}</div>
    </div>`;
  }
  const readOnlyNote = msg => `<div class="so-note">${ico('info', 14)}<span>${h(msg)}</span></div>`;
  const loading = el => { el.innerHTML = '<div class="so-page"><div class="so-loading">Loading…</div></div>'; };
  const failed = (el, err) => { el.innerHTML = `<div class="so-page"><div class="so-empty">${ico('alert', 22)}<b>Couldn't load this page</b><span>${h(err.message)}</span></div></div>`; };

  // Toggle wired to a PATCH /org/config section key; reverts on failure
  function bindToggle(root, id, onChange) {
    const input = root.querySelector('#' + id);
    if (!input) return;
    input.addEventListener('change', async () => {
      const val = input.checked;
      input.disabled = true;
      try { await onChange(val); say('Saved', 'success'); }
      catch (err) { input.checked = !val; say(err.message, 'error'); }
      finally { input.disabled = false; }
    });
  }
  async function patchConfig(section, values) {
    const res = await req('PATCH', '/org/config', { [section]: values });
    window.OrgConfig = res;
    return res;
  }

  // ═══ 1. Config ════════════════════════════════════════════════════ //
  async function renderConfig(el) {
    loading(el);
    let data;
    try { data = await get('/org/config'); window.OrgConfig = data; } catch (err) { return failed(el, err); }
    const c = data.config, admin = isAdmin(), ro = !admin;
    const langOpts = data.languages.map(l => `<option value="${h(l.code)}" ${l.code === c.translation_language ? 'selected' : ''}>${h(l.name)}</option>`).join('');
    const autoDisabled = ro || !c.translation_enabled || !data.gemini_configured;
    const autoDesc = data.gemini_configured
      ? 'Incoming messages that aren’t in the display language are translated as they arrive.'
      : 'Incoming messages are translated as they arrive. Needs a Gemini API key on the server.';

    el.innerHTML = `<div class="so-page">
      ${pageHead(el, 'Config', 'Configure message and display settings for your organization')}
      ${ro ? readOnlyNote('Only admins can change these settings.') : ''}
      ${card('Messages', 'msg', `
        ${row('Enable Message Translation', 'Show a Translate action on incoming messages.', toggle('so-c-tr', c.translation_enabled, ro, 'Enable Message Translation'))}
        ${row('Display language', 'Messages are translated into this language.', `<select class="so-select" id="so-c-lang" ${ro || !c.translation_enabled ? 'disabled' : ''}>${langOpts}</select>`, { cls: 'so-sub' })}
        ${row('Enable Auto-Translation of New Messages <span class="so-pill">Experimental</span>', autoDesc, toggle('so-c-auto', c.auto_translate, autoDisabled, 'Enable Auto-Translation'))}
      `)}
      ${card('Privacy and Display', 'shield', `
        ${row('Show Sender Names', 'Start messages sent from Hyperscope with the agent’s name in bold, e.g. <b>*Priya*:</b>', toggle('so-c-names', c.show_sender_names, ro, 'Show Sender Names'))}
        ${row('Mask User Phone Numbers', 'Hide customers’ phone numbers from agents in chats, messages and exports. Admins always see full numbers.', toggle('so-c-mask', c.mask_phone_numbers, ro, 'Mask User Phone Numbers'))}
        ${row('Show View Message Option On Deleted Messages', 'When a customer deletes a message for everyone, keep a “View message” link to its original text.', toggle('so-c-del', c.show_deleted_messages, ro, 'Show View Message Option'))}
        ${row('Display Active Phone Messages On The Right', 'Only messages sent from the chat’s own number appear on the right. Turn off to show messages from all your numbers on the right.', toggle('so-c-right', c.active_phone_right, ro, 'Display Active Phone Messages On The Right'))}
        ${row('Media Privacy <span class="so-pill so-pill-lock">' + ico('shield', 10) + ' Always on</span>', 'Photos, videos and documents are only served to signed-in team members — there are no public media links.', toggle('so-c-media', true, true, 'Media Privacy'))}
      `)}
    </div>`;

    const refreshAuto = () => {
      const on = el.querySelector('#so-c-tr').checked;
      el.querySelector('#so-c-lang').disabled = ro || !on;
      const auto = el.querySelector('#so-c-auto');
      auto.disabled = ro || !on || !data.gemini_configured;
      auto.closest('.so-switch').classList.toggle('is-disabled', auto.disabled);
    };
    bindToggle(el, 'so-c-tr', async v => { await patchConfig('config', { translation_enabled: v }); refreshAuto(); });
    bindToggle(el, 'so-c-auto', v => patchConfig('config', { auto_translate: v }));
    bindToggle(el, 'so-c-names', v => patchConfig('config', { show_sender_names: v }));
    bindToggle(el, 'so-c-mask', v => patchConfig('config', { mask_phone_numbers: v }));
    bindToggle(el, 'so-c-del', v => patchConfig('config', { show_deleted_messages: v }));
    bindToggle(el, 'so-c-right', v => patchConfig('config', { active_phone_right: v }));
    el.querySelector('#so-c-lang').addEventListener('change', async e => {
      try { await patchConfig('config', { translation_language: e.target.value }); say('Saved', 'success'); }
      catch (err) { say(err.message, 'error'); }
    });
  }

  // ═══ 2. Permissions ═══════════════════════════════════════════════ //
  const ACTIONS = [
    ['create_chats', 'plus', 'Create Chats', 'Start conversations with new numbers (e.g. broadcasts to numbers without a chat).'],
    ['data_export', 'download', 'Data Export', 'Export chats, messages and tickets from the numbers they can access.'],
    ['archive_chats', 'folder', 'Archive or Close Chats', 'Archive, resolve or reopen chats.'],
    ['assign', 'user', 'Assign Chats or Tickets', 'Assign or reassign chats and tickets to team members.'],
    ['update_labels', 'tag', 'Update Labels', 'Add or remove labels on chats and tickets, and create labels from the picker.'],
    ['delete_tickets', 'trash', 'Delete Tickets', 'Permanently delete tickets.'],
  ];
  const MAIN_SCREENS = [
    ['analytics', 'chart', 'Analytics'], ['bulk', 'megaphone', 'Bulk Messages'], ['contacts', 'user', 'Contacts'],
    ['media', 'image', 'Media'], ['ai', 'sparkle', 'AI'], ['automation', 'zap', 'Automation'],
    ['chat_list', 'list', 'Chat List'], ['logs', 'history', 'Logs'],
  ];
  const SETTINGS_SCREENS = [
    ['phones', 'phone', 'Phones'], ['labels', 'tag', 'Labels'], ['tickets', 'ticket', 'Tickets'],
    ['quick_replies', 'wand', 'Quick Replies'], ['custom_properties', 'props', 'Custom Properties'],
    ['media_library', 'folder', 'Media Library'], ['group_templates', 'users', 'Group Templates'],
    ['integrations', 'link', 'Integrations'],
  ];

  async function renderPermissions(el) {
    loading(el);
    let p;
    try { p = await get('/org/permissions'); window.OrgPermissions = p; } catch (err) { return failed(el, err); }
    const ro = !p.is_admin;
    const screenCol = (title, list) => `<div class="so-col"><div class="so-col-h">${title}</div>${list.map(([k, icon, label]) =>
      `<label class="so-check${ro ? ' is-disabled' : ''}"><input type="checkbox" data-screen="${k}" ${p.screens[k] ? 'checked' : ''} ${ro ? 'disabled' : ''}><span class="so-check-box">${ico('check', 11)}</span><span class="so-check-ico">${ico(icon, 14)}</span><span>${h(label)}</span></label>`).join('')}</div>`;

    el.innerHTML = `<div class="so-page">
      ${pageHead(el, 'Permissions', 'Choose what agents can do and which screens they can open. Admins always have full access.')}
      ${ro ? readOnlyNote('Only admins can change permissions.') : ''}
      ${card('Action Permissions', 'shield', ACTIONS.map(([k, icon, label, desc]) =>
        row(h(label), h(desc), toggle('so-a-' + k, p.actions[k], ro, label), { icon })).join(''))}
      ${card('Screens', 'panel', `<p class="so-card-sub">Agents only see the screens that are checked. Blocked screens are also refused by the server.</p>
        <div class="so-cols">${screenCol('MAIN SCREENS', MAIN_SCREENS)}${screenCol('SETTINGS', SETTINGS_SCREENS)}</div>`)}
    </div>`;

    ACTIONS.forEach(([k]) => bindToggle(el, 'so-a-' + k, async v => {
      window.OrgPermissions = await req('PUT', '/org/permissions', { actions: { [k]: v } });
    }));
    el.querySelectorAll('input[data-screen]').forEach(inp => inp.addEventListener('change', async () => {
      const v = inp.checked; inp.disabled = true;
      try { window.OrgPermissions = await req('PUT', '/org/permissions', { screens: { [inp.dataset.screen]: v } }); say('Saved', 'success'); }
      catch (err) { inp.checked = !v; say(err.message, 'error'); }
      finally { inp.disabled = false; }
    }));
  }

  // ═══ 3. Tickets ═══════════════════════════════════════════════════ //
  async function renderTicketsSettings(el) {
    loading(el);
    let data, perms;
    try { [data, perms] = await Promise.all([get('/org/config'), get('/org/permissions')]); } catch (err) { return failed(el, err); }
    const t = data.tickets, ro = !perms.effective.screens.tickets;
    const emojis = (data.ticket_emojis || []).map(e => `<span class="so-emoji">${h(e)}</span>`).join('');
    el.innerHTML = `<div class="so-page">
      ${pageHead(el, 'Tickets', 'Configure how tickets are numbered, linked to messages and created')}
      ${ro ? readOnlyNote('Only admins can change ticket settings.') : ''}
      ${card('General', 'sliders', `
        ${row('Ticket prefix', `Three letters shown before ticket numbers, e.g. <b id="so-t-preview">${h(t.prefix || 'TKT')}-12</b>. Existing tickets keep their numbers.`,
          `<div class="so-inline"><input class="so-input so-input-prefix" id="so-t-prefix" maxlength="3" value="${h(t.prefix)}" placeholder="TKT" ${ro ? 'disabled' : ''} aria-label="Ticket prefix"><button class="so-btn" id="so-t-prefix-save" ${ro ? 'disabled' : ''}>Save</button></div>`)}
        ${row('Enable Automatic Ticket Attachment to Messages', 'When a customer replies to (quotes) a message that belongs to a ticket, the reply is added to the same ticket.', toggle('so-t-attach', t.auto_attach, ro, 'Automatic Ticket Attachment'))}
      `)}
      ${card('Automated Ticketing', 'zap', `
        ${row('Enable emoji based ticketing', `React to a message with ${emojis || 'a ticket emoji'} to create a ticket from it.`, toggle('so-t-emoji', t.emoji_ticketing, ro, 'Emoji based ticketing'))}
        ${row('Send an automated message when a ticket is created', 'Reply in the chat with the ticket number whenever a ticket is raised. Sent from the chat’s own WhatsApp number.', toggle('so-t-auto', t.auto_message, ro, 'Automated ticket message'))}
        <div class="so-block" id="so-t-tpl-wrap">
          <label class="so-flabel" for="so-t-tpl">Message template</label>
          <textarea class="so-textarea" id="so-t-tpl" rows="3" maxlength="1000" ${ro ? 'disabled' : ''}>${h(t.auto_message_template)}</textarea>
          <div class="so-block-foot"><span class="so-hint">Use <code>{{ticket_id}}</code> for the ticket number (e.g. ${h(t.prefix || 'TKT')}-12).</span>
          <button class="so-btn" id="so-t-tpl-save" ${ro ? 'disabled' : ''}>Save Template</button></div>
        </div>
      `)}
    </div>`;

    const prefix = el.querySelector('#so-t-prefix');
    prefix.addEventListener('input', () => {
      prefix.value = prefix.value.replace(/[^a-z]/gi, '').toUpperCase();
      el.querySelector('#so-t-preview').textContent = (prefix.value || 'TKT') + '-12';
    });
    el.querySelector('#so-t-prefix-save').addEventListener('click', async () => {
      if (prefix.value && prefix.value.length !== 3) return say('The prefix must be exactly 3 letters', 'error');
      try { await patchConfig('tickets', { prefix: prefix.value }); say('Ticket prefix saved', 'success'); }
      catch (err) { say(err.message, 'error'); }
    });
    bindToggle(el, 'so-t-attach', v => patchConfig('tickets', { auto_attach: v }));
    bindToggle(el, 'so-t-emoji', v => patchConfig('tickets', { emoji_ticketing: v }));
    const wrap = el.querySelector('#so-t-tpl-wrap');
    const syncWrap = () => wrap.classList.toggle('is-off', !el.querySelector('#so-t-auto').checked);
    syncWrap();
    bindToggle(el, 'so-t-auto', async v => { await patchConfig('tickets', { auto_message: v }); syncWrap(); });
    el.querySelector('#so-t-tpl-save').addEventListener('click', async () => {
      const v = el.querySelector('#so-t-tpl').value.trim();
      if (!v.includes('{{ticket_id}}')) return say('The template must include {{ticket_id}}', 'error');
      try { await patchConfig('tickets', { auto_message_template: v }); say('Template saved', 'success'); }
      catch (err) { say(err.message, 'error'); }
    });
  }

  // ═══ 4. Media Library ═════════════════════════════════════════════ //
  const MAX_UPLOAD = 16 * 1024 * 1024;
  const ACCEPT = {
    media: 'image/jpeg,image/png,image/gif,image/webp,video/mp4,video/3gpp,video/quicktime,video/webm',
    doc: '.pdf,.txt,.csv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.zip',
  };
  const fmtSize = n => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n >= 1024 ? Math.round(n / 1024) + ' KB' : n + ' B';
  const extOf = name => ((name || '').split('.').pop() || '').slice(0, 4).toUpperCase();

  function mediaTile(item, canDelete, pick = false) {
    const isImg = item.mimetype.startsWith('image/'), isVid = item.mimetype.startsWith('video/');
    const visual = isImg ? `<div class="so-ml-prev" data-src="${h(item.url)}" data-kind="image">${ico('image', 24)}</div>`
      : isVid ? `<div class="so-ml-prev" data-src="${h(item.url)}" data-kind="video">${ico('video', 24)}</div>`
      : `<div class="so-ml-prev is-doc">${ico('doc', 26)}<span class="so-ml-ext">${h(extOf(item.name))}</span></div>`;
    return `<div class="so-ml-item${pick ? ' is-pick' : ''}" data-id="${item.id}" title="${h(item.name)}" ${pick ? 'tabindex="0" role="button"' : ''}>
      ${visual}
      <div class="so-ml-meta"><div class="so-ml-name">${h(item.name)}</div><div class="so-ml-size">${fmtSize(item.size)}</div></div>
      ${canDelete && !pick ? `<button type="button" class="so-ml-del" data-del="${item.id}" title="Delete" aria-label="Delete ${h(item.name)}">${ico('trash', 14)}</button>` : ''}
    </div>`;
  }

  function hydratePreviews(root) {
    root.querySelectorAll('.so-ml-prev[data-src]').forEach(async box => {
      const src = box.dataset.src; box.removeAttribute('data-src');
      try {
        const url = await fetchBlobUrl(src);
        box.innerHTML = box.dataset.kind === 'video'
          ? `<video src="${url}" muted preload="metadata"></video><span class="so-ml-play">${ico('play', 14)}</span>`
          : `<img src="${url}" alt="">`;
        box.classList.add('ready');
      } catch (_) {}
    });
  }

  async function renderMediaLibrary(el) {
    let perms = window.OrgPermissions;
    try { perms = await get('/org/permissions'); window.OrgPermissions = perms; } catch (_) {}
    const canEdit = !!perms?.effective?.screens?.media_library;
    const st = { kind: 'media', search: '', seq: 0 };
    el.innerHTML = `<div class="so-page so-page-wide">
      ${pageHead(el, 'Media Library', 'Manage media for quick access across the workspace')}
      <div class="so-ml-toolbar">
        <div class="so-pills" role="tablist">
          <button class="so-pill on" data-kind="media" role="tab" aria-selected="true">Media</button>
          <button class="so-pill" data-kind="doc" role="tab" aria-selected="false">Docs</button>
        </div>
        <div class="so-ml-right">
          <div class="so-search so-search-sm">${ico('search', 13)}<input type="search" id="so-ml-q" placeholder="Search by file name" aria-label="Search by file name"></div>
          ${canEdit ? `<button class="so-btn so-btn-primary so-btn-sm" id="so-ml-up">${fa('cloudUp', 14)} Upload</button><input type="file" id="so-ml-file" hidden multiple>` : ''}
        </div>
      </div>
      <section class="so-card so-ml so-ml-box">
        <div class="so-ml-grid" id="so-ml-grid"></div>
      </section>
    </div>`;
    const grid = el.querySelector('#so-ml-grid');

    async function load() {
      const seq = ++st.seq;
      grid.innerHTML = '<div class="so-loading">Loading…</div>';
      let items;
      try {
        items = await get('/media-library?' + new URLSearchParams({ kind: st.kind, ...(st.search ? { search: st.search } : {}) }));
      } catch (err) { if (seq === st.seq) grid.innerHTML = `<div class="so-empty">${h(err.message)}</div>`; return; }
      if (seq !== st.seq) return;
      if (!items.length) {
        grid.innerHTML = `<div class="so-ml-empty2">${fa(st.kind === 'doc' ? 'fileReg' : 'imageReg', 34)}<b>No ${st.kind === 'doc' ? 'documents' : 'media files'} found</b><span>${st.search ? 'Try a different file name' : (canEdit ? 'Upload files to get started' : 'Ask an admin to upload files')}</span></div>`;
        return;
      }
      grid.innerHTML = items.map(i => mediaTile(i, canEdit)).join('');
      hydratePreviews(grid);
    }

    el.querySelectorAll('.so-pill').forEach(b => b.addEventListener('click', () => {
      st.kind = b.dataset.kind;
      el.querySelectorAll('.so-pill').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', String(x === b)); });
      const f = el.querySelector('#so-ml-file'); if (f) f.accept = ACCEPT[st.kind];
      load();
    }));
    let t;
    el.querySelector('#so-ml-q').addEventListener('input', e => {
      clearTimeout(t); t = setTimeout(() => { st.search = e.target.value.trim(); load(); }, 250);
    });
    grid.addEventListener('click', async e => {
      const del = e.target.closest('[data-del]');
      if (!del) return;
      const name = del.closest('.so-ml-item')?.title || 'this file';
      if (!confirm(`Delete "${name}" from the media library?`)) return;
      try { await req('DELETE', '/media-library/' + del.dataset.del); say('File deleted', 'success'); load(); }
      catch (err) { say(err.message, 'error'); }
    });
    if (canEdit) {
      const input = el.querySelector('#so-ml-file');
      input.accept = ACCEPT.media;
      el.querySelector('#so-ml-up').addEventListener('click', () => input.click());
      input.addEventListener('change', async () => {
        const files = [...input.files]; input.value = '';
        let ok = 0;
        for (const f of files) {
          if (f.size > MAX_UPLOAD) { say(`${f.name} is larger than 16 MB`, 'error'); continue; }
          const fd = new FormData(); fd.append('file', f, f.name);
          try { await req('POST', '/media-library', fd); ok++; }
          catch (err) { say(`${f.name}: ${err.message}`, 'error'); }
        }
        if (ok) say(`Uploaded ${ok} file${ok > 1 ? 's' : ''}`, 'success');
        load();
      });
    }
    load();
  }

  /* Media library picker for the composer (or anything else):
   *   openMediaLibraryPicker(item => { ... }, { kind: 'media' })
   * item = { id, kind, name, mimetype, size, url }. */
  window.openMediaLibraryPicker = function (onPick, opts = {}) {
    const st = { kind: opts.kind === 'doc' ? 'doc' : 'media', search: '' };
    showModal('Media Library', `<div class="so-picker">
      <div class="so-ml-bar">
        <div class="so-seg"><button class="so-seg-btn${st.kind === 'media' ? ' on' : ''}" data-kind="media">Media</button><button class="so-seg-btn${st.kind === 'doc' ? ' on' : ''}" data-kind="doc">Docs</button></div>
        <div class="so-search">${ico('search', 14)}<input type="search" id="so-pk-q" placeholder="Search by file name"></div>
      </div>
      <div class="so-ml-grid so-pk-grid" id="so-pk-grid"></div>
    </div>`);
    const root = document.querySelector('.so-picker');
    const grid = root.querySelector('#so-pk-grid');
    let items = [];
    async function load() {
      grid.innerHTML = '<div class="so-loading">Loading…</div>';
      try { items = await get('/media-library?' + new URLSearchParams({ kind: st.kind, ...(st.search ? { search: st.search } : {}) })); }
      catch (err) { grid.innerHTML = `<div class="so-empty">${h(err.message)}</div>`; return; }
      grid.innerHTML = items.length ? items.map(i => mediaTile(i, false, true)).join('')
        : `<div class="so-empty so-ml-empty">${ico('image', 24)}<b>No media files found</b><span>Upload files in Settings → Media Library</span></div>`;
      hydratePreviews(grid);
    }
    root.querySelectorAll('.so-seg-btn').forEach(b => b.addEventListener('click', () => {
      st.kind = b.dataset.kind; root.querySelectorAll('.so-seg-btn').forEach(x => x.classList.toggle('on', x === b)); load();
    }));
    let t;
    root.querySelector('#so-pk-q').addEventListener('input', e => { clearTimeout(t); t = setTimeout(() => { st.search = e.target.value.trim(); load(); }, 250); });
    const choose = target => {
      const it = items.find(i => i.id == target.dataset.id);
      if (!it) return;
      closeModal();
      if (typeof onPick === 'function') onPick(it);
    };
    grid.addEventListener('click', e => { const tile = e.target.closest('.so-ml-item'); if (tile) choose(tile); });
    grid.addEventListener('keydown', e => { const tile = e.target.closest('.so-ml-item'); if (tile && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); choose(tile); } });
    load();
  };

  // ═══ 5. Group Settings ════════════════════════════════════════════ //
  async function renderGroupSettings(el) {
    loading(el);
    let data, perms, templates;
    try { [data, perms, templates] = await Promise.all([get('/org/config'), get('/org/permissions'), get('/org/group-templates')]); }
    catch (err) { return failed(el, err); }
    const g = data.groups, canEdit = !!perms.effective.screens.group_templates;

    const tplList = () => templates.length ? templates.map(t => `<div class="so-tpl" data-id="${t.id}">
        <span class="so-tpl-ico">${ico('users', 15)}</span>
        <div class="so-row-t"><div class="so-label">${h(t.name)}</div>
          <div class="so-desc">${t.description ? h(t.description) + ' · ' : ''}${t.participants.length} default participant${t.participants.length === 1 ? '' : 's'}${t.messages_admin_only ? ' · admins-only messages' : ''}${t.info_admin_only ? ' · admins-only info' : ''}</div></div>
        ${canEdit ? `<div class="so-ctl"><button class="so-icon-btn" data-edit="${t.id}" title="Edit" aria-label="Edit ${h(t.name)}">${ico('edit', 14)}</button><button class="so-icon-btn danger" data-del="${t.id}" title="Delete" aria-label="Delete ${h(t.name)}">${ico('trash', 14)}</button></div>` : ''}
      </div>`).join('')
      : `<div class="so-tpl-empty2">${fa('usersSolid', 40)}<b>No group templates yet</b><span>${canEdit ? 'Create a new template to get started' : 'Ask an admin to create one'}</span>${canEdit ? '<button class="so-btn so-tpl-new2" data-new="1">Create new Template</button>' : ''}</div>`;

    el.innerHTML = `<div class="so-page">
      ${pageHead(el, 'Group Settings', 'Manage group invites and templates')}
      ${!canEdit ? readOnlyNote('Only admins can change group settings.') : ''}
      ${cardSub('Group Invites', 'Personalize the group invite messages to participants', fa('usersSolid', 15), `
        <div class="so-row so-row-stack">
          <div class="so-row-t"><div class="so-label">Enable Custom Group Invite Message</div>
            <div class="so-desc">When this option is enabled, a custom group invite message will be sent to invited participants.</div>
            <button class="so-btn so-btn-sm so-edit-tpl" id="so-g-edit" ${!canEdit ? 'disabled' : ''}>${fa('pen', 11)} Edit Invite Template</button>
          </div>
          <div class="so-ctl">${toggle('so-g-inv', g.invite_message_enabled, !canEdit, 'Custom Group Invite Message')}</div>
        </div>`)}
      ${cardSub('Group Templates', 'Manage group templates for the workspace', fa('usersSolid', 15), `
        <div class="so-tpl-body">
          ${canEdit ? `<div class="so-tpl-actions"><button class="so-btn so-btn-primary so-btn-sm" id="so-g-new">Create new Template</button></div>` : ''}
          <div id="so-g-list">${tplList()}</div>
        </div>`)}
    </div>`;
    bindToggle(el, 'so-g-inv', v => patchConfig('groups', { invite_message_enabled: v }));
    el.querySelector('#so-g-edit')?.addEventListener('click', () => {
      const cur = window.OrgConfig?.groups?.invite_template ?? g.invite_template;
      showModal('Edit Invite Template', `
        <div class="form-group"><label for="so-g-tpl">Invite message</label>
          <textarea id="so-g-tpl" class="so-textarea" rows="5" maxlength="1000">${h(cur)}</textarea></div>
        <p class="so-hint">Placeholders: <code>{{group_name}}</code> and <code>{{invite_link}}</code> (added at the end if left out).</p>
        <div class="modal-footer"><button class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary" id="so-g-tpl-save">Save</button></div>`);
      document.getElementById('so-g-tpl-save').addEventListener('click', async () => {
        const v = document.getElementById('so-g-tpl').value.trim();
        if (!v) return say('The invite template can’t be empty', 'error');
        try {
          await patchConfig('groups', { invite_template: v });
          closeModal(); say('Invite template saved', 'success');
        } catch (err) { say(err.message, 'error'); }
      });
    });

    const redraw = () => { el.querySelector('#so-g-list').innerHTML = tplList(); };
    const openForm = (tpl) => {
      showModal(tpl ? 'Edit Group Template' : 'Create Group Template', `
        <div class="form-group"><label for="so-gt-name">Name *</label><input id="so-gt-name" maxlength="100" value="${h(tpl?.name || '')}" placeholder="e.g. Customer onboarding"></div>
        <div class="form-group"><label for="so-gt-desc">Description</label><textarea id="so-gt-desc" rows="3" maxlength="2048" placeholder="Group description">${h(tpl?.description || '')}</textarea></div>
        <div class="form-group"><label for="so-gt-parts">Default participants</label><textarea id="so-gt-parts" rows="3" placeholder="One phone number per line, with country code">${h((tpl?.participants || []).join('\n'))}</textarea></div>
        <label class="so-check-line"><input type="checkbox" id="so-gt-msg" ${tpl?.messages_admin_only ? 'checked' : ''}> Only admins can send messages</label>
        <label class="so-check-line"><input type="checkbox" id="so-gt-info" ${tpl?.info_admin_only ? 'checked' : ''}> Only admins can edit group info</label>
        <div class="modal-footer"><button class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary" id="so-gt-save">${tpl ? 'Save' : 'Create Template'}</button></div>`);
      document.getElementById('so-gt-save').addEventListener('click', async () => {
        const body = {
          name: document.getElementById('so-gt-name').value.trim(),
          description: document.getElementById('so-gt-desc').value.trim(),
          participants: document.getElementById('so-gt-parts').value.split(/[\n,;]+/).map(s => s.trim()).filter(Boolean),
          messages_admin_only: document.getElementById('so-gt-msg').checked,
          info_admin_only: document.getElementById('so-gt-info').checked,
        };
        if (!body.name) return say('Name is required', 'error');
        try {
          const saved = tpl ? await req('PATCH', '/org/group-templates/' + tpl.id, body) : await req('POST', '/org/group-templates', body);
          templates = tpl ? templates.map(x => x.id === saved.id ? saved : x) : [...templates, saved].sort((a, b) => a.name.localeCompare(b.name));
          closeModal(); redraw(); say(tpl ? 'Template saved' : 'Template created', 'success');
        } catch (err) { say(err.message, 'error'); }
      });
    };
    el.querySelector('#so-g-new')?.addEventListener('click', () => openForm(null));
    el.querySelector('#so-g-list').addEventListener('click', async e => {
      if (e.target.closest('[data-new]')) return openForm(null);
      const ed = e.target.closest('[data-edit]'), dl = e.target.closest('[data-del]');
      if (ed) return openForm(templates.find(t => t.id == ed.dataset.edit));
      if (dl) {
        const t = templates.find(x => x.id == dl.dataset.del);
        if (!t || !confirm(`Delete the template "${t.name}"?`)) return;
        try { await req('DELETE', '/org/group-templates/' + t.id); templates = templates.filter(x => x.id !== t.id); redraw(); say('Template deleted', 'success'); }
        catch (err) { say(err.message, 'error'); }
      }
    });
  }

  // ── Registry ─────────────────────────────────────────────────────── //
  Object.assign(window.SettingsPages, {
    config: { title: 'Config', subtitle: 'Configure message and display settings for your organization', render: renderConfig },
    permissions: { title: 'Permissions', subtitle: 'Choose what agents can do and which screens they can open', render: renderPermissions },
    tickets: { title: 'Tickets', subtitle: 'Configure how tickets are numbered, linked to messages and created', render: renderTicketsSettings },
    'media-library': { title: 'Media Library', subtitle: 'Upload images, videos and documents once and reuse them', render: renderMediaLibrary },
    'group-settings': { title: 'Group Settings', subtitle: 'Invite messages and reusable templates for WhatsApp groups', render: renderGroupSettings },
  });
})();
