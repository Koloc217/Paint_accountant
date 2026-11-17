/* Catalogs: manufacturers and tags. */

import { LANG, LANGS, t } from '../i18n.js';
import { api, countPaints, explain, h, toast } from './core.js';
import { confirmDialog, pageHead } from './shared.js';
import { render } from './router.js';
import { filters, resetListFilters } from './paints.js';

let catalogSection = 'makers';

export async function viewCatalogs(mount) {
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
      save: v => api('PATCH', `/tags/${tg.id}`, { name: v, lang: LANG }), // only in this language
    });
    // the name in the other language: a new tag gets the same one, it can be changed there
    const other = LANGS.filter(([code]) => code !== LANG)
      .map(([code, label]) => `${label}: ${(tg.names && tg.names[code]) || tg.name}`).join(' · ');
    const sub = [other, tg.keywords.length ? t('на этикетке: ') + tg.keywords.join(', ') : ''].filter(Boolean).join(' · ');
    return catRow({
      lead: color,
      name,
      sub: sub || null,
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
