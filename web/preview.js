  const NO_THUMBNAIL = new Set();
  function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }
  function hide() {
    try { window.pywebview.api.hide(); } catch (e) {}
  }
  // Hides when it loses focus (like the OneDrive flyout) and on Escape.
  window.addEventListener('blur', () => setTimeout(() => { if (!document.hasFocus()) hide(); }, 150));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });

  let toastTimer = null;
  function toast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg; t.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.style.display = 'none'; }, 1800);
  }

  async function post(path, body) {
    return fetch(path, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {}) });
  }
  let currentSection = 'history';
  function showSection(target) {
    currentSection = target;
    for (const id of ['devices', 'settings']) document.getElementById(id).hidden = id !== target;
    document.getElementById('list').hidden = target !== 'history';
    document.getElementById('back').hidden = target === 'history';
    if (target === 'settings') loadSettings();
    if (target === 'devices') loadDevices();
    refresh();
  }
  async function loadDevices() {
    const list = document.getElementById('device-list');
    list.textContent = 'Loading…';
    try {
      const r = await fetch('/app/profile');
      if (!r.ok) throw new Error();
      const d = await r.json();
      list.replaceChildren();
      for (const device of d.linked_pcs || []) {
        const row = document.createElement('div');
        row.className = 'row';
        row.textContent = device.name;
        list.appendChild(row);
      }
      if (!list.childElementCount) list.textContent = 'No devices available';
    } catch (e) { list.textContent = 'CopyOver isn’t responding'; }
  }
  async function loadSettings() {
    try {
      const r = await fetch('/app/profile');
      if (!r.ok) throw new Error();
      const d = await r.json();
      document.getElementById('notices').checked = d.notifications;
      document.getElementById('clipboard-notices').checked = !!d.clipboard_notifications;
      document.getElementById('autostart-row').hidden = d.autostart == null;
      document.getElementById('autostart').checked = !!d.autostart;
      document.getElementById('language').value = d.language;
    } catch (e) { toast('Couldn’t load the settings'); }
  }
  async function saveClipboardNotices(control) {
    control.disabled = true;
    try {
      const r = await post('/app/profile', { clipboard_notifications: control.checked });
      if (!r.ok || !(await r.json()).ok) throw new Error();
    } catch (e) { control.checked = !control.checked; toast('Couldn’t save'); }
    finally { control.disabled = false; }
  }
  async function saveNotices(control) {
    control.disabled = true;
    try {
      const r = await post('/app/profile', { notifications: control.checked });
      if (!r.ok || !(await r.json()).ok) throw new Error();
    } catch (e) { control.checked = !control.checked; toast('Couldn’t save'); }
    finally { control.disabled = false; }
  }
  async function saveAutostart(control) {
    control.disabled = true;
    try {
      const r = await post('/app/autostart', { enable: control.checked });
      if (!r.ok || !(await r.json()).ok) throw new Error();
    } catch (e) { control.checked = !control.checked; toast('Couldn’t save'); }
    finally { control.disabled = false; }
  }
  async function saveLanguage(control) {
    control.disabled = true;
    try {
      const r = await post('/app/profile', { language: control.value });
      if (!r.ok || !(await r.json()).ok) throw new Error();
      location.reload();
    } catch (e) { toast('Couldn’t save'); control.disabled = false; }
  }
  async function quitApp() {
    try { const r = await post('/app/quit'); if (!r.ok) throw new Error(); hide(); }
    catch (e) { toast('CopyOver isn’t responding'); }
  }
  async function action(name) {
    if (name === 'open_panel') {
      // If the panel is already open, this window brings it to the front (it has the foreground); otherwise ask to open it.
      let focused = false;
      try { focused = await window.pywebview.api.focus_panel(); } catch (e) {}
      if (focused) { hide(); return; }
    }
    try { await window.pywebview.api.yield_focus(); } catch (e) {}
    try { const r = await post('/app/action', { action: name }); if (!r.ok) throw new Error(); hide(); } catch (e) { toast('Couldn’t open it'); }
  }
  async function copyItem(id) {
    try {
      const r = await post('/app/copy-history', { id });
      const d = await r.json();
      toast(d.ok ? 'Copied' : (d.message || 'Couldn’t copy'));
    } catch (e) { toast('Couldn’t copy'); }
  }
  async function deleteItem(id) {
    try {
      const r = await post('/app/delete-history', { id });
      if (!r.ok && r.status !== 404) throw new Error();
      lastHistory = null;
      await refresh();
      toast('Entry removed from history');
    } catch (e) { toast('Couldn’t delete the entry. Try again.'); }
  }
  async function openItem(id) {
    try { await post('/app/open-history', { id, folder: false }); } catch (e) {}
    hide();
  }

  const FILE_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
  const TEXT_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="16" y2="12"/><line x1="4" y1="18" x2="12" y2="18"/></svg>';

  const COPY_TITLE = 'Copy to clipboard';
  let lastHistory = null;
  function whenText(h) {
    if (!h.timestamp) return h.time || '';
    const f = new Date(h.timestamp * 1000);
    const time = `${String(f.getHours()).padStart(2, '0')}:${String(f.getMinutes()).padStart(2, '0')}`;
    const today = new Date();
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    if (f.toDateString() === today.toDateString()) return time;
    if (f.toDateString() === yesterday.toDateString()) return `yesterday ${time}`;
    return `${String(f.getDate()).padStart(2, '0')}/${String(f.getMonth() + 1).padStart(2, '0')} ${time}`;
  }

  async function refresh() {
    try {
      const rState = await fetch('/app/state?_t=' + Date.now());
      if (!rState.ok) throw new Error();
      const state = await rState.json();
      document.getElementById('state').textContent = state.announced ? 'Running on this PC' : 'No network';
      if (currentSection !== 'history') return;
      const rHist = await fetch('/app/history');
      if (!rHist.ok) throw new Error();
      const items = await rHist.json();
      const fingerprint = JSON.stringify(items);
      if (fingerprint === lastHistory) return;
      lastHistory = fingerprint;
      const list = document.getElementById('list');
      if (!items.length) {
        list.innerHTML = '<div class="empty"><strong>Nothing received yet</strong></div>';
        return;
      }
      // Recent: the list has what was sent and received. The server puts pinned items first; here it's by date.
      const recent = items.slice().sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
      list.innerHTML = '<div class="section">Recent</div>' + recent.map(h => {
        const isLink = !h.path && !!h.link;
        const isText = !h.path && /^(Texto|Nota): /.test(h.detail || '');
        const isImage = /\.(png|jpe?g|gif|webp|bmp)$/i.test(h.path || '') && h.exists !== false && !NO_THUMBNAIL.has(h.id);
        const name = isLink ? h.link : (isText ? h.detail.replace(/^(Texto|Nota): /, '') : h.detail);
        const view = isImage
          ? `<img class="thumb" src="/app/history/image?id=${encodeURIComponent(h.id)}" alt="" loading="lazy" data-id="${esc(h.id)}" onerror="NO_THUMBNAIL.add(this.dataset.id); this.style.visibility='hidden'">`
          : `<div class="icon">${isText ? TEXT_ICON : FILE_ICON}</div>`;
        return `<div class="item">
          <button class="copy-item" type="button" data-id="${esc(h.id)}" onclick="copyItem(this.dataset.id)" title="${esc(COPY_TITLE)}">
          ${view}
          <div class="info">
            <div class="name ${isText ? 'text' : ''}">${esc(name)}</div>
            <div class="meta">${esc(whenText(h))} · ${esc(String(h.origin || '').replace(/\s*\(\d{1,3}(\.\d{1,3}){3}\)$/, ''))}${!isText && !isLink && h.size_text ? ' · ' + esc(h.size_text) : ''}</div>
          </div>
          </button>
          ${h.path || isLink ? `<button class="open-btn" type="button" data-id="${esc(h.id)}" onclick="openItem(this.dataset.id)">Open</button>` : ''}
          <button class="delete-btn" type="button" data-id="${esc(h.id)}" onclick="deleteItem(this.dataset.id)" title="Delete from history" aria-label="Delete from history"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"/></svg></button>
        </div>`;
      }).join('');
    } catch (e) {
      document.getElementById('state').textContent = 'CopyOver isn’t responding';
    }
  }
  window.addEventListener('airbridge-mostrar', () => showSection('history'));
  // WebView2 doesn't change document.visibilityState when the window is hidden: Python calls
  // copyoverVisible(false/true) and polling pauses while it's hidden.
  let visibleView = true;
  let refreshTimer = null;
  function scheduleRefresh() {
    clearInterval(refreshTimer);
    refreshTimer = visibleView
      ? setInterval(() => { if (document.visibilityState !== 'hidden') refresh(); }, 2500)
      : null;
  }
  window.copyoverVisible = function (visible) {
    visibleView = !!visible;
    scheduleRefresh();
    if (visibleView) refresh();
  };
  refresh();
  scheduleRefresh();
