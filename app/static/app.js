// ---- Shared utilities + global state ----
const ACCENT = '#24a9a7';
const PRICE_COLOR = '#1a1f24';
const NEG_COLOR = '#d24b4b';
const YEAR_COLORS = ['#24a9a7', '#7c5cff', '#e8a93b', '#d24b4b', '#5cb85c', '#5b8def', '#a04ec0', '#666'];

const STORAGE_KEY = 'altdata.ticker';

const AltData = (() => {
  const subs = new Set();
  let current = null;
  try { current = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch {}

  const _syncUrl = (t) => {
    const url = new URL(window.location.href);
    if (t && t.slug) url.searchParams.set('ticker', t.slug);
    else url.searchParams.delete('ticker');
    history.replaceState(null, '', url.toString());
    // also update sidebar links so navigation preserves the ticker
    document.querySelectorAll('.sidebar-item').forEach(a => {
      const u = new URL(a.href, window.location.origin);
      if (t && t.slug) u.searchParams.set('ticker', t.slug);
      else u.searchParams.delete('ticker');
      a.href = u.pathname + u.search;
    });
  };

  const get = () => current;
  const set = (t) => {
    current = t;
    if (t) localStorage.setItem(STORAGE_KEY, JSON.stringify(t));
    else localStorage.removeItem(STORAGE_KEY);
    _syncUrl(t);
    subs.forEach(fn => { try { fn(t); } catch (e) { console.error(e); } });
  };
  const subscribe = (fn) => { subs.add(fn); return () => subs.delete(fn); };

  // Hydrate from ?ticker=<slug> if present and different from cached
  async function _hydrateFromUrl() {
    const slug = new URLSearchParams(window.location.search).get('ticker');
    if (!slug) {
      _syncUrl(current);
      return;
    }
    if (current && current.slug === slug) {
      _syncUrl(current);
      return;
    }
    try {
      const r = await fetch('/api/ticker/' + encodeURIComponent(slug));
      if (!r.ok) return;
      const t = await r.json();
      set(t);
    } catch (e) { console.error(e); }
  }

  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  async function jsonGet(url) {
    const r = await fetch(url);
    if (!r.ok) {
      let msg = r.statusText;
      try { const j = await r.json(); msg = j.detail || msg; } catch {}
      throw new Error(msg);
    }
    return r.json();
  }

  function mountTickerSearch(rootEl, opts = {}) {
    const { onPick, placeholder = 'Search ticker or company…', compact = false, selectedChip = false } = opts;
    let activeIdx = -1;
    let results = [];
    let mode = (selectedChip && current) ? 'chip' : 'search';

    function renderRoot() {
      if (mode === 'chip' && current) {
        rootEl.innerHTML = `
          <button type="button" class="ticker-chip" data-edit>
            <span class="ticker-chip-icon">●</span>
            <span class="ticker-chip-text">
              <span class="ticker-chip-name">${current.name || current.slug}</span>
              <span class="ticker-chip-meta">${current.bloomberg_ticker || ''}${current.bloomberg_ticker && current.yahoo_ticker ? ' · ' : ''}${current.yahoo_ticker || ''}</span>
            </span>
            <span class="ticker-chip-action" title="Change ticker">change</span>
            <span class="ticker-chip-x" data-clear title="Clear">×</span>
          </button>
        `;
        rootEl.querySelector('[data-edit]').addEventListener('click', (e) => {
          if (e.target.closest('[data-clear]')) return;
          mode = 'search';
          renderRoot();
          rootEl.querySelector('input').focus();
        });
        rootEl.querySelector('[data-clear]').addEventListener('click', (e) => {
          e.stopPropagation();
          set(null);
          mode = 'search';
          renderRoot();
        });
        return;
      }
      rootEl.innerHTML = `
        <div class="search ${compact ? 'compact' : ''}">
          <input type="search" placeholder="${placeholder}" autocomplete="off" />
          <div class="search-results"></div>
        </div>
      `;
      const input = rootEl.querySelector('input');
      const list = rootEl.querySelector('.search-results');
      if (current && !selectedChip) input.value = current.name || current.slug;

      const renderList = () => {
        if (!results.length) { list.classList.remove('open'); list.innerHTML = ''; return; }
        list.innerHTML = results.map((r, i) => `
          <div class="search-result ${i === activeIdx ? 'active' : ''}" data-i="${i}">
            <div>
              <div class="name">${r.name || r.slug}</div>
              <div class="ticker">${r.bloomberg_ticker || ''}${r.bloomberg_ticker && r.yahoo_ticker ? ' · ' : ''}${r.yahoo_ticker || ''}</div>
            </div>
            <span class="muted" style="font-size:11px">${r.market_status || ''}</span>
          </div>
        `).join('');
        list.classList.add('open');
        list.querySelectorAll('.search-result').forEach(el => {
          el.addEventListener('mousedown', e => {
            e.preventDefault();
            pick(results[+el.dataset.i]);
          });
        });
      };

      const pick = (r) => {
        list.classList.remove('open');
        set(r);
        if (selectedChip) {
          mode = 'chip';
          renderRoot();
        } else {
          input.value = r.name || r.slug;
        }
        onPick && onPick(r);
      };

      const search = debounce(async () => {
        const q = input.value.trim();
        try {
          const { results: rs } = await jsonGet('/api/tickers?q=' + encodeURIComponent(q) + '&limit=15');
          results = rs;
          activeIdx = -1;
          renderList();
        } catch (e) { console.error(e); }
      }, 180);

      input.addEventListener('input', search);
      input.addEventListener('focus', search);
      input.addEventListener('blur', () => setTimeout(() => list.classList.remove('open'), 150));
      input.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown') { activeIdx = Math.min(activeIdx + 1, results.length - 1); renderList(); e.preventDefault(); }
        else if (e.key === 'ArrowUp') { activeIdx = Math.max(activeIdx - 1, 0); renderList(); e.preventDefault(); }
        else if (e.key === 'Enter' && activeIdx >= 0) { pick(results[activeIdx]); e.preventDefault(); }
        else if (e.key === 'Escape') {
          list.classList.remove('open');
          if (selectedChip && current) { mode = 'chip'; renderRoot(); }
        }
      });
    }

    renderRoot();

    // Keep UI synced if global ticker changes elsewhere (other tab pages, sidebar nav, etc)
    const off = subscribe((t) => {
      if (selectedChip) {
        mode = t ? 'chip' : 'search';
        renderRoot();
      } else {
        const inp = rootEl.querySelector('input');
        if (inp) inp.value = t ? (t.name || t.slug) : '';
      }
    });
    return { unmount: off };
  }

  function installNavTicker(rootEl) {
    mountTickerSearch(rootEl, { compact: true, placeholder: 'Set ticker…', selectedChip: true });
  }

  function mountKeywordInput(rootEl, initial = []) {
    rootEl.innerHTML = `
      <div class="row" style="gap:8px">
        <input type="text" placeholder="add keyword + Enter" style="flex:0 0 220px" data-input />
        <div class="chip-row" data-chips></div>
      </div>
    `;
    const chipsEl = rootEl.querySelector('[data-chips]');
    const input = rootEl.querySelector('[data-input]');
    let kws = [...initial];

    const render = () => {
      chipsEl.innerHTML = kws.map((k, i) => `
        <span class="chip">${k}<span class="x" data-i="${i}">×</span></span>
      `).join('');
      chipsEl.querySelectorAll('.x').forEach(el => {
        el.addEventListener('click', () => { kws.splice(+el.dataset.i, 1); render(); });
      });
    };

    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && input.value.trim()) {
        kws.push(input.value.trim());
        input.value = '';
        render();
        e.preventDefault();
      } else if (e.key === 'Backspace' && !input.value && kws.length) {
        kws.pop(); render();
      }
    });

    render();
    return {
      get: () => [...kws],
      set: (next) => { kws = [...next]; render(); },
    };
  }

  function mountWikiPageInput(rootEl, opts = {}) {
    const { max = 5, placeholder = 'add Wikipedia page' } = opts;
    rootEl.innerHTML = `
      <div class="wiki-input">
        <div class="row" style="gap:8px;align-items:flex-start">
          <div class="search" style="flex:0 0 260px;position:relative">
            <input type="text" placeholder="${placeholder}" autocomplete="off" data-input />
            <div class="search-results" data-suggest></div>
          </div>
          <div class="chip-row" data-chips></div>
        </div>
      </div>
    `;
    const input = rootEl.querySelector('[data-input]');
    const list = rootEl.querySelector('[data-suggest]');
    const chipsEl = rootEl.querySelector('[data-chips]');
    let pages = [];
    let suggestions = [];
    let activeIdx = -1;

    const renderChips = () => {
      chipsEl.innerHTML = pages.map((p, i) => `
        <span class="chip">${p}<span class="x" data-i="${i}">×</span></span>
      `).join('');
      chipsEl.querySelectorAll('.x').forEach(el => {
        el.addEventListener('click', () => { pages.splice(+el.dataset.i, 1); renderChips(); });
      });
    };

    const renderSuggest = () => {
      if (!suggestions.length) { list.classList.remove('open'); list.innerHTML = ''; return; }
      list.innerHTML = suggestions.map((s, i) => `
        <div class="search-result ${i === activeIdx ? 'active' : ''}" data-i="${i}">
          <div class="name">${s.title}</div>
        </div>
      `).join('');
      list.classList.add('open');
      list.querySelectorAll('.search-result').forEach(el => {
        el.addEventListener('mousedown', e => {
          e.preventDefault();
          pick(suggestions[+el.dataset.i]);
        });
      });
    };

    const addPage = (title) => {
      const t = title.replace(/ /g, '_');
      if (pages.includes(t)) return;
      if (pages.length >= max) return;
      pages.push(t);
      renderChips();
    };

    const pick = (s) => {
      addPage(s.title);
      input.value = '';
      suggestions = [];
      renderSuggest();
    };

    const search = debounce(async () => {
      const q = input.value.trim();
      if (!q) { suggestions = []; renderSuggest(); return; }
      try {
        const { results } = await jsonGet('/api/wiki/suggest?q=' + encodeURIComponent(q));
        suggestions = results;
        activeIdx = -1;
        renderSuggest();
      } catch (e) { console.error(e); }
    }, 220);

    input.addEventListener('input', search);
    input.addEventListener('focus', search);
    input.addEventListener('blur', () => setTimeout(() => list.classList.remove('open'), 150));
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') { activeIdx = Math.min(activeIdx + 1, suggestions.length - 1); renderSuggest(); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { activeIdx = Math.max(activeIdx - 1, 0); renderSuggest(); e.preventDefault(); }
      else if (e.key === 'Enter' && activeIdx >= 0) { pick(suggestions[activeIdx]); e.preventDefault(); }
      else if (e.key === 'Backspace' && !input.value && pages.length) { pages.pop(); renderChips(); }
      else if (e.key === 'Escape') { list.classList.remove('open'); }
    });

    renderChips();
    return {
      get: () => [...pages],
      set: (next) => { pages = [...next]; renderChips(); },
    };
  }

  function baseChartOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      // Reserve room at the bottom for the Smartkarma logo strip
      layout: { padding: { bottom: 44 } },
      plugins: {
        title: {
          display: true,
          text: '',
          align: 'start',
          color: '#1a1f24',
          font: { family: 'Roboto', size: 15, weight: '500' },
          padding: { top: 4, bottom: 2 },
        },
        subtitle: {
          display: true,
          text: '',
          align: 'start',
          color: '#6b7480',
          font: { family: 'Roboto', size: 12, weight: '400' },
          padding: { top: 0, bottom: 14 },
        },
        legend: {
          position: 'top', align: 'end',
          labels: { boxWidth: 12, boxHeight: 12, padding: 16, font: { family: 'Roboto', size: 12 } },
        },
        tooltip: {
          backgroundColor: '#1a1f24',
          titleFont: { family: 'Roboto', size: 12, weight: '500' },
          bodyFont: { family: 'Roboto', size: 12 },
          padding: 10, cornerRadius: 4,
        },
      },
      scales: {
        x: {
          type: 'time', time: { unit: 'month' },
          grid: { display: false },
          ticks: {
            font: { family: 'Roboto', size: 11 }, color: '#9aa3ad',
            maxRotation: 90, minRotation: 90,
            autoSkip: true, autoSkipPadding: 6,
          },
        },
        yPrice: {
          type: 'linear', position: 'left',
          grid: { color: '#f0f1f3' },
          ticks: { font: { family: 'Roboto', size: 11 }, color: '#9aa3ad' },
          title: { display: true, text: 'Price', font: { family: 'Roboto', size: 11 }, color: '#6b7480' },
        },
        ySignal: {
          type: 'linear', position: 'right',
          grid: { display: false },
          ticks: { font: { family: 'Roboto', size: 11 }, color: '#9aa3ad' },
          title: { display: true, text: 'Signal', font: { family: 'Roboto', size: 11 }, color: '#6b7480' },
        },
      },
    };
  }

  // ---- Smartkarma branding ----
  // Logo is served from our own /static so it doesn't taint the canvas (cross-origin
  // images without CORS headers prevent toDataURL from working for PNG export).
  const SK_LOGO_URL = '/static/sk-logo.png';
  let _skLogoImg = null;
  function _loadSkLogo() {
    if (_skLogoImg) return _skLogoImg;
    _skLogoImg = new Image();
    _skLogoImg.src = SK_LOGO_URL;
    return _skLogoImg;
  }
  _loadSkLogo();

  // Chart.js plugin: paints the Smartkarma logo on the bottom-right of the canvas.
  // Drawing on the canvas means the branding is included in toDataURL() exports.
  const SmartkarmaWatermark = {
    id: 'smartkarmaWatermark',
    afterDraw(chart) {
      const img = _skLogoImg;
      if (!img || !img.complete || !img.naturalWidth) return;
      const { ctx, width, height } = chart;
      const padX = 14;
      const padY = 10;
      const logoH = 24;
      const ratio = img.naturalWidth / img.naturalHeight;
      const logoW = logoH * ratio;
      ctx.save();
      ctx.drawImage(img, width - padX - logoW, height - padY - logoH, logoW, logoH);
      ctx.restore();
    },
  };
  if (typeof Chart !== 'undefined') {
    Chart.register(SmartkarmaWatermark);
  }

  function setChartTitle(chart, text) {
    if (!chart) return;
    chart.options.plugins.title.text = text || '';
    chart.options.plugins.title.display = !!text;
    chart.update('none');
  }

  // ---- Export helpers ----
  function downloadChartPng(chart, filename) {
    if (!chart) return;
    // Re-render onto an offscreen canvas with white background so exports aren't transparent
    const src = chart.canvas;
    const off = document.createElement('canvas');
    off.width = src.width; off.height = src.height;
    const c = off.getContext('2d');
    c.fillStyle = '#ffffff';
    c.fillRect(0, 0, off.width, off.height);
    c.drawImage(src, 0, 0);
    const url = off.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = url; a.download = filename || 'altdata-chart.png';
    document.body.appendChild(a); a.click(); a.remove();
  }

  function buildEmbedSnippet(opts = {}) {
    const { title = 'Alt-Data Analysis Tool · Smartkarma', height = 600 } = opts;
    const url = window.location.href;
    return `<iframe src="${url}" width="100%" height="${height}" frameborder="0" loading="lazy" title="${title}" style="border:1px solid #e6e8eb;border-radius:6px;"></iframe>`;
  }

  async function copyEmbedSnippet(opts) {
    const snippet = buildEmbedSnippet(opts);
    try {
      await navigator.clipboard.writeText(snippet);
      return true;
    } catch {
      // Fallback: prompt
      window.prompt('Copy embed code:', snippet);
      return false;
    }
  }

  function renderWarnings(el, warnings) {
    if (!el) return;
    if (!warnings || !warnings.length) { el.innerHTML = ''; return; }
    el.innerHTML = `
      <div class="warnings">
        ${warnings.map(w => `
          <div class="warning-item">
            <span class="warning-icon">⚠</span>
            <span><b>${w.source}:</b> ${w.message}</span>
          </div>
        `).join('')}
      </div>
    `;
  }

  function toast(msg, ms = 1800) {
    let el = document.querySelector('.toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    requestAnimationFrame(() => el.classList.add('show'));
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('show'), ms);
  }

  // Persist the last study result so navigating between studies keeps it visible.
  const STUDY_RESULT_KEY = (id) => `altdata.study.${id}`;
  function saveResult(studyId, payload) {
    try { sessionStorage.setItem(STUDY_RESULT_KEY(studyId), JSON.stringify(payload)); } catch {}
  }
  function loadResult(studyId) {
    try {
      const raw = sessionStorage.getItem(STUDY_RESULT_KEY(studyId));
      return raw ? JSON.parse(raw) : null;
    } catch { return null; }
  }
  function clearResult(studyId) {
    try { sessionStorage.removeItem(STUDY_RESULT_KEY(studyId)); } catch {}
  }

  return {
    get, set, subscribe,
    debounce, jsonGet,
    mountTickerSearch, installNavTicker, mountKeywordInput, mountWikiPageInput,
    baseChartOptions,
    hydrateFromUrl: _hydrateFromUrl,
    saveResult, loadResult, clearResult,
    downloadChartPng, copyEmbedSnippet, buildEmbedSnippet, toast,
    setChartTitle, renderWarnings,
  };
})();
