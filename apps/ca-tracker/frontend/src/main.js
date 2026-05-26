import './style.css';

function initThemeToggle() {
  const root = document.documentElement;
  const btn = document.querySelector('.theme-toggle');
  if (!btn) return;

  const sync = () => {
    const dark = root.dataset.theme === 'dark';
    btn.setAttribute('aria-label', dark ? '切换白天模式' : '切换黑夜模式');
    btn.setAttribute('title', dark ? '切换白天模式' : '切换黑夜模式');
  };

  sync();
  btn.addEventListener('click', () => {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    localStorage.setItem('motools-theme', next);
    sync();
  });
}

function initAutoFilters() {
  document.querySelectorAll('form.auto-filter').forEach((form) => {
    let timer;
    const resetPage = () => {
      const page = form.querySelector('input[name="page"]');
      if (page) page.value = '1';
    };
    const delayedSubmit = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        resetPage();
        form.requestSubmit();
      }, 300);
    };
    form.querySelectorAll('select, input[type="checkbox"]').forEach((el) => {
      el.addEventListener('change', () => {
        resetPage();
        form.requestSubmit();
      });
    });
    form.querySelectorAll('input[type="search"]').forEach((el) => {
      el.addEventListener('input', delayedSubmit);
    });
  });
}

function initCopyButtons() {
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    e.preventDefault();
    const text = btn.dataset.copy;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
      } catch {}
      document.body.removeChild(ta);
    }
    btn.classList.add('copied');
    const use = btn.querySelector('use');
    const prev = use && use.getAttribute('href');
    if (use) use.setAttribute('href', '#i-check');
    setTimeout(() => {
      btn.classList.remove('copied');
      if (use && prev) use.setAttribute('href', prev);
    }, 1200);
  });
}

initThemeToggle();
initAutoFilters();
initCopyButtons();
