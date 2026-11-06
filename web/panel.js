  // History images that are no longer on disk: not requested again on every refresh.
  const NO_THUMBNAIL = new Set();
  // Returns the typed text (true if there's no field) or null on cancel.
  let _resolveDialog = null;
  function panelDialog({ title: title, text: text = '', value: value = null, accept: accept = 'OK', danger = false }) {
    const background = document.getElementById('modal-dialog');
    const field = document.getElementById('dialog-value');
    const paragraph = document.getElementById('dialog-text');
    const ok = document.getElementById('dialog-ok');
    document.getElementById('dialog-title').textContent = title;
    paragraph.textContent = text;
    paragraph.style.display = text ? '' : 'none';
    field.style.display = value === null ? 'none' : '';
    field.value = value === null ? '' : value;
    ok.textContent = accept;
    ok.style.background = danger ? 'var(--rose)' : '';
    ok.style.color = danger ? '#fff' : '';
    background.classList.add('open');
    if (value !== null) setTimeout(() => { field.focus(); field.select(); }, 30);
    return new Promise(resolve => { _resolveDialog = resolve; });
  }
  function resolveDialog(accepted) {
    const background = document.getElementById('modal-dialog');
    const field = document.getElementById('dialog-value');
    background.classList.remove('open');
    const resolve = _resolveDialog;
    _resolveDialog = null;
    if (!resolve) return;
    if (!accepted) return resolve(null);
    resolve(field.style.display === 'none' ? true : field.value);
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && _resolveDialog) resolveDialog(null); });

  // Names come from other devices on the network: always escaped before going into the HTML.
  function esc(v) {
    return String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }

  const VERSION_LOCAL = 2026110505;
  const SHORTCUT_LIMIT_BYTES = 500 * 1024 * 1024;
  let currentState = {};
  let openSelector = false;
  const ICONS = {
    iphone: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="2.5"/><line x1="11" y1="18" x2="13" y2="18"/></svg>',
    ipad: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2.5"/><line x1="11" y1="18" x2="13" y2="18"/></svg>',
    android: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2" width="14" height="20" rx="3"/><circle cx="12" cy="5" r="1"/><line x1="9" y1="19" x2="15" y2="19"/></svg>',
    laptop: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><line x1="2" y1="20" x2="22" y2="20"/></svg>',
    desktop: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
    pc: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><line x1="2" y1="20" x2="22" y2="20"/></svg>'
  };
  let selectedFiles = [];
  let selectedDestination = { id: 'iphone', name: 'My iPhone', isPc: false, destination: 'iphone' };

  function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.style.display = 'block';
    clearTimeout(window._toastTimer);
    window._toastTimer = setTimeout(() => { t.style.display = 'none'; }, 3200);
  }

  function copyText(txt, msg) {
    if (!txt) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(() => showToast(msg || 'Copied to clipboard')).catch(() => fallbackCopy(txt, msg));
    } else {
      fallbackCopy(txt, msg);
    }
  }

  function fallbackCopy(txt, msg) {
    const el = document.createElement('textarea');
    el.value = txt;
    el.style.position = 'fixed';
    el.style.opacity = '0';
    document.body.appendChild(el);
    el.select();
    try {
      document.execCommand('copy');
      showToast(msg || 'Copied to clipboard');
    } catch(e) {
      showToast('Select and copy it manually');
    }
    document.body.removeChild(el);
  }

  function changePhoneTab(tab) {
    for (const t of ['shortcut', 'android', 'qr']) {
      const panel = document.getElementById('panel-phone-' + t);
      const button = document.getElementById('tab-btn-' + t);
      if (!panel || !button) continue;
      panel.style.visibility = t === tab ? 'visible' : 'hidden';
      // A hidden tab takes no space, so a short tab doesn't leave an empty area to scroll.
      panel.style.display = t === tab ? 'flex' : 'none';
      panel.inert = t !== tab;
      button.setAttribute('aria-selected', String(t === tab));
    }
    if (tab === 'android') loadApkQr();
    const pcName = document.getElementById('android-pc-name');
    // The phone sees the PC by the name it announces on the network (mDNS), not the Windows one.
    const visibleName = currentState.network_name || currentState.pc_name;
    if (pcName && visibleName) pcName.textContent = visibleName;
  }

  // QR to download the Android app from this PC (30 minutes), or from GitHub when the PC has no APK yet.
  let apkQrTimer = null;
  async function loadApkQr() {
    const box = document.getElementById('qr-apk-box');
    const svg = document.getElementById('qr-apk-svg');
    const state = document.getElementById('qr-apk-state');
    clearInterval(apkQrTimer);
    box.classList.remove('expired');
    svg.textContent = 'Generating code…';
    try {
      const r = await fetch('/app/android-apk');
      const q = await r.json();
      if (!q.qr_svg) throw new Error('without QR');
      svg.innerHTML = q.qr_svg;
      if (!q.from_pc) {
        state.textContent = 'Downloads from GitHub';
        return;
      }
      const expires = Date.now() + (q.validity || 1800) * 1000;
      const paint = () => {
        const missing = Math.max(0, Math.round((expires - Date.now()) / 1000));
        if (missing === 0) {
          clearInterval(apkQrTimer);
          box.classList.add('expired');
          state.textContent = 'The code expired';
          return;
        }
        state.textContent = `Expires in ${Math.floor(missing / 60)}:${String(missing % 60).padStart(2, '0')}`;
      };
      paint();
      apkQrTimer = setInterval(paint, 1000);
    } catch (e) {
      svg.textContent = 'Couldn’t generate the code';
      state.textContent = 'Check that CopyOver is still running';
    }
  }

  // Single-use QR: the phone that scans it gets its own key. Expires after 5 minutes.
  let shortcutQrTimer = null;
  async function loadShortcutQr() {
    const box = document.getElementById('qr-shortcut-box');
    const svg = document.getElementById('qr-shortcut-svg');
    const state = document.getElementById('qr-shortcut-state');
    clearInterval(shortcutQrTimer);
    box.classList.remove('expired');
    svg.textContent = 'Generating code…';
    state.textContent = 'Valid for 5 minutes';
    try {
      const r = await fetch('/app/qr');
      const q = await r.json();
      if (!q.svg) throw new Error('without QR');
      svg.innerHTML = q.svg;
      const expires = Date.now() + (q.validity || 300) * 1000;
      const paint = () => {
        const missing = Math.max(0, Math.round((expires - Date.now()) / 1000));
        if (missing === 0) {
          clearInterval(shortcutQrTimer);
          box.classList.add('expired');
          state.textContent = 'The code expired';
          return;
        }
        state.textContent = `Expires in ${Math.floor(missing / 60)}:${String(missing % 60).padStart(2, '0')}`;
      };
      paint();
      shortcutQrTimer = setInterval(paint, 1000);
    } catch (e) {
      svg.textContent = 'Couldn’t generate the code';
      state.textContent = 'Check that CopyOver is still running';
    }
  }

  function copyShortcutSummary() {
    const text = id => (document.getElementById(id) || {}).textContent || '';
    const summary = `CopyOver - Manual shortcut setup (“Get Contents of URL” action)
Send PC: POST ${text('modal-shortcut-send-url')}
   Body: Form, field “archivo” (File) with Shortcut Input
Receive PC: GET ${text('modal-shortcut-fetch-url')}
Header on both:
- X-Key: ${text('modal-shortcut-key')} (the PC’s shared key; the QR in the panel gives each device its own key)
Device name (optional): at the end of each URL, ${'?device='} and the “Device Name” Shortcuts variable with URL Encode`;
    copyText(summary, 'Guide copied to clipboard');
  }

  // Settings with unsaved changes: instead of losing them, a warning shows inside the modal.
  // A second attempt to close (or «Discard») does close it.
  const SETTINGS_FIELDS = ['cfg-pc-name', 'cfg-keep', 'cfg-language', 'cfg-notices', 'cfg-theme', 'cfg-clipboard-notices'];
  let settingsLoaded = null;
  function settingsValues() {
    return JSON.stringify(SETTINGS_FIELDS.map(id => { const field = document.getElementById(id); return field.type === 'checkbox' ? field.checked : field.value; }));
  }
  function unsavedSettings() {
    return document.getElementById('modal-settings').classList.contains('open')
      && settingsLoaded !== null && settingsValues() !== settingsLoaded;
  }

  // Shown over the linking modal: closing it goes back there.
  function showShortcutQr(button) {
    const qr = button.querySelector('.shortcut-qr');
    const link = document.getElementById('qr-shortcut-link');
    document.getElementById('qr-shortcut-name').textContent = button.textContent.trim();
    link.href = qr.dataset.qr;
    link.innerHTML = qr.innerHTML;
    document.getElementById('modal-qr-shortcut').classList.add('open');
  }
  function closeShortcutQr() {
    document.getElementById('modal-qr-shortcut').classList.remove('open');
  }
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.getElementById('modal-qr-shortcut').classList.contains('open')) {
      e.stopImmediatePropagation();
      closeShortcutQr();
    }
  }, true);

  function closeModals(force) {
    const notice = document.getElementById('changes-notice');
    if (force !== true && unsavedSettings() && !notice.classList.contains('visible')) {
      notice.classList.add('visible');
      document.getElementById('btn-save-settings').focus();
      return;
    }
    notice.classList.remove('visible');
    // Closing without saving undoes the theme that was only being previewed.
    if (settingsLoaded !== null) document.documentElement.dataset.theme = JSON.parse(settingsLoaded)[SETTINGS_FIELDS.indexOf('cfg-theme')];
    settingsLoaded = null;
    clearInterval(shortcutQrTimer);
    document.querySelectorAll('.modal-backdrop').forEach(m => m.classList.remove('open'));
  }

  function sameNetwork(a, b) {
    const pa = String(a).split('.'), pb = String(b).split('.');
    if (pa.length !== 4 || pb.length !== 4) return true;
    // On 172.16-31.x.x networks (campuses, offices) the /24 subnets change from one building to another: two octets are enough.
    const octets = pa[0] === '172' ? 2 : 3;
    return pa.slice(0, octets).join('.') === pb.slice(0, octets).join('.');
  }

  function selectDestination(id, name, isPc, destination) {
    selectedDestination = { id, name: name, isPc, destination: destination || id };
    paintDestination();
    renderDevices();
    validateCurrentSelection();
  }

  // A PC gets it directly; for the phone it's prepared and the phone fetches it with «Receive PC».
  function paintDestination() {
    const sub = document.getElementById('target-subtitle');
    const btn = document.getElementById('btn-send-main');
    if (!selectedDestination.id) {
      sub.textContent = 'Link a phone or start CopyOver on another PC to send it something.';
      btn.style.display = 'none';
      btn.disabled = true;
      return;
    }
    btn.disabled = false;
    btn.style.display = selectedFiles.length ? '' : 'none';
    const name = `<strong>${esc(selectedDestination.name)}</strong>`;
    sub.innerHTML = selectedDestination.isPc
      ? `Goes straight to ${name}; a window will pop up there to accept it.`
      : selectedDestination.destination === 'android'
        ? `It’ll be ready for ${name}; on the phone, open CopyOver → Files and tap Receive.`
        : String(selectedDestination.destination).startsWith('guest:')
          ? `It shows up for ${name} on their CopyOver page; only that guest sees it.`
          : `It’ll be ready for ${name}; on the phone, run <strong>Receive PC</strong> to receive it.`;
    btn.textContent = selectedDestination.isPc ? `Send to ${selectedDestination.name}` : `Prepare for ${selectedDestination.name}`;
  }

  function renderDevices() {
    const cont = document.getElementById('devices-container');
    const items = [];

    const appleRaw = currentState.apple_devices || [];
    const seenApple = new Set();
    const appleList = [];
    appleRaw.forEach(d => {
      const nameText = (d.name || (d.type === 'ipad' ? 'My iPad' : (d.type === 'android' ? 'Android phone' : 'My iPhone'))).trim();
      // Two linked devices with the same name are two devices: otherwise the one that repeats never gets its card.
      const key = d.id ? 'id:' + d.id : nameText.toLowerCase();
      if (!seenApple.has(key)) {
        seenApple.add(key);
        appleList.push(d);
      }
    });
    const hasIphone = appleList.some(d => d.type === 'iphone');
    const hasIpad = appleList.some(d => d.type === 'ipad');

    appleList.sort((a, b) => (a.online ? 0 : 1) - (b.online ? 0 : 1));
    appleList.forEach(d => {
      const live = d.last_seen === 'live' || d.in_app || d.online;
      const nameText = d.name || (d.type === 'ipad' ? 'My iPad' : (d.type === 'android' ? 'Android phone' : 'My iPhone'));
      let icon = ICONS.iphone;
      const last = d.last_event || '';
      // If the phone's last IP isn't on the PC's current network, they won't see each other: saying so here
      // saves the minute Shortcuts takes to give up. If it answers, it's on the same network even if its IP looks foreign.
      const network = d.online ? 'same'
        : ['same', 'other', 'unknown'].includes(d.network) ? d.network
        : (d.ip && currentState.ip && !sameNetwork(d.ip, currentState.ip) ? 'other' : 'same');
      const onOtherNetwork = network === 'other';
      const deviceKind = d.type === 'ipad' ? 'iPad' : d.type === 'iphone' ? 'iPhone' : 'mobile phone';
      const typeDesc = onOtherNetwork
        ? `Last seen on a different network. Connect the ${deviceKind} to the same Wi-Fi as this PC.`
        : network === 'unknown'
          ? 'No news since this PC changed networks'
          : d.online
            ? (last || 'Responding now on this network')
            : d.linked
              ? (last ? `Not responding now · ${last}` : 'Not responding now')
              : 'With the shared key · scan the QR code to link it';
      if (d.type === 'ipad') icon = ICONS.ipad;
      else if (d.type === 'android') icon = ICONS.android;

      items.push({
        id: d.id || d.ip || d.type,
        destination: d.guest ? 'guest:' + d.ip : (d.type || d.ip),  // a guest only gets what goes to its own card
        realId: d.id || '',
        realIp: d.ip,
        ip: d.ip,
        name: nameText,
        icon: icon,
        desc: typeDesc,
        pill: onOtherNetwork ? 'On another network' : d.online ? 'Online' : d.linked ? 'Linked' : 'Not linked',
        notice: onOtherNetwork,
        repeats: d.repeats || null,
        order: onOtherNetwork ? 2 : d.online ? 0 : 1,
        isOwn: true,
        live: live,
        isPc: false
      });
    });

    (currentState.pcs || []).forEach(eq => {
      const isDesktop = eq.chassis === 'desktop';
      items.push({
        id: eq.ip,
        ip: eq.ip,
        name: eq.name || eq.ip,
        icon: isDesktop ? ICONS.desktop : ICONS.laptop,
        desc: isDesktop ? 'Desktop PC on this network' : 'Laptop on this network',
        pill: isDesktop ? 'Desktop PC' : 'Laptop',
        live: true,
        isPc: true
      });
    });

    window._destinationItems = items;
    renderRadar(items);

    // If the chosen target is gone (or there never was one), the first in the list is used.
    const current = items.find(it => it.id === selectedDestination.id) || items[0];
    selectedDestination = current ? { id: current.id, name: current.name, isPc: current.isPc, destination: current.destination || current.id } : { id: '', name: '', isPc: false, destination: '' };
    paintDestination();

    if (!items.length) {
      // No linked devices and no nearby PCs: offer linking instead of making up an iPhone.
      cont.innerHTML = `
        <div class="devices-empty">
          <div class="devices-empty-title">No devices yet</div>
          <div class="devices-empty-text">Link your iPhone or iPad with a QR code, or start CopyOver on another PC on this network.</div>
          <button class="btn btn-primary" type="button" onclick="openConnectPhoneModal()">Link phone</button>
        </div>`;
      return;
    }

    cont.innerHTML = items.map((it, i) => {
      const selected = selectedDestination.id === it.id;
      return `
        <div class="device-item ${selected ? 'selected' : ''}" onclick="selectByIndex(${i})" title="${it.ip ? 'IP ' + esc(it.ip) : ''}">
          <div class="device-left">
            <div class="device-avatar">
              ${it.icon}
              ${it.live ? '<span class="live-badge-dot"></span>' : ''}
            </div>
            <div style="min-width:0;">
              <div class="device-name" style="display:flex; align-items:center; gap:6px;">
                <span title="${esc(it.name)}">${esc(it.name)}</span>
                ${it.isOwn && it.realId ? `<button class="btn btn-icon-small" type="button" data-id="${esc(it.realId)}" data-name="${esc(it.name)}" onclick="event.stopPropagation(); renameDevice(this.dataset.id, this.dataset.name)" title="Rename" aria-label="Rename"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></button>` : ''}
                ${(it.isOwn && (it.realId || it.realIp)) ? `<button class="btn btn-icon-small" type="button" style="color:var(--rose);" data-id="${esc(it.realId || '')}" data-ip="${esc(it.realIp || '')}" onclick="event.stopPropagation(); removeDevice(this.dataset.id, this.dataset.ip)" title="Remove device" aria-label="Remove device"><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg></button>` : ''}
              </div>
              <div class="device-desc">${esc(it.desc)}</div>
              ${it.repeats ? `<div class="device-repeat" onclick="event.stopPropagation()">
                <div>Is this the same device as <strong>${esc(it.repeats.name)}</strong>?</div>
                <div class="device-desc">${esc(it.repeats.linked)}</div>
                <div class="device-desc">If you linked it again, replace it: its old key stops working.</div>
                <div class="device-repeat-buttons">
                  <button class="btn btn-primary" type="button" style="padding:5px 10px; font-size:12px;" data-id="${esc(it.realId)}" onclick="resolveRepeated(this.dataset.id, true)">Replace</button>
                  <button class="btn" type="button" style="padding:5px 10px; font-size:12px;" data-id="${esc(it.realId)}" onclick="resolveRepeated(this.dataset.id, false)">They’re different</button>
                </div>
              </div>` : ''}
            </div>
          </div>
          <div class="device-actions">
            <span class="pill ${it.notice ? 'notice' : it.isOwn ? 'live' : ''}">${esc(it.pill)}</span>
            <button class="btn" type="button" style="padding:6px 11px; font-size:12px;" data-i="${i}" onclick="event.stopPropagation(); sendToDevice(Number(this.dataset.i))">Send files…</button>
          </div>
        </div>
      `;
    }).join('');
  }

  async function sendToDevice(i) {
    selectByIndex(i);
    await chooseNativeFiles();
    if (selectedFiles.length) await runMainSend(document.getElementById('btn-send-main'));
  }

  function validateCurrentSelection() {
    paintDestination();  // the send button only shows when files are chosen
    const notice = document.getElementById('delivery-notice');
    const noticeTxt = document.getElementById('delivery-notice-text');
    const noticeBtn = document.getElementById('delivery-notice-btn');

    if (!selectedFiles.length) {
      const of = currentState.phone_offer;
      if (of && !of.delivered) {
        notice.className = of.exceeds_shortcut ? 'notice-box amber' : 'notice-box emerald';
        const onAndroid = of.destination === 'android';
        noticeTxt.innerHTML = onAndroid
          ? `<strong>Ready for Android:</strong> ${esc(of.name)} (${esc(of.size_text)}). Open CopyOver → Files and tap Receive.`
          : of.exceeds_shortcut
            ? (of.destination === 'ipad'
              ? `<strong>${esc(of.name)} (${esc(of.size_text)})</strong> is over the shortcut’s 500 MB: get it in the iPad’s web app.`
              : of.destination === 'iphone'
                ? `<strong>${esc(of.name)} (${esc(of.size_text)})</strong> is over the shortcut’s 500 MB: get it in the iPhone’s web app.`
                : `<strong>${esc(of.name)} (${esc(of.size_text)})</strong> is over the shortcut’s 500 MB: get it in the phone’s web app.`)
            : `<strong>Ready for the phone:</strong> ${esc(of.name)} (${esc(of.size_text)}).`;
        noticeBtn.style.display = onAndroid ? 'none' : 'inline-flex';
        return;
      } else if (of && of.delivered) {
        notice.className = 'notice-box emerald';
        noticeTxt.innerHTML = `<strong>Delivered to the phone:</strong> ${esc(of.name)}`;
        noticeBtn.style.display = 'none';
        return;
      }
      notice.className = 'notice-box';
      return;
    }

    const totalBytes = selectedFiles.reduce((acc, a) => acc + (a.size || 0), 0);
    // Android receives in chunks with no cap; the warning is only for the iOS shortcut.
    if (!selectedDestination.isPc && selectedDestination.destination !== 'android' && totalBytes > SHORTCUT_LIMIT_BYTES) {
      notice.className = 'notice-box amber';
      noticeTxt.innerHTML = selectedDestination.destination === 'ipad'
        ? `<strong>Over 500 MB:</strong> the iOS shortcut goes up to 500 MB, so the iPad gets it in the web app.`
        : selectedDestination.destination === 'iphone'
          ? `<strong>Over 500 MB:</strong> the iOS shortcut goes up to 500 MB, so the iPhone gets it in the web app.`
          : `<strong>Over 500 MB:</strong> the iOS shortcut goes up to 500 MB, so the phone gets it in the web app.`;
      noticeBtn.style.display = 'inline-flex';
    } else {
      notice.className = 'notice-box';
    }
  }

  async function chooseNativeFiles() {
    if (openSelector) return;
    openSelector = true;
    try {
      const api = apiLinux();
      if (api) {
        const paths = await api.choose_paths(false);
        if (paths.length) await useLocalPaths(paths);
        return;
      }
      const r = await fetch('/app/choose-files', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
      const d = await r.json();
      if (d.busy) return;
      if (d.files && d.files.length) {
        selectedFiles = d.files;
        const names = d.files.length === 1 ? d.files[0].name : `${d.files.length} files selected`;
        document.getElementById('drop-title').textContent = `${names} (${d.total_text})`;
        document.getElementById('drop-sub').textContent = 'Ready to send';
        validateCurrentSelection();
      }
    } catch (e) {
      showToast('Couldn’t open the file picker');
    } finally {
      openSelector = false;
    }
  }

  async function runMainSend(btn) {
    if (!selectedFiles.length) {
      await chooseNativeFiles();
      if (!selectedFiles.length) return;
    }
    const origText = btn.textContent;
    btn.textContent = 'Preparing transfer...';
    try {
      const paths = selectedFiles.map(a => a.path);
      const r = await fetch('/app/send', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ destination: selectedDestination.destination || selectedDestination.id, paths: paths })
      });
      const res = await r.json();
      if (res.ok) {
        selectedFiles = [];
        document.getElementById('drop-title').textContent = 'Choose files or drag them here';
        document.getElementById('drop-sub').textContent = 'Up to 10 GB';
        showToast(res.message || 'Ready to receive');
        await updateState();
        if (res.exceeds_shortcut) {
          openConnectPhoneModal('qr');
        }
      } else {
        showToast(res.message || 'Error preparing the transfer');
      }
    } catch (e) {
      showToast('Local connection error');
    } finally {
      btn.textContent = origText;
    }
  }

  async function openConnectPhoneModal(tab) {
    document.getElementById('modal-phone').classList.add('open');
    changePhoneTab(tab || 'shortcut');
    try {
      const rPerf = await fetch('/app/profile');
      const p = await rPerf.json();
      const port = p.port || 8765;
      const base = currentState.ip ? `http://${currentState.ip}:${port}` : `http://${p.name || 'puente-pc'}.local:${port}`;
      document.getElementById('modal-shortcut-send-url').textContent = `${base}/send`;
      document.getElementById('modal-shortcut-fetch-url').textContent = `${base}/receive`;
      document.getElementById('modal-shortcut-key').textContent = p.key || '';
    } catch(e) {}
    loadShortcutQr();
    await loadGuestQr();
  }

  async function loadGuestQr() {
    try {
      const r = await fetch('/app/guest-token');
      const d = await r.json();
      if (d.ok) {
        document.getElementById('qr-mobile-svg').innerHTML = d.qr_svg;
        document.getElementById('qr-mobile-url').textContent = d.url;
        guestQrExpires = d.expires * 1000;
        guestsClock();
      }
    } catch (e) {}
  }

  // Countdown of the guest QR and of every guest that is connected. A QR nobody scanned is replaced when it
  // runs out; a guest's time starts over while its page stays open.
  let guestQrExpires = 0;
  const minSecs = ms => { const s = Math.max(0, Math.ceil(ms / 1000)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  function guestsClock() {
    const panel = document.getElementById('panel-phone-qr');
    if (!panel || panel.style.visibility !== 'visible') return;
    const now = Date.now();
    const expires = document.getElementById('qr-mobile-expires');
    if (guestQrExpires && now >= guestQrExpires) { guestQrExpires = 0; loadGuestQr(); }
    if (expires) expires.textContent = guestQrExpires ? 'This code expires in ' + minSecs(guestQrExpires - now) : '';
    const list = document.getElementById('active-guests');
    if (!list) return;
    const activeOnes = (currentState.guests || []).filter(i => i.expires * 1000 > now);
    list.replaceChildren(...activeOnes.map(i => {
      const row = document.createElement('div');
      row.textContent = i.name + ' · ' + 'access for ' + minSecs(i.expires * 1000 - now) + ' more';
      return row;
    }));
    if (activeOnes.length) list.prepend(Object.assign(document.createElement('strong'), {textContent: 'Connected now', style: 'display:block; color:var(--text);'}));
  }
  setInterval(guestsClock, 1000);

  // Every guest link stops working at once; the QR on screen is replaced by a new one.
  async function cutGuests(btn) {
    btn.disabled = true;
    try {
      const r = await fetch('/app/cut-guests', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error();
      await loadGuestQr();
      showToast('Done: the earlier links no longer work');
    } catch (e) {
      showToast('Couldn’t cut off access');
    } finally {
      btn.disabled = false;
    }
  }

  async function changeCommonKey(btn) {
    const safe = await panelDialog({
      title: 'Change the shared key?',
      text: 'Shortcuts set up by hand and devices linked with earlier versions will stop working until you link them again with the QR code. Phones with their own key and PCs linked with a code keep working.',
      accept: 'Change key', danger: true,
    });
    if (!safe) return;
    btn.disabled = true;
    try {
      const r = await fetch('/app/change-common-key', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ confirm: true }) });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error();
      document.getElementById('cfg-key').textContent = d.key;
      document.getElementById('modal-shortcut-key').textContent = d.key;
      showToast('Shared key changed');
    } catch (e) {
      showToast('Couldn’t change the key');
    } finally {
      btn.disabled = false;
    }
  }

  function openWifiDirectModal() {
    document.getElementById('modal-wfd').classList.add('open');
    syncWifiDirectUi(currentState.wifi_direct);
    loadWfdNetworkName();
    // Already on (or the panel reloaded): QR codes only arrive when requested, so request them.
    const wfd = currentState.wifi_direct;
    if (wfd && wfd.active && !document.getElementById('wfd-qr-wifi').innerHTML.trim()) {
      fetch('/app/wifi-direct', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'start' }) })
        .then(r => r.json()).then(d => { currentState.wifi_direct = d; syncWifiDirectUi(d); paintWifiDirectQr(d); })
        .catch(() => {});
    }
  }

  async function loadWfdNetworkName() {
    try {
      const r = await fetch('/app/wifi-direct-network', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
      const d = await r.json();
      document.getElementById('wfd-prefix').textContent = d.prefix || 'CopyOver-';
      document.getElementById('wfd-suffix').value = d.suffix || '';
    } catch (e) {}
  }

  async function applyWfdNetworkChange(change, btn) {
    btn.disabled = true;
    try {
      const r = await fetch('/app/wifi-direct-network', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(change) });
      const d = await r.json();
      if (d.invalid) {
        showToast(d.message || 'Invalid network name');
        return;
      }
      document.getElementById('wfd-suffix').value = d.suffix || '';
      const wasActive = currentState.wifi_direct && currentState.wifi_direct.active;
      if (d.qr_wifi_svg || (wasActive && !d.active)) {
        // Restarted with the new name or password (or it couldn't start again).
        currentState.wifi_direct = d;
        syncWifiDirectUi(d);
        paintWifiDirectQr(d);
      }
      if (!d.ok) {
        showToast(d.message || 'Couldn’t start Wi-Fi Direct');
      } else {
        showToast('Done: scan the QR again on the devices that were using the network');
      }
    } catch (e) {
      showToast('Couldn’t change the Wi-Fi Direct network');
    } finally {
      btn.disabled = false;
    }
  }

  function saveWfdNetworkName(btn) {
    const suffix = document.getElementById('wfd-suffix').value.trim();
    if (!/^[A-Za-z0-9-]{1,20}$/.test(suffix)) {
      showToast('The network name can only have letters, numbers and hyphens (1 to 20).');
      return;
    }
    applyWfdNetworkChange({ suffix: suffix }, btn);
  }

  async function changeWfdKey(btn) {
    const safe = await panelDialog({
      title: 'Change the Wi-Fi Direct password?',
      text: 'Devices that already saved the network have to scan the QR again.',
      accept: 'New password',
    });
    if (!safe) return;
    applyWfdNetworkChange({ new_key: true }, btn);
  }

  function paintWifiDirectQr(d) {
    if (!(d && d.active && d.qr_wifi_svg)) return false;
    document.getElementById('wfd-qr-section').style.display = 'flex';
    document.getElementById('wfd-qr-wifi').innerHTML = d.qr_wifi_svg;
    document.getElementById('wfd-qr-app').innerHTML = d.qr_app_svg;
    document.getElementById('wfd-cred-txt').textContent = `Network: ${d.ssid} · Key: ${d.key}`;
    return true;
  }

  function syncWifiDirectUi(wfd) {
    const active = wfd && wfd.active;
    const btnTop = document.getElementById('btn-wfd-top');
    const txtTop = document.getElementById('wfd-top-text');
    const btnToggle = document.getElementById('btn-wfd-toggle');
    const stTitle = document.getElementById('wfd-status-title');
    const stSub = document.getElementById('wfd-status-sub');
    const secQr = document.getElementById('wfd-qr-section');
    // The network name and password are only editable while Wi-Fi Direct is off.
    document.getElementById('wfd-network-config').style.display = active ? 'none' : 'flex';
    document.getElementById('wfd-network-locked').style.display = active ? 'block' : 'none';

    if (active) {
      btnTop.classList.add('active');
      txtTop.textContent = `Wi-Fi Direct on (${wfd.ssid})`;
      stTitle.textContent = `On: ${wfd.ssid}`;
      stSub.textContent = `Key: ${wfd.key} · Direct IP: ${wfd.ip}`;
      btnToggle.textContent = 'Turn off Wi-Fi Direct';
      btnToggle.className = 'btn';
    } else {
      btnTop.classList.remove('active');
      txtTop.textContent = 'Wi-Fi Direct';
      stTitle.textContent = 'Status: Off';
      stSub.textContent = 'Turn it on to connect your devices directly without going through the router';
      btnToggle.textContent = 'Turn on Wi-Fi Direct';
      btnToggle.className = 'btn btn-primary';
      secQr.style.display = 'none';
    }
  }

  async function toggleWifiDirect(btn) {
    const active = currentState.wifi_direct && currentState.wifi_direct.active;
    const action = active ? 'stop' : 'start';
    btn.textContent = active ? 'Turning off...' : 'Starting the direct signal...';
    try {
      const r = await fetch('/app/wifi-direct', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ action: action })
      });
      const d = await r.json();
      currentState.wifi_direct = d;
      syncWifiDirectUi(d);
      if (paintWifiDirectQr(d)) {
        showToast('Wi-Fi Direct is on');
      } else if (!d.active && action === 'start') {
        showToast(d.message || 'Couldn’t start Wi-Fi Direct');
      } else {
        showToast('Wi-Fi Direct is off');
      }
    } catch (e) {
      showToast('Error switching Wi-Fi Direct');
    }
  }

  async function openSettingsModal() {
    settingsLoaded = null;
    document.getElementById('changes-notice').classList.remove('visible');
    document.getElementById('modal-settings').classList.add('open');
    try {
      const r = await fetch('/app/profile');
      const p = await r.json();
      document.getElementById('cfg-pc-name').value = p.name || '';
      document.getElementById('cfg-folder').value = p.download_folder || '';
      document.getElementById('btn-reset-folder').style.display = p.download_folder_is_default ? 'none' : '';
      document.getElementById('cfg-keep').value = String(p.keep_days || 0);
      document.getElementById('cfg-language').value = p.language || 'en';
      loadedLanguage = p.language || 'en';
      document.getElementById('cfg-notices').value = p.notifications === false ? '0' : '1';
      document.getElementById('cfg-clipboard-notices').checked = !!p.clipboard_notifications;
      document.getElementById('cfg-theme').value = p.theme || 'dark';
      document.getElementById('cfg-mdns').textContent = `http://${p.name}.local:${p.port}`;
      document.getElementById('cfg-ip').textContent = `http://${currentState.ip || '127.0.0.1'}:${p.port}`;
      document.getElementById('cfg-key').textContent = p.key || '';
      settingsLoaded = settingsValues();
    } catch (e) {}
  }

  async function changeDownloadsFolder() {
    if (openSelector) return;
    openSelector = true;
    let r;
    try {
      r = await fetch('/app/choose-downloads-folder', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
    } finally {
      openSelector = false;
    }
    const d = await r.json();
    if (d.ok && d.folder) {
      document.getElementById('cfg-folder').value = d.folder;
      paintFolder(d.folder);
      document.getElementById('btn-reset-folder').style.display = '';
      showToast('Folder updated');
    }
  }

  async function resetDownloadsFolder() {
    const r = await fetch('/app/reset-downloads-folder', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
    const d = await r.json();
    if (d.ok && d.folder) {
      document.getElementById('cfg-folder').value = d.folder;
      document.getElementById('btn-reset-folder').style.display = 'none';
      paintFolder(d.folder);
      showToast('Folder reset');
    }
  }

  // The folder name shows («Documents»); the full path goes in the title.
  function paintFolder(path) {
    const el = document.getElementById('hist-folder');
    const parts = String(path).split(/[\\/]+/).filter(Boolean);
    el.textContent = parts.length ? parts[parts.length - 1] : String(path);
    el.title = path;
  }

  var loadedLanguage = '';

  async function saveSettings(btn) {
    const body = {
      name: document.getElementById('cfg-pc-name').value.trim(),
      keep_days: Number(document.getElementById('cfg-keep').value),
      language: document.getElementById('cfg-language').value,
      notifications: document.getElementById('cfg-notices').value === '1',
      clipboard_notifications: document.getElementById('cfg-clipboard-notices').checked,
      theme: document.getElementById('cfg-theme').value,
    };
    await fetch('/app/profile', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    if (body.language !== loadedLanguage) { location.reload(); return; }  // the panel is served already translated
    settingsLoaded = settingsValues();  // saved: closing must keep the new theme, not undo it
    showToast('Settings saved');
    closeModals(true);
    updateState();
  }

  async function openDownloadsFolder(type = 'descargas') {
    try {
      const r = await fetch('/app/open-folder', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({type: type}) });
      const d = await r.json();
      if (d.ok) {
        showToast('Opening the folder in File Explorer...');
      } else {
        showToast('Couldn’t open the folder');
      }
    } catch (e) {
      showToast('Error opening the folder');
    }
  }

  async function openHistoryItem(id, folder) {
    try {
      const r = await fetch('/app/open-history', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id, folder: folder}) });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showToast(d.message || 'Couldn’t open it');
      }
    } catch (e) {
      showToast('Couldn’t open it');
    }
  }

  function renderRadar(items) {
    const layer = document.getElementById('radar-nodos');
    if (!layer) return;
    const signature = items.map(it => it.id + (it.live ? '1' : '0')).join('|') + '#' + selectedDestination.id;
    if (layer.dataset.firma === signature) return;
    layer.dataset.firma = signature;
    const n = items.length || 1;
    layer.innerHTML = items.map((it, i) => {
      const ang = (-90 + (360 / n) * i) * Math.PI / 180;
      const r = it.isPc ? 30 : 41;
      const x = 50 + r * Math.cos(ang);
      const y = 50 + r * Math.sin(ang);
      const selected = selectedDestination.id === it.id;
      return `
        <button class="radar-node ${selected ? 'picked' : ''}" data-indice="${i}" style="left:${x}%; top:${y}%; animation-delay:${i * 60}ms;" onclick="selectByIndex(${i})" title="${esc(it.desc)}">
          <span class="bubble">${it.icon}${it.live ? '<span class="live-badge-dot"></span>' : ''}</span>
          <span class="label">${esc(it.name)}</span>
        </button>`;
    }).join('');
    layer.querySelectorAll('.radar-node').forEach(node => {
      const idx = parseInt(node.dataset.index, 10);
      node.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        node.classList.add('drag-target');
      });
      node.addEventListener('dragleave', (e) => {
        e.preventDefault();
        e.stopPropagation();
        node.classList.remove('drag-target');
      });
      node.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        node.classList.remove('drag-target');
        selectByIndex(idx);
        await receiveDrag(e.dataTransfer);
      });
    });
    const pcs = items.filter(it => it.isPc).length;
    document.getElementById('radar-state').textContent =
      pcs ? `${pcs} PC${pcs > 1 ? 's' : ''} nearby` : '';
  }

  function selectByIndex(i) {
    const it = (window._destinationItems || [])[i];
    if (it) selectDestination(it.id, it.name, it.isPc, it.destination);
  }

    async function renameDevice(id, current) {
    const response = await panelDialog({ title: 'Device name', value: current, accept: 'Save' });
    const name = (response || '').trim();
    if (!name || name === current) return;
    try {
      const r = await fetch('/app/change-device-type', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id, name: name})
      });
      if (!r.ok) throw new Error();
      showToast('Device renamed');
      await updateState();
    } catch (e) {
      showToast('Couldn’t rename it');
    }
  }

    async function removeDevice(id, ip) {
    const safe = await panelDialog({
      title: 'Remove this device?',
      text: id ? 'Its key will stop working. To use it again it will have to scan a new QR code.' : 'It won’t show up in the list until it connects again.',
      accept: 'Remove', danger: true,
    });
    if (!safe) return;
    try {
      const r = await fetch('/app/remove-device', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id, ip})
      });
      if (!r.ok) throw new Error();
      showToast('Device removed');
      await updateState();
    } catch (e) {
      showToast('Couldn’t remove the device');
    }
  }

  async function resolveRepeated(id, replace) {
    try {
      const r = await fetch('/app/resolve-repeated', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id, replace: replace})
      });
      if (!r.ok) throw new Error();
      showToast(replace ? 'Done: only the new one is left' : 'Done: both stay');
      await updateState();
    } catch (e) {
      showToast('Couldn’t save. Try again.');
    }
  }

  async function scanNow(btn) {
    const radar = document.getElementById('radar');
    const t = btn.textContent;
    btn.textContent = 'Searching...';
    btn.disabled = true;
    radar.classList.add('searching');
    document.getElementById('radar-state').textContent = 'Looking for other PCs…';
    try {
      await fetch('/app/scan', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
      await new Promise(res => setTimeout(res, 2500));
      document.getElementById('radar-nodos').dataset.firma = '';
      await updateState();
      if (!(currentState.pcs || []).length) {
        document.getElementById('radar-state').textContent = 'No other PC with CopyOver found on this network';
      }
    } catch (e) {
      document.getElementById('radar-state').textContent = 'Couldn’t search. Try again.';
    } finally {
      radar.classList.remove('searching');
      btn.textContent = t;
      btn.disabled = false;
    }
  }

  async function leaveTextReady(form) {
    const field = document.getElementById('phone-text');
    const text = field.value.trim();
    if (!text) return;
    try {
      const r = await fetch('/app/send-text', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ text: text, destination: selectedDestination.destination || '' }) });
      const d = await r.json();
      if (!d.ok) throw new Error(d.message);
      field.value = '';
      showToast('Text ready for the phone');
      await updateState();
    } catch (e) {
      showToast('Couldn’t get the text ready');
    }
  }

  async function chooseNativeFolder() {
    if (openSelector) return;
    openSelector = true;
    try {
      const api = apiLinux();
      if (api) {
        const paths = await api.choose_paths(true);
        if (paths.length) await useLocalPaths(paths);
        return;
      }
      const r = await fetch('/app/choose-folder', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
      const d = await r.json();
      if (d.busy) return;
      if (d.folder) {
        selectedFiles = [d.folder];
        document.getElementById('drop-title').textContent = `Folder: ${d.folder.name} (${d.folder.size_text})`;
        document.getElementById('drop-sub').textContent = 'It will be zipped automatically so it goes in one piece';
        validateCurrentSelection();
      }
    } catch (e) {
      showToast('Couldn’t open the folder picker');
    } finally {
      openSelector = false;
    }
  }

  // Only the Linux window exposes it: the system picker and the paths of a drop (WebKitGTK gives no files).
  function apiLinux() {
    const api = window.pywebview && window.pywebview.api;
    return api && api.choose_paths ? api : null;
  }

  // Paths on this PC (from the Linux picker or a drop): CopyOver uses them in place, without uploading them.
  async function useLocalPaths(paths) {
    const r = await fetch('/app/local-paths', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ paths: paths }) });
    const d = await r.json();
    if (!d.files || !d.files.length) return false;
    selectedFiles = d.files;
    const single = d.files.length === 1 ? d.files[0] : null;
    if (single && single.is_directory) {
      document.getElementById('drop-title').textContent = `Folder: ${single.name} (${d.total_text})`;
      document.getElementById('drop-sub').textContent = 'It will be zipped automatically so it goes in one piece';
    } else {
      document.getElementById('drop-title').textContent = `${single ? single.name : `${d.files.length} files selected`} (${d.total_text})`;
      document.getElementById('drop-sub').textContent = 'Ready to send';
    }
    validateCurrentSelection();
    return true;
  }

  // true if something was dropped: files (Windows) or paths (Linux).
  async function receiveDrag(dt) {
    if (dt && dt.files && dt.files.length) {
      await uploadDraggedFiles(dt.files);
      return true;
    }
    const api = apiLinux();
    const paths = api ? await api.last_drag() : [];
    if (!paths.length) return false;
    try {
      if (await useLocalPaths(paths)) showToast('Files ready to send');
      else showToast('No file came through. To send a folder, use “Send a folder”.');
    } catch (e) {
      showToast('Error processing the dropped files');
    }
    return true;
  }

  async function uploadDraggedFiles(files) {
    if (!files || !files.length) return;
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('file_' + i, files[i], files[i].name);
    }
    showToast('Loading dropped files...');
    try {
      const r = await fetch('/app/upload-dragged-files', { method: 'POST', body: formData });
      const d = await r.json();
      if (d.files && d.files.length) {
        selectedFiles = d.files;
        const names = d.files.length === 1 ? d.files[0].name : `${d.files.length} files dropped`;
        document.getElementById('drop-title').textContent = `${names} (${d.total_text})`;
        document.getElementById('drop-sub').textContent = 'Ready to send';
        validateCurrentSelection();
        showToast('Files ready to send');
      } else {
        showToast('No file came through. To send a folder, use “Send a folder”.');
      }
    } catch (e) {
      showToast('Error processing the dropped files');
    }
  }

  async function copyHistoryItem(id) {
    try {
      const r = await fetch('/app/copy-history', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id})
      });
      const d = await r.json();
      showToast(d.message || (d.ok ? 'Copied to clipboard' : 'Error copying'));
    } catch (e) {
      showToast('Error copying');
    }
  }

  // The first click asks for confirmation on the same button; a second one within 3 s clears the list.
  let confirmClear = null;
  async function cleanHistory(button) {
    const label = button && button.querySelector('span');
    if (label && !confirmClear) {
      const original = label.textContent;
      label.textContent = 'Clear the list?';
      confirmClear = setTimeout(() => { label.textContent = original; confirmClear = null; }, 3000);
      return;
    }
    if (confirmClear) {
      clearTimeout(confirmClear);
      confirmClear = null;
      if (label) label.textContent = 'Clear list';
    }
    try {
      const r = await fetch('/app/clear-history', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: '{}'
      });
      if (!r.ok) throw new Error();
      const d = await r.json();
      showToast(d.remaining ? 'List cleared; pinned items stay' : 'List cleared; the files are still in the folder');
      await updateHistory(true);
    } catch (e) {
      showToast('Couldn’t clear the list. Try again.');
    }
  }

  async function deleteHistoryItem(id) {
    try {
      const r = await fetch('/app/delete-history', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id})
      });
      if (!r.ok && r.status !== 404) throw new Error();
      showToast('Entry removed from history');
      await updateHistory(true);
    } catch (e) { showToast('Couldn’t delete the entry. Try again.'); }
  }

  async function pinHistoryItem(id, pinned) {
    // Shows right away; if the server doesn't save it, the next refresh puts it back.
    const item = HIST_ITEMS.find(h => h.id === id);
    if (item) { item.pinned = pinned; paintHistory(); }
    try {
      const r = await fetch('/app/pin-history', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id, pinned: pinned})
      });
      if (!r.ok) throw new Error();
      showToast(pinned ? 'Pinned: it stays on top and won’t be cleaned up' : 'No longer pinned');
    } catch (e) {
      showToast('Couldn’t save. Try again.');
    }
    await updateHistory(true);
  }

  const COPY_TITLE = 'Copy to clipboard';
  function whenHistory(h) {
    if (!h.timestamp) return h.time || '';
    const f = new Date(h.timestamp * 1000);
    const time = `${String(f.getHours()).padStart(2, '0')}:${String(f.getMinutes()).padStart(2, '0')}`;
    const today = new Date();
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    if (f.toDateString() === today.toDateString()) return time;
    if (f.toDateString() === yesterday.toDateString()) return `yesterday ${time}`;
    return `${String(f.getDate()).padStart(2, '0')}/${String(f.getMonth() + 1).padStart(2, '0')} ${time}`;
  }

  const FILE_ICON_H = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
  const TEXT_ICON_H = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="16" y2="12"/><line x1="4" y1="18" x2="12" y2="18"/></svg>';
  const LINK_ICON_H = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
  const PIN_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="17" x2="12" y2="22"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24z"/></svg>';
  const RECENT_VISIBLE = 8;   // without a search: pinned items and the latest 8
  const MAX_RESULTS = 150;       // when searching, so a huge list doesn't freeze the panel
  let HIST_ITEMS = [];
  let HIST_SIGNATURE = '';

  function noIp(origin) {
    return String(origin || '').replace(/\s*\(\d{1,3}(\.\d{1,3}){3}\)$/, '');
  }
  // Ignores accents and case: «envio» finds «Envío».
  function normalizeSearch(v) {
    return String(v ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }
  function searchableText(h) {
    if (h._searchable === undefined) {
      const file = String(h.path || '').split(/[\\/]/).pop();
      h._searchable = normalizeSearch([h.detail, h.name, file, h.link, noIp(h.origin)].filter(Boolean).join(' '));
    }
    return h._searchable;
  }

  async function updateHistory(force) {
    try {
      const r = await fetch('/app/history');
      const text = await r.text();
      // The status is polled every few seconds: if nothing changed the list isn't redrawn.
      if (!force && text === HIST_SIGNATURE) return;
      HIST_SIGNATURE = text;
      HIST_ITEMS = JSON.parse(text) || [];
      paintHistory();
    } catch (e) {}
  }

  function historyRow(h) {
    const isLink = !h.path && !!h.link;
    const isText = !h.path && /^(Texto|Nota): /.test(h.detail || '');
    const isImage = /\.(png|jpe?g|gif|webp|bmp)$/i.test(h.path || '') && h.exists !== false && !NO_THUMBNAIL.has(h.id);
    const name = isLink ? h.link : (isText ? h.detail.replace(/^(Texto|Nota): /, '') : h.detail);
    const view = isImage
      ? `<img class="history-thumb" src="/app/history/image?id=${encodeURIComponent(h.id)}" alt="" loading="lazy" data-id="${esc(h.id)}" onerror="SIN_MINIATURA.add(this.dataset.id); this.style.visibility='hidden'">`
      : `<div class="history-icon">${isLink ? LINK_ICON_H : (isText ? TEXT_ICON_H : FILE_ICON_H)}</div>`;
    const pinned = !!h.pinned;
    const labelPin = pinned ? 'Unpin' : 'Pin to top';
    const buttonPin = `<button class="btn-pin ${pinned ? 'pinned' : ''}" type="button" data-id="${esc(h.id)}" data-pinned="${pinned ? '1' : ''}" onclick="event.stopPropagation(); pinHistoryItem(this.dataset.id, !this.dataset.pinned)" title="${labelPin}" aria-label="${labelPin}" aria-pressed="${pinned}">${PIN_ICON}</button>`;
    const openItem = (h.path || isLink)
      ? `<button class="btn" type="button" style="padding:6px 10px; font-size:12px;" data-id="${esc(h.id)}" onclick="event.stopPropagation(); openHistoryItem(this.dataset.id, false)" title="${esc(isLink ? h.link : '')}">Open</button>`
      : '';
    const folder = h.path
      ? `<button class="btn" type="button" style="padding:6px 8px;" data-id="${esc(h.id)}" onclick="event.stopPropagation(); openHistoryItem(this.dataset.id, true)" title="Show in folder" aria-label="Show in folder">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
            </button>`
      : '';
    return `
        <div class="history-item ${pinned ? 'pinned' : ''}" data-id="${esc(h.id)}" onclick="copyHistoryItem(this.dataset.id)" title="${esc(COPY_TITLE)}">
          ${view}
          <div class="history-info">
            <div class="history-name ${isText || isLink ? 'text' : ''}">${esc(name)}</div>
            <div class="history-meta">${pinned ? '<span class="pinned-mark">Pinned</span> · ' : ''}${esc(whenHistory(h))} · ${esc(noIp(h.origin))}${!isText && !isLink && h.size_text ? ' · ' + esc(h.size_text) : ''}</div>
          </div>
          <div class="history-actions">${openItem}${folder}${buttonPin}<button class="btn-delete" type="button" data-id="${esc(h.id)}" onclick="event.stopPropagation(); deleteHistoryItem(this.dataset.id)" title="Delete from history" aria-label="Delete from history"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V4h6v2M5 6l1 14h12l1-14M10 10v6M14 10v6"/></svg></button></div>
        </div>`;
  }

  // Filters in the browser without requesting anything: by detail, file name, link and device.
  function paintHistory() {
    const cont = document.getElementById('history-container');
    const field = document.getElementById('hist-search');
    const items = HIST_ITEMS;
    document.getElementById('hist-search-box').hidden = !items.length && !field.value;
    if (!items.length) {
      cont.innerHTML = '<div class="history-empty">Nothing received yet</div>';
      return;
    }
    const searched = field.value.trim();
    let visible;
    if (searched) {
      const words = normalizeSearch(searched).split(/\s+/).filter(Boolean);
      visible = items.filter(h => { const text = searchableText(h); return words.every(p => text.includes(p)); });
      if (!visible.length) {
        cont.innerHTML = `<div class="history-empty">Nothing matches “${esc(searched)}”. Try another word or clear the search.</div>`;
        return;
      }
    } else {
      const pinnedItems = items.filter(h => h.pinned);
      visible = pinnedItems.concat(items.filter(h => !h.pinned).slice(0, RECENT_VISIBLE));
    }
    const extra = visible.length - MAX_RESULTS;
    // Clicking the row copies to the clipboard; the buttons open the file, its folder or the link.
    cont.innerHTML = visible.slice(0, MAX_RESULTS).map(historyRow).join('')
      + (extra > 0 ? `<div class="history-empty">${extra} more: type something more specific to see them.</div>` : '');
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }

  function paintDiagnostics(problems) {
    const card = document.getElementById('diagnostics');
    const list = document.getElementById('diagnostics-list');
    card.hidden = !problems.length;
    list.innerHTML = problems.map(p => `
      <div class="diagnostics-item">
        <div><strong>${escapeHtml(p.title)}</strong><p>${escapeHtml(p.detail)}</p></div>
        ${p.fix ? `<button class="btn btn-primary" type="button" data-key="${escapeHtml(p.key)}" onclick="fixProblem(this)">${escapeHtml(p.fix)}</button>` : ''}
      </div>`).join('');
  }

  async function loadDiagnostics(button) {
    if (button) button.disabled = true;
    try {
      const r = await fetch('/app/diagnostics');
      paintDiagnostics(await r.json());
    } catch (e) {
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function fixProblem(button) {
    const text = button.textContent;
    button.disabled = true;
    button.textContent = 'Accept the Windows prompt…';
    try {
      const r = await fetch('/app/fix', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: button.dataset.key }) });
      const d = await r.json();
      paintDiagnostics(d.problems || []);
      if (!d.ok) { button.disabled = false; button.textContent = text; }
    } catch (e) {
      button.disabled = false;
      button.textContent = text;
    }
  }

  // SMIL ignores prefers-reduced-motion: the drawing stays still, with one copy halfway.
  if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) {
    const svg = document.getElementById('welcome-svg');
    if (svg && svg.pauseAnimations) { svg.pauseAnimations(); svg.setCurrentTime(1.5); }
  }

  async function closeWelcome() {
    const card = document.getElementById('welcome');
    if (!card.hidden && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      card.classList.add('leaving');
      await new Promise(ready => setTimeout(ready, 280));
    }
    card.hidden = true;
    card.classList.remove('leaving');
    try { await fetch('/app/welcome-seen', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch (e) {}
  }

  // Installed copy: one click downloads the new installer and runs it (CopyOver closes and opens again).
  // Portable copy, or if that fails: the link opens the releases page.
  let installingVersion = false;
  async function installNewVersion(event) {
    if (!currentState || !currentState.self_update) return;
    event.preventDefault();
    if (installingVersion) return;
    const vn = document.getElementById('new-version');
    installingVersion = true;
    vn.textContent = 'Downloading the new version…';
    try {
      const r = await fetch('/app/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const d = await r.json();
      if (d.ok) {
        vn.textContent = 'Installing… CopyOver opens again by itself';
        return;
      }
    } catch (e) {}
    installingVersion = false;
    window.open(vn.href, '_blank', 'noopener');
  }

  async function updateState() {
    try {
      // panel=1: only the panel gets «abrir_modal»; the quick view asks for the status without it.
      const r = await fetch('/app/state?panel=1&_t=' + Date.now());
      const d = await r.json();
      if (d.app_version && d.app_version !== VERSION_LOCAL) {
        location.reload();
        return;
      }
      currentState = d;
      if (d.open_modal) openConnectPhoneModal(d.open_modal);
      document.getElementById('welcome').hidden = !d.welcome;
      const vn = document.getElementById('new-version');
      vn.hidden = !d.new_version;
      if (d.releases_page) vn.href = d.releases_page;
      if (d.new_version && !installingVersion) {
        vn.textContent = d.self_update ? `Update to version ${d.new_version}` : `Version ${d.new_version} available`;
      }
      if (d.download_folder) paintFolder(d.download_folder);

      const dot = document.getElementById('net-dot');
      const lbl = document.getElementById('net-label');
      if (d.wifi_direct && d.wifi_direct.active) {
        dot.className = 'status-dot';
        lbl.textContent = 'Wi-Fi Direct on';
        lbl.title = `Wi-Fi Direct ${d.wifi_direct.ip} · ${d.ip || ''}`;
      } else if (d.ip) {
        dot.className = 'status-dot';
        lbl.textContent = 'On the network';
        lbl.title = `${d.network_name}.local · ${d.ip}`;
      } else {
        dot.className = 'status-dot warn';
        lbl.textContent = 'No Wi-Fi network — turn on Wi-Fi Direct above';
        lbl.title = '';
      }

      const tBox = document.getElementById('transfer-box');
      if (d.transfer) {
        tBox.classList.add('active');
        const tr = d.transfer;
        // Who and where: «Sending to Ana's iPad» / «Receiving from PC-oficina».
        const direction = tr.direction === 'sent'
          ? (tr.peer ? `Sending to ${tr.peer}` : 'Sending')
          : tr.direction === 'received'
            ? (tr.peer ? `Receiving from ${tr.peer}` : 'Receiving')
            : '';
        document.getElementById('transfer-name').textContent =
          `${direction ? direction + ' · ' : ''}${tr.file} (${tr.speed_mb_s || 0} MB/s)`;
        document.getElementById('transfer-pct').textContent = `${d.transfer.percent || 0}%`;
        document.getElementById('transfer-fill').style.width = `${d.transfer.percent || 0}%`;
      } else {
        tBox.classList.remove('active');
      }

      if (d.pc_chassis) {
        const cIcon = document.getElementById('radar-center-icon');
        if (cIcon) {
          cIcon.innerHTML = (d.pc_chassis === 'desktop' ? ICONS.desktop : ICONS.laptop);
          cIcon.title = 'This PC (' + (d.pc_chassis === 'desktop' ? 'Tower/Desktop' : 'Laptop') + ')';
        }
      }
      syncWifiDirectUi(d.wifi_direct);
      renderDevices();
      validateCurrentSelection();
      updateHistory();
    } catch (e) {}
  }

  const pcdz = document.getElementById('pc-dropzone');
  if (pcdz) {
    pcdz.addEventListener('dragover', (e) => { e.preventDefault(); pcdz.classList.add('dragover'); });
    pcdz.addEventListener('dragleave', () => pcdz.classList.remove('dragover'));
    pcdz.addEventListener('drop', async (e) => {
      e.preventDefault();
      pcdz.classList.remove('dragover');
      if (!await receiveDrag(e.dataTransfer)) chooseNativeFiles();
    });
  }

  updateState();
  setInterval(updateState, 2000);
  loadDiagnostics();
  // Right after startup the network announcement takes a few seconds: check again.
  setTimeout(loadDiagnostics, 15000);
