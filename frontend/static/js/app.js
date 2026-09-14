// MediQueue AI — UI utilities (visual only; no business logic)
document.addEventListener("DOMContentLoaded", () => {
  // Loader hide
  const l = document.getElementById("loader");
  if (l) setTimeout(() => l.classList.remove("show"), 400);

  // Animated counters (ease-out)
  document.querySelectorAll("[data-count]").forEach(el => {
    const target = +el.dataset.count, dur = 1400, start = performance.now();
    const step = t => {
      const p = Math.min(1, (t - start) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.floor(target * eased).toLocaleString();
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = target.toLocaleString();
    };
    requestAnimationFrame(step);
  });

  // Typing effect
  const type = document.querySelector("[data-type]");
  if (type) {
    const words = JSON.parse(type.dataset.type);
    let i = 0, j = 0, del = false;
    const tick = () => {
      const w = words[i];
      type.textContent = w.slice(0, j);
      if (!del && j < w.length) { j++; }
      else if (del && j > 0) { j--; }
      else { del = !del; if (!del) i = (i + 1) % words.length; }
      setTimeout(tick, del ? 55 : 105);
    };
    tick();
  }

  // Scroll reveal (staggered)
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduceMotion && "IntersectionObserver" in window) {
    const targets = document.querySelectorAll(".card,.doc,.section-title,.section-sub,table");
    targets.forEach(el => el.classList.add("reveal-init"));
    const io = new IntersectionObserver(entries => {
      entries.forEach((e, idx) => {
        if (e.isIntersecting) {
          const el = e.target;
          el.style.transitionDelay = Math.min(idx * 60, 240) + "ms";
          el.classList.add("reveal-in");
          el.classList.remove("reveal-init");
          io.unobserve(el);
        }
      });
    }, { threshold: 0.08, rootMargin: "0px 0px -30px 0px" });
    targets.forEach(el => io.observe(el));
  }

  // Button ripple effect
  document.addEventListener("click", ev => {
    const btn = ev.target.closest(".btn");
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const size = Math.max(rect.width, rect.height);
    const span = document.createElement("span");
    span.className = "ripple";
    span.style.width = span.style.height = size + "px";
    span.style.left = (ev.clientX - rect.left - size / 2) + "px";
    span.style.top = (ev.clientY - rect.top - size / 2) + "px";
    btn.appendChild(span);
    setTimeout(() => span.remove(), 650);
  });

  // Highlight the active nav link
  const path = window.location.pathname.replace(/\/$/, "") || "/";
  document.querySelectorAll(".nav a.link[href]").forEach(a => {
    const href = a.getAttribute("href").replace(/\/$/, "") || "/";
    if (href === path) a.classList.add("active");
  });

  // Subtle 3D tilt on stat cards (desktop, motion-safe only)
  if (!reduceMotion && window.matchMedia("(hover:hover) and (min-width:900px)").matches) {
    document.querySelectorAll(".card.stat, .gradient-card").forEach(card => {
      card.addEventListener("mousemove", e => {
        const r = card.getBoundingClientRect();
        const rx = ((e.clientY - r.top) / r.height - 0.5) * -6;
        const ry = ((e.clientX - r.left) / r.width - 0.5) * 6;
        card.style.transform = `perspective(900px) rotateX(${rx}deg) rotateY(${ry}deg) translateY(-4px)`;
      });
      card.addEventListener("mouseleave", () => { card.style.transform = ""; });
    });
  }
});

async function loadDoctorsByDept(dept, select){
  select.innerHTML = '<option>Loading...</option>';
  const r = await fetch(`/api/doctors/${encodeURIComponent(dept)}`);
  const list = await r.json();
  select.innerHTML = list.map(d=>`<option value="${d.id}">${d.name} — ₹${d.fee} · ${d.experience}y</option>`).join("");
}

// ---- v3 UI extras (visual only) ----
document.addEventListener("DOMContentLoaded", () => {
  // Mobile hamburger toggle (injected; no template change)
  const inner = document.querySelector(".nav-inner");
  if (inner && !inner.querySelector(".nav-toggle")) {
    const t = document.createElement("button");
    t.className = "nav-toggle";
    t.setAttribute("aria-label", "Toggle navigation");
    t.innerHTML = "&#9776;";
    t.addEventListener("click", () => {
      inner.classList.toggle("open");
      t.innerHTML = inner.classList.contains("open") ? "&#10005;" : "&#9776;";
    });
    const brand = inner.querySelector(".brand");
    if (brand) brand.after(t); else inner.prepend(t);
  }

  // Alerts: close button + auto-dismiss slide-out
  document.querySelectorAll(".alert").forEach(al => {
    if (al.querySelector(".alert-x")) return;
    const x = document.createElement("button");
    x.className = "alert-x";
    x.setAttribute("aria-label", "Dismiss");
    x.innerHTML = "&#10005;";
    const dismiss = () => { al.classList.add("alert-out"); setTimeout(() => al.remove(), 420); };
    x.addEventListener("click", dismiss);
    al.appendChild(x);
    setTimeout(dismiss, 6500);
  });
});
