  const token = new URLSearchParams(window.location.search).get('t') || '';
  // «Which one are you?»: the linked device this page is ('other' = none of them). Only an owner link gets
  // the list; localStorage can fail (private mode), so the page works without it.
  function readLocal(key) { try { return localStorage.getItem(key) || ''; } catch (e) { return ''; } }
  function saveLocal(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
  // Stored under another name by earlier versions.
  const savedByOldVersion = readLocal('copyover_soy');
  if (savedByOldVersion) {
    saveLocal('copyover_me', savedByOldVersion === 'otro' ? 'other' : savedByOldVersion);
    saveLocal('copyover_soy', '');
  }
  let selfId = readLocal('copyover_me');
  let linkedByOwner = [];
  let lastSeenOfferId = '';
  let currentOfferText = '';

  document.getElementById('btn-manual-download').href = '/guest/download?t=' + encodeURIComponent(token);

  function detectLocalDevice() {
    const ua = navigator.userAgent || '';
    // The old key keeps the name saved before this one existed.
    const saved = localStorage.getItem('copyover_my_name') || localStorage.getItem('airbridge_mi_nombre');
    let type = 'mobile';
    let model = '';

    if (/android/i.test(ua)) {
      type = 'android';
      const m = ua.match(/android\s+[\d\.]+;\s*([^;\)]+?)(?:\s+build|[;\)])/i);
      if (m && m[1]) {
        let mod = m[1].trim();
        if (/^SM-/i.test(mod)) mod = 'Samsung ' + mod;
        model = mod;
      } else {
        model = 'Android phone';
      }
    } else if (/ipad/i.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
      type = 'ipad';
      const w = Math.min(screen.width, screen.height);
      const h = Math.max(screen.width, screen.height);
      if (w === 1024 && h === 1366) model = 'iPad Pro 12.9"';
      else if (w === 834 && h === 1194) model = 'iPad Pro 11"';
      else if (w === 820 && h === 1180) model = 'iPad Air 10.9"';
      else if (w === 810 && h === 1080) model = 'iPad 10.2"';
      else if (w === 768 && h === 1024) model = 'iPad mini';
      else model = 'iPad';
    } else if (/iphone/i.test(ua)) {
      type = 'iphone';
      const w = Math.min(screen.width, screen.height);
      const h = Math.max(screen.width, screen.height);
      const r = window.devicePixelRatio || 1;
      if (w === 440 && h === 956) model = 'iPhone 17 Pro Max / 16 Pro Max';
      else if (w === 402 && h === 874) model = 'iPhone 17';
      else if (w === 420 && h === 912) model = 'iPhone 17 Air';
      else if (w === 430 && h === 932) model = 'iPhone 17 / 16 Plus';
      else if (w === 393 && h === 852) model = 'iPhone 16 / 15';
      else if (w === 428 && h === 926) model = 'iPhone 14 Plus / 13 Pro Max';
      else if (w === 390 && h === 844) model = 'iPhone 14 / 13 / 12';
      else if (w === 414 && h === 896) model = (r === 3 ? 'iPhone 11 Pro Max / XS Max' : 'iPhone 11 / XR');
      else if (w === 375 && h === 812) model = 'iPhone 11 Pro / XS / X';
      else if (w === 375 && h === 667) model = 'iPhone SE';
      else model = 'iPhone 17';
    }
    const finalName = saved || model || (type === 'ipad' ? 'iPad' : (type === 'android' ? 'Android phone' : 'iPhone'));
    return { type: type, model: finalName };
  }

  function showToast(m) {
    const t = document.getElementById('toast');
    t.textContent = m;
    t.style.display = 'block';
    setTimeout(() => { t.style.display = 'none'; }, 2800);
  }

  async function queryPcState() {
    try {
      const myDevice = detectLocalDevice();
      const selfParam = selfId && selfId !== 'other' ? '&me=' + encodeURIComponent(selfId) : '';
      const r = await fetch('/guest/status?t=' + encodeURIComponent(token) + '&type=' + encodeURIComponent(myDevice.type) + '&device=' + encodeURIComponent(myDevice.model) + selfParam + '&_t=' + Date.now());
      const d = await r.json();
      if (!d.ok) {
        // The link expired: nothing done on this page will reach the PC.
        document.getElementById('live-txt').textContent = d.expired ? 'LINK EXPIRED' : 'OFFLINE';
        document.getElementById('pc-title').textContent = d.expired ? 'Scan another QR code on the PC' : 'CopyOver';
        document.getElementById('incoming-card').classList.remove('show');
        return;
      }
      document.getElementById('live-txt').textContent = 'CONNECTED LIVE';
      if (d.pc_name) {
        document.getElementById('pc-title').textContent = 'Connected to ' + d.pc_name;
      }
      const incCard = document.getElementById('incoming-card');
      if (d.offer && !d.offer.delivered) {
        incCard.classList.add('show');
        document.getElementById('inc-name').textContent = d.offer.name;
        document.getElementById('inc-size').textContent = d.offer.size_text || '';
        const dlBtn = document.getElementById('inc-download-btn');
        const cpBtn = document.getElementById('inc-copy-btn');
        if (d.offer.type === 'text') {
          currentOfferText = d.offer.text || '';
          dlBtn.style.display = 'none';
          cpBtn.style.display = 'flex';
        } else {
          dlBtn.style.display = 'flex';
          cpBtn.style.display = 'none';
          dlBtn.href = '/guest/download?t=' + encodeURIComponent(token) + '&id=' + encodeURIComponent(d.offer.id);
        }
      } else {
        incCard.classList.remove('show');
      }
    } catch (e) {
      document.getElementById('live-txt').textContent = 'OFFLINE';
    }
  }

  function markReceived() {
    setTimeout(() => {
      fetch('/guest/confirm-receipt?t=' + encodeURIComponent(token), { method: 'POST' });
      showToast('Downloading from the PC...');
    }, 300);
  }

  async function copyOfferText() {
    if (!(await copyToClipboard(currentOfferText))) {
      // No way to copy automatically: the text stays visible to copy by hand.
      document.getElementById('inc-name').textContent = currentOfferText;
      showToast('Press and hold the text to copy it');
      return;
    }
    await fetch('/guest/confirm-receipt?t=' + encodeURIComponent(token), { method: 'POST' }).catch(() => {});
    showToast('Text copied on this device!');
    queryPcState();
  }

  async function uploadByStreaming(files) {
    if (!files || !files.length) return;
    const progBg = document.getElementById('prog-bg');
    const progFill = document.getElementById('prog-fill');
    const statusTxt = document.getElementById('status-txt');
    progBg.style.display = 'block';
    const failed = [];
    let reason = '';

    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const ok = await new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/guest/upload?t=' + encodeURIComponent(token));
        xhr.setRequestHeader('X-File-Name', encodeURIComponent(f.name));
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            progFill.style.width = pct + '%';
            statusTxt.textContent = `Sending (${i + 1}/${files.length}): ${f.name} (${pct}%)`;
          }
        };
        xhr.onload = () => {
          // The PC answers with text when it rejects the file (no space, expired link...).
          if (xhr.status < 200 || xhr.status >= 300) reason = (xhr.responseText || '').slice(0, 160);
          resolve(xhr.status >= 200 && xhr.status < 300);
        };
        xhr.onerror = () => { reason = 'Couldn’t reach the PC'; resolve(false); };
        xhr.send(f);
      });
      if (!ok) failed.push(f.name);
    }
    if (!failed.length) {
      statusTxt.textContent = 'Sent to the PC!';
      showToast('The PC got it!');
    } else {
      progFill.style.width = '0%';
      statusTxt.textContent = `Didn’t reach the PC: ${failed.join(', ')}` + (reason ? ` (${reason})` : '');
      showToast('Couldn’t send it to the PC');
    }
  }

  async function sendText() {
    const txt = document.getElementById('text-input').value.trim();
    if (!txt) return;
    try {
      const r = await fetch('/guest/text?t=' + encodeURIComponent(token), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ text: txt })
      });
      const d = r.ok ? await r.json() : {};
      if (d.ok) {
        document.getElementById('text-input').value = '';
        showToast('Text copied on the PC!');
        return;
      }
    } catch (e) {}
    showToast('Couldn’t send the text to the PC');
  }

  // navigator.clipboard only exists on https; this page runs over http on the local network.
  async function copyToClipboard(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (e) {}
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    area.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) {}
    area.remove();
    return ok;
  }

  const _deviceInfo = detectLocalDevice();
  const _chipEl = document.getElementById('device-chip');
  function requestOwnName() {
    const newValue = prompt('Name of your iPhone / device:', _deviceInfo.model);
    if (newValue && newValue.trim()) {
      saveLocal('copyover_my_name', newValue.trim());
      _deviceInfo.model = newValue.trim();
      _chipEl.textContent = 'This device: ' + newValue.trim() + ' ✎';
      queryPcState();
    }
  }
  if (_chipEl) {
    _chipEl.textContent = 'This device: ' + _deviceInfo.model + ' ✎';
    _chipEl.style.cursor = 'pointer';
    _chipEl.title = 'Tap to change the name';
    // The owner can change which linked device this is; anyone else just renames it.
    _chipEl.onclick = () => { if (linkedByOwner.length) showWhoAreYou(); else requestOwnName(); };
  }

  function showWhoAreYou() {
    const list = document.getElementById('who-list');
    list.replaceChildren();
    for (const device of linkedByOwner) {
      const button = document.createElement('button');
      button.className = 'btn btn-primary';
      button.textContent = device.name;  // textContent: the name is never read as HTML
      button.onclick = () => chooseLinked(device);
      list.appendChild(button);
    }
    document.getElementById('who-card').classList.add('show');
  }

  function chooseLinked(device) {
    selfId = device.id;
    saveLocal('copyover_me', device.id);
    document.getElementById('who-card').classList.remove('show');
    _chipEl.textContent = 'This device: ' + device.name + ' ✎';
    queryPcState();
  }

  function chooseOtherDevice() {
    selfId = 'other';
    saveLocal('copyover_me', 'other');
    document.getElementById('who-card').classList.remove('show');
    _chipEl.textContent = 'This device: ' + _deviceInfo.model + ' ✎';
    requestOwnName();
  }

  async function prepareWhoAreYou() {
    let d;
    try {
      const r = await fetch('/guest/who?t=' + encodeURIComponent(token));
      if (!r.ok) return;  // a guest link: it never gets the list
      d = await r.json();
    } catch (e) { return; }
    linkedByOwner = d.devices || [];
    if (!linkedByOwner.length) return;
    if (selfId && selfId !== 'other') {
      const chosen = linkedByOwner.find(x => x.id === selfId);
      if (chosen) { _chipEl.textContent = 'This device: ' + chosen.name + ' ✎'; return; }
      selfId = '';  // removed from the PC: ask again
      saveLocal('copyover_me', '');
    }
    if (selfId !== 'other') showWhoAreYou();
  }

  prepareWhoAreYou();
  queryPcState();
  setInterval(queryPcState, 1500);
