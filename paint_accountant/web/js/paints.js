/* The paint list: search, filters, sorting, list or table, bulk actions, export. */

import { LANG, t } from '../i18n.js';
import { api, copyText, countPaints, countPots, debounce, explain, h, toast } from './core.js';
import {
  choiceDialog, confirmDialog, locationsText, paintList, paintWatchers, swatch, toggleChip,
} from './shared.js';
import { onLeave, render } from './router.js';

const SORTS = ['maker', 'name', 'qty_desc', 'qty_asc', 'new'];

/** "Remember the sort order" on this device: off by default. */
export function rememberSort(set) {
  try {
    if (set !== undefined) {
      localStorage.setItem('pa.rememberSort', set ? '1' : '0');
      if (!set) localStorage.removeItem('pa.sort');
    }
    return localStorage.getItem('pa.rememberSort') === '1';
  } catch {
    return Boolean(set);
  }
}

function savedSort() {
  if (!rememberSort()) return 'maker';
  try {
    const s = localStorage.getItem('pa.sort');
    return SORTS.includes(s) ? s : 'maker';
  } catch {
    return 'maker';
  }
}

export function saveSort(sort) {
  if (!rememberSort()) return;
  try { localStorage.setItem('pa.sort', sort); } catch { /* private mode */ }
}

/** Table view of the paint list: on wide (PC) screens only; the choice is remembered on this device. */
const wideScreen = window.matchMedia('(min-width: 900px)');
function viewMode() {
  try { return localStorage.getItem('pa.view') === 'table' ? 'table' : 'list'; } catch { return 'list'; }
}
function setViewMode(mode) {
  try { localStorage.setItem('pa.view', mode); } catch { /* private mode */ }
}
const tableOn = () => wideScreen.matches && viewMode() === 'table';

export const newFilters = () => ({ q: '', storage: '', maker: '', tags: new Set(), noTags: false, atHand: false, stock: '', sort: savedSort() });
export const filters = newFilters();          // the main paint list
export const storageFilters = new Map();      // one set per storage page, kept while you move around

function resetFiltersOf(f) {
  Object.assign(f, newFilters(), { tags: f.tags, sort: f.sort });  // the order is a view choice, not a filter
  f.tags.clear();
}

export function resetListFilters() { resetFiltersOf(filters); }

function anyFilterIn(f, storageId = null) {
  return Boolean(f.q.trim() || (!storageId && f.storage) || f.maker || f.tags.size || f.noTags || f.atHand || f.stock);
}

/**
 * The paint list as a table (PC). Column headers sort: name, manufacturer, pots.
 * sel: as for paintList — while on, a click picks the row instead of opening it.
 */
function paintTable(paints, { storageId = null, sel = null, f, onSort }) {
  const picking = Boolean(sel && sel.on);
  const head = (label, sorts) => {
    if (!sorts) return h('th', null, label);
    const at = sorts.indexOf(f.sort);
    const mark = at === -1 ? '' : f.sort === 'qty_desc' ? ' ▼' : ' ▲';
    return h('th', { class: 'sortable', 'aria-sort': at === -1 ? 'none' : f.sort === 'qty_desc' ? 'descending' : 'ascending' },
      h('button', { type: 'button', title: t('Сортировка'), onclick: () => onSort(sorts[(at + 1) % sorts.length]) }, label + mark));
  };

  const rows = paints.map(p => {
    const n = storageId
      ? ((p.locations.find(l => l.storage && l.storage.id === storageId) || {}).quantity || 0)
      : p.quantity;
    // h() leaves out null cells; DOM append() would print them as the word "null"
    const tr = h('tr', { class: 'paint-tr' + (n === 0 ? ' out' : '') },
      picking ? h('td', { class: 'pick-cell' }, h('span', { class: 'pick-box', 'aria-hidden': 'true' })) : null,
      h('td', { class: 'sw-cell' }, swatch(p.hex)),
      h('td', null, h('a', { class: 't-name', href: `#/paint/${p.id}` }, p.name)),
      h('td', null, p.manufacturer),
      h('td', { class: 'muted' }, p.code || ''),
      storageId ? null : h('td', null, locationsText(p)),
      h('td', null, h('span', { class: 'paint-tags' },
        p.tags.map(tg => h('span', { class: 'chip-mini', style: `--tc:${tg.color}` }, tg.name)))),
      h('td', { class: 'num' }, n, storageId && p.quantity > n ? h('span', { class: 'muted small' }, ' ' + t('всего {0}', p.quantity)) : null));
    if (picking) {
      tr.classList.add('selectable');
      const mark = () => tr.classList.toggle('picked', sel.ids.has(p.id));
      tr.addEventListener('click', e => {
        e.preventDefault();
        if (sel.ids.has(p.id)) sel.ids.delete(p.id); else sel.ids.add(p.id);
        mark();
        sel.onPick();
      });
      mark();
    } else {
      tr.addEventListener('click', e => { if (!e.target.closest('a')) location.hash = `#/paint/${p.id}`; });
    }
    return tr;
  });

  return h('div', { class: 'table-wrap' }, h('table', { class: 'paint-table' },
    h('thead', null, h('tr', null,
      picking ? h('th', { class: 'pick-cell' }) : null,
      h('th', { class: 'sw-cell' }),
      head(t('Название'), ['name']),
      head(t('Производитель'), ['maker']),
      head(t('Код'), null),
      storageId ? null : head(t('Где лежит'), null),
      head(t('Теги'), null),
      h('th', { class: 'num sortable', 'aria-sort': f.sort === 'qty_desc' ? 'descending' : f.sort === 'qty_asc' ? 'ascending' : 'none' },
        h('button', { type: 'button', title: t('Сортировка'), onclick: () => onSort(f.sort === 'qty_desc' ? 'qty_asc' : 'qty_desc') },
          t('Банок') + (f.sort === 'qty_desc' ? ' ▼' : f.sort === 'qty_asc' ? ' ▲' : ''))))),
    h('tbody', null, rows)));
}

/**
 * Search, filters, summary line and the paint list. Used by the main list (all storages)
 * and by a storage page (storageId: only paints in it; +/- and counts work in that storage).
 */
export function paintBrowser({ f, storageId = null, tags, storages, makers, emptyText, emptyBig = null, onFabHref = null }) {
  // forget filters that point to deleted tags/storages/manufacturers
  const tagIds = new Set(tags.map(tg => tg.id));
  for (const id of [...f.tags]) if (!tagIds.has(id)) f.tags.delete(id);
  if (f.storage && f.storage !== 'none' && !storages.some(s => String(s.id) === f.storage)) f.storage = '';
  if (f.maker && !makers.some(m => String(m.id) === f.maker)) f.maker = '';

  const listBox = h('div');
  const summary = h('div', { class: 'summary' });
  const bar = h('div', { class: 'bulk-bar', hidden: true });
  let current = [];
  // picking several paints for one action (move, tags)
  const sel = { on: false, ids: new Set(), onPick: () => drawBar() };

  function drawList() {
    if (current.length) {
      listBox.replaceChildren(tableOn()
        ? paintTable(current, { storageId, sel, f, onSort: sort => { f.sort = sort; saveSort(sort); sortSel.value = sort; refresh(); } })
        : paintList(current, { storageId, sel }));
    }
    else if (anyFilterIn(f, storageId)) listBox.replaceChildren(h('div', { class: 'empty' }, t('Ничего не нашлось')));
    else listBox.replaceChildren(emptyBig ? emptyBig() : h('div', { class: 'empty' }, emptyText));
  }

  function selecting(on) {
    sel.on = on;
    sel.ids.clear();
    document.body.classList.toggle('selecting', on);
    drawList();
    drawBar();
    updateSummary();
  }
  onLeave(() => document.body.classList.remove('selecting'));

  async function bulk(body, okText) {
    try {
      const r = await api('POST', '/paints/bulk', { paint_ids: [...sel.ids], ...body });
      toast(r.changed ? okText(r.changed) : t('Ничего не изменилось'), r.changed ? 'ok' : '');
    } catch (e) {
      toast(explain(e), 'err');
    }
    await refresh();
  }

  function drawBar() {
    bar.hidden = !sel.on;
    if (!sel.on) { bar.replaceChildren(); return; }
    const n = sel.ids.size;
    const allPicked = current.length > 0 && current.every(p => sel.ids.has(p.id));
    const pickAll = h('button', { type: 'button', class: 'btn ghost', disabled: !current.length, onclick: () => {
      if (allPicked) sel.ids.clear(); else current.forEach(p => sel.ids.add(p.id));
      drawList();
      drawBar();
    } }, allPicked ? t('Снять все') : t('Выбрать все'));

    const targets = storages.filter(s => s.id !== storageId);
    const moveSel = h('select', { class: 'input', disabled: !n, 'aria-label': t('Переложить выбранные') },
      h('option', { value: '' }, t('Переложить в…')),
      targets.map(s => h('option', { value: String(s.id) }, s.name + (s.at_hand ? ' ✋' : ''))),
      h('option', { value: 'none' }, t('без хранилища')));
    moveSel.addEventListener('change', async () => {
      const v = moveSel.value;
      if (!v) return;
      const to = v === 'none' ? null : Number(v);
      const ok = await confirmDialog({
        title: to === null
          ? t('Оставить {0} без хранилища?', countPaints(n))
          : t('Переложить {0} в «{1}»?', countPaints(n), targets.find(s => s.id === to).name),
        text: storageId ? t('Переедут банки из этого хранилища.') : t('Переедут все банки этих красок, из всех хранилищ.'),
        confirmLabel: t('Переложить'),
        kind: 'primary',
      });
      if (!ok) { moveSel.value = ''; return; }
      const body = { action: 'move', to_storage_id: to };
      if (storageId) body.from_storage_id = storageId;
      await bulk(body, k => t('Переложено красок: {0}', k));
    });

    const tagSel = (action, label, okText) => {
      const el = h('select', { class: 'input', disabled: !n || !tags.length, 'aria-label': label },
        h('option', { value: '' }, label),
        tags.map(tg => h('option', { value: String(tg.id) }, tg.name)));
      el.addEventListener('change', () => {
        if (el.value) bulk({ action, tag_ids: [Number(el.value)] }, k => okText(k, tags.find(tg => tg.id === Number(el.value)).name));
      });
      return el;
    };

    bar.replaceChildren(
      h('div', { class: 'bulk-head' },
        h('b', null, n ? t('Выбрано: {0}', n) : t('Отметьте краски')),
        pickAll,
        h('button', { type: 'button', class: 'btn', onclick: () => selecting(false) }, t('Готово'))),
      h('div', { class: 'bulk-actions' },
        moveSel,
        tagSel('add_tags', t('+ тег…'), (k, name) => t('Тег «{1}» добавлен: {0}', k, name)),
        tagSel('remove_tags', t('− тег…'), (k, name) => t('Тег «{1}» снят: {0}', k, name))));
  }

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

  const sortSel = h('select', { class: 'input', 'aria-label': t('Сортировка'), onchange: e => { f.sort = e.target.value; saveSort(f.sort); refresh(); } },
    h('option', { value: 'maker' }, t('По производителю')),
    h('option', { value: 'name' }, t('По названию')),
    h('option', { value: 'qty_desc' }, t('Больше банок')),
    h('option', { value: 'qty_asc' }, t('Меньше банок')),
    h('option', { value: 'new' }, t('Недавно добавленные')));
  sortSel.value = f.sort;

  // "no tags" and a tag are opposites: turning one on turns the other off
  const noTagsChip = toggleChip(t('🏷 Без тегов'), {
    plain: true,
    pressed: f.noTags,
    onToggle: v => { f.noTags = v; if (v && f.tags.size) { f.tags.clear(); render(); } else refresh(); },
  });
  const chips = h('div', { class: 'chips-scroll' },
    storageId ? null : toggleChip(t('✋ Под рукой'), { plain: true, pressed: f.atHand, onToggle: v => { f.atHand = v; refresh(); } }),
    tags.length ? noTagsChip : null,
    tags.map(tg => toggleChip(tg.name, {
      color: tg.color,
      pressed: f.tags.has(tg.id),
      onToggle: v => {
        v ? f.tags.add(tg.id) : f.tags.delete(tg.id);
        if (v && f.noTags) { f.noTags = false; noTagsChip.setAttribute('aria-pressed', 'false'); }
        refresh();
      },
    })));

  const here = p => (storageId
    ? ((p.locations.find(l => l.storage && l.storage.id === storageId) || {}).quantity || 0)
    : p.quantity);

  function updateSummary() {
    const pots = current.reduce((n, p) => n + here(p), 0);
    summary.replaceChildren(h('span', null, `${countPaints(current.length)} · ${countPots(pots)}`));
    const buttons = h('span', { class: 'summary-actions' });
    const viewButton = (mode, label) => h('button', {
      type: 'button', 'aria-pressed': String((tableOn() ? 'table' : 'list') === mode),
      onclick: () => { setViewMode(mode); applyView(); },
    }, label);
    // the choice only exists on wide screens (hidden by CSS on a phone)
    buttons.append(h('span', { class: 'view-toggle', role: 'group', 'aria-label': t('Вид списка') },
      viewButton('list', t('Список')), viewButton('table', t('Таблица'))));
    if (anyFilterIn(f, storageId)) {
      buttons.append(h('button', { type: 'button', onclick: () => { resetFiltersOf(f); render(); } }, t('Сбросить фильтры')));
    }
    if (current.length && !sel.on) {
      buttons.append(h('button', { type: 'button', onclick: () => selecting(true) }, t('Выбрать')));
      buttons.append(h('button', { type: 'button', onclick: () => exportDialog(filterParams(), anyFilterIn(f, storageId)) }, t('Выгрузить')));
    }
    summary.append(buttons);
  }

  function filterParams() {
    const params = new URLSearchParams();
    if (f.q.trim()) params.set('q', f.q.trim());
    if (storageId) params.set('storage_id', storageId);
    else if (f.storage === 'none') params.set('no_storage', 'true');
    else if (f.storage) params.set('storage_id', f.storage);
    if (f.maker) params.set('manufacturer_id', f.maker);
    if (f.noTags) params.set('no_tags', 'true');
    else f.tags.forEach(id => params.append('tag_id', id));
    if (f.atHand && !storageId) params.set('at_hand', 'true');
    if (f.stock && !storageId) params.set('in_stock', f.stock === 'in' ? 'true' : 'false');
    return params;
  }

  let seq = 0;
  async function refresh() {
    const mySeq = ++seq;
    const params = filterParams();
    if (f.sort && f.sort !== 'maker') params.set('sort', f.sort);
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
    // a filter or a move can hide picked paints: they stop being picked
    const visible = new Set(paints.map(p => p.id));
    for (const id of [...sel.ids]) if (!visible.has(id)) sel.ids.delete(id);
    updateSummary();
    drawList();
    drawBar();
  }

  function applyView() {
    document.body.classList.toggle('wide-table', tableOn());
    drawList();
    updateSummary();
  }
  document.body.classList.toggle('wide-table', tableOn());
  wideScreen.addEventListener('change', applyView);
  onLeave(() => {
    wideScreen.removeEventListener('change', applyView);
    document.body.classList.remove('wide-table');
  });

  // quick +/- changes totals in the summary line
  document.addEventListener('paints-changed', updateSummary);
  onLeave(() => document.removeEventListener('paints-changed', updateSummary));

  const selects = [storageSel, stockSel, makerSel, sortSel].filter(Boolean);
  const el = h('div', null,
    h('div', { class: 'filters' }, search, h('div', { class: 'filter-row' }, selects), chips),
    summary,
    listBox,
    bar);
  return { el, refresh };
}

/** Export the listed paints as Markdown: short (for a chatbot) or full; copy or download. */
async function exportDialog(params, filtered) {
  const level = (value, title, hint, checked) => h('label', { class: 'export-level' },
    h('input', { type: 'radio', name: 'export-level', value, checked }),
    h('span', null, h('b', null, title), h('br'), h('span', { class: 'muted' }, hint)));
  const form = h('div', { class: 'export-levels' },
    level('short', t('Кратко — для нейросети'),
      t('Что есть в наличии: производитель, название, код, цвет, теги. Можно отдать нейросети и спросить, чем заменить нужную краску.'), true),
    level('full', t('Подробно'),
      t('Плюс хранилища с количеством банок, штрихкоды, сводка по хранилищам и закончившиеся краски.'), false));
  const choice = await choiceDialog({
    title: t('Выгрузить список'),
    text: filtered ? t('Выгружается то, что выбрано фильтрами.') : '',
    content: form,
    buttons: [
      { label: t('Копировать'), value: 'copy', kind: 'primary' },
      { label: t('Скачать .md'), value: 'download' },
      { label: t('Отмена'), value: null },
    ],
  });
  if (!choice) return;
  const q = new URLSearchParams(params);
  q.set('level', form.querySelector('input:checked').value);
  q.set('lang', LANG);
  if (choice === 'download') {
    q.set('download', 'true');
    const a = h('a', { href: '/api/export/paints.md?' + q, download: '' });
    document.body.append(a);
    a.click();
    a.remove();
    return;
  }
  try {
    const res = await fetch('/api/export/paints.md?' + q);
    if (!res.ok) throw new Error(res.statusText);
    toast(await copyText(await res.text()) ? t('Список скопирован') : t('Не получилось скопировать — скачайте файл'), '');
  } catch (e) {
    toast(explain(e), 'err');
  }
}

export async function viewPaints(mount) {
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
