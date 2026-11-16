/* Pieces used by several screens: dialogs, paint rows, where the pots lie. */

import { t } from '../i18n.js';
import { api, ApiError, countPots, explain, h, toast } from './core.js';

export function swatch(hex, big = false) {
  return h('span', {
    class: 'swatch' + (hex ? '' : ' no-color') + (big ? ' big' : ''),
    style: hex ? `--c:${hex}` : null,
    title: hex || t('цвет не задан'),
  });
}

export function setSwatch(el, hex) {
  el.classList.toggle('no-color', !hex);
  el.style.cssText = hex ? `--c:${hex}` : '';
  el.title = hex || t('цвет не задан');
}

export function toggleChip(label, { color, pressed, plain, onToggle }) {
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
export function choiceDialog({ title, text = '', content = null, buttons }) {
  return new Promise(resolve => {
    const dlg = h('dialog', { class: 'dialog', 'aria-labelledby': 'dlg-title' });
    const close = result => { dlg.close(); dlg.remove(); resolve(result); };
    const btns = buttons.map(b => h('button', { type: 'button', class: 'btn ' + (b.kind || ''), onclick: () => close(b.value) }, b.label));
    // DOM append() would print a missing part as the word "null", so leave it out
    dlg.append(...[
      h('h3', { id: 'dlg-title' }, title),
      text ? h('p', { class: 'muted' }, text) : null,
      content,
      h('div', { class: 'dialog-actions' }, btns)].filter(Boolean));
    dlg.addEventListener('cancel', e => { e.preventDefault(); close(null); }); // Esc / back gesture
    dlg.addEventListener('click', e => { if (e.target === dlg) close(null); }); // tap outside
    document.body.append(dlg);
    dlg.showModal();
    btns[0].focus();
  });
}

/** Modal "are you sure?" window. Resolves true if the user confirms. */
export async function confirmDialog({ title, text = '', confirmLabel = t('Удалить'), kind = 'danger-solid' }) {
  return (await choiceDialog({
    title, text,
    buttons: [{ label: t('Отмена'), value: false }, { label: confirmLabel, value: true, kind }],
  })) === true;
}

/**
 * A second pot of a paint in an "at hand" storage needs a yes. Asks where it should go.
 * Resolves with extra fields for the request ({allow_duplicate} or {storage_id, allow_duplicate}),
 * or null if the user cancelled.
 */
export async function askAboutDuplicate(paintName, detail, count = 1) {
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

export const isPaintExists = err => err instanceof ApiError && err.status === 409 && err.detail && err.detail.code === 'paint_exists';

export const isDuplicate = err => err instanceof ApiError && err.status === 409 && err.detail && err.detail.code === 'duplicate_at_hand';

/** POST /paints/{id}/add, asking about a duplicate at hand when needed. Resolves null if cancelled. */
export async function addPots(paint, body = {}) {
  try {
    return await api('POST', `/paints/${paint.id}/add`, body);
  } catch (err) {
    if (!isDuplicate(err)) throw err;
    const extra = await askAboutDuplicate(paint.name, err.detail, body.count || 1);
    return extra ? api('POST', `/paints/${paint.id}/add`, { ...body, ...extra }) : null;
  }
}

/** Pick a duplicate of `paint` and fold it into this one. Resolves with the merged paint, or null. */
export async function mergeDialog(paint) {
  const found = (await api('GET', '/paints?q=' + encodeURIComponent(paint.name)))
    .filter(x => x.id !== paint.id);
  if (!found.length) { toast(t('Похожих красок не нашлось'), 'err'); return null; }
  found.sort((a, b) => (b.manufacturer_id === paint.manufacturer_id) - (a.manufacturer_id === paint.manufacturer_id));
  const label = x => [x.name, x.manufacturer, x.code || t('без кода'), locationsText(x)].join(' · ');
  const pick = h('select', { class: 'input', 'aria-label': t('Дубликат') },
    found.map(x => h('option', { value: String(x.id) }, label(x))));
  const barcodes = h('div', { class: 'field' });
  const bsel = h('select', { class: 'input', 'aria-label': t('Какой штрихкод оставить') });
  const redraw = () => {
    const other = found.find(x => x.id === Number(pick.value));
    if (paint.barcode && other.barcode && paint.barcode !== other.barcode) {
      bsel.replaceChildren(
        h('option', { value: 'keep' }, t('Этой краски: {0}', paint.barcode)),
        h('option', { value: 'from' }, t('Дубликата: {0}', other.barcode)));
      barcodes.replaceChildren(h('span', { class: 'label' }, t('Какой штрихкод оставить')), bsel);
      barcodes.hidden = false;
    } else {
      barcodes.hidden = true; // one or none — nothing to choose
    }
  };
  pick.addEventListener('change', redraw);
  redraw();
  const ok = await choiceDialog({
    title: t('Объединить с дубликатом'),
    text: t('Банки и теги дубликата перейдут сюда, сам он удалится.'),
    content: h('div', null, h('div', { class: 'field' }, pick), barcodes),
    buttons: [{ label: t('Отмена'), value: false }, { label: t('Объединить'), value: true, kind: 'primary' }],
  });
  if (ok !== true) return null;
  return api('POST', `/paints/${paint.id}/merge`,
    { from_id: Number(pick.value), barcode: barcodes.hidden ? 'keep' : bsel.value });
}

/** Delete button that asks for confirmation in a modal window. */
export function deleteButton(label, question, onConfirm) {
  const b = h('button', { type: 'button', class: 'btn danger' }, label);
  b.addEventListener('click', async () => {
    const { title, text } = typeof question === 'function' ? question() : question;
    if (!(await confirmDialog({ title, text }))) return;
    b.disabled = true;
    try { await onConfirm(); } finally { b.disabled = false; }
  });
  return b;
}

export function pageHead(title, backHref, extra) {
  return h('div', { class: 'page-head' },
    backHref ? h('a', { class: 'back', href: backHref, 'aria-label': t('Назад') }, '‹') : null,
    h('h1', null, title),
    extra);
}

const storageName = st => (st ? t('«{0}»', st.name) : t('без хранилища'));

/** "Под рукой ✋, Запас 2" — where the pots of a paint lie. */
export function locationsText(p) {
  if (!p.locations.length) return t('нет в наличии');
  return p.locations.map(l => {
    const label = l.storage ? l.storage.name + (l.storage.at_hand ? ' ✋' : '') : t('без хранилища');
    return p.locations.length > 1 || l.quantity > 1 ? `${label} ${l.quantity}` : label;
  }).join(', ');
}

/** Select "where to put pots": the storages (the default one preselected) or none. */
export function placementSelect(storages, selected, attrs = {}) {
  const def = storages.find(s => s.is_default);
  const sel = h('select', { class: 'input', ...attrs },
    storages.map(s => h('option', { value: String(s.id) }, s.name + (s.at_hand ? ' ✋' : ''))),
    h('option', { value: 'none' }, t('— без хранилища —')));
  const pick = selected != null ? selected : def ? def.id : null;
  sel.value = pick == null ? 'none' : String(pick);
  return sel;
}

/** API body part for a placement select. */
export function placementBody(value) {
  return { storage_id: value === 'none' ? null : Number(value) };
}

/** Tell where pots went / came from; offer to refill "at hand" from a reserve. */
export function showNotice(paint, notice) {
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
export const paintWatchers = new Map();
function paintChanged(paint) {
  (paintWatchers.get(paint.id) || []).forEach(fn => fn(paint));
  document.dispatchEvent(new CustomEvent('paints-changed'));
}
function watchPaint(id, fn) {
  if (!paintWatchers.has(id)) paintWatchers.set(id, []);
  paintWatchers.get(id).push(fn);
}

/** Paint rows with quick +/-. On a storage page (storageId) they count and change pots in that storage. */
/** sel: { on, ids, onPick } — while on, tapping a row picks it instead of opening it. */
export function paintList(paints, { storageId = null, sel = null } = {}) {
  return h('ul', { class: 'paint-list' }, paints.map(p => paintRow(p, storageId, sel)));
}

function paintRow(p, storageId, sel = null) {
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
    // in several storages: the list only shows the total, pots are changed on the paint's page
    const spread = !storageId && p.locations.length > 1;
    minus.hidden = plus.hidden = spread;
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

  const picking = Boolean(sel && sel.on);
  const link = h('a', { class: 'paint-link', href: `#/paint/${p.id}` },
      picking ? h('span', { class: 'pick-box', 'aria-hidden': 'true' }) : null,
      swatch(p.hex),
      h('div', { class: 'paint-info' },
        h('div', { class: 'paint-name' }, p.name),
        meta,
        p.tags.length ? h('div', { class: 'paint-tags' },
          p.tags.map(tg => h('span', { class: 'chip-mini', style: `--tc:${tg.color}` }, tg.name))) : null));
  if (picking) {
    row.classList.add('selectable');
    const mark = () => {
      row.classList.toggle('picked', sel.ids.has(p.id));
      link.setAttribute('aria-pressed', String(sel.ids.has(p.id)));
    };
    link.setAttribute('role', 'button');
    link.addEventListener('click', e => {
      e.preventDefault();
      if (sel.ids.has(p.id)) sel.ids.delete(p.id); else sel.ids.add(p.id);
      mark();
      sel.onPick();
    });
    mark();
  }
  row.append(link,
    h('div', { class: 'qty', title: storageId ? t('Банок в этом хранилище') : t('Банок всего') }, minus, value, plus));
  return row;
}

/** Where the pots of one paint lie, with −/+ per storage, "move 1 to…" and "+1 pot". */
export function locationsEditor(paint, storages) {
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

export function segButtons(label, options, current, onPick) {
  return h('div', { class: 'seg', role: 'group', 'aria-label': label }, options.map(([value, text]) => {
    const b = h('button', { type: 'button', 'aria-pressed': String(value === current) }, text);
    b.addEventListener('click', () => onPick(value));
    return b;
  }));
}
