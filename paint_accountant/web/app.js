'use strict';

/* Paint Accountant web UI — plain JS, no build step, no external libraries.
   Talks to the REST API under /api. Screens are switched by the URL hash:
     #/                 paints list
     #/paint/new        new paint (optionally ?storage=ID)
     #/paint/ID         edit paint
     #/storages         storages list
     #/storage/ID       one storage with its paints
     #/catalogs         manufacturers and tags                                */

// ======================= small helpers =======================

const viewEl = document.getElementById('view');

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') el.style.cssText = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (typeof v !== 'string' && k in el) el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

const countPaints = n => (LANG === 'en' ? `${n} ${n === 1 ? 'paint' : 'paints'}`
  : `${n} ${plural(n, 'краска', 'краски', 'красок')}`);
const countPots = n => (LANG === 'en' ? `${n} ${n === 1 ? 'pot' : 'pots'}`
  : `${n} ${plural(n, 'банка', 'банки', 'банок')}`);

function debounce(fn, ms) {
  let tg;
  return (...args) => { clearTimeout(tg); tg = setTimeout(() => fn(...args), ms); };
}

function toast(message, kind = '', action = null) {
  const el = document.getElementById('toast');
  el.replaceChildren(h('span', null, message));
  if (action) {
    el.append(h('button', {
      type: 'button', class: 'toast-action',
      onclick: async () => { el.className = ''; await action.onClick(); },
    }, action.label));
  }
  el.className = 'show ' + kind + (action ? ' has-action' : '');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.className = ''; }, action ? 8000 : kind === 'err' || kind === 'warn' ? 5000 : 2500);
}

function normHex(value) {
  const v = (value || '').trim();
  if (!v) return '';
  const m = v.match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!m) return null;
  let d = m[1].toLowerCase();
  if (d.length === 3) d = [...d].map(c => c + c).join('');
  return '#' + d;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // navigator.clipboard is unavailable on plain http from another device
    const ta = h('textarea', { style: 'position:fixed;top:0;left:0;opacity:0' });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* ignore */ }
    ta.remove();
    return ok;
  }
}

// ======================= API =======================

class ApiError extends Error {
  constructor(status, message, detail = null) { super(message); this.status = status; this.detail = detail; }
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const res = await fetch('/api' + path, options);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const d = data && data.detail;
    let msg = t('Ошибка {0}', res.status);
    if (typeof d === 'string') msg = d;
    else if (Array.isArray(d)) msg = d.map(e => (e.loc ? e.loc.slice(1).join('.') + ': ' : '') + e.msg).join('; ');
    else if (d && typeof d === 'object') msg = d.message || msg;
    if (res.status === 401 && d && d.code === 'auth_required') goToLogin();
    throw new ApiError(res.status, msg, d && typeof d === 'object' && !Array.isArray(d) ? d : null);
  }
  return data;
}

function explain(err, conflictText) {
  if (!(err instanceof ApiError)) return t('Нет связи с сервером');
  if (err.status === 409 && conflictText) return conflictText;
  if (err.status === 404) return t('Не найдено — возможно, уже удалено');
  if (err.status === 422) return t('Проверьте поля: ') + err.message;
  return err.message;
}

// ======================= shared pieces =======================

function swatch(hex, big = false) {
  return h('span', {
    class: 'swatch' + (hex ? '' : ' no-color') + (big ? ' big' : ''),
    style: hex ? `--c:${hex}` : null,
    title: hex || t('цвет не задан'),
  });
}

function setSwatch(el, hex) {
  el.classList.toggle('no-color', !hex);
  el.style.cssText = hex ? `--c:${hex}` : '';
  el.title = hex || t('цвет не задан');
}

function toggleChip(label, { color, pressed, plain, onToggle }) {
  const b = h('button', {
    type: 'button',
    class: 'chip' + (plain ? ' plain' : ''),
    style: color ? `--tc:${color}` : null,
    'aria-pressed': String(!!pressed),
  }, label);
  b.addEventListener('click', () => {
    const now = b.getAttribute('aria-pressed') !== 'true';
    b.setAttribute('aria-pressed', String(now));
    onToggle(now);
  });
  return b;
}

/** Modal window with buttons. Resolves with the value of the pressed button, or null if dismissed. */
function choiceDialog({ title, text = '', content = null, buttons }) {
  return new Promise(resolve => {
    const dlg = h('dialog', { class: 'dialog', 'aria-labelledby': 'dlg-title' });
    const close = result => { dlg.close(); dlg.remove(); resolve(result); };
    const btns = buttons.map(b => h('button', { type: 'button', class: 'btn ' + (b.kind || ''), onclick: () => close(b.value) }, b.label));
    dlg.append(
      h('h3', { id: 'dlg-title' }, title),
      text ? h('p', { class: 'muted' }, text) : null,
      content,
      h('div', { class: 'dialog-actions' }, btns));
    dlg.addEventListener('cancel', e => { e.preventDefault(); close(null); }); // Esc / back gesture
    dlg.addEventListener('click', e => { if (e.target === dlg) close(null); }); // tap outside
    document.body.append(dlg);
    dlg.showModal();
    btns[0].focus();
  });
}

/** Modal "are you sure?" window. Resolves true if the user confirms. */
async function confirmDialog({ title, text = '', confirmLabel = t('Удалить') }) {
  return (await choiceDialog({
    title, text,
    buttons: [{ label: t('Отмена'), value: false }, { label: confirmLabel, value: true, kind: 'danger-solid' }],
  })) === true;
}

/**
 * A second pot of a paint in an "at hand" storage needs a yes. Asks where it should go.
 * Resolves with extra fields for the request ({allow_duplicate} or {storage_id, allow_duplicate}),
 * or null if the user cancelled.
 */
async function askAboutDuplicate(paintName, detail, count = 1) {
  const storages = await api('GET', '/storages');
  const others = storages.filter(s => s.id !== detail.storage.id);
  const sel = h('select', { class: 'input', 'aria-label': t('Другое хранилище') },
    others.map(s => h('option', { value: String(s.id) }, s.name + (s.at_hand ? t(' · под рукой') : ''))),
    h('option', { value: 'none' }, t('— без хранилища —')));
  const preferred = (detail.suggest && others.find(s => s.id === detail.suggest.id)) || others.find(s => !s.at_hand);
  sel.value = preferred ? String(preferred.id) : 'none';
  const choice = await choiceDialog({
    title: detail.have
      ? t('«{0}» уже есть в «{1}» ({2})', paintName, detail.storage.name, detail.have)
      : t('{0} «{1}» в «{2}»?', countPots(count), paintName, detail.storage.name),
    text: detail.have
      ? t('Добавить ещё одну банку туда или положить в другое хранилище?')
      : t('Положить их все под руку или в другое хранилище?'),
    content: h('div', { class: 'field' }, h('span', { class: 'label' }, t('Другое хранилище')), sel),
    buttons: [
      { label: t('Отмена'), value: null },
      { label: t('В выбранное'), value: 'other' },
      { label: t('Да, в «{0}»', detail.storage.name), value: 'here', kind: 'primary' },
    ],
  });
  if (choice === 'here') return { allow_duplicate: true };
  if (choice === 'other') return { storage_id: sel.value === 'none' ? null : Number(sel.value), allow_duplicate: true };
  return null;
}

const isDuplicate = err => err instanceof ApiError && err.status === 409 && err.detail && err.detail.code === 'duplicate_at_hand';

/** POST /paints/{id}/add, asking about a duplicate at hand when needed. Resolves null if cancelled. */
async function addPots(paint, body = {}) {
  try {
    return await api('POST', `/paints/${paint.id}/add`, body);
  } catch (err) {
    if (!isDuplicate(err)) throw err;
    const extra = await askAboutDuplicate(paint.name, err.detail, body.count || 1);
    return extra ? api('POST', `/paints/${paint.id}/add`, { ...body, ...extra }) : null;
  }
}

/** Delete button that asks for confirmation in a modal window. */
function deleteButton(label, question, onConfirm) {
  const b = h('button', { type: 'button', class: 'btn danger' }, label);
  b.addEventListener('click', async () => {
    const { title, text } = typeof question === 'function' ? question() : question;
    if (!(await confirmDialog({ title, text }))) return;
    b.disabled = true;
    try { await onConfirm(); } finally { b.disabled = false; }
  });
  return b;
}

function pageHead(title, backHref, extra) {
  return h('div', { class: 'page-head' },
    backHref ? h('a', { class: 'back', href: backHref, 'aria-label': t('Назад') }, '‹') : null,
    h('h1', null, title),
    extra);
}

const storageName = st => (st ? t('«{0}»', st.name) : t('без хранилища'));

/** "Под рукой ✋, Запас 2" — where the pots of a paint lie. */
function locationsText(p) {
  if (!p.locations.length) return t('нет в наличии');
  return p.locations.map(l => {
    const label = l.storage ? l.storage.name + (l.storage.at_hand ? ' ✋' : '') : t('без хранилища');
    return p.locations.length > 1 || l.quantity > 1 ? `${label} ${l.quantity}` : label;
  }).join(', ');
}

/** Select "where to put pots": the storages (the default one preselected) or none. */
function placementSelect(storages, selected, attrs = {}) {
  const def = storages.find(s => s.is_default);
  const sel = h('select', { class: 'input', ...attrs },
    storages.map(s => h('option', { value: String(s.id) }, s.name + (s.at_hand ? ' ✋' : ''))),
    h('option', { value: 'none' }, t('— без хранилища —')));
  const pick = selected != null ? selected : def ? def.id : null;
  sel.value = pick == null ? 'none' : String(pick);
  return sel;
}

/** API body part for a placement select. */
function placementBody(value) {
  return { storage_id: value === 'none' ? null : Number(value) };
}

/** Tell where pots went / came from; offer to refill "at hand" from a reserve. */
function showNotice(paint, notice) {
  if (!notice) return;
  if (notice.action === 'added') {
    const parts = notice.placed.map(x => `+${x.count} → ${storageName(x.storage)}`).join(', ');
    if (notice.already_in.length) {
      const where = notice.already_in.map(x => `${storageName(x.storage)} (${x.quantity})`).join(', ');
      toast(t('{0}: {1}. Уже есть в {2}', paint.name, parts, where), 'warn');
    } else {
      toast(`${paint.name}: ${parts}`, 'ok');
    }
  } else if (notice.action === 'removed') {
    const taken = notice.taken.map(x => t('−{0} из {1}', x.count, storageName(x.storage))).join(', ');
    const src = notice.refill_from[0];
    const dst = notice.emptied[0];
    if (src && dst) {
      toast(t('{0}: {1}. Под рукой закончилась, есть в {2} ({3})', paint.name, taken, storageName(src.storage), src.quantity), 'warn', {
        label: t('Взять'),
        onClick: async () => {
          try {
            const r = await api('POST', `/paints/${paint.id}/move`,
              { from_storage_id: src.storage ? src.storage.id : null, to_storage_id: dst.id });
            showNotice(r.paint, r.notice);
            paintChanged(r.paint);
          } catch (err) { toast(explain(err), 'err'); }
        },
      });
    } else {
      toast(`${paint.name}: ${taken}`, 'ok');
    }
  } else if (notice.action === 'moved') {
    toast(`${paint.name}: ${notice.count} → ${storageName(notice.to)}`, 'ok');
  }
}

// rows and editors on screen register here so a change made elsewhere (a toast button) shows up
const paintWatchers = new Map();
function paintChanged(paint) {
  (paintWatchers.get(paint.id) || []).forEach(fn => fn(paint));
  document.dispatchEvent(new CustomEvent('paints-changed'));
}
function watchPaint(id, fn) {
  if (!paintWatchers.has(id)) paintWatchers.set(id, []);
  paintWatchers.get(id).push(fn);
}

/** Paint rows with quick +/-. On a storage page (storageId) they count and change pots in that storage. */
function paintList(paints, { storageId = null } = {}) {
  return h('ul', { class: 'paint-list' }, paints.map(p => paintRow(p, storageId)));
}

function paintRow(p, storageId) {
  const here = () => (storageId
    ? ((p.locations.find(l => l.storage && l.storage.id === storageId) || {}).quantity || 0)
    : p.quantity);
  const value = h('span', { class: 'qty-value' });
  const minus = h('button', { type: 'button', 'aria-label': t('Меньше') }, '−');
  const plus = h('button', { type: 'button', 'aria-label': t('Больше') }, '+');
  const meta = h('div', { class: 'paint-meta' });
  const row = h('li', { class: 'paint' });

  function draw(np) {
    Object.assign(p, np);
    const n = here();
    value.textContent = n;
    minus.disabled = n === 0;
    row.classList.toggle('out', n === 0);
    const parts = [p.manufacturer, p.code].filter(Boolean);
    if (!storageId) parts.push(locationsText(p));
    else if (p.quantity > n) parts.push(t('всего {0}', p.quantity));
    meta.textContent = parts.join(' · ');
  }

  async function change(kind) {
    minus.disabled = plus.disabled = true;
    try {
      const body = storageId ? { storage_id: storageId } : {};
      const r = kind === 'add' ? await addPots(p, body) : await api('POST', `/paints/${p.id}/remove`, body);
      if (!r) { draw(p); return; } // cancelled
      showNotice(r.paint, r.notice);
      paintChanged(r.paint);
    } catch (e) {
      toast(explain(e), 'err');
      draw(p);
    } finally {
      plus.disabled = false;
    }
  }
  minus.addEventListener('click', () => change('remove'));
  plus.addEventListener('click', () => change('add'));
  watchPaint(p.id, draw);
  draw(p);

  row.append(
    h('a', { class: 'paint-link', href: `#/paint/${p.id}` },
      swatch(p.hex),
      h('div', { class: 'paint-info' },
        h('div', { class: 'paint-name' }, p.name),
        meta,
        p.tags.length ? h('div', { class: 'paint-tags' },
          p.tags.map(tg => h('span', { class: 'chip-mini', style: `--tc:${tg.color}` }, tg.name))) : null)),
    h('div', { class: 'qty', title: storageId ? t('Банок в этом хранилище') : t('Банок всего') }, minus, value, plus));
  return row;
}

/** Where the pots of one paint lie, with −/+ per storage, "move 1 to…" and "+1 pot". */
function locationsEditor(paint, storages) {
  const box = h('div', { class: 'loc-editor' });

  function draw(p) {
    paint = p;
    const rows = p.locations.map(loc => {
      const sid = loc.storage ? loc.storage.id : null;
      const moveSel = h('select', { class: 'input small-select', 'aria-label': t('Переложить одну банку') },
        h('option', { value: '' }, t('Переложить 1 в…')),
        storages.filter(s => s.id !== sid).map(s => h('option', { value: String(s.id) }, s.name)),
        sid !== null ? h('option', { value: 'none' }, t('без хранилища')) : null);
      moveSel.addEventListener('change', () => {
        if (moveSel.value) act('move', { from_storage_id: sid, to_storage_id: moveSel.value === 'none' ? null : Number(moveSel.value) });
      });
      return h('div', { class: 'loc-row' },
        h('div', { class: 'grow' },
          h('div', { class: 'loc-name' },
            loc.storage ? loc.storage.name : t('Без хранилища'),
            loc.storage && loc.storage.at_hand ? ' ✋' : '',
            loc.storage && loc.storage.is_default ? h('span', { class: 'badge' }, t('по умолчанию')) : null),
          moveSel),
        h('div', { class: 'qty' },
          h('button', { type: 'button', 'aria-label': t('Меньше'), onclick: () => act('remove', { storage_id: sid }) }, '−'),
          h('span', { class: 'qty-value' }, loc.quantity),
          h('button', { type: 'button', 'aria-label': t('Больше'), onclick: () => act('add', { storage_id: sid }) }, '+')));
    });
    const addSel = placementSelect(storages, null, { 'aria-label': t('Куда добавить банку') });
    box.replaceChildren(
      rows.length ? h('div', { class: 'loc-list' }, rows) : h('div', { class: 'muted small' }, t('Банок нет — краска закончилась, но осталась в каталоге')),
      h('div', { class: 'row loc-add' }, addSel,
        h('button', { type: 'button', class: 'btn', onclick: () => act('add', placementBody(addSel.value)) }, t('+1 банка'))));
  }

  async function act(kind, body) {
    try {
      const r = kind === 'add' ? await addPots(paint, body) : await api('POST', `/paints/${paint.id}/${kind}`, body);
      if (!r) return; // cancelled
      showNotice(r.paint, r.notice);
      paintChanged(r.paint);
    } catch (err) {
      toast(explain(err, t('Там столько банок нет')), 'err');
      draw(paint);
    }
  }

  watchPaint(paint.id, draw);
  draw(paint);
  return box;
}

// ======================= router =======================

const routes = [
  [/^#\/?$/, (m, mount) => viewPaints(mount)],
  [/^#\/paint\/new(?:\?storage=(\d+))?$/, (m, mount) => viewPaintForm(mount, null, m[1] ? Number(m[1]) : null)],
  [/^#\/paint\/(\d+)$/, (m, mount) => viewPaintForm(mount, Number(m[1]))],
  [/^#\/storages$/, (m, mount) => viewStorages(mount)],
  [/^#\/storage\/(\d+)$/, (m, mount) => viewStorage(mount, Number(m[1]))],
  [/^#\/catalogs$/, (m, mount) => viewCatalogs(mount)],
  [/^#\/labels(?:\?ids=([\d,]+))?$/, (m, mount) => viewLabels(mount, m[1] ? m[1].split(',').map(Number) : null)],
  [/^#\/tags$/, () => location.replace('#/catalogs')],
  [/^#\/settings$/, (m, mount) => viewSettings(mount)],
  [/^#\/login$/, (m, mount) => viewLogin(mount)],
];

let renderSeq = 0;
let prevHash = null;
let currentHash = location.hash || '#/';
let cleanups = [];

/** Run when the current screen is replaced (remove listeners etc.). */
function onLeave(fn) { cleanups.push(fn); }

function highlightTab(hash) {
  const tab = hash.startsWith('#/storage') || hash.startsWith('#/labels') ? 'storages'
    : hash.startsWith('#/catalogs') ? 'catalogs' : hash.startsWith('#/settings') ? 'settings' : 'paints';
  document.body.classList.toggle('signed-out', hash === '#/login');
  document.querySelectorAll('.tabs a').forEach(a => a.classList.toggle('active', a.dataset.tab === tab));
}

async function render() {
  cleanups.forEach(fn => fn());
  cleanups = [];
  paintWatchers.clear();
  const hash = location.hash || '#/';
  highlightTab(hash);
  const seq = ++renderSeq;
  // each screen gets its own mount(): if the user has already moved on,
  // a slow screen finishing late doesn't overwrite the new one
  const mount = node => { if (seq === renderSeq) viewEl.replaceChildren(node); };
  for (const [re, view] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    try {
      await view(m, mount);
    } catch (e) {
      mount(h('div', { class: 'empty' }, explain(e), h('br'), h('a', { class: 'btn', href: '#/' }, t('К списку красок'))));
    }
    return;
  }
  location.replace('#/');
}

window.addEventListener('hashchange', () => {
  prevHash = currentHash;
  currentHash = location.hash || '#/';
  render();
});

/** After saving a form go back where the user came from (list, storage page…). */
function goBack(fallback = '#/') {
  let target = prevHash;
  if (!target || target.startsWith('#/paint/') || target === currentHash) target = fallback;
  if (target === (location.hash || '#/')) render();
  else location.hash = target;
}

// ======================= paints list =======================

const newFilters = () => ({ q: '', storage: '', maker: '', tags: new Set(), atHand: false, stock: '' });
const filters = newFilters();          // the main paint list
const storageFilters = new Map();      // one set per storage page, kept while you move around

function resetFiltersOf(f) {
  Object.assign(f, newFilters(), { tags: f.tags });
  f.tags.clear();
}

function resetListFilters() { resetFiltersOf(filters); }

function anyFilterIn(f, storageId = null) {
  return Boolean(f.q.trim() || (!storageId && f.storage) || f.maker || f.tags.size || f.atHand || f.stock);
}

function anyFilter() { return anyFilterIn(filters); }

/**
 * Search, filters, summary line and the paint list. Used by the main list (all storages)
 * and by a storage page (storageId: only paints in it; +/- and counts work in that storage).
 */
function paintBrowser({ f, storageId = null, tags, storages, makers, emptyText, emptyBig = null, onFabHref = null }) {
  // forget filters that point to deleted tags/storages/manufacturers
  const tagIds = new Set(tags.map(tg => tg.id));
  for (const id of [...f.tags]) if (!tagIds.has(id)) f.tags.delete(id);
  if (f.storage && f.storage !== 'none' && !storages.some(s => String(s.id) === f.storage)) f.storage = '';
  if (f.maker && !makers.some(m => String(m.id) === f.maker)) f.maker = '';

  const listBox = h('div');
  const summary = h('div', { class: 'summary' });
  let current = [];

  const search = h('input', {
    class: 'input', type: 'search', value: f.q, enterkeyhint: 'search',
    placeholder: storageId ? t('Поиск в этом хранилище') : t('Поиск: название, производитель, код'),
    oninput: debounce(e => { f.q = e.target.value; refresh(); }, 200),
  });

  const storageSel = storageId ? null : h('select', { class: 'input', 'aria-label': t('Хранилище'), onchange: e => { f.storage = e.target.value; refresh(); } },
    h('option', { value: '' }, t('Все хранилища')),
    h('option', { value: 'none' }, t('Без хранилища')),
    storages.map(s => h('option', { value: String(s.id) }, s.name + (s.at_hand ? t(' · под рукой') : ''))));
  if (storageSel) storageSel.value = f.storage;

  const makerSel = h('select', { class: 'input', 'aria-label': t('Производитель'), onchange: e => { f.maker = e.target.value; refresh(); } },
    h('option', { value: '' }, t('Все производители')),
    makers.filter(m => m.paint_count > 0 || String(m.id) === f.maker)
      .map(m => h('option', { value: String(m.id) }, storageId ? m.name : `${m.name} (${m.paint_count})`)));
  makerSel.value = f.maker;

  const stockSel = h('select', { class: 'input', 'aria-label': t('Наличие'), onchange: e => { f.stock = e.target.value; refresh(); } },
    h('option', { value: '' }, t('Любое наличие')),
    h('option', { value: 'in' }, t('Есть в наличии')),
    h('option', { value: 'out' }, t('Закончились')));
  stockSel.value = f.stock;
  // in a storage every listed paint has pots there, so "used up" makes no sense
  if (storageId) stockSel.hidden = true;

  const chips = h('div', { class: 'chips-scroll' },
    storageId ? null : toggleChip(t('✋ Под рукой'), { plain: true, pressed: f.atHand, onToggle: v => { f.atHand = v; refresh(); } }),
    tags.map(tg => toggleChip(tg.name, {
      color: tg.color,
      pressed: f.tags.has(tg.id),
      onToggle: v => { v ? f.tags.add(tg.id) : f.tags.delete(tg.id); refresh(); },
    })));

  const here = p => (storageId
    ? ((p.locations.find(l => l.storage && l.storage.id === storageId) || {}).quantity || 0)
    : p.quantity);

  function updateSummary() {
    const pots = current.reduce((n, p) => n + here(p), 0);
    summary.replaceChildren(h('span', null, `${countPaints(current.length)} · ${countPots(pots)}`));
    if (anyFilterIn(f, storageId)) {
      summary.append(h('button', { type: 'button', onclick: () => { resetFiltersOf(f); render(); } }, t('Сбросить фильтры')));
    }
  }

  let seq = 0;
  async function refresh() {
    const mySeq = ++seq;
    const params = new URLSearchParams();
    if (f.q.trim()) params.set('q', f.q.trim());
    if (storageId) params.set('storage_id', storageId);
    else if (f.storage === 'none') params.set('no_storage', 'true');
    else if (f.storage) params.set('storage_id', f.storage);
    if (f.maker) params.set('manufacturer_id', f.maker);
    f.tags.forEach(id => params.append('tag_id', id));
    if (f.atHand && !storageId) params.set('at_hand', 'true');
    if (f.stock && !storageId) params.set('in_stock', f.stock === 'in' ? 'true' : 'false');
    if (onFabHref) onFabHref(f);

    let paints;
    try {
      paints = await api('GET', '/paints?' + params);
    } catch (e) {
      if (mySeq === seq) listBox.replaceChildren(h('div', { class: 'empty' }, explain(e)));
      return;
    }
    if (mySeq !== seq) return;
    paintWatchers.clear();
    current = paints;
    updateSummary();
    if (paints.length) listBox.replaceChildren(paintList(paints, { storageId }));
    else if (anyFilterIn(f, storageId)) listBox.replaceChildren(h('div', { class: 'empty' }, t('Ничего не нашлось')));
    else listBox.replaceChildren(emptyBig ? emptyBig() : h('div', { class: 'empty' }, emptyText));
  }

  // quick +/- changes totals in the summary line
  document.addEventListener('paints-changed', updateSummary);
  onLeave(() => document.removeEventListener('paints-changed', updateSummary));

  const selects = [storageSel, stockSel, makerSel].filter(Boolean);
  const el = h('div', null,
    h('div', { class: 'filters' }, search, h('div', { class: 'filter-row' }, selects), chips),
    summary,
    listBox);
  return { el, refresh };
}

async function viewPaints(mount) {
  const [tags, storages, makers] = await Promise.all([api('GET', '/tags'), api('GET', '/storages'), api('GET', '/manufacturers')]);
  const fab = h('a', { class: 'fab', href: '#/paint/new', 'aria-label': t('Добавить краску'), title: t('Добавить краску') }, '+');
  const browser = paintBrowser({
    f: filters, tags, storages, makers,
    onFabHref: f => { fab.href = f.storage && f.storage !== 'none' ? `#/paint/new?storage=${f.storage}` : '#/paint/new'; },
    emptyBig: () => h('div', { class: 'empty-big' },
      h('p', null, h('b', null, t('Каталог пока пуст'))),
      h('p', { class: 'muted' }, t('Добавьте первую краску. Хранилища — коробки, ящики, полки — можно завести сразу или потом.')),
      h('div', { class: 'row', style: 'justify-content:center;margin-top:14px' },
        h('a', { class: 'btn primary', href: '#/paint/new' }, t('Добавить краску')),
        h('a', { class: 'btn', href: '#/storages' }, t('Хранилища')))),
  });
  await browser.refresh();
  mount(h('div', null, browser.el, fab));
}

// ======================= paint form =======================

async function viewPaintForm(mount, id, prefillStorage = null) {
  const [tags, storages, makers, paint, photo] = await Promise.all([
    api('GET', '/tags'),
    api('GET', '/storages'),
    api('GET', '/manufacturers'),
    id ? api('GET', `/paints/${id}`) : Promise.resolve(null),
    id ? Promise.resolve(null) : api('GET', '/recognize').catch(() => null),
  ]);
  const isNew = !paint;
  const p = paint || {
    name: '', manufacturer_id: lastManufacturer(makers), code: '', hex: null, tags: [],
  };
  const selectedTags = new Set(p.tags.map(tg => tg.id));

  const name = h('input', { class: 'input', id: 'f-name', required: true, maxlength: 200, value: p.name, autocomplete: 'off' });
  // manufacturer: pick from the list, or add a new one right here
  const NEW = '__new';
  const maker = h('select', { class: 'input', id: 'f-maker' },
    h('option', { value: '' }, t('— выберите —')),
    makers.map(m => h('option', { value: String(m.id), selected: m.id === p.manufacturer_id }, m.name)),
    h('option', { value: NEW }, t('＋ Новый производитель…')));
  let prevMaker = maker.value;
  const newMakerInput = h('input', { class: 'input grow', id: 'f-new-maker', maxlength: 100, placeholder: t('Название производителя'), autocomplete: 'off' });
  const newMakerBox = h('div', { class: 'row', hidden: true, style: 'margin-top:8px' },
    newMakerInput,
    h('button', { type: 'button', class: 'btn', onclick: () => addMaker() }, t('Добавить')),
    h('button', { type: 'button', class: 'btn ghost', onclick: () => { maker.value = prevMaker; newMakerBox.hidden = true; } }, t('Отмена')));
  maker.addEventListener('change', () => {
    if (maker.value === NEW) {
      newMakerBox.hidden = false;
      newMakerInput.value = '';
      newMakerInput.focus();
    } else {
      prevMaker = maker.value;
      newMakerBox.hidden = true;
    }
  });
  newMakerInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addMaker(); } });

  function selectMaker(id) {
    maker.value = String(id);
    prevMaker = maker.value;
    newMakerBox.hidden = true;
  }

  async function addMaker() {
    const value = newMakerInput.value.trim();
    if (!value) { newMakerInput.focus(); return; }
    try {
      const created = await api('POST', '/manufacturers', { name: value });
      // keep the list alphabetical; the "new" option stays last
      const options = [...maker.options].filter(o => o.value && o.value !== NEW);
      const before = options.find(o => o.textContent.localeCompare(created.name, 'ru', { sensitivity: 'base' }) > 0);
      maker.insertBefore(h('option', { value: String(created.id) }, created.name), before || maker.querySelector(`option[value="${NEW}"]`));
      selectMaker(created.id);
      toast(t('Производитель «{0}» добавлен', created.name), 'ok');
    } catch (err) {
      const existing = [...maker.options].find(o => o.value !== NEW && o.textContent.toLowerCase() === value.toLowerCase());
      if (err.status === 409 && existing) {
        selectMaker(existing.value);
        toast(t('Такой производитель уже есть — выбрали его'), '');
      } else {
        toast(explain(err), 'err');
      }
    }
  }
  const code = h('input', { class: 'input', id: 'f-code', maxlength: 50, value: p.code || '', autocomplete: 'off' });
  const barcode = h('input', { class: 'input', id: 'f-barcode', maxlength: 100, value: p.barcode || '', inputmode: 'numeric', autocomplete: 'off' });

  // color: text field + native picker + preview, kept in sync
  const preview = swatch(p.hex);
  const hexText = h('input', {
    class: 'input', id: 'f-hex', value: p.hex || '', maxlength: 7, placeholder: '#rrggbb',
    autocomplete: 'off', autocapitalize: 'off', spellcheck: false,
  });
  const picker = h('input', { type: 'color', class: 'color-picker', value: p.hex || '#808080', 'aria-label': t('Выбрать цвет') });
  hexText.addEventListener('input', () => {
    const n = normHex(hexText.value);
    hexText.classList.toggle('invalid', n === null);
    if (n) { picker.value = n; setSwatch(preview, n); } else if (n === '') setSwatch(preview, null);
  });
  picker.addEventListener('input', () => {
    hexText.value = picker.value;
    hexText.classList.remove('invalid');
    setSwatch(preview, picker.value);
  });
  const clearColor = h('button', { type: 'button', class: 'icon-btn', title: t('Без цвета'), 'aria-label': t('Без цвета'), onclick: () => { hexText.value = ''; hexText.classList.remove('invalid'); setSwatch(preview, null); } }, '✕');

  const qty = h('input', { class: 'input', id: 'f-qty', type: 'number', min: 0, step: 1, inputmode: 'numeric', value: '1' });
  const step = d => { qty.value = String(Math.max(0, (parseInt(qty.value, 10) || 0) + d)); };
  const placement = placementSelect(storages, prefillStorage, { id: 'f-storage' });

  const tagChips = new Map();
  const tagPicker = h('div', { class: 'tag-picker' }, tags.map(tg => {
    const chip = toggleChip(tg.name, {
      color: tg.color,
      pressed: selectedTags.has(tg.id),
      onToggle: v => { v ? selectedTags.add(tg.id) : selectedTags.delete(tg.id); },
    });
    tagChips.set(tg.id, chip);
    return chip;
  }));
  function setTag(tagId, on) {
    const chip = tagChips.get(tagId);
    if (!chip) return;
    on ? selectedTags.add(tagId) : selectedTags.delete(tagId);
    chip.setAttribute('aria-pressed', String(on));
  }

  // ---- add by photo (new paints only; optional part of the app)
  const photoBlock = isNew ? photoRecognizer({
    status: photo,
    fill: {
      get name() { return name.value; }, set name(v) { name.value = v; },
      get code() { return code.value; }, set code(v) { code.value = v; },
      get barcode() { return barcode.value; }, set barcode(v) { barcode.value = v; },
      makerOptions: () => [...maker.options].filter(o => o.value && o.value !== NEW),
      selectMaker(id, label) {
        if (!maker.querySelector(`option[value="${id}"]`)) {
          maker.insertBefore(h('option', { value: String(id) }, label), maker.querySelector(`option[value="${NEW}"]`));
        }
        selectMaker(id);
      },
      proposeMaker(text) {
        maker.value = NEW;
        newMakerBox.hidden = false;
        newMakerInput.value = text;
        newMakerInput.focus();
      },
      setTag,
      placement: () => placementBody(placement.value),
      clearForNext() {
        name.value = ''; code.value = ''; barcode.value = ''; hexText.value = ''; setSwatch(preview, null); qty.value = '1';
      },
    },
  }) : null;

  function collect() {
    const hex = normHex(hexText.value);
    if (hex === null) { hexText.focus(); throw new Error(t('Цвет должен быть в виде #rrggbb')); }
    if (!maker.value || maker.value === NEW) { maker.focus(); throw new Error(t('Выберите производителя')); }
    const body = {
      name: name.value.trim(),
      manufacturer_id: Number(maker.value),
      code: code.value.trim() || null,
      barcode: barcode.value.trim() || null,
      hex: hex || null,
      tag_ids: [...selectedTags],
    };
    if (isNew) {
      const quantity = parseInt(qty.value, 10);
      if (!(quantity >= 0)) { qty.focus(); throw new Error(t('Количество — целое число от 0')); }
      Object.assign(body, { quantity }, placementBody(placement.value));
    }
    return body;
  }

  const conflict = t('Такая краска уже есть в каталоге — найдите её и поменяйте количество');
  let andAnother = false;

  const form = h('form', { class: 'form', novalidate: false });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    let body;
    try { body = collect(); } catch (err) { toast(err.message, 'err'); return; }
    const buttons = form.querySelectorAll('button[type=submit]');
    buttons.forEach(b => { b.disabled = true; });
    try {
      if (isNew) {
        let created;
        try {
          created = await api('POST', '/paints', body);
        } catch (err) {
          if (!isDuplicate(err)) throw err;
          const extra = await askAboutDuplicate(body.name, err.detail, body.quantity);
          if (!extra) return; // cancelled — stay on the form
          created = await api('POST', '/paints', { ...body, ...extra });
        }
        rememberManufacturer(created.manufacturer_id);
        if (created.notice) showNotice(created, created.notice);
        else toast(t('«{0}» добавлена (0 банок)', created.name), 'ok');
        if (andAnother) {
          // keep manufacturer, storage and tags — usually the next pot is from the same batch
          name.value = ''; code.value = ''; barcode.value = ''; hexText.value = ''; setSwatch(preview, null); qty.value = '1';
          if (photoBlock) photoBlock.reset();
          name.focus();
          return;
        }
      } else {
        await api('PATCH', `/paints/${id}`, body);
        rememberManufacturer(body.manufacturer_id);
        toast(t('Сохранено'), 'ok');
      }
      goBack();
    } catch (err) {
      const text = err.status === 409 && /barcode/.test(err.message)
        ? t('Этот штрихкод уже привязан к другой краске') : explain(err, conflict);
      toast(text, 'err');
    } finally {
      buttons.forEach(b => { b.disabled = false; });
      andAnother = false;
    }
  });

  form.append(
    photoBlock ? photoBlock.el : '', // append() would print null as text
    h('div', { class: 'field' }, h('label', { for: 'f-name' }, t('Название')), name),
    h('div', { class: 'field' }, h('label', { for: 'f-maker' }, t('Производитель')), maker, newMakerBox),
    h('div', { class: 'two' },
      h('div', { class: 'field' }, h('label', { for: 'f-code' }, t('Код / артикул')), code),
      h('div', { class: 'field' }, h('label', { for: 'f-barcode' }, t('Штрихкод')), barcode)),
    h('div', { class: 'field' }, h('label', { for: 'f-hex' }, t('Цвет')),
      h('div', { class: 'color-row' }, preview, hexText, picker, clearColor)),
    isNew
      ? h('div', { class: 'qty-row' },
        h('div', { class: 'field' }, h('label', { for: 'f-qty', title: t('0 — закончилась, но остаётся в каталоге') }, t('Банок')),
          h('div', { class: 'stepper' },
            h('button', { type: 'button', 'aria-label': t('Меньше'), onclick: () => step(-1) }, '−'),
            qty,
            h('button', { type: 'button', 'aria-label': t('Больше'), onclick: () => step(+1) }, '+'))),
        h('div', { class: 'field' }, h('label', { for: 'f-storage' }, t('Куда положить')), placement))
      : h('div', { class: 'field' }, h('span', { class: 'label' }, t('Где лежит')), locationsEditor(paint, storages)),
    h('div', { class: 'field' }, h('span', { class: 'label' }, t('Теги')),
      tags.length ? tagPicker : h('div', { class: 'muted small' }, t('Тегов нет — их можно завести в «Справочниках»'))),
    // stays at the bottom of the screen, also above the on-screen keyboard
    h('div', { class: 'action-bar' },
      photoBlock ? photoBlock.buttons : '',
      h('span', { class: 'spacer' }),
      isNew ? h('button', { type: 'submit', class: 'btn', title: t('Добавить и сразу заполнить следующую'), onclick: () => { andAnother = true; } }, t('+ ещё')) : null,
      h('button', { type: 'submit', class: 'btn primary' }, isNew ? t('Добавить') : t('Сохранить')),
      isNew ? null : deleteButton(t('Удалить'), () => ({ title: t('Удалить «{0}»?', name.value.trim() || p.name), text: t('Краска исчезнет из каталога.') }), async () => {
        try {
          await api('DELETE', `/paints/${id}`);
          toast(t('Краска удалена'), 'ok');
          goBack();
        } catch (err) { toast(explain(err), 'err'); }
      })));

  mount(h('div', null, pageHead(isNew ? t('Новая краска') : p.name, '#/'), form));
  window.scrollTo(0, 0);
  if (isNew && !(photo && photo.available)) name.focus({ preventScroll: true });
}

// ======================= add by photo =======================

/** Phones take 12+ MP photos; send ~1600 px instead. Also applies EXIF rotation. */
async function shrinkImage(file, maxSide = 1600) {
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: 'from-image' });
    const k = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bmp.width * k);
    canvas.height = Math.round(bmp.height * k);
    canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
    if (bmp.close) bmp.close();
    return await new Promise((resolve, reject) =>
      canvas.toBlob(b => (b ? resolve(b) : reject(new Error('encode'))), 'image/jpeg', 0.88));
  } catch {
    return file; // a format the browser can't decode — let the server try
  }
}

function photoRecognizer({ status, fill }) {
  const el = h('div', { class: 'photo-block' });
  if (!status || !status.available) {
    el.append(h('div', { class: 'panel muted small' },
      t('Распознавание по фото не установлено. На компьютере с приложением выполните '),
      h('code', null, (status && status.install) || 'pip install -r requirements-photo.txt'),
      t(' и перезапустите его.')));
    return { el, buttons: '', reset() {} };
  }

  // Phones: "photo" mode — capture="environment" opens the rear camera app straight away (works over
  // plain http); "stream" mode — live video in the page that catches barcodes, plus a shutter for the label
  // (browsers allow live video only on HTTPS, or with a Chrome flag). Computers: just picking a file.
  const touch = window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0;
  let mode = touch ? cameraMode() : 'photo';
  const cameraInput = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, id: 'f-camera' });
  const input = h('input', { type: 'file', accept: 'image/*', hidden: true, id: 'f-photo' });
  const button = h('button', { type: 'button', class: 'btn photo-btn' });
  const galleryButton = h('button', { type: 'button', class: 'btn photo-btn secondary', title: t('Фото из галереи'), 'aria-label': t('Фото из галереи') }, touch ? '🖼' : t('🖼 Фото этикетки'));
  const panel = h('div', { class: 'panel photo-panel', hidden: true });
  let autoTags = [];
  let thumbUrl = null;

  const modeSeg = touch ? h('div', { class: 'seg', role: 'group', 'aria-label': t('Режим камеры') },
    [['photo', t('Фото')], ['stream', t('Поток + фото')]].map(([value, label]) => {
      const b = h('button', { type: 'button', 'data-mode': value, 'aria-pressed': String(value === mode) }, label);
      b.addEventListener('click', () => {
        mode = value;
        cameraMode(value);
        modeSeg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
        stopStream();
        labelButton();
      });
      return b;
    })) : null;

  function labelButton() {
    button.textContent = mode === 'stream' ? t('🎥 Камера') : t('📷 Снять');
    button.hidden = !touch;
  }
  labelButton();

  button.addEventListener('click', () => (mode === 'stream' ? startStream() : cameraInput.click()));
  galleryButton.addEventListener('click', () => input.click());
  for (const inp of [cameraInput, input]) {
    inp.addEventListener('change', () => {
      const file = inp.files && inp.files[0];
      inp.value = '';
      if (file) recognize(file);
    });
  }

  function reset() {
    stopStream();
    autoTags.forEach(tg => fill.setTag(tg, false));
    autoTags = [];
    panel.hidden = true;
    panel.replaceChildren();
    labelButton();
  }

  // ---------- live stream ----------
  const video = h('video', { class: 'stream-video', playsinline: true, muted: true, autoplay: true });
  video.muted = true;
  const streamStatus = h('div', { class: 'stream-status' }, t('Наведите камеру на штрихкод банки'));
  const shutter = h('button', { type: 'button', class: 'btn primary' }, t('📸 Снимок этикетки'));
  const closeStream = h('button', { type: 'button', class: 'btn' }, t('Закрыть'));
  const streamBox = h('div', { class: 'panel stream-box', hidden: true },
    h('div', { class: 'stream-frame' }, video, h('div', { class: 'stream-aim' })),
    streamStatus,
    h('div', { class: 'row' }, shutter, closeStream));
  let stream = null;
  let scanning = false;
  let scanTimer = null;
  let lastCode = null;
  let detector = null;

  closeStream.addEventListener('click', () => stopStream());
  shutter.addEventListener('click', async () => {
    const blob = await frameBlob(1600, 0.9);
    stopStream();
    if (blob) recognize(blob);
  });
  onLeave(() => stopStream());

  async function startStream() {
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showFlagHelp();
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false,
      });
    } catch (err) {
      panel.hidden = false;
      panel.replaceChildren(h('div', null, h('b', null, t('Нет доступа к камере')),
        h('div', { class: 'muted small' }, t('Разрешите камеру для этого сайта в настройках браузера. '), String(err.message || err))));
      return;
    }
    panel.hidden = true;
    video.srcObject = stream;
    streamBox.hidden = false;
    lastCode = null;
    streamStatus.replaceChildren(t('Наведите камеру на штрихкод банки'));
    try { await video.play(); } catch { /* autoplay is allowed for muted video */ }
    detector = await barcodeDetector();
    scanning = true;
    tick();
  }

  function stopStream() {
    scanning = false;
    clearTimeout(scanTimer);
    if (stream) stream.getTracks().forEach(tg => tg.stop());
    stream = null;
    video.srcObject = null;
    streamBox.hidden = true;
  }

  async function barcodeDetector() {
    // Chrome on Android has a built-in fast detector; elsewhere frames go to the server
    if (!('BarcodeDetector' in window)) return null;
    try {
      const wanted = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128'];
      const formats = (await window.BarcodeDetector.getSupportedFormats()).filter(f => wanted.includes(f));
      return formats.length ? new window.BarcodeDetector({ formats }) : null;
    } catch {
      return null;
    }
  }

  async function frameBlob(maxSide, quality) {
    if (!video.videoWidth) return null;
    const k = Math.min(1, maxSide / Math.max(video.videoWidth, video.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(video.videoWidth * k);
    canvas.height = Math.round(video.videoHeight * k);
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
  }

  async function tick() {
    if (!scanning) return;
    let code = null;
    try {
      if (detector) {
        const found = await detector.detect(video);
        code = found.length ? found[0].rawValue : null;
      } else {
        const blob = await frameBlob(1000, 0.8);
        if (blob) {
          const res = await fetch('/api/recognize?barcode_only=true', { method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: blob });
          if (res.ok) {
            const data = await res.json();
            code = data.barcodes.length ? data.barcodes[0].text : null;
          }
        }
      }
    } catch { /* a bad frame — try the next one */ }
    if (code && scanning && code !== lastCode) await onBarcode(code);
    if (scanning) scanTimer = setTimeout(tick, detector ? 250 : 700);
  }

  async function onBarcode(code) {
    lastCode = code;
    if (navigator.vibrate) navigator.vibrate(60);
    let found = [];
    try { found = await api('GET', `/paints?barcode=${encodeURIComponent(code)}`); } catch { /* offline */ }
    if (found.length) {
      // known paint: +1 right here, and keep scanning for the next pot
      const afterAdd = updated => {
        streamStatus.replaceChildren(
          h('div', { class: 'muted small' }, t('Добавлено. Ещё одна такая же — жмите «+1» снова, другая банка — наведите на её штрихкод.')),
          matchRow({ paint: updated, score: 100, by: 'barcode' }, { barcode: null }, afterAdd));
      };
      streamStatus.replaceChildren(
        h('div', { class: 'muted small' }, t('Штрихкод {0} — это:', code)),
        matchRow({ paint: found[0], score: 100, by: 'barcode' }, { barcode: null }, afterAdd));
    } else {
      if (!fill.barcode.trim()) fill.barcode = code;
      streamStatus.replaceChildren(
        h('b', null, t('Новый штрихкод {0}', code)),
        h('div', { class: 'muted small' }, t('Его ещё нет в каталоге. Наведите камеру на этикетку и нажмите «Снимок этикетки» — заполню остальное.')));
      shutter.classList.add('pulse');
    }
  }

  function showFlagHelp() {
    const origin = location.origin;
    const flag = 'chrome://flags/#unsafely-treat-insecure-origin-as-secure';
    const copyBtn = text => h('button', { type: 'button', class: 'btn small-btn', onclick: async () => toast(await copyText(text) ? t('Скопировано') : t('Выделите и скопируйте вручную')) }, t('Копировать'));
    panel.hidden = false;
    panel.replaceChildren(
      h('b', null, t('Поток с камеры здесь недоступен')),
      h('div', { class: 'muted small' }, t('Живое видео браузер разрешает только по HTTPS. На этом телефоне его можно включить флагом Chrome:')),
      h('ol', { class: 'flag-steps' },
        h('li', null, t('Откройте в Chrome адрес'), h('div', { class: 'link-box' }, h('code', null, flag), copyBtn(flag))),
        h('li', null, t('В поле впишите адрес приложения'), h('div', { class: 'link-box' }, h('code', null, origin), copyBtn(origin))),
        h('li', null, t('Переключите на «Enabled» и нажмите «Relaunch».'))),
      h('div', { class: 'muted small' }, t('Пока можно пользоваться режимом «Фото».')));
  }

  async function recognize(file) {
    if (thumbUrl) URL.revokeObjectURL(thumbUrl);
    thumbUrl = URL.createObjectURL(file);
    panel.hidden = false;
    panel.replaceChildren(head(t('Распознаю…'), t('Обычно это пара секунд')));
    button.disabled = galleryButton.disabled = true;
    try {
      const blob = await shrinkImage(file);
      const res = await fetch('/api/recognize', { method: 'POST', headers: { 'Content-Type': blob.type || 'image/jpeg' }, body: blob });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new ApiError(res.status, (data && data.detail) || t('Ошибка {0}', res.status));
      show(data);
    } catch (err) {
      if (!(err instanceof ApiError)) console.error(err);
      panel.replaceChildren(head(t('Не получилось распознать'), err.status === 422 ? t('Файл не похож на фото') : explain(err)));
    } finally {
      button.disabled = galleryButton.disabled = false;
    }
  }

  function head(title, subtitle) {
    return h('div', { class: 'photo-head' },
      h('img', { class: 'photo-thumb', src: thumbUrl, alt: '' }),
      h('div', null, h('b', null, title), subtitle ? h('div', { class: 'muted small' }, subtitle) : null));
  }

  function show(data) {
    const s = data.suggestion;
    const filled = [];

    // fill empty fields; never overwrite what the user typed
    autoTags.forEach(tg => fill.setTag(tg, false));
    autoTags = [];
    if (s.name && !fill.name.trim()) { fill.name = s.name; filled.push(t('название')); }
    if (s.manufacturer) { fill.selectMaker(s.manufacturer.id, s.manufacturer.name); filled.push(t('производитель')); }
    if (s.code && !fill.code.trim()) { fill.code = s.code; filled.push(t('код')); }
    if (s.barcode && !fill.barcode.trim()) { fill.barcode = s.barcode; filled.push(t('штрихкод')); }
    s.tag_ids.forEach(tg => fill.setTag(tg, true));
    autoTags = [...s.tag_ids];
    if (s.tag_ids.length) filled.push(s.tag_ids.length === 1 ? t('тег') : t('теги'));

    const lines = data.lines.filter(l => l.kind !== 'noise');
    const parts = [
      head(filled.length ? t('Заполнено: ') + filled.join(', ') : t('Ничего уверенно не распознано'),
        t('{0} с · проверьте поля ниже', data.seconds)),
    ];

    if (data.matches.length) {
      parts.push(h('div', { class: 'photo-section' }, h('b', null, t('Похоже, это уже есть в каталоге')),
        h('div', { class: 'match-list' }, data.matches.map(m => matchRow(m, s)))));
    }

    if (lines.length) {
      let target = 'name';
      const seg = h('div', { class: 'seg', role: 'group', 'aria-label': t('Куда вставлять') },
        [['name', t('Название')], ['maker', t('Производитель')], ['code', t('Код')]].map(([value, label]) => {
          const b = h('button', { type: 'button', 'aria-pressed': String(value === target) }, label);
          b.addEventListener('click', () => {
            target = value;
            seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
          });
          return b;
        }));
      // Name: chips work as toggles — tapped pieces are joined in tap order ("FLAT" + "BLACK"),
      // tapping a piece again takes it out. If the name was typed by hand, the next tap starts over.
      // Code and manufacturer: a tap replaces the field.
      let picked = [];
      let builtName = null;
      if (s.name && fill.name === s.name) { // the name came from the photo: show its pieces as picked
        picked = lines.map((l, i) => (l.kind === 'name' ? i : -1)).filter(i => i >= 0);
        builtName = s.name;
      }
      const markPicked = () => chips.forEach((c, i) => c.setAttribute('aria-pressed', String(picked.includes(i))));
      const chips = lines.map((l, i) => h('button', {
        type: 'button', class: 'chip plain line-chip',
        onclick: () => {
          const text = l.text;
          if (target === 'name') {
            if (fill.name !== builtName) picked = [];
            picked = picked.includes(i) ? picked.filter(x => x !== i) : [...picked, i];
            builtName = picked.map(x => niceCase(lines[x].text)).join(' ');
            fill.name = builtName;
            markPicked();
          } else if (target === 'code') {
            fill.code = text;
          } else {
            const opt = fill.makerOptions().find(o => o.textContent.toLowerCase() === text.toLowerCase())
              || fill.makerOptions().find(o => text.toLowerCase().includes(o.textContent.toLowerCase()));
            if (opt) fill.selectMaker(Number(opt.value), opt.textContent);
            else fill.proposeMaker(niceCase(text));
          }
        },
      }, l.text));
      markPicked();
      parts.push(h('div', { class: 'photo-section' },
        h('b', null, t('Текст с этикетки')),
        h('div', { class: 'muted small' }, t('В название куски добавляются по порядку, повторный тап убирает. Код и производитель — замена.')),
        seg,
        h('div', { class: 'tag-picker' }, chips)));
    } else {
      parts.push(h('p', { class: 'muted small', style: 'margin:8px 0 0' },
        t('Текст не найден. Снимите этикетку ближе, ровно и при хорошем свете, без бликов.')));
    }

    if (data.barcodes.length) {
      const b = data.barcodes[0];
      parts.push(h('div', { class: 'muted small photo-section' }, t('Штрихкод {0}: ', b.text),
        b.paint_id ? t('уже привязан к краске из каталога') : t('новый — сохранится вместе с краской')));
    }
    panel.replaceChildren(...parts);
  }

  function matchRow(m, s, onDone = null) {
    const p = m.paint;
    const meta = [p.manufacturer, p.code, locationsText(p)].filter(Boolean).join(' · ');
    const plus = h('button', { type: 'button', class: 'btn primary small-btn' }, t('+1 банка'));
    plus.addEventListener('click', async () => {
      plus.disabled = true;
      try {
        if (s.barcode && !p.barcode) {
          await api('PATCH', `/paints/${p.id}`, { barcode: s.barcode }); // remember it for next time
        }
        const r = await addPots(p, fill.placement());
        if (!r) { plus.disabled = false; return; } // cancelled
        showNotice(r.paint, r.notice);
        if (onDone) { onDone(r.paint); return; } // live scanning goes on
        fill.clearForNext();
        reset();
      } catch (err) {
        toast(explain(err), 'err');
        plus.disabled = false;
      }
    });
    return h('div', { class: 'match' },
      swatch(p.hex),
      h('div', { class: 'grow' },
        h('div', { class: 'paint-name' }, p.name),
        h('div', { class: 'paint-meta' }, meta, ' · ', countPots(p.quantity)),
        h('div', { class: 'muted small' }, m.by === 'barcode' ? t('совпал штрихкод') : t('похоже на {0}%', m.score))),
      h('div', { class: 'match-actions' },
        plus,
        h('a', { class: 'btn small-btn', href: `#/paint/${p.id}` }, t('Открыть'))));
  }

  el.append(modeSeg || '', cameraInput, input, streamBox, panel);
  const buttons = h('div', { class: 'photo-buttons' }, button, galleryButton);
  return { el, buttons, reset };
}

/** Camera mode on this device: 'photo' or 'stream' (remembered per browser). */
function cameraMode(set) {
  try {
    if (set) localStorage.setItem('pa.cameraMode', set);
    return localStorage.getItem('pa.cameraMode') === 'stream' ? 'stream' : 'photo';
  } catch {
    return set || 'photo';
  }
}

function niceCase(text) {
  return text === text.toUpperCase() && /[A-Z]/.test(text)
    ? text.split(' ').map(w => (/^[A-Z]+$/.test(w) ? w[0] + w.slice(1).toLowerCase() : w)).join(' ')
    : text;
}

/** The manufacturer used last time, if it still exists — new paints usually come in batches. */
function lastManufacturer(makers) {
  let id = null;
  try { id = Number(localStorage.getItem('pa.lastManufacturerId')); } catch { /* storage blocked */ }
  return makers.some(m => m.id === id) ? id : null;
}

function rememberManufacturer(id) {
  try { localStorage.setItem('pa.lastManufacturerId', String(id)); } catch { /* private mode */ }
}

// ======================= storages =======================

async function viewStorages(mount) {
  const storages = await api('GET', '/storages');

  const nameInput = h('input', { class: 'input grow', placeholder: t('Новое хранилище: коробка, полка…'), maxlength: 100, required: true });
  const form = h('form', { class: 'panel form' },
    h('div', { class: 'row' }, nameInput, h('button', { type: 'submit', class: 'btn primary' }, t('Добавить'))));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('POST', '/storages', { name: nameInput.value.trim() });
      toast(t('Хранилище добавлено'), 'ok');
      render();
    } catch (err) {
      toast(explain(err, t('Хранилище с таким названием уже есть')), 'err');
    }
  });

  const find = h('input', {
    class: 'input', type: 'search', placeholder: t('Поиск хранилища'), enterkeyhint: 'search',
    oninput: () => {
      const q = find.value.trim().toLowerCase();
      list.querySelectorAll('.storage-card').forEach(c => { c.hidden = q && !c.dataset.search.includes(q); });
    },
  });
  const list = storages.length
    ? h('div', { class: 'card-list' }, storages.map(s =>
      h('a', { class: 'card storage-card', href: `#/storage/${s.id}`, 'data-search': `${s.name} ${s.nfc_tag || ''}`.toLowerCase() },
        h('span', { class: 'storage-icon' + (s.at_hand ? ' hand' : '') }, s.at_hand ? '✋' : '📦'),
        h('div', { class: 'grow' },
          h('div', { class: 'name' }, s.name, ' ',
            s.is_default ? h('span', { class: 'badge' }, t('по умолчанию')) : s.at_hand ? h('span', { class: 'badge' }, t('под рукой')) : null),
          h('div', { class: 'muted small' }, `${countPaints(s.paint_count)} · ${countPots(s.pot_count)}`,
            s.copies_storage ? t(' · дубликаты → {0}', s.copies_storage.name) : '', s.nfc_tag ? t(' · метка') : '')),
        h('span', { class: 'chev' }, '›'))))
    : h('div', { class: 'empty' }, t('Хранилищ пока нет. Коробки, ящики, полки — всё, где лежат краски.'));

  mount(h('div', null, pageHead(t('Хранилища'), null,
    storages.length ? h('a', { class: 'btn', href: '#/labels', style: 'margin-left:auto' }, t('🖨 QR-метки')) : null), form, h('h2', null, t('Все хранилища')), storages.length > 3 ? h('div', { style: 'margin-bottom:8px' }, find) : null, list));
}

async function viewStorage(mount, id) {
  const [s, info, storages, tags, makers] = await Promise.all([
    api('GET', `/storages/${id}`),
    api('GET', '/info').catch(() => ({})),
    api('GET', '/storages'),
    api('GET', '/tags'),
    api('GET', '/manufacturers'),
  ]);
  if (!storageFilters.has(id)) storageFilters.set(id, newFilters());
  const browser = paintBrowser({
    f: storageFilters.get(id), storageId: s.id, tags, storages, makers, emptyText: t('Здесь пока пусто'),
  });
  await browser.refresh();

  const link = storageLink(info, s.id);

  const nameInput = h('input', { class: 'input', id: 's-name', value: s.name, maxlength: 100, required: true });
  const tagInput = h('input', { class: 'input', id: 's-tag', value: s.nfc_tag || '', maxlength: 100, autocomplete: 'off' });
  const atHand = h('input', { type: 'checkbox', checked: s.at_hand });
  const isDefault = h('input', { type: 'checkbox', checked: s.is_default, disabled: s.is_default });
  const copies = h('select', { class: 'input', id: 's-copies' },
    h('option', { value: '' }, t('— не выбрано —')),
    storages.filter(x => x.id !== s.id).map(x => h('option', { value: String(x.id) }, x.name)));
  copies.value = s.copies_storage ? String(s.copies_storage.id) : '';
  const settings = h('form', { class: 'form' },
    h('div', { class: 'field' }, h('label', { for: 's-name' }, t('Название')), nameInput),
    h('div', { class: 'field' }, h('label', { for: 's-tag' }, t('Метка (необязательно)')), tagInput,
      h('div', { class: 'hint' }, t('Любая пометка для себя: номер или ID NFC-метки, цвет наклейки…'))),
    h('label', { class: 'check' }, atHand, t('Под рукой')),
    h('label', { class: 'check' }, isDefault,
      h('span', null, t('По умолчанию '), h('span', { class: 'muted small' }, s.is_default
        ? t('— новые банки идут сюда. Чтобы снять, отметьте другое хранилище')
        : t('— новые банки будут идти сюда')))),
    h('div', { class: 'field' }, h('label', { for: 's-copies' }, t('Куда предлагать дубликаты')), copies,
      h('div', { class: 'hint' }, t('Если хранилище «под рукой» и сюда кладут вторую банку той же краски, приложение спросит, куда её деть, — это хранилище будет выбрано заранее.'))),
    h('div', { class: 'form-actions' },
      h('button', { type: 'submit', class: 'btn primary' }, t('Сохранить')),
      h('span', { class: 'spacer' }),
      deleteButton(t('Удалить хранилище'), { title: t('Удалить «{0}»?', s.name), text: t('Краски из него останутся в каталоге, просто без хранилища.') }, async () => {
        try {
          await api('DELETE', `/storages/${id}`);
          toast(t('Хранилище удалено, краски остались в каталоге'), 'ok');
          location.hash = '#/storages';
        } catch (err) { toast(explain(err, t('Это хранилище по умолчанию — сначала отметьте другое')), 'err'); }
      })));
  settings.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      const body = {
        name: nameInput.value.trim(), nfc_tag: tagInput.value.trim() || null, at_hand: atHand.checked,
        copies_storage_id: copies.value ? Number(copies.value) : null,
      };
      if (isDefault.checked && !s.is_default) body.is_default = true;
      await api('PATCH', `/storages/${id}`, body);
      toast(t('Сохранено'), 'ok');
      render();
    } catch (err) {
      toast(explain(err, t('Хранилище с таким названием уже есть')), 'err');
    }
  });

  const copyBtn = h('button', { type: 'button', class: 'btn' }, t('Копировать'));
  copyBtn.addEventListener('click', async () => toast(await copyText(link) ? t('Ссылка скопирована') : t('Не получилось — выделите ссылку вручную'), ''));

  mount(h('div', null,
    storageHead(s),
    s.copies_storage ? h('div', { class: 'muted small', style: 'margin:-6px 0 10px' }, t('Дубликаты предлагаются в «{0}»', s.copies_storage.name)) : null,
    browser.el,
    h('div', { class: 'row', style: 'margin-top:12px' },
      h('a', { class: 'btn primary', href: `#/paint/new?storage=${s.id}` }, t('+ Добавить краску сюда'))),
    h('h2', null, t('Метка')),
    h('div', { class: 'panel qr-panel' },
      qrImg(link),
      h('div', { class: 'grow' },
        h('div', { class: 'link-box' }, h('code', null, link), copyBtn),
        h('p', { class: 'muted small', style: 'margin:10px 0' },
          t('Наведите камеру телефона на QR-код — откроется это хранилище (телефон должен быть в той же сети). '),
          t('Ссылку можно записать и в NFC-метку (например, приложением NFC Tools: «Записать» → «URL»).')),
        h('div', { class: 'row' },
          h('a', { class: 'btn', href: `#/labels?ids=${s.id}` }, t('🖨 Напечатать метку')),
          saveImageButton(() => [{ name: s.name, link }], () => getLabelSize(), `qr-${s.name}`, true)))),
    h('h2', null, t('Настройки')),
    h('details', { class: 'panel' }, h('summary', null, t('Название, метка, по умолчанию, дубликаты, удаление')), settings)));
}

/** Storage page title with ✎: rename right there (Enter saves, Esc cancels). */
function storageHead(s) {
  const badge = s.is_default ? h('span', { class: 'badge' }, t('по умолчанию'))
    : s.at_hand ? h('span', { class: 'badge' }, t('под рукой')) : null;
  const head = h('div', { class: 'page-head' });
  const show = () => head.replaceChildren(
    h('a', { class: 'back', href: '#/storages', 'aria-label': t('Назад') }, '‹'),
    h('h1', null, s.name),
    h('button', { type: 'button', class: 'icon-btn', id: 'rename-storage', title: t('Переименовать'), 'aria-label': t('Переименовать'), onclick: edit }, '✎'),
    ...(badge ? [badge] : []));
  const edit = () => {
    const input = h('input', { class: 'input grow', value: s.name, maxlength: 100, 'aria-label': t('Название') });
    const done = async ok => {
      const v = input.value.trim();
      if (!ok || !v || v === s.name) { show(); return; }
      try {
        await api('PATCH', `/storages/${s.id}`, { name: v });
        toast(t('Сохранено'), 'ok');
        render();
      } catch (err) {
        toast(explain(err, t('Хранилище с таким названием уже есть')), 'err');
        input.focus();
      }
    };
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); done(true); }
      if (e.key === 'Escape') { e.preventDefault(); done(false); }
    });
    head.replaceChildren(
      h('a', { class: 'back', href: '#/storages', 'aria-label': t('Назад') }, '‹'), input,
      h('button', { type: 'button', class: 'icon-btn ok', title: t('Сохранить'), 'aria-label': t('Сохранить'), onclick: () => done(true) }, '✓'),
      h('button', { type: 'button', class: 'icon-btn', title: t('Отмена'), 'aria-label': t('Отмена'), onclick: () => done(false) }, '✕'));
    input.focus();
    input.select();
  };
  show();
  return head;
}

// ======================= QR labels =======================

/** Link to a storage that works from a phone: the LAN address if this page was opened as localhost. */
function storageLink(info, id) {
  const local = ['localhost', '127.0.0.1', '::1', '[::1]'].includes(location.hostname);
  const base = local && info.lan_url ? info.lan_url : location.origin;
  return `${base}/storage/${id}`;
}

function qrImg(link, cls = 'qr') {
  const img = h('img', { class: cls, alt: t('QR-код'), src: `/api/qr.svg?data=${encodeURIComponent(link)}` });
  img.addEventListener('error', () => img.replaceWith(h('div', { class: cls + ' qr-missing muted small' },
    t('QR не получился — обновите зависимости: pip install -r requirements.txt'))));
  return img;
}

const LABEL_SIZES = [['s', t('3 см')], ['m', t('4 см')], ['l', t('5,5 см')]];
const getLabelSize = () => { try { return localStorage.getItem('pa.labelSize') || 'm'; } catch { return 'm'; } };

// the same geometry as the printed sheet (style.css): QR side in mm for each size
const LABEL_QR_MM = { s: 22, m: 30, l: 44 };
const IMAGE_DPI = 300;

/** PNG of labels [{name, link}] at real size: 300 dpi, written into the file so it prints 1:1. */
async function labelsImage(items, size) {
  const px = mm => Math.round(mm * IMAGE_DPI / 25.4);
  const qr = LABEL_QR_MM[size] || 30;
  const w = Math.max(qr + 8, 30), padX = 4, padY = 3, gap = 1.5;
  const fontMm = qr * 0.11 + 5 * 25.4 / 72, lineMm = fontMm * 1.15;
  const cols = Math.max(1, Math.min(items.length, Math.floor(190 / w)));  // fits an A4 page

  const codes = await Promise.all(items.map(async it => {
    const res = await fetch(`/api/qr.svg?data=${encodeURIComponent(it.link)}`);
    if (!res.ok) throw new ApiError(res.status, t('QR не получился — обновите зависимости: pip install -r requirements.txt'));
    const url = URL.createObjectURL(await res.blob());
    const img = new Image();
    await new Promise((ok, fail) => { img.onload = ok; img.onerror = fail; img.src = url; });
    return { img, url };
  }));

  const ctx0 = document.createElement('canvas').getContext('2d');
  const font = `600 ${px(fontMm)}px system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`;
  ctx0.font = font;
  const wrap = text => {  // break the name into lines that fit the label
    const lines = [];
    let line = '';
    for (const word of text.split(/\s+/)) {
      const tryLine = line ? `${line} ${word}` : word;
      if (!line || ctx0.measureText(tryLine).width <= px(w - 2 * padX)) line = tryLine;
      else { lines.push(line); line = word; }
    }
    if (line) lines.push(line);
    return lines;
  };
  const texts = items.map(it => wrap(it.name));
  const rows = [];
  for (let i = 0; i < items.length; i += cols) rows.push(texts.slice(i, i + cols));
  const rowH = rows.map(r => padY * 2 + qr + gap + Math.max(...r.map(l => l.length)) * lineMm);

  const canvas = document.createElement('canvas');
  canvas.width = px(w * cols) + 2;
  canvas.height = px(rowH.reduce((a, b) => a + b, 0)) + 2;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  let y = 0;
  rows.forEach((row, r) => {
    row.forEach((lines, c) => {
      const i = r * cols + c, x = c * w;
      ctx.strokeStyle = '#999';
      ctx.lineWidth = 2;
      ctx.setLineDash([px(1.2), px(0.8)]);
      ctx.strokeRect(px(x) + 1, px(y) + 1, px(w), px(rowH[r]));
      ctx.drawImage(codes[i].img, px(x + (w - qr) / 2) + 1, px(y + padY) + 1, px(qr), px(qr));
      ctx.fillStyle = '#000';
      ctx.font = font;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      lines.forEach((ln, k) => ctx.fillText(ln, px(x + w / 2) + 1, px(y + padY + qr + gap + k * lineMm) + 1));
    });
    y += rowH[r];
  });
  codes.forEach(c => URL.revokeObjectURL(c.url));
  const blob = await new Promise(ok => canvas.toBlob(ok, 'image/png'));
  return withDpi(new Uint8Array(await blob.arrayBuffer()), IMAGE_DPI);
}

/** Put a pHYs chunk (pixels per metre) into a PNG, so image viewers print it at real size. */
function withDpi(png, dpi) {
  const ppm = Math.round(dpi / 0.0254);
  const chunk = new Uint8Array(21);
  const view = new DataView(chunk.buffer);
  view.setUint32(0, 9);
  chunk.set([0x70, 0x48, 0x59, 0x73], 4);  // "pHYs"
  view.setUint32(8, ppm);
  view.setUint32(12, ppm);
  chunk[16] = 1;  // unit: metre
  view.setUint32(17, crc32(chunk.subarray(4, 17)));
  const at = 33;  // right after the signature (8) and IHDR (25)
  const out = new Uint8Array(png.length + chunk.length);
  out.set(png.subarray(0, at), 0);
  out.set(chunk, at);
  out.set(png.subarray(at), at + chunk.length);
  return new Blob([out], { type: 'image/png' });
}

function crc32(bytes) {
  let c = ~0;
  for (const b of bytes) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xEDB88320 & -(c & 1));
  }
  return ~c >>> 0;
}

// browsers may replace a non-Latin download name with "download", so names are transliterated
const TRANSLIT = Object.fromEntries('а:a б:b в:v г:g д:d е:e ё:e ж:zh з:z и:i й:j к:k л:l м:m н:n о:o п:p р:r с:s т:t у:u ф:f х:h ц:c ч:ch ш:sh щ:sch ъ: ы:y ь: э:e ю:yu я:ya'
  .split(' ').map(pair => pair.split(':')));

function asciiFileName(name) {
  const latin = [...name].map(ch => {
    const lower = ch.toLowerCase();
    if (!(lower in TRANSLIT)) return ch;
    const tr = TRANSLIT[lower];
    return ch !== lower ? tr.charAt(0).toUpperCase() + tr.slice(1) : tr;
  }).join('');
  return latin.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '') || 'qr';
}

/** «💾 Сохранить картинку»: downloads labelsImage() of getItems() at getSize(). */
function saveImageButton(getItems, getSize, fileName, showSize = false) {
  const label = () => (showSize ? `${t('💾 Сохранить картинку')} · ${LABEL_SIZES.find(([k]) => k === getSize())[1]}` : t('💾 Сохранить картинку'));
  const b = h('button', { type: 'button', class: 'btn', id: 'save-image' }, label());
  b.addEventListener('click', async () => {
    const items = getItems();
    if (!items.length) { toast(t('Не выбрано ни одного хранилища'), 'err'); return; }
    b.disabled = true;
    try {
      const blob = await labelsImage(items, getSize());
      const a = h('a', { href: URL.createObjectURL(blob), download: `${asciiFileName(typeof fileName === 'function' ? fileName() : fileName)}.png` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : t('Не получилось сохранить картинку'), 'err');
    } finally {
      b.disabled = false;
    }
  });
  return b;
}

async function viewLabels(mount, preselected) {
  const [storages, info] = await Promise.all([api('GET', '/storages'), api('GET', '/info').catch(() => ({}))]);
  const chosen = new Set(preselected ? preselected.filter(id => storages.some(s => s.id === id)) : storages.map(s => s.id));
  let size = getLabelSize();

  const sheet = h('div', { class: 'label-sheet' });
  const drawSheet = () => {
    sheet.className = `label-sheet size-${size}`;
    const picked = storages.filter(s => chosen.has(s.id));
    sheet.replaceChildren(...(picked.length
      ? picked.map(s => h('div', { class: 'qr-label' },
        qrImg(storageLink(info, s.id), 'qr-label-code'),
        h('div', { class: 'qr-label-name' }, s.name)))
      : [h('div', { class: 'empty no-print' }, t('Отметьте хранилища, для которых нужны метки'))]));
    count.textContent = picked.length ? t('Меток: {0}', picked.length) : '';
  };

  const count = h('span', { class: 'muted small' });
  const checks = storages.map(s => {
    const box = h('input', { type: 'checkbox', checked: chosen.has(s.id) });
    box.addEventListener('change', () => { box.checked ? chosen.add(s.id) : chosen.delete(s.id); drawSheet(); });
    return h('label', { class: 'check' }, box, s.name);
  });
  const all = h('button', { type: 'button', class: 'btn small' }, t('Все'));
  const none = h('button', { type: 'button', class: 'btn small' }, t('Ни одного'));
  const setAll = on => {
    storages.forEach(s => (on ? chosen.add(s.id) : chosen.delete(s.id)));
    checks.forEach(l => { l.querySelector('input').checked = on; });
    drawSheet();
  };
  all.addEventListener('click', () => setAll(true));
  none.addEventListener('click', () => setAll(false));

  const seg = h('div', { class: 'seg' }, LABEL_SIZES.map(([key, label]) => {
    const b = h('button', { type: 'button', 'aria-pressed': String(key === size) }, label);
    b.addEventListener('click', () => {
      size = key;
      try { localStorage.setItem('pa.labelSize', key); } catch { /* private mode */ }
      seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      drawSheet();
    });
    return b;
  }));

  const printBtn = h('button', { type: 'button', class: 'btn primary' }, t('🖨 Печать'));
  printBtn.addEventListener('click', () => {
    if (!chosen.size) { toast(t('Не выбрано ни одного хранилища'), 'err'); return; }
    window.print();
  });

  const base = storageLink(info, 0).replace(/\/storage\/0$/, '');
  drawSheet();
  mount(h('div', null,
    h('div', { class: 'no-print' },
      pageHead(t('QR-метки'), '#/storages'),
      h('div', { class: 'panel' },
        h('div', { class: 'field' }, h('label', null, t('Ширина метки')), seg),
        h('div', { class: 'field' },
          h('label', null, t('Хранилища '), h('span', { class: 'label-picks' }, all, none)),
          h('div', { class: 'label-checks' }, checks)),
        h('div', { class: 'row', style: 'align-items:center' }, printBtn,
          saveImageButton(() => storages.filter(s => chosen.has(s.id)).map(s => ({ name: s.name, link: storageLink(info, s.id) })),
            () => size, () => (chosen.size === 1 ? `qr-${storages.find(s => chosen.has(s.id)).name}` : 'qr-labels')),
          count)),
      h('p', { class: 'muted small' },
        t('Метки ведут на адрес '), h('code', null, base), t('. Если у компьютера сменится IP, напечатанные метки перестанут открываться — '),
        t('закрепите за ним адрес в настройках роутера (резервирование DHCP). '),
        t('Печатайте в масштабе 100% («Фактический размер»), режьте по пунктиру, сверху можно заклеить скотчем.')),
      h('h2', null, t('Предпросмотр'))),
    sheet));
}

// ======================= sign-in and settings =======================

/** Sign-in is on and this device isn't signed in: show the login screen, then come back here. */
function goToLogin() {
  if (location.hash === '#/login') return;
  try { sessionStorage.setItem('pa.next', location.hash || '#/'); } catch { /* private mode */ }
  location.hash = '#/login';
}

async function viewLogin(mount) {
  const state = await api('GET', '/auth');
  const next = () => {
    let to = '#/';
    try { to = sessionStorage.getItem('pa.next') || '#/'; sessionStorage.removeItem('pa.next'); } catch { /* */ }
    location.replace(to === '#/login' ? '#/' : to);
  };
  if (state.signed_in) { next(); return; }
  const password = h('input', { class: 'input', id: 'l-password', type: 'password', autocomplete: 'current-password', required: true });
  const error = h('div', { class: 'login-error', role: 'alert' });
  const form = h('form', { class: 'panel login' },
    h('h1', null, 'Paint Accountant'),
    h('div', { class: 'field' }, h('label', null, t('Пользователь')), h('input', { class: 'input', value: state.user, readonly: true, autocomplete: 'username' })),
    h('div', { class: 'field' }, h('label', { for: 'l-password' }, t('Пароль')), password),
    error,
    h('button', { type: 'submit', class: 'btn primary' }, t('Войти')));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    error.textContent = '';
    try {
      await api('POST', '/auth/login', { password: password.value });
      next();
    } catch (err) {
      const d = err.detail || {};
      error.textContent = d.code === 'wrong_password' ? t('Неверный пароль')
        : d.code === 'too_many_attempts' ? t('Слишком много попыток — подождите {0} с', d.wait) : explain(err);
      password.select();
    }
  });
  mount(h('div', { class: 'login-wrap' }, form,
    h('p', { class: 'muted small' }, t('Забыли пароль? На компьютере с приложением выполните '),
      h('code', null, 'python run.py --reset-auth'), t(' — вход отключится, новый пароль можно задать в настройках.'))));
  password.focus();
}

function passwordPair(onSubmit, buttonLabel) {
  const first = h('input', { class: 'input', type: 'password', id: 'p-new', autocomplete: 'new-password', minlength: 4, required: true });
  const again = h('input', { class: 'input', type: 'password', id: 'p-again', autocomplete: 'new-password', minlength: 4, required: true });
  const form = h('form', { class: 'form' },
    h('div', { class: 'field' }, h('label', { for: 'p-new' }, t('Новый пароль')), first,
      h('div', { class: 'hint' }, t('Не короче 4 символов'))),
    h('div', { class: 'field' }, h('label', { for: 'p-again' }, t('Ещё раз')), again),
    h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn primary' }, buttonLabel)));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (first.value !== again.value) { toast(t('Пароли не совпадают'), 'err'); again.focus(); return; }
    if (first.value.length < 4) { toast(t('Пароль короче 4 символов'), 'err'); first.focus(); return; }
    try { await onSubmit(first.value); } catch (err) { toast(explain(err), 'err'); }
  });
  return form;
}

function segButtons(label, options, current, onPick) {
  return h('div', { class: 'seg', role: 'group', 'aria-label': label }, options.map(([value, text]) => {
    const b = h('button', { type: 'button', 'aria-pressed': String(value === current) }, text);
    b.addEventListener('click', () => onPick(value));
    return b;
  }));
}

async function viewSettings(mount) {
  const [state, info] = await Promise.all([api('GET', '/auth'), api('GET', '/info').catch(() => ({}))]);
  const touch = window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0;

  const device = h('div', { class: 'panel form' },
    h('div', { class: 'field' }, h('span', { class: 'label' }, t('Язык')),
      segButtons(t('Язык'), LANGS, LANG, code => { if (code !== LANG) setLang(code); })),
    touch ? h('div', { class: 'field' }, h('span', { class: 'label' }, t('Камера при добавлении краски')),
      segButtons(t('Режим камеры'), [['photo', t('Фото')], ['stream', t('Поток + фото')]], cameraMode(), value => {
        cameraMode(value);
        render();
      }),
      h('div', { class: 'hint' }, t('«Фото» открывает приложение камеры. «Поток» ловит штрихкоды в живом видео, но без HTTPS требует флаг Chrome.'))) : null);

  let signIn;
  if (!state.enabled) {
    signIn = h('div', { class: 'panel' },
      h('p', { style: 'margin-top:0' }, t('Сейчас вход выключен: приложение открывается без пароля у всех в вашей сети.')),
      h('p', { class: 'muted small' }, t('Включите вход, чтобы спрашивать пароль. Пользователь — {0}. Этот телефон или компьютер останется в системе, на остальных устройствах нужно будет войти один раз.', state.user)),
      passwordPair(async pw => {
        await api('POST', '/auth/password', { password: pw });
        toast(t('Вход по паролю включён'), 'ok');
        render();
      }, t('Включить вход')));
  } else {
    const logout = h('button', { type: 'button', class: 'btn' }, t('Выйти'));
    logout.addEventListener('click', async () => {
      await api('POST', '/auth/logout').catch(() => null);
      location.hash = '#/login';
    });
    const off = h('button', { type: 'button', class: 'btn danger' }, t('Отключить вход'));
    off.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: t('Отключить вход по паролю?'),
        text: t('Приложение снова будет открываться без пароля у всех в вашей сети.'),
        confirmLabel: t('Отключить'),
      });
      if (!ok) return;
      try {
        await api('POST', '/auth/disable');
        toast(t('Вход отключён'), 'ok');
        render();
      } catch (err) { toast(explain(err), 'err'); }
    });
    signIn = h('div', { class: 'panel' },
      h('p', { style: 'margin-top:0' }, t('Вход включён. Пользователь — {0}.', state.user)),
      h('div', { class: 'row' }, logout, off),
      h('details', { class: 'sub-details' }, h('summary', null, t('Сменить пароль')),
        h('p', { class: 'muted small' }, t('После смены на остальных устройствах нужно будет войти заново.')),
        passwordPair(async pw => {
          await api('POST', '/auth/password', { password: pw });
          toast(t('Пароль изменён'), 'ok');
          render();
        }, t('Сменить пароль'))));
  }

  mount(h('div', null,
    pageHead(t('Настройки')),
    h('h2', null, t('На этом устройстве')), device,
    h('h2', null, t('Вход по паролю')), signIn,
    h('p', { class: 'muted small' },
      t('Пароль передаётся по сети без шифрования (http), поэтому не используйте пароль от важных аккаунтов. '),
      t('Забыли пароль? На компьютере с приложением выполните '), h('code', null, 'python run.py --reset-auth'), '.'),
    info.version ? h('p', { class: 'muted small' }, `Paint Accountant ${info.version}`) : null));
}

// ======================= catalogs: manufacturers and tags =======================

let catalogSection = 'makers';

async function viewCatalogs(mount) {
  const [makers, tags] = await Promise.all([api('GET', '/manufacturers'), api('GET', '/tags')]);
  const body = h('div', { class: 'card-list' });
  const draw = () => body.replaceChildren(...(catalogSection === 'makers' ? manufacturersSection(makers) : tagsSection(tags)));
  const seg = h('div', { class: 'seg catalog-seg', role: 'group', 'aria-label': t('Раздел') },
    [['makers', t('Производители ({0})', makers.length)], ['tags', t('Теги ({0})', tags.length)]].map(([value, label]) => {
      const b = h('button', { type: 'button', 'data-section': value, 'aria-pressed': String(value === catalogSection) }, label);
      b.addEventListener('click', () => {
        catalogSection = value;
        seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
        draw();
      });
      return b;
    }));
  draw();
  mount(h('div', null, pageHead(t('Справочники')), seg, body));
}

/** Name shown as text; ✎ turns it into a field with ✓ / ✕ (Enter saves, Esc cancels). */
function editableName(item, { save, label, maxlength }) {
  const box = h('div', { class: 'cat-name' });
  const show = () => box.replaceChildren(h('span', { class: 'cat-title' }, item.name));
  const edit = () => {
    const input = h('input', { class: 'input', value: item.name, maxlength, 'aria-label': label });
    const done = async ok => {
      const v = input.value.trim();
      if (!ok || !v || v === item.name) { show(); return; }
      try {
        Object.assign(item, await save(v));
        toast(t('Сохранено'), 'ok');
      } catch (err) {
        toast(explain(err, t('Такое название уже есть')), 'err');
      }
      show();
    };
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); done(true); }
      if (e.key === 'Escape') { e.preventDefault(); done(false); }
    });
    box.replaceChildren(input,
      h('button', { type: 'button', class: 'icon-btn ok', title: t('Сохранить'), 'aria-label': t('Сохранить'), onclick: () => done(true) }, '✓'),
      h('button', { type: 'button', class: 'icon-btn', title: t('Отмена'), 'aria-label': t('Отмена'), onclick: () => done(false) }, '✕'));
    input.focus();
    input.select();
  };
  show();
  return { el: box, edit };
}

/** Icon button that asks for confirmation before deleting. */
function deleteIcon(question, onConfirm) {
  const b = h('button', { type: 'button', class: 'icon-btn danger-icon', title: t('Удалить'), 'aria-label': t('Удалить') }, '🗑');
  b.addEventListener('click', async e => {
    e.preventDefault();
    if (await confirmDialog(question)) onConfirm();
  });
  return b;
}

/** Open the paint list filtered by a manufacturer or a tag. */
function showPaintsOf(kind, id) {
  resetListFilters();
  if (kind === 'maker') filters.maker = String(id);
  else filters.tags.add(id);
  location.hash = '#/';
}

function addRow(placeholder, onAdd, extra = null) {
  const input = h('input', { class: 'input grow', placeholder, maxlength: 100, required: true });
  const form = h('form', { class: 'cat-add' }, extra, input, h('button', { type: 'submit', class: 'btn primary' }, t('Добавить')));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (await onAdd(input.value.trim())) render();
  });
  return form;
}

function catRow({ lead, name, sub, count, onOpen, actions }) {
  // the whole middle of the row opens the paint list; the name gets the full width
  const info = h('div', { class: 'grow cat-info', role: 'link', tabindex: 0, title: t('Показать краски') },
    name.el,
    h('div', { class: 'keywords' },
      h('span', { class: count ? 'cat-count' : '' }, count ? countPaints(count) : t('нет красок')),
      sub ? ` · ${sub}` : ''));
  const open = e => {
    if (e.target.closest('input, button')) return; // renaming in progress
    onOpen();
  };
  info.addEventListener('click', open);
  info.addEventListener('keydown', e => { if (e.key === 'Enter') open(e); });
  return h('div', { class: 'cat-row' }, lead, info, h('span', { class: 'chev' }, '›'), actions);
}

function manufacturersSection(makers) {
  const add = addRow(t('Новый производитель'), async name => {
    try {
      await api('POST', '/manufacturers', { name });
      toast(t('Производитель добавлен'), 'ok');
      return true;
    } catch (err) {
      toast(explain(err, t('Такой производитель уже есть')), 'err');
      return false;
    }
  });
  const rows = makers.map(m => {
    const name = editableName(m, {
      label: t('Название производителя'), maxlength: 100,
      save: v => api('PATCH', `/manufacturers/${m.id}`, { name: v }),
    });
    return catRow({
      name,
      count: m.paint_count,
      onOpen: () => showPaintsOf('maker', m.id),
      actions: h('div', { class: 'cat-actions' },
        h('button', { type: 'button', class: 'icon-btn', title: t('Переименовать'), 'aria-label': t('Переименовать'), onclick: name.edit }, '✎'),
        m.paint_count ? null : deleteIcon({ title: t('Удалить производителя «{0}»?', m.name) }, async () => {
          try {
            await api('DELETE', `/manufacturers/${m.id}`);
            toast(t('Производитель удалён'), 'ok');
            render();
          } catch (err) { toast(explain(err, t('Нельзя удалить: у производителя есть краски')), 'err'); }
        })),
    });
  });
  return [add, ...rows];
}

function tagsSection(tags) {
  const newColor = h('input', { type: 'color', class: 'color-dot', value: '#888888', 'aria-label': t('Цвет тега'), title: t('Цвет') });
  const add = addRow(t('Новый тег'), async name => {
    try {
      await api('POST', '/tags', { name, color: newColor.value });
      toast(t('Тег добавлен'), 'ok');
      return true;
    } catch (err) {
      toast(explain(err, t('Тег с таким названием уже есть')), 'err');
      return false;
    }
  }, newColor);
  const rows = tags.map(tg => {
    const color = h('input', { type: 'color', class: 'color-dot', value: tg.color, 'aria-label': t('Цвет тега {0}', tg.name), title: t('Сменить цвет') });
    color.addEventListener('change', async () => {
      try {
        Object.assign(tg, await api('PATCH', `/tags/${tg.id}`, { color: color.value }));
        toast(t('Цвет сохранён'), 'ok');
      } catch (err) {
        color.value = tg.color;
        toast(explain(err), 'err');
      }
    });
    const name = editableName(tg, {
      label: t('Название тега'), maxlength: 50,
      save: v => api('PATCH', `/tags/${tg.id}`, { name: v }),
    });
    return catRow({
      lead: color,
      name,
      sub: tg.keywords.length ? t('на этикетке: ') + tg.keywords.join(', ') : null,
      count: tg.paint_count,
      onOpen: () => showPaintsOf('tag', tg.id),
      actions: h('div', { class: 'cat-actions' },
        h('button', { type: 'button', class: 'icon-btn', title: t('Переименовать'), 'aria-label': t('Переименовать'), onclick: name.edit }, '✎'),
        deleteIcon({ title: t('Удалить тег «{0}»?', tg.name), text: tg.paint_count ? t('Он снимется с {0}.', countPaints(tg.paint_count)) : '' }, async () => {
          try {
            await api('DELETE', `/tags/${tg.id}`);
            toast(t('Тег удалён'), 'ok');
            render();
          } catch (err) { toast(explain(err), 'err'); }
        })),
    });
  });
  return [add, ...rows];
}

// ======================= on-screen keyboard =======================

// On phones the keyboard shrinks the page (see the viewport meta). Keep the field being edited
// in the middle of what's left, clear of the sticky bottom bar — otherwise it hides under it.
if (window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0) {
  const keepFocusedVisible = () => {
    const el = document.activeElement;
    if (el && el.matches('input:not([type=checkbox]):not([type=color]):not([type=file]), textarea') && el.closest('main')) {
      el.scrollIntoView({ block: 'center' });
    }
  };
  window.addEventListener('resize', keepFocusedVisible);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', keepFocusedVisible);
  document.addEventListener('focusin', () => setTimeout(keepFocusedVisible, 350)); // after the keyboard slides in
}

// ======================= start =======================

render();
