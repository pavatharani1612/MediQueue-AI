/* MediQueue AI - live dashboard channel (Socket.IO if present, else SSE, else poll). */
(function () {
  const listeners = [];
  const MQ = window.MQLive = {
    on: function (fn) { listeners.push(fn); return MQ; },
    emit: function (evt) { listeners.forEach(function (fn) { try { fn(evt); } catch (e) {} }); }
  };

  function applyStats(stats) {
    if (!stats) return;
    document.querySelectorAll('[data-stat]').forEach(function (el) {
      const key = el.getAttribute('data-stat');
      if (stats[key] !== undefined && stats[key] !== null) el.textContent = stats[key];
    });
    const t = document.getElementById('srv-time');
    if (t && stats.server_time) t.textContent = stats.server_time;
  }

  function applySettings(s) {
    if (!s) return;
    document.querySelectorAll('[data-setting]').forEach(function (el) {
      const key = el.getAttribute('data-setting');
      if (s[key] !== undefined) el.textContent = s[key];
    });
    document.body.classList.toggle('mq-dark', s.theme === 'dark');
  }

  async function pollState() {
    try {
      const r = await fetch('/api/realtime/state', { credentials: 'same-origin' });
      if (!r.ok) return;
      const state = await r.json();
      applyStats(state.stats); applySettings(state.settings);
      MQ.emit({ topic: 'state', data: state });
    } catch (e) { /* offline: retry next tick */ }
  }

  if (window.EventSource) {
    try {
      const es = new EventSource('/api/realtime/stream');
      es.onmessage = function (m) {
        try { MQ.emit(JSON.parse(m.data)); } catch (e) {}
        pollState();
      };
      ['checkin', 'queue', 'settings', 'notification', 'audit', 'settings_bulk'].forEach(function (topic) {
        es.addEventListener(topic, function (m) {
          try { MQ.emit(JSON.parse(m.data)); } catch (e) {}
          pollState();
        });
      });
    } catch (e) { /* fall back to polling only */ }
  }

  pollState();
  setInterval(pollState, 8000);
})();

/* Real-time AM/PM slot board: <div id="mq-slots" data-doctor-input="doctor_id" ...> */
(function () {
  const box = document.getElementById('mq-slots');
  if (!box) return;
  const hidden = document.querySelector(box.dataset.target || '#slot_time');
  function selectedDoctor() {
    const el = document.querySelector(box.dataset.doctorInput || '[name=doctor_id]');
    return el ? el.value : '';
  }
  function selectedDate() {
    const el = document.querySelector(box.dataset.dateInput || '[name=appt_date]');
    return el ? el.value : '';
  }
  async function load() {
    const doctor = selectedDoctor();
    if (!doctor) { box.innerHTML = '<p class="mq-hint">Select a doctor to see live available slots.</p>'; return; }
    const r = await fetch('/api/slots/live?doctor_id=' + encodeURIComponent(doctor) +
      '&date=' + encodeURIComponent(selectedDate() || ''));
    const data = await r.json();
    box.innerHTML = '<p class="mq-hint">Now ' + (data.now || '') + ' · ' +
      data.available_count + ' slot(s) available</p><div class="mq-row">' +
      data.slots.map(function (s) {
        const cls = s.available ? '' : (s.past ? 'past' : 'taken');
        return '<button type="button" class="mq-slot ' + cls + '" data-time="' + s.time + '"' +
          (s.available ? '' : ' disabled') + '>' + s.label + '</button>';
      }).join('') + '</div>';
    box.querySelectorAll('.mq-slot:not([disabled])').forEach(function (b) {
      b.addEventListener('click', function () {
        box.querySelectorAll('.mq-slot').forEach(function (x) { x.classList.remove('selected'); });
        b.classList.add('selected');
        if (hidden) hidden.value = b.dataset.time;
      });
    });
  }
  ['change', 'input'].forEach(function (evt) {
    document.addEventListener(evt, function (e) {
      if (e.target.matches('[name=doctor_id],[name=appt_date]')) load();
    });
  });
  load();
  setInterval(load, 30000);                        // slots stay fresh automatically
  if (window.MQLive) window.MQLive.on(function (ev) {
    if (['checkin', 'queue', 'settings'].indexOf(ev.topic) >= 0) load();
  });
})();
