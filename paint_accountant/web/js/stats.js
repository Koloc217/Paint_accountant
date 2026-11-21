/* Statistics. */

import { t } from '../i18n.js';
import { api, countPaints, countPots, h } from './core.js';
import { onLeave } from './router.js';
import { pageHead } from './shared.js';

export async function viewStats(mount) {
  document.body.classList.add('wide-stats');
  onLeave(() => document.body.classList.remove('wide-stats'));

  const d = await api('GET', '/stats');
  const tot = d.totals;

  const tile = (value, label, sub = null, title = null) => h('div', { class: 'stat-tile', title: title || null },
    h('div', { class: 'stat-value' }, value),
    h('div', { class: 'muted small' }, label),
    sub ? h('div', { class: 'stat-sub' }, sub) : null);

  /** Horizontal bars; an item with href is a link. */
  const bars = (items, { value, label, note, href = null, color = null }) => {
    const max = Math.max(1, ...items.map(value));
    return h('div', { class: 'bars' }, items.map(it => h(href ? 'a' : 'div', { class: 'bar-row', href: href ? href(it) : null },
      h('span', { class: 'bar-label' }, label(it)),
      h('span', { class: 'bar-track' }, h('span', { class: 'bar-fill', style: `width:${Math.round(100 * value(it) / max)}%` + (color ? `;background:${color(it)}` : '') })),
      h('span', { class: 'bar-value' }, note ? note(it) : value(it)))));
  };
  const section = (title, ...body) => h('div', { class: 'stat-block' }, h('h2', null, title), h('div', { class: 'panel' }, ...body));
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

  // Color palette / spectrum / wheel
  let colorSection = null;
  if (d.colors && d.colors.total_with_color) {
    const col = d.colors;
    const totColor = col.total_with_color;

    // SVG donut wheel
    const r = 44;
    const c = 2 * Math.PI * r;
    let offset = 0;
    const slices = col.groups.map(g => {
      const len = (g.paints / totColor) * c;
      const el = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      el.setAttribute('cx', '60');
      el.setAttribute('cy', '60');
      el.setAttribute('r', String(r));
      el.setAttribute('fill', 'transparent');
      el.setAttribute('stroke', g.color);
      el.setAttribute('stroke-width', '18');
      el.setAttribute('stroke-dasharray', `${len.toFixed(2)} ${(c - len).toFixed(2)}`);
      el.setAttribute('stroke-dashoffset', `${(-offset).toFixed(2)}`);
      el.innerHTML = `<title>${t(g.name)}: ${countPaints(g.paints)} (${Math.round(100 * g.paints / totColor)}%)</title>`;
      offset += len;
      return el;
    });

    const svgWheel = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svgWheel.setAttribute('class', 'wheel-svg');
    svgWheel.setAttribute('viewBox', '0 0 120 120');
    const gRotate = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    gRotate.setAttribute('transform', 'rotate(-90 60 60)');
    slices.forEach(s => gRotate.appendChild(s));
    svgWheel.appendChild(gRotate);

    const txtCount = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    txtCount.setAttribute('x', '60');
    txtCount.setAttribute('y', '56');
    txtCount.setAttribute('text-anchor', 'middle');
    txtCount.setAttribute('class', 'wheel-text-count');
    txtCount.textContent = String(totColor);
    svgWheel.appendChild(txtCount);

    const txtLbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    txtLbl.setAttribute('x', '60');
    txtLbl.setAttribute('y', '72');
    txtLbl.setAttribute('text-anchor', 'middle');
    txtLbl.setAttribute('class', 'wheel-text-lbl');
    txtLbl.textContent = t('с цветом');
    svgWheel.appendChild(txtLbl);

    // Spectrum strip
    const spectrumBar = h('div', { class: 'spectrum-bar' },
      col.groups.map(g => h('div', {
        class: 'spectrum-slice',
        style: `width:${(100 * g.paints / totColor).toFixed(2)}%;background:${g.color}`,
        title: `${t(g.name)}: ${countPaints(g.paints)} (${Math.round(100 * g.paints / totColor)}%)`,
      })));

    // Group rows with real paint swatches
    const groupRows = h('div', { class: 'palette-groups' },
      col.groups.map(g => h('div', { class: 'color-group-row' },
        h('span', { class: 'color-dot', style: `background:${g.color}` }),
        h('span', { class: 'color-name' }, t(g.name)),
        h('span', { class: 'color-count' }, `${g.paints} · ${Math.round(100 * g.paints / totColor)}%`),
        h('div', { class: 'color-swatches' },
          g.samples.map(p => h('a', {
            class: 'swatch-dot',
            href: `#/paint/${p.id}`,
            style: `background:${p.hex}`,
            title: `${p.name} · ${p.manufacturer}${p.code ? ' · ' + p.code : ''}`,
          }))))));

    colorSection = section(t('Цветовая палитра'),
      h('div', { class: 'palette-layout' },
        spectrumBar,
        h('div', { class: 'palette-body' },
          h('div', { class: 'wheel-wrap' }, svgWheel),
          groupRows),
        col.without_color
          ? h('p', { class: 'muted small', style: 'margin:4px 0 0' }, t('Без цвета: {0} красок', col.without_color))
          : null));
  }

  const tidy = d.tidy || {};
  const tidyItems = [
    tidy.no_tag ? t('Без тега: {0}', tidy.no_tag) : null,
    tidy.no_color ? t('Без цвета: {0}', tidy.no_color) : null,
    tidy.no_barcode ? t('Без штрихкода: {0}', tidy.no_barcode) : null,
  ].filter(Boolean);

  const tidyTile = tidyItems.length
    ? h('div', { class: 'stat-tile stat-tile-tidy' + (tidyItems.length >= 3 ? ' many' : ''), title: tidyItems.join('\n') },
        tidyItems.map(txt => h('div', { class: 'stat-tidy-line' }, txt)))
    : h('div', { class: 'stat-tile', title: t('всё в порядке') },
        h('div', { class: 'stat-value', style: 'color: var(--accent);' }, '✓'),
        h('div', { class: 'muted small' }, t('всё в порядке')));

  const blocks = [where, makers, colorSection, tags, repeats, twins].filter(Boolean);
  const sections = h('div', { class: 'stat-blocks' }, ...blocks);

  mount(h('div', { class: 'stats-view' }, pageHead(t('Статистика'), null),
    h('div', { class: 'stat-grid' },
      tile(tot.paints, t('красок')),
      tile(tot.pots, t('банок')),
      tile(tot.storages, t('хранилищ')),
      tidyTile),
    sections));
}
