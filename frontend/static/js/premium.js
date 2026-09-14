/* MediQueue AI - Premium UI helpers (Phase 2) */
(function(){
  // -------- Dark / light mode toggle --------
  const KEY='mq_theme';
  const applyTheme = t => document.documentElement.classList.toggle('dark', t==='dark');
  applyTheme(localStorage.getItem(KEY) || 'light');
  window.addEventListener('DOMContentLoaded', ()=>{
    if(document.querySelector('.mode-toggle')) return;
    const b=document.createElement('button');
    b.className='mode-toggle'; b.title='Toggle theme';
    b.innerHTML = document.documentElement.classList.contains('dark') ? '☀️' : '🌙';
    b.onclick=()=>{
      const cur=document.documentElement.classList.toggle('dark')?'dark':'light';
      localStorage.setItem(KEY,cur);
      b.innerHTML = cur==='dark' ? '☀️' : '🌙';
    };
    document.body.appendChild(b);

    // Floating medical background icons (subtle)
    ['🩺','💊','❤️','🧬','⚕️'].forEach((ic,i)=>{
      const el=document.createElement('div');
      el.className='med-float';
      el.textContent=ic;
      el.style.left=(10+i*22)+'%';
      el.style.top=(20+((i*17)%60))+'%';
      el.style.animationDelay=(i*.9)+'s';
      document.body.appendChild(el);
    });

    // Auto animate counters
    document.querySelectorAll('.num[data-count]').forEach(el=>{
      const t=+el.dataset.count||0; let n=0; const step=Math.max(1,Math.ceil(t/40));
      const id=setInterval(()=>{n+=step; if(n>=t){n=t;clearInterval(id);} el.textContent=n;},20);
    });
  });

  // -------- Toast API --------
  window.toast = function(msg, type='info'){
    let w=document.querySelector('.toast-wrap');
    if(!w){w=document.createElement('div');w.className='toast-wrap';document.body.appendChild(w);}
    const t=document.createElement('div'); t.className='toast '+type; t.textContent=msg;
    w.appendChild(t); setTimeout(()=>t.remove(),3500);
  };

  // -------- Auto ripples on all .btn --------
  document.addEventListener('click', e=>{
    const b = e.target.closest('.btn');
    if(b && !b.classList.contains('ripple')) b.classList.add('ripple');
  }, true);
})();
