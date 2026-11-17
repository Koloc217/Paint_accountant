/* Statistics. */

import { t } from '../i18n.js';
import { api, countPaints, countPots, h } from './core.js';
import { pageHead } from './shared.js';

export async function viewStats(mount) {
  const d = await api('GET', '/stats');
  const tot = d.totals;

  const tile = (value, label) => h('div', { class: 'stat-tile' },
    h('div', { class: 'stat-value' }, value), h('div', { class: 'muted small' }, label));

  /** Horizontal bars; an item with href is a link. */
  const bars = (items, { value, label, note, href = null, color = null }) => {
    const max = Math.max(1, ...items.map(value));
    return h('div', { class: 'bars' }, items.map(it => h(href ? 'a' : 'div', { class: 'bar-row', href: href ? href(it) : null },
      h('span', { class: 'bar-label' }, label(it)),
      h('span', { class: 'bar-track' }, h('span', { class: 'bar-fill', style: `width:${Math.round(100 * value(it) / max)}%` + (color ? `;background:${color(it)}` : '') })),
      h('span', { class: 'bar-value' }, note ? note(it) : value(it)))));
  };
  const section = (title, ...body) => h('div', null, h('h2', null, title), h('div', { class: 'panel' }, ...body));
  const pct = n => (tot.pots ? Math.round(100 * n / tot.pots) : 0);

  // where the pots lie
  const places = d.storages.map(s => ({ label: (s.at_hand ? '✋ ' : '📦 ') + s.name, pots: s.pots, href: `#/storage/${s.id}` }));
  if (d.without_storage.pots) places.push({ label: t('без хранилища'), pots: d.without_storage.pots, href: null });
  const where = section(t('Где лежат банки'),
    places.length ? bars(places, { value: x => x.pots, label: x => x.label, href: x => x.href, note: x => `${x.pots} · ${pct(x.pots)}%` }) : null,
    d.empty_storages ? h('p', { class: 'muted small', style: 'margin:10px 0 0' }, t('Пустых хранилищ: {0}', d.empty_storages)) : null);

  // one paint, several pots
  const rep = d.repeated;
  const repeats = section(t('Несколько банок одной краски'),
    rep.paints
      ? h('p', { class: 'muted small', style: 'margin:0 0 10px' }, t('{0} с несколькими банками, лишних банок: {1}. В нескольких хранилищах сразу: {2}.', countPaints(rep.paints), rep.extra_pots, rep.spread))
      : h('p', { class: 'muted', style: 'margin:0' }, t('Повторов нет')),
    rep.top.length ? bars(rep.top, {
      value: x => x.pots, label: x => `${x.name} · ${x.manufacturer}`, href: x => `#/paint/${x.id}`,
      note: x => `×${x.pots}`, color: x => x.hex || null,
    }) : null);

  // probably the same paint entered twice
  const twins = section(t('Возможные дубликаты записей'),
    h('p', { class: 'muted small', style: 'margin:0 0 10px' }, t('Одинаковые производитель и название, но отдельные записи (код, штрихкод). Откройте краску и нажмите «Объединить».')),
    d.possible_duplicates.length
      ? h('div', { class: 'card-list' }, d.possible_duplicates.map(g => h('div', { class: 'twin' },
        h('div', { class: 'name' }, `${g.name} · ${g.manufacturer}`),
        h('div', { class: 'twin-items' }, g.paints.map(x => h('a', { class: 'chip plain', href: `#/paint/${x.id}` },
          `${x.code || t('без кода')} · ${countPots(x.pots)}${x.barcode ? ' · ' + x.barcode : ''}`))))))
      : h('p', { class: 'muted', style: 'margin:0' }, t('Не нашлось')),
    d.possible_duplicates_total > d.possible_duplicates.length
      ? h('p', { class: 'muted small', style: 'margin:10px 0 0' }, t('и ещё {0}', d.possible_duplicates_total - d.possible_duplicates.length)) : null);

  const makers = d.manufacturers.length ? section(t('По производителям'), bars(d.manufacturers, {
    value: x => x.pots, label: x => x.name, note: x => `${countPaints(x.paints)} · ${countPots(x.pots)}`,
  })) : null;

  const tags = d.tags.length ? section(t('По тегам'), bars(d.tags, {
    value: x => x.paints, label: x => x.name, color: x => x.color,
  })) : null;

  const tidy = d.tidy;
  const tidySection = tot.paints ? section(t('Навести порядок'), h('div', { class: 'muted' },
    [t('без тега: {0}', tidy.no_tag), t('без цвета: {0}', tidy.no_color), t('без штрихкода: {0}', tidy.no_barcode)]
      .map(x => h('div', null, x)))) : null;

  mount(h('div', null, pageHead(t('Статистика'), null),
    h('div', { class: 'stat-grid' },
      tile(tot.paints, t('красок')), tile(tot.pots, t('банок')),
      tile(tot.storages, t('хранилищ')), tile(tot.out_of_stock, t('закончились'))),
    where, repeats, twins, makers, tags, tidySection));
}
