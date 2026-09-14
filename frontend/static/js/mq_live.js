/* =========================================================
   MediQueue AI — mq_live.js
   Lightweight AJAX polling for the live queue tracker,
   toast notifications, counter animations and scroll reveals.
   Requires no framework — plain vanilla JS.
   ========================================================= */
(function () {
  "use strict";

  /* ---------------- toasts ---------------- */
  function toastHost() {
    var host = document.getElementById("mq-toasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "mq-toasts";
      document.body.appendChild(host);
    }
    return host;
  }

  function toast(message, type, ms) {
    var el = document.createElement("div");
    el.className = "mq-toast " + (type || "");
    el.textContent = message;
    toastHost().appendChild(el);
    setTimeout(function () {
      el.classList.add("fade");
      setTimeout(function () { el.remove(); }, 400);
    }, ms || 4200);
  }
  window.mqToast = toast;

  /* ---------------- counter animation ---------------- */
  function animateCount(el, to) {
    var from = parseFloat(el.getAttribute("data-mq-count") || "0");
    if (from === to) { return; }
    el.setAttribute("data-mq-count", to);
    var start = performance.now(), dur = 600;
    function step(now) {
      var p = Math.min(1, (now - start) / dur);
      var val = from + (to - from) * (1 - Math.pow(1 - p, 3));
      el.textContent = Math.round(val);
      if (p < 1) { requestAnimationFrame(step); }
    }
    requestAnimationFrame(step);
  }
  window.mqAnimateCount = animateCount;

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-count-to]").forEach(function (el) {
      animateCount(el, parseFloat(el.getAttribute("data-count-to")) || 0);
    });

    /* scroll reveal */
    var reveals = document.querySelectorAll(".mq-reveal");
    if (reveals.length && "IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
        });
      }, { threshold: 0.12 });
      reveals.forEach(function (el) { io.observe(el); });
    } else {
      reveals.forEach(function (el) { el.classList.add("in"); });
    }

    startLiveQueue();
  });

  /* ---------------- live queue polling ---------------- */
  var lastSignature = {};
  var pollTimer = null;
  var failures = 0;

  function pill(status) {
    var s = (status || "").toLowerCase();
    if (s.indexOf("consult") >= 0) { return "info"; }
    if (s.indexOf("complete") >= 0) { return "ok"; }
    if (s.indexOf("cancel") >= 0 || s.indexOf("miss") >= 0) { return "danger"; }
    if (s.indexOf("emergency") >= 0) { return "emergency"; }
    return "warn";
  }

  function renderCard(root, d) {
    root.querySelectorAll("[data-mq]").forEach(function (el) {
      var key = el.getAttribute("data-mq");
      var val = d[key];
      if (val === undefined || val === null) { return; }
      if (el.hasAttribute("data-mq-animate") && typeof val === "number") {
        animateCount(el, val);
      } else {
        el.textContent = val;
      }
    });

    var badge = root.querySelector("[data-mq-badge]");
    if (badge) {
      badge.textContent = d.appointment_status;
      badge.className = "mq-pill " + pill(d.appointment_status);
    }

    var ring = root.querySelector("[data-mq-ring]");
    if (ring) {
      var total = Math.max(1, (d.patients_ahead || 0) + 1);
      var pct = Math.max(4, Math.round((1 / total) * 100));
      ring.style.setProperty("--pct", pct);
    }

    var bar = root.querySelector("[data-mq-progress]");
    if (bar) {
      var ql = Math.max(1, d.queue_length || 1);
      var done = Math.max(0, ql - (d.patients_ahead || 0));
      bar.style.width = Math.min(100, Math.round((done / ql) * 100)) + "%";
    }

    var steps = root.querySelectorAll("[data-mq-step]");
    if (steps.length) {
      var order = ["Booked", "Checked-In", "Waiting", "Consulting", "Completed"];
      var current = d.raw_status === "InProgress" ? "Consulting"
        : d.raw_status === "Completed" ? "Completed"
          : (d.checkin_status === "Checked-In" ? "Waiting" : "Booked");
      var idx = order.indexOf(current);
      steps.forEach(function (el, i) {
        el.classList.remove("done", "active");
        if (i < idx) { el.classList.add("done"); }
        if (i === idx) { el.classList.add("active"); }
      });
    }

    /* notify on meaningful changes */
    var sig = d.current_token + "|" + d.appointment_status + "|" + d.patients_ahead;
    if (lastSignature[d.appointment_id] && lastSignature[d.appointment_id] !== sig) {
      if (d.raw_status === "InProgress") {
        toast("Your consultation has started — please proceed.", "ok");
      } else if ((d.patients_ahead || 0) === 0) {
        toast("You are next! Please stay near the consultation room.", "warn");
      } else {
        toast("Queue updated — " + d.patients_ahead + " patient(s) ahead of you.", "");
      }
    }
    lastSignature[d.appointment_id] = sig;
  }

  function renderAll(payload) {
    var host = document.getElementById("mq-live-host");
    var stamp = document.querySelectorAll("[data-mq-updated]");
    stamp.forEach(function (el) { el.textContent = payload.server_time; });

    (payload.appointments || []).forEach(function (d) {
      var card = document.querySelector('[data-mq-appt="' + d.appointment_id + '"]');
      if (!card && host) {
        card = buildCard(d);
        host.appendChild(card);
      }
      if (card) { renderCard(card, d); }
    });

    if (host && (!payload.appointments || payload.appointments.length === 0)) {
      if (!host.querySelector(".mq-empty")) {
        host.innerHTML = '<div class="mq-card mq-empty">No active appointment right now. ' +
          'Book an appointment to see your live queue position.</div>';
      }
    }
  }

  function buildCard(d) {
    var el = document.createElement("div");
    el.className = "mq-live mq-mt";
    el.setAttribute("data-mq-appt", d.appointment_id);
    el.innerHTML =
      '<div class="mq-live-head"><span class="mq-dot"></span>' +
      '<h3 class="mq-live-title">Live Queue — <span data-mq="doctor"></span></h3>' +
      '<span class="mq-spacer"></span><span class="mq-pill" data-mq-badge></span></div>' +
      '<div class="mq-live-grid">' +
      '<div class="mq-live-tile"><div class="k">Current Token</div><div class="v" data-mq="current_token">-</div></div>' +
      '<div class="mq-live-tile"><div class="k">Your Token</div><div class="v" data-mq="your_token">-</div></div>' +
      '<div class="mq-live-tile"><div class="k">Patients Ahead</div><div class="v" data-mq="patients_ahead" data-mq-animate>0</div></div>' +
      '<div class="mq-live-tile"><div class="k">Estimated Wait</div><div class="v small" data-mq="estimated_wait_text">-</div></div>' +
      '<div class="mq-live-tile"><div class="k">Predicted Consultation</div><div class="v small" data-mq="predicted_consultation">-</div></div>' +
      '<div class="mq-live-tile"><div class="k">Doctor Status</div><div class="v small" data-mq="doctor_status">-</div></div>' +
      '<div class="mq-live-tile"><div class="k">Queue Status</div><div class="v small" data-mq="queue_status">-</div></div>' +
      '</div>';
    return el;
  }

  function poll() {
    var root = document.querySelector("[data-mq-live-root]");
    if (!root) { return; }
    var url = root.getAttribute("data-mq-url") || "/api/patient/queue-live";
    var only = root.getAttribute("data-mq-appointment");
    if (only) { url += (url.indexOf("?") >= 0 ? "&" : "?") + "appointment_id=" + only; }

    fetch(url, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" },
      cache: "no-store"
    })
      .then(function (r) {
        if (r.status === 401) {
          throw new Error("Your session expired — please log in again.");
        }
        if (r.status === 403) {
          throw new Error("You are not allowed to view this queue.");
        }
        return r.json();
      })
      .then(function (data) {
        failures = 0;
        if (data && data.ok) { renderAll(data); }
      })
      .catch(function (err) {
        failures += 1;
        if (failures === 1) { toast(err.message || "Live queue unavailable.", "danger"); }
        if (failures >= 5 && pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      });
  }

  function startLiveQueue() {
    var root = document.querySelector("[data-mq-live-root]");
    if (!root || pollTimer) { return; }
    var every = parseInt(root.getAttribute("data-mq-interval") || "8000", 10);
    poll();
    pollTimer = setInterval(poll, Math.max(3000, every));
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) { poll(); }
    });
  }
  window.mqStartLiveQueue = startLiveQueue;
})();
