/* MediQueue AI - Admin QR IMAGE UPLOAD check-in.
   No camera, no getUserMedia, no manual token entry.
   Flow: choose image -> FileReader -> Canvas -> jsQR decode -> /api/admin/qr-lookup
         -> show patient details -> confirm -> /api/admin/qr-scan */
(function () {
  const fileInput = document.getElementById('qr-file');
  if (!fileInput) return;

  const dropZone = document.getElementById('qr-drop');
  const preview = document.getElementById('qr-preview');
  const previewWrap = document.getElementById('qr-preview-wrap');
  const out = document.getElementById('scan-result');
  const details = document.getElementById('qr-details');
  const detailBody = document.getElementById('qr-detail-body');
  const confirmBtn = document.getElementById('btn-confirm');
  const clearBtn = document.getElementById('btn-clear');
  const canvas = document.getElementById('qr-canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });

  const ALLOWED = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp'];
  const READ_FAIL = 'Unable to read this QR image. Please upload a clear QR code image.';
  let payload = '';
  let busy = false;

  function message(text, kind, extraHtml) {
    out.hidden = false;
    out.className = 'mq-result ' + (kind || '');
    out.innerHTML = '<b>' + text + '</b>' + (extraHtml || '');
  }

  function resetResult() {
    out.hidden = true;
    out.innerHTML = '';
    details.hidden = true;
    detailBody.innerHTML = '';
    confirmBtn.hidden = true;
    payload = '';
  }

  function clearAll() {
    resetResult();
    fileInput.value = '';
    preview.removeAttribute('src');
    previewWrap.hidden = true;
  }

  function row(label, value) {
    return '<div class="mq-detail-row"><span>' + label + '</span><b>' +
      (value === null || value === undefined || value === '' ? '—' : String(value)) +
      '</b></div>';
  }

  /* ---------- decode helpers ---------- */

  function decodeFromImage(img) {
    // Try the natural size first, then a couple of scaled passes so small or
    // very large QR images still decode reliably.
    const scales = [1, 2, 0.5];
    for (let i = 0; i < scales.length; i++) {
      const w = Math.max(1, Math.round(img.naturalWidth * scales[i]));
      const h = Math.max(1, Math.round(img.naturalHeight * scales[i]));
      if (w > 4000 || h > 4000) continue;
      canvas.width = w;
      canvas.height = h;
      ctx.clearRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      try {
        const data = ctx.getImageData(0, 0, w, h);
        const attempts = ['dontInvert', 'attemptBoth'];
        for (let j = 0; j < attempts.length; j++) {
          const code = window.jsQR &&
            window.jsQR(data.data, data.width, data.height, { inversionAttempts: attempts[j] });
          if (code && code.data) return code.data;
        }
      } catch (e) { /* try the next scale */ }
    }
    return '';
  }

  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      const reader = new FileReader();
      reader.onerror = function () { reject(new Error('read')); };
      reader.onload = function () {
        const img = new Image();
        img.onload = function () { resolve(img); };
        img.onerror = function () { reject(new Error('decode')); };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    });
  }

  /* ---------- backend calls ---------- */

  async function lookup(token) {
    const r = await fetch('/api/admin/qr-lookup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ payload: token })
    });
    return await r.json();
  }

  async function handleFile(file) {
    if (busy) return;
    resetResult();
    if (!file) return;

    const type = (file.type || '').toLowerCase();
    const nameOk = /\.(png|jpe?g|webp)$/i.test(file.name || '');
    if (ALLOWED.indexOf(type) === -1 && !nameOk) {
      message('Unsupported file type. Please upload a PNG, JPG, JPEG or WEBP image.', 'bad');
      return;
    }

    busy = true;
    let img;
    try {
      img = await loadImage(file);
    } catch (e) {
      busy = false;
      message(READ_FAIL, 'bad');
      return;
    }

    preview.src = img.src;
    previewWrap.hidden = false;

    const decoded = decodeFromImage(img);
    if (!decoded) {
      busy = false;
      message(READ_FAIL, 'bad');
      return;
    }

    message('QR detected successfully', 'ok');

    let res;
    try {
      res = await lookup(decoded);
    } catch (e) {
      busy = false;
      message('Network error — please try again.', 'bad');
      return;
    }
    busy = false;

    if (!res || !res.found) {
      message((res && res.message) || 'Invalid or expired QR code.', 'bad');
      return;
    }

    const a = res.appointment || {};
    detailBody.innerHTML =
      row('Patient Name', a.patient_name) +
      row('Patient ID', a.patient_id) +
      row('Appointment ID', a.appointment_id) +
      row('Department', a.department) +
      row('Doctor', a.doctor) +
      row('Appointment Date', a.appt_date) +
      row('Appointment Time', a.appt_time) +
      row('Queue / Token No', a.queue_no) +
      row('Appointment Status', a.status) +
      row('Check-In Status', a.checkin_status);
    details.hidden = false;

    if (res.can_check_in) {
      payload = decoded;
      confirmBtn.hidden = false;
      message('QR detected successfully', 'ok',
        '<div>Verify the details below, then confirm the check-in.</div>');
    } else {
      confirmBtn.hidden = true;
      message(res.message || 'This QR cannot be used for check-in.',
        res.code === 'already' ? 'warn' : 'bad');
    }
  }

  async function confirmCheckIn() {
    if (!payload || busy) return;
    busy = true;
    confirmBtn.disabled = true;
    try {
      const r = await fetch('/api/admin/qr-scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ payload: payload, stage: 'checkin' })
      });
      const res = await r.json();
      if (res.ok) {
        message('Patient checked in successfully.', 'ok');
        confirmBtn.hidden = true;
        payload = '';
      } else {
        message(res.message === 'Already Checked In'
          ? 'Patient already checked in.'
          : (res.message || 'Invalid or expired QR code.'),
          res.code === 'already' ? 'warn' : 'bad');
        confirmBtn.hidden = true;
      }
      refreshRows();
    } catch (e) {
      message('Network error — please try again.', 'bad');
    }
    confirmBtn.disabled = false;
    busy = false;
  }

  /* ---------- today's scans table ---------- */

  async function refreshRows() {
    try {
      const r = await fetch('/api/admin/checkins');
      const data = await r.json();
      const tbody = document.getElementById('checkin-rows');
      if (!tbody || !data.rows) return;
      tbody.innerHTML = data.rows.map(function (x) {
        return '<tr><td>' + (x.scan_time_label || '') + '</td><td>' + (x.patient_name || '—') +
          '</td><td>' + (x.queue_no || '—') + '</td><td>' + x.method + '</td><td>' +
          x.admin_name + '</td><td><span class="mq-badge ' + (x.status === 'ok' ? 'ok' : 'bad') +
          '">' + x.status + '</span></td></tr>';
      }).join('') || '<tr><td colspan="6" class="mq-empty">No scans yet today.</td></tr>';
      if (data.counts) {
        const set = function (id, v) {
          const el = document.getElementById(id);
          if (el) el.textContent = v;
        };
        set('kpi-checkins', data.counts.checked_in);
        set('kpi-qr', data.counts.qr_scans);
        set('kpi-barcode', data.counts.barcode_scans);
      }
    } catch (e) { /* keep the table as-is */ }
  }

  /* ---------- wiring ---------- */

  fileInput.addEventListener('change', function () {
    handleFile(fileInput.files && fileInput.files[0]);
  });

  confirmBtn.addEventListener('click', confirmCheckIn);
  clearBtn.addEventListener('click', clearAll);

  if (dropZone) {
    ['dragenter', 'dragover'].forEach(function (ev) {
      dropZone.addEventListener(ev, function (e) {
        e.preventDefault();
        dropZone.classList.add('is-over');
      });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      dropZone.addEventListener(ev, function (e) {
        e.preventDefault();
        dropZone.classList.remove('is-over');
      });
    });
    dropZone.addEventListener('drop', function (e) {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) {
        fileInput.value = '';
        handleFile(f);
      }
    });
  }
})();
