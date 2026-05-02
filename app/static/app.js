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
    if (t && t.slug) {
      url.searchParams.set('ticker', t.slug);
    } else {
      // Cleared ticker: drop everything study-related too
      url.searchParams.delete('ticker');
      url.searchParams.delete('keywords');
      url.searchParams.delete('pages');
      url.searchParams.delete('range');
      url.searchParams.delete('years');
    }
    history.replaceState(null, '', url.toString());
    // Sidebar links: only carry the ticker forward across studies; study-specific
    // params (keywords/pages/range/years) are reset when navigating to another study.
    document.querySelectorAll('.sidebar-item').forEach(a => {
      const u = new URL(a.href, window.location.origin);
      ['keywords', 'pages', 'range', 'years'].forEach(k => u.searchParams.delete(k));
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
      // Reserve room at the bottom for the Smartkarma logo strip (logo is ~22px,
      // and we want clear space between the rotated x-axis labels and the logo).
      layout: { padding: { bottom: 60 } },
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
          labels: {
            boxWidth: 12, boxHeight: 12, padding: 16,
            font: { family: 'Roboto', size: 12 },
            // Hide legend entries for datasets that have no data points
            filter: (item, chartData) => {
              const ds = chartData.datasets[item.datasetIndex];
              return ds && Array.isArray(ds.data) && ds.data.length > 0;
            },
          },
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

  // Chart.js plugin: paints the Smartkarma logo at the bottom-right, aligned
  // with the chart area's right edge. Drawn on the canvas so it's baked into
  // toDataURL() exports.
  const SmartkarmaWatermark = {
    id: 'smartkarmaWatermark',
    afterDraw(chart) {
      const img = _skLogoImg;
      if (!img || !img.complete || !img.naturalWidth) return;
      const { ctx, chartArea, height } = chart;
      const logoH = 22;
      const ratio = img.naturalWidth / img.naturalHeight;
      const logoW = logoH * ratio;
      const rightEdge = (chartArea && chartArea.right) || chart.width;
      const bottomPad = 10;
      ctx.save();
      // High-quality downscale: the source is 1000x190 px, we draw at ~22px tall.
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(img, rightEdge - logoW, height - bottomPad - logoH, logoW, logoH);
      ctx.restore();
    },
  };
  // Faded "Smartkarma" wordmark drawn behind the chart data, centered in the
  // chart area. Uses beforeDatasetsDraw so it sits under the lines/bars but
  // above the gridlines.
  const SmartkarmaCenterMark = {
    id: 'smartkarmaCenterMark',
    beforeDatasetsDraw(chart) {
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      const cx = (chartArea.left + chartArea.right) / 2;
      const cy = (chartArea.top + chartArea.bottom) / 2;
      // Scale the wordmark to roughly half the chart width
      const targetWidth = (chartArea.right - chartArea.left) * 0.45;
      ctx.save();
      ctx.font = '700 64px Roboto, sans-serif';
      // Pre-measure to scale to targetWidth
      const measured = ctx.measureText('Smartkarma').width;
      const scale = targetWidth / measured;
      ctx.translate(cx, cy);
      ctx.scale(scale, scale);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = 'rgba(36, 169, 167, 0.07)';
      ctx.fillText('Smartkarma', 0, 0);
      ctx.restore();
    },
  };

  if (typeof Chart !== 'undefined') {
    Chart.register(SmartkarmaWatermark);
    Chart.register(SmartkarmaCenterMark);
  }

  // Compute axis bounds that hug the data: [min * (1 - pad), max * (1 + pad)].
  // For series that cross zero, expand symmetrically by pad of the absolute span.
  function tightAxisBounds(values, pad = 0.05) {
    const nums = values.filter(v => Number.isFinite(v));
    if (!nums.length) return null;
    const min = Math.min(...nums);
    const max = Math.max(...nums);
    if (min === max) {
      // flat series: centre the value with a small visible band
      const delta = Math.abs(min) * pad || 1;
      return { min: min - delta, max: max + delta };
    }
    if (min >= 0) {
      return { min: min * (1 - pad), max: max * (1 + pad) };
    }
    if (max <= 0) {
      return { min: min * (1 + pad), max: max * (1 - pad) };
    }
    // crosses zero: pad both ends by `pad` of the span
    const span = max - min;
    return { min: min - span * pad, max: max + span * pad };
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
    // Re-render onto an offscreen canvas with white background and padding
    // so exports aren't transparent and have breathing room around them.
    const src = chart.canvas;
    const pad = 24;
    const off = document.createElement('canvas');
    off.width = src.width + pad * 2;
    off.height = src.height + pad * 2;
    const c = off.getContext('2d');
    c.fillStyle = '#ffffff';
    c.fillRect(0, 0, off.width, off.height);
    c.drawImage(src, pad, pad);
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

  // ---- URL params for shareable study links ----
  // Reads / writes the chart's input params (keywords, pages, range, years) on
  // top of the existing ?ticker=. Keeps the URL the source of truth so a copied
  // link reproduces the chart on someone else's machine.
  function urlParams() {
    return new URLSearchParams(window.location.search);
  }

  function readUrlParams() {
    const p = urlParams();
    const out = {};
    if (p.has('keywords')) out.keywords = p.get('keywords').split(',').map(s => s.trim()).filter(Boolean);
    if (p.has('pages')) out.pages = p.get('pages').split(',').map(s => s.trim()).filter(Boolean);
    if (p.has('range')) out.range = p.get('range');
    if (p.has('years')) out.years = parseInt(p.get('years'), 10) || null;
    return out;
  }

  function writeUrlParams(updates) {
    const url = new URL(window.location.href);
    Object.entries(updates).forEach(([k, v]) => {
      if (v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0)) {
        url.searchParams.delete(k);
      } else if (Array.isArray(v)) {
        url.searchParams.set(k, v.join(','));
      } else {
        url.searchParams.set(k, String(v));
      }
    });
    history.replaceState(null, '', url.toString());
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
    readUrlParams, writeUrlParams,
    tightAxisBounds,
  };
})();
