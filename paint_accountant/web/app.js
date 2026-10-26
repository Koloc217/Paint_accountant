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

function toast(message, kind = '') {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.className = 'show ' + kind;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.className = ''; }, kind === 'err' ? 5000 : 2500);
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
  constructor(status, message) { super(message); this.status = status; }
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
    throw new ApiError(res.status, msg);
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

/** Delete button that asks "sure?" on the first tap instead of a browser dialog. */
function deleteButton(label, onConfirm) {
  const b = h('button', { type: 'button', class: 'btn danger' }, label);
  let armed = false;
  let timer;
  const reset = () => { armed = false; b.textContent = label; b.classList.remove('armed'); };
  b.addEventListener('click', async () => {
    if (!armed) {
      armed = true;
      b.textContent = 'Точно удалить?';
      b.classList.add('armed');
      timer = setTimeout(reset, 3500);
      return;
    }
    clearTimeout(timer);
    b.disabled = true;
    try { await onConfirm(); } finally { b.disabled = false; reset(); }
  });
  return b;
}

function pageHead(title, backHref, extra) {
  return h('div', { class: 'page-head' },
    backHref ? h('a', { class: 'back', href: backHref, 'aria-label': 'Назад' }, '‹') : null,
    h('h1', null, title),
    extra);
}

function storageOptions(storages, selected) {
  return [
    h('option', { value: '' }, '— без хранилища —'),
    storages.map(s => h('option', { value: String(s.id), selected: s.id === selected },
      s.name + (s.at_hand ? ' · под рукой' : ''))),
  ];
}

/** Paint rows with quick +/- for the number of pots. */
function paintList(paints, { showStorage = true } = {}) {
  return h('ul', { class: 'paint-list' }, paints.map(p => paintRow(p, showStorage)));
}

function paintRow(p, showStorage) {
  const value = h('span', { class: 'qty-value' }, p.quantity);
  const minus = h('button', { type: 'button', 'aria-label': 'Меньше', disabled: p.quantity === 0 }, '−');
  const plus = h('button', { type: 'button', 'aria-label': 'Больше' }, '+');
  const row = h('li', { class: 'paint' + (p.quantity === 0 ? ' out' : '') });

  const meta = [p.manufacturer, p.code].filter(Boolean);
  if (showStorage) meta.push(p.storage ? p.storage.name + (p.storage.at_hand ? ' ✋' : '') : 'без хранилища');

  async function change(delta) {
    const next = p.quantity + delta;
    if (next < 0) return;
    minus.disabled = plus.disabled = true;
    try {
      const updated = await api('PATCH', `/paints/${p.id}`, { quantity: next });
      p.quantity = updated.quantity;
      value.textContent = p.quantity;
      row.classList.toggle('out', p.quantity === 0);
      document.dispatchEvent(new CustomEvent('paints-changed'));
    } catch (e) {
      toast(explain(e), 'err');
    } finally {
      plus.disabled = false;
      minus.disabled = p.quantity === 0;
    }
  }
  minus.addEventListener('click', () => change(-1));
  plus.addEventListener('click', () => change(+1));

  row.append(
    h('a', { class: 'paint-link', href: `#/paint/${p.id}` },
      swatch(p.hex),
      h('div', { class: 'paint-info' },
        h('div', { class: 'paint-name' }, p.name),
        h('div', { class: 'paint-meta' }, meta.join(' · ')),
        p.tags.length ? h('div', { class: 'paint-tags' },
          p.tags.map(t => h('span', { class: 'chip-mini', style: `--tc:${t.color}` }, t.name))) : null)),
    h('div', { class: 'qty', title: 'Количество банок' }, minus, value, plus));
  return row;
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
  const [tags, storages, makers, paint] = await Promise.all([
    api('GET', '/tags'),
    api('GET', '/storages'),
    api('GET', '/manufacturers'),
    id ? api('GET', `/paints/${id}`) : Promise.resolve(null),
  ]);
  const isNew = !paint;
  const p = paint || {
    name: '', manufacturer_id: lastManufacturer(makers), code: '', hex: null, quantity: 1,
    storage: prefillStorage ? { id: prefillStorage } : null, tags: [],
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

  const qty = h('input', { class: 'input', id: 'f-qty', type: 'number', min: 0, step: 1, inputmode: 'numeric', value: String(p.quantity) });
  const step = d => { qty.value = String(Math.max(0, (parseInt(qty.value, 10) || 0) + d)); };

  const storageSel = h('select', { class: 'input', id: 'f-storage' }, storageOptions(storages, p.storage ? p.storage.id : null));

  const tagPicker = h('div', { class: 'tag-picker' }, tags.map(t => toggleChip(t.name, {
    color: t.color,
    pressed: selectedTags.has(t.id),
    onToggle: v => { v ? selectedTags.add(t.id) : selectedTags.delete(t.id); },
  })));

  function collect() {
    const hex = normHex(hexText.value);
    if (hex === null) { hexText.focus(); throw new Error('Цвет должен быть в виде #rrggbb'); }
    if (!maker.value || maker.value === NEW) { maker.focus(); throw new Error('Выберите производителя'); }
    const quantity = parseInt(qty.value, 10);
    if (!(quantity >= 0)) { qty.focus(); throw new Error('Количество — целое число от 0'); }
    return {
      name: name.value.trim(),
      manufacturer_id: Number(maker.value),
      code: code.value.trim() || null,
      hex: hex || null,
      quantity,
      storage_id: storageSel.value ? Number(storageSel.value) : null,
      tag_ids: [...selectedTags],
    };
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
        const created = await api('POST', '/paints', body);
        rememberManufacturer(created.manufacturer_id);
        if (andAnother) {
          toast(`«${created.name}» добавлена`, 'ok');
          // keep manufacturer, storage and tags — usually the next pot is from the same batch
          name.value = ''; code.value = ''; hexText.value = ''; setSwatch(preview, null); qty.value = '1';
          name.focus();
          return;
        }
        toast('Краска добавлена', 'ok');
      } else {
        await api('PATCH', `/paints/${id}`, body);
        rememberManufacturer(body.manufacturer_id);
        toast('Сохранено', 'ok');
      }
      goBack();
    } catch (err) {
      toast(explain(err, conflict), 'err');
    } finally {
      buttons.forEach(b => { b.disabled = false; });
      andAnother = false;
    }
  });

  form.append(
    h('div', { class: 'field' }, h('label', { for: 'f-name' }, 'Название'), name),
    h('div', { class: 'two' },
      h('div', { class: 'field' }, h('label', { for: 'f-maker' }, 'Производитель'), maker, newMakerBox),
      h('div', { class: 'field' }, h('label', { for: 'f-code' }, 'Код / артикул'), code)),
    h('div', { class: 'field' }, h('label', { for: 'f-hex' }, 'Цвет'),
      h('div', { class: 'color-row' }, preview, hexText, picker),
      h('div', { class: 'row', style: 'margin-top:4px' }, clearColor)),
    h('div', { class: 'two' },
      h('div', { class: 'field' }, h('label', { for: 'f-qty' }, 'Банок'),
        h('div', { class: 'stepper' },
          h('button', { type: 'button', 'aria-label': 'Меньше', onclick: () => step(-1) }, '−'),
          qty,
          h('button', { type: 'button', 'aria-label': 'Больше', onclick: () => step(+1) }, '+')),
        h('div', { class: 'hint' }, '0 — закончилась, но остаётся в каталоге')),
      h('div', { class: 'field' }, h('label', { for: 'f-storage' }, 'Хранилище'), storageSel)),
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'Теги'),
      tags.length ? tagPicker : h('div', { class: 'muted small' }, 'Тегов нет — их можно завести на вкладке «Теги»')),
    h('div', { class: 'form-actions' },
      h('button', { type: 'submit', class: 'btn primary' }, isNew ? 'Добавить' : 'Сохранить'),
      isNew ? h('button', { type: 'submit', class: 'btn', onclick: () => { andAnother = true; } }, 'Добавить и ещё') : null,
      h('span', { class: 'spacer' }),
      isNew ? null : deleteButton('Удалить', async () => {
        try {
          await api('DELETE', `/paints/${id}`);
          toast('Краска удалена', 'ok');
          goBack();
        } catch (err) { toast(explain(err), 'err'); }
      })));

  mount(h('div', null, pageHead(isNew ? 'Новая краска' : p.name, '#/'), form));
  window.scrollTo(0, 0);
  if (isNew) name.focus({ preventScroll: true });
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
          h('div', { class: 'name' }, s.name, ' ', s.at_hand ? h('span', { class: 'badge' }, 'под рукой') : null),
          h('div', { class: 'muted small' }, `${countPaints(s.paint_count)} · ${countPots(s.pot_count)}`, s.nfc_tag ? ' · метка' : '')),
        h('span', { class: 'chev' }, '›'))))
    : h('div', { class: 'empty' }, 'Хранилищ пока нет. Коробки, ящики, полки — всё, где лежат краски.');

  mount(h('div', null, pageHead('Хранилища'), form, h('h2', null, 'Все хранилища'), list));
}

async function viewStorage(mount, id) {
  const [s, paints, info] = await Promise.all([
    api('GET', `/storages/${id}`),
    api('GET', `/paints?storage_id=${id}`),
    api('GET', '/info').catch(() => ({})),
  ]);

  // a link that works from a phone: use the LAN address if this page was opened as localhost
  const local = ['localhost', '127.0.0.1', '::1', '[::1]'].includes(location.hostname);
  const base = local && info.lan_url ? info.lan_url : location.origin;
  const link = `${base}/storage/${s.id}`;

  const nameInput = h('input', { class: 'input', id: 's-name', value: s.name, maxlength: 100, required: true });
  const tagInput = h('input', { class: 'input', id: 's-tag', value: s.nfc_tag || '', maxlength: 100, autocomplete: 'off' });
  const atHand = h('input', { type: 'checkbox', checked: s.at_hand });
  const settings = h('form', { class: 'form' },
    h('div', { class: 'field' }, h('label', { for: 's-name' }, 'Название'), nameInput),
    h('div', { class: 'field' }, h('label', { for: 's-tag' }, 'Метка (необязательно)'), tagInput,
      h('div', { class: 'hint' }, 'Любая пометка для себя: номер или ID NFC-метки, цвет наклейки…')),
    h('label', { class: 'check' }, atHand, 'Под рукой'),
    h('div', { class: 'form-actions' },
      h('button', { type: 'submit', class: 'btn primary' }, 'Сохранить'),
      h('span', { class: 'spacer' }),
      deleteButton('Удалить хранилище', async () => {
        try {
          await api('DELETE', `/storages/${id}`);
          toast('Хранилище удалено, краски остались в каталоге', 'ok');
          location.hash = '#/storages';
        } catch (err) { toast(explain(err), 'err'); }
      })));
  settings.addEventListener('submit', async e => {
    e.preventDefault();
    try {
      await api('PATCH', `/storages/${id}`, { name: nameInput.value.trim(), nfc_tag: tagInput.value.trim() || null, at_hand: atHand.checked });
      toast('Сохранено', 'ok');
      render();
    } catch (err) {
      toast(explain(err, 'Хранилище с таким названием уже есть'), 'err');
    }
  });

  const copyBtn = h('button', { type: 'button', class: 'btn' }, 'Копировать');
  copyBtn.addEventListener('click', async () => toast(await copyText(link) ? 'Ссылка скопирована' : 'Не получилось — выделите ссылку вручную', ''));

  const pots = paints.reduce((n, p) => n + p.quantity, 0);

  mount(h('div', null,
    pageHead(s.name, '#/storages', s.at_hand ? h('span', { class: 'badge' }, 'под рукой') : null),
    h('div', { class: 'summary' }, h('span', null, `${countPaints(paints.length)} · ${countPots(pots)}`)),
    paints.length
      ? paintList(paints, { showStorage: false })
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
    h('details', { class: 'panel' }, h('summary', null, 'Название, метка, «под рукой», удаление'), settings)));
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
      deleteButton('Удалить', async () => {
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
      deleteButton('Удалить', async () => {
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
