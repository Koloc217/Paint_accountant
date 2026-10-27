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

const countPaints = n => `${n} ${plural(n, 'краска', 'краски', 'красок')}`;
const countPots = n => `${n} ${plural(n, 'банка', 'банки', 'банок')}`;

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
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
    let msg = `Ошибка ${res.status}`;
    if (typeof d === 'string') msg = d;
    else if (Array.isArray(d)) msg = d.map(e => (e.loc ? e.loc.slice(1).join('.') + ': ' : '') + e.msg).join('; ');
    else if (d && typeof d === 'object') msg = d.message || msg;
    throw new ApiError(res.status, msg, d && typeof d === 'object' && !Array.isArray(d) ? d : null);
  }
  return data;
}

function explain(err, conflictText) {
  if (!(err instanceof ApiError)) return 'Нет связи с сервером';
  if (err.status === 409 && conflictText) return conflictText;
  if (err.status === 404) return 'Не найдено — возможно, уже удалено';
  if (err.status === 422) return 'Проверьте поля: ' + err.message;
  return err.message;
}

// ======================= shared pieces =======================

function swatch(hex, big = false) {
  return h('span', {
    class: 'swatch' + (hex ? '' : ' no-color') + (big ? ' big' : ''),
    style: hex ? `--c:${hex}` : null,
    title: hex || 'цвет не задан',
  });
}

function setSwatch(el, hex) {
  el.classList.toggle('no-color', !hex);
  el.style.cssText = hex ? `--c:${hex}` : '';
  el.title = hex || 'цвет не задан';
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
async function confirmDialog({ title, text = '', confirmLabel = 'Удалить' }) {
  return (await choiceDialog({
    title, text,
    buttons: [{ label: 'Отмена', value: false }, { label: confirmLabel, value: true, kind: 'danger-solid' }],
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
  const sel = h('select', { class: 'input', 'aria-label': 'Другое хранилище' },
    others.map(s => h('option', { value: String(s.id) }, s.name + (s.at_hand ? ' · под рукой' : ''))),
    h('option', { value: 'none' }, '— без хранилища —'));
  const preferred = (detail.suggest && others.find(s => s.id === detail.suggest.id)) || others.find(s => !s.at_hand);
  sel.value = preferred ? String(preferred.id) : 'none';
  const choice = await choiceDialog({
    title: detail.have
      ? `«${paintName}» уже есть в «${detail.storage.name}» (${detail.have})`
      : `${count} ${plural(count, 'банка', 'банки', 'банок')} «${paintName}» в «${detail.storage.name}»?`,
    text: detail.have
      ? 'Добавить ещё одну банку туда или положить в другое хранилище?'
      : 'Положить их все под руку или в другое хранилище?',
    content: h('div', { class: 'field' }, h('span', { class: 'label' }, 'Другое хранилище'), sel),
    buttons: [
      { label: 'Отмена', value: null },
      { label: 'В выбранное', value: 'other' },
      { label: `Да, в «${detail.storage.name}»`, value: 'here', kind: 'primary' },
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
    backHref ? h('a', { class: 'back', href: backHref, 'aria-label': 'Назад' }, '‹') : null,
    h('h1', null, title),
    extra);
}

const storageName = st => (st ? `«${st.name}»` : 'без хранилища');

/** "Под рукой ✋, Запас 2" — where the pots of a paint lie. */
function locationsText(p) {
  if (!p.locations.length) return 'нет в наличии';
  return p.locations.map(l => {
    const label = l.storage ? l.storage.name + (l.storage.at_hand ? ' ✋' : '') : 'без хранилища';
    return p.locations.length > 1 || l.quantity > 1 ? `${label} ${l.quantity}` : label;
  }).join(', ');
}

/** Select "where to put pots": the storages (the default one preselected) or none. */
function placementSelect(storages, selected, attrs = {}) {
  const def = storages.find(s => s.is_default);
  const sel = h('select', { class: 'input', ...attrs },
    storages.map(s => h('option', { value: String(s.id) },
      s.name + (s.is_default ? ' · по умолчанию' : s.at_hand ? ' · под рукой' : ''))),
    h('option', { value: 'none' }, '— без хранилища —'));
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
      toast(`${paint.name}: ${parts}. Уже есть в ${where}`, 'warn');
    } else {
      toast(`${paint.name}: ${parts}`, 'ok');
    }
  } else if (notice.action === 'removed') {
    const taken = notice.taken.map(x => `−${x.count} из ${storageName(x.storage)}`).join(', ');
    const src = notice.refill_from[0];
    const dst = notice.emptied[0];
    if (src && dst) {
      toast(`${paint.name}: ${taken}. Под рукой закончилась, есть в ${storageName(src.storage)} (${src.quantity})`, 'warn', {
        label: 'Взять',
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
  const minus = h('button', { type: 'button', 'aria-label': 'Меньше' }, '−');
  const plus = h('button', { type: 'button', 'aria-label': 'Больше' }, '+');
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
    else if (p.quantity > n) parts.push(`всего ${p.quantity}`);
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
          p.tags.map(t => h('span', { class: 'chip-mini', style: `--tc:${t.color}` }, t.name))) : null)),
    h('div', { class: 'qty', title: storageId ? 'Банок в этом хранилище' : 'Банок всего' }, minus, value, plus));
  return row;
}

/** Where the pots of one paint lie, with −/+ per storage, "move 1 to…" and "+1 pot". */
function locationsEditor(paint, storages) {
  const box = h('div', { class: 'loc-editor' });

  function draw(p) {
    paint = p;
    const rows = p.locations.map(loc => {
      const sid = loc.storage ? loc.storage.id : null;
      const moveSel = h('select', { class: 'input small-select', 'aria-label': 'Переложить одну банку' },
        h('option', { value: '' }, 'Переложить 1 в…'),
        storages.filter(s => s.id !== sid).map(s => h('option', { value: String(s.id) }, s.name)),
        sid !== null ? h('option', { value: 'none' }, 'без хранилища') : null);
      moveSel.addEventListener('change', () => {
        if (moveSel.value) act('move', { from_storage_id: sid, to_storage_id: moveSel.value === 'none' ? null : Number(moveSel.value) });
      });
      return h('div', { class: 'loc-row' },
        h('div', { class: 'grow' },
          h('div', { class: 'loc-name' },
            loc.storage ? loc.storage.name : 'Без хранилища',
            loc.storage && loc.storage.at_hand ? ' ✋' : '',
            loc.storage && loc.storage.is_default ? h('span', { class: 'badge' }, 'по умолчанию') : null),
          moveSel),
        h('div', { class: 'qty' },
          h('button', { type: 'button', 'aria-label': 'Меньше', onclick: () => act('remove', { storage_id: sid }) }, '−'),
          h('span', { class: 'qty-value' }, loc.quantity),
          h('button', { type: 'button', 'aria-label': 'Больше', onclick: () => act('add', { storage_id: sid }) }, '+')));
    });
    const addSel = placementSelect(storages, null, { 'aria-label': 'Куда добавить банку' });
    box.replaceChildren(
      rows.length ? h('div', { class: 'loc-list' }, rows) : h('div', { class: 'muted small' }, 'Банок нет — краска закончилась, но осталась в каталоге'),
      h('div', { class: 'row loc-add' }, addSel,
        h('button', { type: 'button', class: 'btn', onclick: () => act('add', placementBody(addSel.value)) }, '+1 банка')));
  }

  async function act(kind, body) {
    try {
      const r = kind === 'add' ? await addPots(paint, body) : await api('POST', `/paints/${paint.id}/${kind}`, body);
      if (!r) return; // cancelled
      showNotice(r.paint, r.notice);
      paintChanged(r.paint);
    } catch (err) {
      toast(explain(err, 'Там столько банок нет'), 'err');
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
  [/^#\/tags$/, () => location.replace('#/catalogs')],
];

let renderSeq = 0;
let prevHash = null;
let currentHash = location.hash || '#/';
let cleanups = [];

/** Run when the current screen is replaced (remove listeners etc.). */
function onLeave(fn) { cleanups.push(fn); }

function highlightTab(hash) {
  const tab = hash.startsWith('#/storage') ? 'storages' : hash.startsWith('#/catalogs') ? 'catalogs' : 'paints';
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
      mount(h('div', { class: 'empty' }, explain(e), h('br'), h('a', { class: 'btn', href: '#/' }, 'К списку красок')));
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

const filters = { q: '', storage: '', maker: '', tags: new Set(), atHand: false, stock: '' };

function resetListFilters() {
  Object.assign(filters, { q: '', storage: '', maker: '', atHand: false, stock: '' });
  filters.tags.clear();
}

function anyFilter() {
  return Boolean(filters.q.trim() || filters.storage || filters.maker || filters.tags.size || filters.atHand || filters.stock);
}

async function viewPaints(mount) {
  const [tags, storages, makers] = await Promise.all([api('GET', '/tags'), api('GET', '/storages'), api('GET', '/manufacturers')]);

  // forget filters that point to deleted tags/storages
  const tagIds = new Set(tags.map(t => t.id));
  for (const id of [...filters.tags]) if (!tagIds.has(id)) filters.tags.delete(id);
  if (filters.storage && filters.storage !== 'none' && !storages.some(s => String(s.id) === filters.storage)) filters.storage = '';
  if (filters.maker && !makers.some(m => String(m.id) === filters.maker)) filters.maker = '';

  const listBox = h('div');
  const summary = h('div', { class: 'summary' });
  const fab = h('a', { class: 'fab', href: '#/paint/new', 'aria-label': 'Добавить краску', title: 'Добавить краску' }, '+');

  const search = h('input', {
    class: 'input', type: 'search', value: filters.q, enterkeyhint: 'search',
    placeholder: 'Поиск: название, производитель, код',
    oninput: debounce(e => { filters.q = e.target.value; refresh(); }, 200),
  });

  const storageSel = h('select', { class: 'input', 'aria-label': 'Хранилище', onchange: e => { filters.storage = e.target.value; refresh(); } },
    h('option', { value: '' }, 'Все хранилища'),
    h('option', { value: 'none' }, 'Без хранилища'),
    storages.map(s => h('option', { value: String(s.id) }, s.name + (s.at_hand ? ' · под рукой' : ''))));
  storageSel.value = filters.storage;

  const makerSel = h('select', { class: 'input', 'aria-label': 'Производитель', onchange: e => { filters.maker = e.target.value; refresh(); } },
    h('option', { value: '' }, 'Все производители'),
    makers.filter(m => m.paint_count > 0 || String(m.id) === filters.maker)
      .map(m => h('option', { value: String(m.id) }, `${m.name} (${m.paint_count})`)));
  makerSel.value = filters.maker;

  const stockSel = h('select', { class: 'input', 'aria-label': 'Наличие', onchange: e => { filters.stock = e.target.value; refresh(); } },
    h('option', { value: '' }, 'Любое наличие'),
    h('option', { value: 'in' }, 'Есть в наличии'),
    h('option', { value: 'out' }, 'Закончились'));
  stockSel.value = filters.stock;

  const chips = h('div', { class: 'chips-scroll' },
    toggleChip('✋ Под рукой', { plain: true, pressed: filters.atHand, onToggle: v => { filters.atHand = v; refresh(); } }),
    tags.map(t => toggleChip(t.name, {
      color: t.color,
      pressed: filters.tags.has(t.id),
      onToggle: v => { v ? filters.tags.add(t.id) : filters.tags.delete(t.id); refresh(); },
    })));

  let seq = 0;
  async function refresh() {
    const mySeq = ++seq;
    const params = new URLSearchParams();
    if (filters.q.trim()) params.set('q', filters.q.trim());
    if (filters.storage === 'none') params.set('no_storage', 'true');
    else if (filters.storage) params.set('storage_id', filters.storage);
    if (filters.maker) params.set('manufacturer_id', filters.maker);
    filters.tags.forEach(id => params.append('tag_id', id));
    if (filters.atHand) params.set('at_hand', 'true');
    if (filters.stock) params.set('in_stock', filters.stock === 'in' ? 'true' : 'false');

    fab.href = filters.storage && filters.storage !== 'none' ? `#/paint/new?storage=${filters.storage}` : '#/paint/new';

    let paints;
    try {
      paints = await api('GET', '/paints?' + params);
    } catch (e) {
      if (mySeq === seq) listBox.replaceChildren(h('div', { class: 'empty' }, explain(e)));
      return;
    }
    if (mySeq !== seq) return;
    paintWatchers.clear();
    updateSummary(paints);

    if (paints.length) {
      listBox.replaceChildren(paintList(paints));
    } else if (anyFilter()) {
      listBox.replaceChildren(h('div', { class: 'empty' }, 'Ничего не нашлось'));
    } else {
      listBox.replaceChildren(h('div', { class: 'empty-big' },
        h('p', null, h('b', null, 'Каталог пока пуст')),
        h('p', { class: 'muted' }, 'Добавьте первую краску. Хранилища — коробки, ящики, полки — можно завести сразу или потом.'),
        h('div', { class: 'row', style: 'justify-content:center;margin-top:14px' },
          h('a', { class: 'btn primary', href: '#/paint/new' }, 'Добавить краску'),
          h('a', { class: 'btn', href: '#/storages' }, 'Хранилища'))));
    }

    function updateSummary(list) {
      const pots = list.reduce((s, p) => s + p.quantity, 0);
      summary.replaceChildren(h('span', null, `${countPaints(list.length)} · ${countPots(pots)}`));
      if (anyFilter()) summary.append(h('button', { type: 'button', onclick: resetFilters }, 'Сбросить фильтры'));
    }
    listBox._update = updateSummary;
    listBox._paints = paints;
  }

  function resetFilters() {
    resetListFilters();
    render();
  }

  // quick +/- changes totals in the summary line
  const onChanged = () => { if (listBox._update) listBox._update(listBox._paints); };
  document.addEventListener('paints-changed', onChanged);
  onLeave(() => document.removeEventListener('paints-changed', onChanged));

  await refresh();
  mount(h('div', null,
    h('div', { class: 'filters' },
      search,
      h('div', { class: 'filter-row' }, storageSel, stockSel, makerSel),
      chips),
    summary,
    listBox,
    fab));
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
  const selectedTags = new Set(p.tags.map(t => t.id));

  const name = h('input', { class: 'input', id: 'f-name', required: true, maxlength: 200, value: p.name, autocomplete: 'off' });
  // manufacturer: pick from the list, or add a new one right here
  const NEW = '__new';
  const maker = h('select', { class: 'input', id: 'f-maker' },
    h('option', { value: '' }, '— выберите —'),
    makers.map(m => h('option', { value: String(m.id), selected: m.id === p.manufacturer_id }, m.name)),
    h('option', { value: NEW }, '＋ Новый производитель…'));
  let prevMaker = maker.value;
  const newMakerInput = h('input', { class: 'input grow', id: 'f-new-maker', maxlength: 100, placeholder: 'Название производителя', autocomplete: 'off' });
  const newMakerBox = h('div', { class: 'row', hidden: true, style: 'margin-top:8px' },
    newMakerInput,
    h('button', { type: 'button', class: 'btn', onclick: () => addMaker() }, 'Добавить'),
    h('button', { type: 'button', class: 'btn ghost', onclick: () => { maker.value = prevMaker; newMakerBox.hidden = true; } }, 'Отмена'));
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
      toast(`Производитель «${created.name}» добавлен`, 'ok');
    } catch (err) {
      const existing = [...maker.options].find(o => o.value !== NEW && o.textContent.toLowerCase() === value.toLowerCase());
      if (err.status === 409 && existing) {
        selectMaker(existing.value);
        toast('Такой производитель уже есть — выбрали его', '');
      } else {
        toast(explain(err), 'err');
      }
    }
  }
  const code = h('input', { class: 'input', id: 'f-code', maxlength: 50, value: p.code || '', autocomplete: 'off' });
  const barcode = h('input', { class: 'input', id: 'f-barcode', maxlength: 100, value: p.barcode || '', inputmode: 'numeric', autocomplete: 'off' });

  // color: text field + native picker + preview, kept in sync
  const preview = swatch(p.hex, true);
  const hexText = h('input', {
    class: 'input', id: 'f-hex', value: p.hex || '', maxlength: 7, placeholder: '#rrggbb',
    autocomplete: 'off', autocapitalize: 'off', spellcheck: false,
  });
  const picker = h('input', { type: 'color', class: 'color-picker', value: p.hex || '#808080', 'aria-label': 'Выбрать цвет' });
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
  const clearColor = h('button', { type: 'button', class: 'btn ghost', onclick: () => { hexText.value = ''; hexText.classList.remove('invalid'); setSwatch(preview, null); } }, 'Без цвета');

  const qty = h('input', { class: 'input', id: 'f-qty', type: 'number', min: 0, step: 1, inputmode: 'numeric', value: '1' });
  const step = d => { qty.value = String(Math.max(0, (parseInt(qty.value, 10) || 0) + d)); };
  const placement = placementSelect(storages, prefillStorage, { id: 'f-storage' });

  const tagChips = new Map();
  const tagPicker = h('div', { class: 'tag-picker' }, tags.map(t => {
    const chip = toggleChip(t.name, {
      color: t.color,
      pressed: selectedTags.has(t.id),
      onToggle: v => { v ? selectedTags.add(t.id) : selectedTags.delete(t.id); },
    });
    tagChips.set(t.id, chip);
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
    if (hex === null) { hexText.focus(); throw new Error('Цвет должен быть в виде #rrggbb'); }
    if (!maker.value || maker.value === NEW) { maker.focus(); throw new Error('Выберите производителя'); }
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
      if (!(quantity >= 0)) { qty.focus(); throw new Error('Количество — целое число от 0'); }
      Object.assign(body, { quantity }, placementBody(placement.value));
    }
    return body;
  }

  const conflict = 'Такая краска уже есть в каталоге — найдите её и поменяйте количество';
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
        else toast(`«${created.name}» добавлена (0 банок)`, 'ok');
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
        toast('Сохранено', 'ok');
      }
      goBack();
    } catch (err) {
      const text = err.status === 409 && /barcode/.test(err.message)
        ? 'Этот штрихкод уже привязан к другой краске' : explain(err, conflict);
      toast(text, 'err');
    } finally {
      buttons.forEach(b => { b.disabled = false; });
      andAnother = false;
    }
  });

  form.append(
    photoBlock ? photoBlock.el : '', // append() would print null as text
    h('div', { class: 'field' }, h('label', { for: 'f-name' }, 'Название'), name),
    h('div', { class: 'field' }, h('label', { for: 'f-maker' }, 'Производитель'), maker, newMakerBox),
    h('div', { class: 'two' },
      h('div', { class: 'field' }, h('label', { for: 'f-code' }, 'Код / артикул'), code),
      h('div', { class: 'field' }, h('label', { for: 'f-barcode' }, 'Штрихкод'), barcode)),
    h('div', { class: 'field' }, h('label', { for: 'f-hex' }, 'Цвет'),
      h('div', { class: 'color-row' }, preview, hexText, picker),
      h('div', { class: 'row', style: 'margin-top:4px' }, clearColor)),
    isNew
      ? h('div', { class: 'two' },
        h('div', { class: 'field' }, h('label', { for: 'f-qty' }, 'Банок'),
          h('div', { class: 'stepper' },
            h('button', { type: 'button', 'aria-label': 'Меньше', onclick: () => step(-1) }, '−'),
            qty,
            h('button', { type: 'button', 'aria-label': 'Больше', onclick: () => step(+1) }, '+')),
          h('div', { class: 'hint' }, '0 — закончилась, но остаётся в каталоге')),
        h('div', { class: 'field' }, h('label', { for: 'f-storage' }, 'Куда положить'), placement))
      : h('div', { class: 'field' }, h('span', { class: 'label' }, 'Где лежит'), locationsEditor(paint, storages)),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Теги'),
      tags.length ? tagPicker : h('div', { class: 'muted small' }, 'Тегов нет — их можно завести в «Справочниках»')),
    h('div', { class: 'form-actions' },
      h('button', { type: 'submit', class: 'btn primary' }, isNew ? 'Добавить' : 'Сохранить'),
      isNew ? h('button', { type: 'submit', class: 'btn', onclick: () => { andAnother = true; } }, 'Добавить и ещё') : null,
      h('span', { class: 'spacer' }),
      isNew ? null : deleteButton('Удалить', () => ({ title: `Удалить «${name.value.trim() || p.name}»?`, text: 'Краска исчезнет из каталога.' }), async () => {
        try {
          await api('DELETE', `/paints/${id}`);
          toast('Краска удалена', 'ok');
          goBack();
        } catch (err) { toast(explain(err), 'err'); }
      })));

  mount(h('div', null, pageHead(isNew ? 'Новая краска' : p.name, '#/'), form));
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
      'Распознавание по фото не установлено. На компьютере с приложением выполните ',
      h('code', null, (status && status.install) || 'pip install -r requirements-photo.txt'),
      ' и перезапустите его.'));
    return { el, reset() {} };
  }

  // Phones: "photo" mode — capture="environment" opens the rear camera app straight away (works over
  // plain http); "stream" mode — live video in the page that catches barcodes, plus a shutter for the label
  // (browsers allow live video only on HTTPS, or with a Chrome flag). Computers: just picking a file.
  const touch = window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0;
  let mode = touch ? cameraMode() : 'photo';
  const cameraInput = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true, id: 'f-camera' });
  const input = h('input', { type: 'file', accept: 'image/*', hidden: true, id: 'f-photo' });
  const button = h('button', { type: 'button', class: 'btn photo-btn' });
  const galleryButton = h('button', { type: 'button', class: 'btn photo-btn secondary' }, touch ? '🖼 Галерея' : '🖼 Загрузить фото этикетки');
  const panel = h('div', { class: 'panel photo-panel', hidden: true });
  let autoTags = [];
  let thumbUrl = null;

  const modeSeg = touch ? h('div', { class: 'seg', role: 'group', 'aria-label': 'Режим камеры' },
    [['photo', 'Фото'], ['stream', 'Поток + фото']].map(([value, label]) => {
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
    button.textContent = mode === 'stream' ? '🎥 Камера' : '📷 Снять этикетку';
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
    autoTags.forEach(t => fill.setTag(t, false));
    autoTags = [];
    panel.hidden = true;
    panel.replaceChildren();
    labelButton();
  }

  // ---------- live stream ----------
  const video = h('video', { class: 'stream-video', playsinline: true, muted: true, autoplay: true });
  video.muted = true;
  const streamStatus = h('div', { class: 'stream-status' }, 'Наведите камеру на штрихкод банки');
  const shutter = h('button', { type: 'button', class: 'btn primary' }, '📸 Снимок этикетки');
  const closeStream = h('button', { type: 'button', class: 'btn' }, 'Закрыть');
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
      panel.replaceChildren(h('div', null, h('b', null, 'Нет доступа к камере'),
        h('div', { class: 'muted small' }, 'Разрешите камеру для этого сайта в настройках браузера. ', String(err.message || err))));
      return;
    }
    panel.hidden = true;
    video.srcObject = stream;
    streamBox.hidden = false;
    lastCode = null;
    streamStatus.replaceChildren('Наведите камеру на штрихкод банки');
    try { await video.play(); } catch { /* autoplay is allowed for muted video */ }
    detector = await barcodeDetector();
    scanning = true;
    tick();
  }

  function stopStream() {
    scanning = false;
    clearTimeout(scanTimer);
    if (stream) stream.getTracks().forEach(t => t.stop());
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
          h('div', { class: 'muted small' }, 'Добавлено. Ещё одна такая же — жмите «+1» снова, другая банка — наведите на её штрихкод.'),
          matchRow({ paint: updated, score: 100, by: 'barcode' }, { barcode: null }, afterAdd));
      };
      streamStatus.replaceChildren(
        h('div', { class: 'muted small' }, `Штрихкод ${code} — это:`),
        matchRow({ paint: found[0], score: 100, by: 'barcode' }, { barcode: null }, afterAdd));
    } else {
      if (!fill.barcode.trim()) fill.barcode = code;
      streamStatus.replaceChildren(
        h('b', null, `Новый штрихкод ${code}`),
        h('div', { class: 'muted small' }, 'Его ещё нет в каталоге. Наведите камеру на этикетку и нажмите «Снимок этикетки» — заполню остальное.'));
      shutter.classList.add('pulse');
    }
  }

  function showFlagHelp() {
    const origin = location.origin;
    const flag = 'chrome://flags/#unsafely-treat-insecure-origin-as-secure';
    const copyBtn = text => h('button', { type: 'button', class: 'btn small-btn', onclick: async () => toast(await copyText(text) ? 'Скопировано' : 'Выделите и скопируйте вручную') }, 'Копировать');
    panel.hidden = false;
    panel.replaceChildren(
      h('b', null, 'Поток с камеры здесь недоступен'),
      h('div', { class: 'muted small' }, 'Живое видео браузер разрешает только по HTTPS. На этом телефоне его можно включить флагом Chrome:'),
      h('ol', { class: 'flag-steps' },
        h('li', null, 'Откройте в Chrome адрес', h('div', { class: 'link-box' }, h('code', null, flag), copyBtn(flag))),
        h('li', null, 'В поле впишите адрес приложения', h('div', { class: 'link-box' }, h('code', null, origin), copyBtn(origin))),
        h('li', null, 'Переключите на «Enabled» и нажмите «Relaunch».')),
      h('div', { class: 'muted small' }, 'Пока можно пользоваться режимом «Фото».'));
  }

  async function recognize(file) {
    if (thumbUrl) URL.revokeObjectURL(thumbUrl);
    thumbUrl = URL.createObjectURL(file);
    panel.hidden = false;
    panel.replaceChildren(head('Распознаю…', 'Обычно это пара секунд'));
    button.disabled = galleryButton.disabled = true;
    try {
      const blob = await shrinkImage(file);
      const res = await fetch('/api/recognize', { method: 'POST', headers: { 'Content-Type': blob.type || 'image/jpeg' }, body: blob });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new ApiError(res.status, (data && data.detail) || `Ошибка ${res.status}`);
      show(data);
    } catch (err) {
      if (!(err instanceof ApiError)) console.error(err);
      panel.replaceChildren(head('Не получилось распознать', err.status === 422 ? 'Файл не похож на фото' : explain(err)));
    } finally {
      button.disabled = galleryButton.disabled = false;
      button.textContent = '📷 Снять ещё';
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
    autoTags.forEach(t => fill.setTag(t, false));
    autoTags = [];
    if (s.name && !fill.name.trim()) { fill.name = s.name; filled.push('название'); }
    if (s.manufacturer) { fill.selectMaker(s.manufacturer.id, s.manufacturer.name); filled.push('производитель'); }
    if (s.code && !fill.code.trim()) { fill.code = s.code; filled.push('код'); }
    if (s.barcode && !fill.barcode.trim()) { fill.barcode = s.barcode; filled.push('штрихкод'); }
    s.tag_ids.forEach(t => fill.setTag(t, true));
    autoTags = [...s.tag_ids];
    if (s.tag_ids.length) filled.push(s.tag_ids.length === 1 ? 'тег' : 'теги');

    const lines = data.lines.filter(l => l.kind !== 'noise');
    const parts = [
      head(filled.length ? 'Заполнено: ' + filled.join(', ') : 'Ничего уверенно не распознано',
        `${data.seconds} с · проверьте поля ниже`),
    ];

    if (data.matches.length) {
      parts.push(h('div', { class: 'photo-section' }, h('b', null, 'Похоже, это уже есть в каталоге'),
        h('div', { class: 'match-list' }, data.matches.map(m => matchRow(m, s)))));
    }

    if (lines.length) {
      let target = 'name';
      const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Куда вставлять' },
        [['name', 'Название'], ['maker', 'Производитель'], ['code', 'Код']].map(([value, label]) => {
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
        h('b', null, 'Текст с этикетки'),
        h('div', { class: 'muted small' },
          'Название: нажимайте куски по порядку — они склеятся, повторное нажатие убирает кусок. ',
          'Код и производитель: нажатие заменяет поле.'),
        seg,
        h('div', { class: 'tag-picker' }, chips)));
    } else {
      parts.push(h('p', { class: 'muted small', style: 'margin:8px 0 0' },
        'Текст не найден. Снимите этикетку ближе, ровно и при хорошем свете, без бликов.'));
    }

    if (data.barcodes.length) {
      const b = data.barcodes[0];
      parts.push(h('div', { class: 'muted small photo-section' }, `Штрихкод ${b.text}: `,
        b.paint_id ? 'уже привязан к краске из каталога' : 'новый — сохранится вместе с краской'));
    }
    panel.replaceChildren(...parts);
  }

  function matchRow(m, s, onDone = null) {
    const p = m.paint;
    const meta = [p.manufacturer, p.code, locationsText(p)].filter(Boolean).join(' · ');
    const plus = h('button', { type: 'button', class: 'btn primary small-btn' }, '+1 банка');
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
        h('div', { class: 'muted small' }, m.by === 'barcode' ? 'совпал штрихкод' : `похоже на ${m.score}%`)),
      h('div', { class: 'match-actions' },
        plus,
        h('a', { class: 'btn small-btn', href: `#/paint/${p.id}` }, 'Открыть')));
  }

  el.append(modeSeg || '', h('div', { class: 'photo-buttons' }, button, galleryButton), cameraInput, input, streamBox, panel);
  return { el, reset };
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

  const nameInput = h('input', { class: 'input grow', placeholder: 'Например: Коробка 1, Ящик у стола', maxlength: 100, required: true });
  const atHand = h('input', { type: 'checkbox' });
  const form = h('form', { class: 'panel form' },
    h('div', { class: 'row' }, nameInput, h('button', { type: 'submit', class: 'btn primary' }, 'Добавить')),
    h('label', { class: 'check' }, atHand, h('span', null, 'Под рукой ', h('span', { class: 'muted small' }, '— ящик или органайзер в прямом доступе'))));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('POST', '/storages', { name: nameInput.value.trim(), at_hand: atHand.checked });
      toast('Хранилище добавлено', 'ok');
      render();
    } catch (err) {
      toast(explain(err, 'Хранилище с таким названием уже есть'), 'err');
    }
  });

  const list = storages.length
    ? h('div', { class: 'card-list' }, storages.map(s =>
      h('a', { class: 'card storage-card', href: `#/storage/${s.id}` },
        h('span', { class: 'storage-icon' + (s.at_hand ? ' hand' : '') }, s.at_hand ? '✋' : '📦'),
        h('div', { class: 'grow' },
          h('div', { class: 'name' }, s.name, ' ',
            s.is_default ? h('span', { class: 'badge' }, 'по умолчанию') : s.at_hand ? h('span', { class: 'badge' }, 'под рукой') : null),
          h('div', { class: 'muted small' }, `${countPaints(s.paint_count)} · ${countPots(s.pot_count)}`,
            s.copies_storage ? ` · дубликаты → ${s.copies_storage.name}` : '', s.nfc_tag ? ' · метка' : '')),
        h('span', { class: 'chev' }, '›'))))
    : h('div', { class: 'empty' }, 'Хранилищ пока нет. Коробки, ящики, полки — всё, где лежат краски.');

  mount(h('div', null, pageHead('Хранилища'), form, h('h2', null, 'Все хранилища'), list));
}

async function viewStorage(mount, id) {
  const [s, paints, info, storages] = await Promise.all([
    api('GET', `/storages/${id}`),
    api('GET', `/paints?storage_id=${id}`),
    api('GET', '/info').catch(() => ({})),
    api('GET', '/storages'),
  ]);

  // a link that works from a phone: use the LAN address if this page was opened as localhost
  const local = ['localhost', '127.0.0.1', '::1', '[::1]'].includes(location.hostname);
  const base = local && info.lan_url ? info.lan_url : location.origin;
  const link = `${base}/storage/${s.id}`;

  const nameInput = h('input', { class: 'input', id: 's-name', value: s.name, maxlength: 100, required: true });
  const tagInput = h('input', { class: 'input', id: 's-tag', value: s.nfc_tag || '', maxlength: 100, autocomplete: 'off' });
  const atHand = h('input', { type: 'checkbox', checked: s.at_hand });
  const isDefault = h('input', { type: 'checkbox', checked: s.is_default, disabled: s.is_default });
  const copies = h('select', { class: 'input', id: 's-copies' },
    h('option', { value: '' }, '— не выбрано —'),
    storages.filter(x => x.id !== s.id).map(x => h('option', { value: String(x.id) }, x.name)));
  copies.value = s.copies_storage ? String(s.copies_storage.id) : '';
  const settings = h('form', { class: 'form' },
    h('div', { class: 'field' }, h('label', { for: 's-name' }, 'Название'), nameInput),
    h('div', { class: 'field' }, h('label', { for: 's-tag' }, 'Метка (необязательно)'), tagInput,
      h('div', { class: 'hint' }, 'Любая пометка для себя: номер или ID NFC-метки, цвет наклейки…')),
    h('label', { class: 'check' }, atHand, 'Под рукой'),
    h('label', { class: 'check' }, isDefault,
      h('span', null, 'По умолчанию ', h('span', { class: 'muted small' }, s.is_default
        ? '— новые банки идут сюда. Чтобы снять, отметьте другое хранилище'
        : '— новые банки будут идти сюда'))),
    h('div', { class: 'field' }, h('label', { for: 's-copies' }, 'Куда предлагать дубликаты'), copies,
      h('div', { class: 'hint' }, 'Если хранилище «под рукой» и сюда кладут вторую банку той же краски, приложение спросит, куда её деть, — это хранилище будет выбрано заранее.')),
    h('div', { class: 'form-actions' },
      h('button', { type: 'submit', class: 'btn primary' }, 'Сохранить'),
      h('span', { class: 'spacer' }),
      deleteButton('Удалить хранилище', { title: `Удалить «${s.name}»?`, text: 'Краски из него останутся в каталоге, просто без хранилища.' }, async () => {
        try {
          await api('DELETE', `/storages/${id}`);
          toast('Хранилище удалено, краски остались в каталоге', 'ok');
          location.hash = '#/storages';
        } catch (err) { toast(explain(err, 'Это хранилище по умолчанию — сначала отметьте другое'), 'err'); }
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
      toast('Сохранено', 'ok');
      render();
    } catch (err) {
      toast(explain(err, 'Хранилище с таким названием уже есть'), 'err');
    }
  });

  const copyBtn = h('button', { type: 'button', class: 'btn' }, 'Копировать');
  copyBtn.addEventListener('click', async () => toast(await copyText(link) ? 'Ссылка скопирована' : 'Не получилось — выделите ссылку вручную', ''));

  const summary = h('div', { class: 'summary' });
  const updateSummary = () => {
    const pots = paints.reduce((n, p) => n + ((p.locations.find(l => l.storage && l.storage.id === s.id) || {}).quantity || 0), 0);
    summary.replaceChildren(h('span', null, `${countPaints(paints.length)} · ${countPots(pots)}`,
      s.copies_storage ? ` · дубликаты → ${s.copies_storage.name}` : ''));
  };
  updateSummary();
  document.addEventListener('paints-changed', updateSummary);
  onLeave(() => document.removeEventListener('paints-changed', updateSummary));

  mount(h('div', null,
    pageHead(s.name, '#/storages', s.is_default ? h('span', { class: 'badge' }, 'по умолчанию')
      : s.at_hand ? h('span', { class: 'badge' }, 'под рукой') : null),
    summary,
    paints.length
      ? paintList(paints, { storageId: s.id })
      : h('div', { class: 'empty' }, 'Здесь пока пусто'),
    h('div', { class: 'row', style: 'margin-top:12px' },
      h('a', { class: 'btn primary', href: `#/paint/new?storage=${s.id}` }, '+ Добавить краску сюда')),
    h('h2', null, 'Ссылка для метки'),
    h('div', { class: 'panel' },
      h('div', { class: 'link-box' }, h('code', null, link), copyBtn),
      h('p', { class: 'muted small', style: 'margin:10px 0 0' },
        'Запишите эту ссылку в NFC-метку (например, приложением NFC Tools: «Записать» → «URL») или сделайте из неё QR-код. ',
        'Телефон в той же сети откроет это хранилище сразу.')),
    h('h2', null, 'Настройки'),
    h('details', { class: 'panel' }, h('summary', null, 'Название, метка, по умолчанию, дубликаты, удаление'), settings)));
}

// ======================= catalogs: manufacturers and tags =======================

async function viewCatalogs(mount) {
  const [makers, tags] = await Promise.all([api('GET', '/manufacturers'), api('GET', '/tags')]);
  mount(h('div', null,
    pageHead('Справочники'),
    h('h2', { id: 'makers' }, 'Производители'),
    manufacturersSection(makers),
    h('h2', { id: 'tags' }, 'Теги'),
    tagsSection(tags)));
}

function manufacturersSection(makers) {
  const rows = makers.map(m => {
    const nameInput = h('input', { class: 'input', value: m.name, maxlength: 100, 'aria-label': 'Название производителя' });
    nameInput.addEventListener('change', async () => {
      const v = nameInput.value.trim();
      if (!v || v === m.name) { nameInput.value = m.name; return; }
      try {
        Object.assign(m, await api('PATCH', `/manufacturers/${m.id}`, { name: v }));
        nameInput.value = m.name;
        toast('Сохранено', 'ok');
      } catch (err) {
        nameInput.value = m.name;
        toast(explain(err, 'Производитель с таким названием уже есть'), 'err');
      }
    });
    const count = m.paint_count
      ? h('a', { href: '#/', class: 'muted small', onclick: () => { resetListFilters(); filters.maker = String(m.id); } }, countPaints(m.paint_count))
      : h('span', { class: 'muted small' }, 'нет красок');
    return h('div', { class: 'tag-row' },
      h('div', { class: 'grow' }, nameInput, h('div', { class: 'keywords' }, count)),
      deleteButton('Удалить', () => ({ title: `Удалить производителя «${m.name}»?` }), async () => {
        try {
          await api('DELETE', `/manufacturers/${m.id}`);
          toast('Производитель удалён', 'ok');
          render();
        } catch (err) {
          toast(explain(err, 'Нельзя удалить: у производителя есть краски. Сначала перенесите или удалите их.'), 'err');
        }
      }));
  });

  const newName = h('input', { class: 'input grow', placeholder: 'Новый производитель', maxlength: 100, required: true });
  const addForm = h('form', { class: 'panel row' }, newName, h('button', { type: 'submit', class: 'btn primary' }, 'Добавить'));
  addForm.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('POST', '/manufacturers', { name: newName.value.trim() });
      toast('Производитель добавлен', 'ok');
      render();
    } catch (err) {
      toast(explain(err, 'Такой производитель уже есть'), 'err');
    }
  });

  return h('div', { class: 'card-list' }, addForm, rows);
}

function tagsSection(tags) {

  const rows = tags.map(t => {
    const nameInput = h('input', { class: 'input', value: t.name, maxlength: 50, 'aria-label': 'Название тега' });
    const color = h('input', { type: 'color', class: 'color-picker', value: t.color, 'aria-label': 'Цвет тега' });

    async function save(changes) {
      try {
        const updated = await api('PATCH', `/tags/${t.id}`, changes);
        Object.assign(t, updated);
        toast('Сохранено', 'ok');
      } catch (err) {
        nameInput.value = t.name;
        color.value = t.color;
        toast(explain(err, 'Тег с таким названием уже есть'), 'err');
      }
    }
    nameInput.addEventListener('change', () => {
      const v = nameInput.value.trim();
      if (!v) { nameInput.value = t.name; return; }
      if (v !== t.name) save({ name: v });
    });
    color.addEventListener('change', () => { if (color.value !== t.color) save({ color: color.value }); });

    return h('div', { class: 'tag-row' },
      color,
      h('div', { class: 'grow' }, nameInput,
        t.keywords.length ? h('div', { class: 'keywords' }, 'На этикетке: ' + t.keywords.join(', ')) : null),
      deleteButton('Удалить', () => ({ title: `Удалить тег «${t.name}»?`, text: 'Он снимется со всех красок.' }), async () => {
        try {
          await api('DELETE', `/tags/${t.id}`);
          toast('Тег удалён', 'ok');
          render();
        } catch (err) { toast(explain(err), 'err'); }
      }));
  });

  const newName = h('input', { class: 'input grow', placeholder: 'Новый тег', maxlength: 50, required: true });
  const newColor = h('input', { type: 'color', class: 'color-picker', value: '#888888', 'aria-label': 'Цвет тега' });
  const addForm = h('form', { class: 'panel row' }, newColor, newName, h('button', { type: 'submit', class: 'btn primary' }, 'Добавить'));
  addForm.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('POST', '/tags', { name: newName.value.trim(), color: newColor.value });
      toast('Тег добавлен', 'ok');
      render();
    } catch (err) {
      toast(explain(err, 'Тег с таким названием уже есть'), 'err');
    }
  });

  return h('div', { class: 'card-list' },
    addForm,
    h('p', { class: 'muted small', style: 'margin:0' }, 'Название и цвет сохраняются сразу после изменения. Удалённый тег снимается со всех красок.'),
    rows);
}

// ======================= start =======================

render();
