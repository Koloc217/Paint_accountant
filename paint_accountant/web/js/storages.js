/* Storages: the list and a storage's own page. */

import { LANG, LANGS, t } from '../i18n.js';
import { api, copyText, countPaints, countPots, explain, h, toast } from './core.js';
import { deleteButton, pageHead } from './shared.js';
import { render } from './router.js';
import { newFilters, paintBrowser, storageFilters } from './paints.js';
import { getLabelSize, qrImg, saveImageButton, storageLink } from './labels.js';

export async function viewStorages(mount) {
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

export async function viewStorage(mount, id) {
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
    h('div', { class: 'field' }, h('label', { for: 's-name' }, t('Название')), nameInput,
      h('div', { class: 'hint' }, LANGS.filter(([code]) => code !== LANG)
        .map(([code, label]) => `${label}: ${(s.names && s.names[code]) || s.name}`).join(' · '))),
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
        name: nameInput.value.trim(), lang: LANG, nfc_tag: tagInput.value.trim() || null, at_hand: atHand.checked,
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
        await api('PATCH', `/storages/${s.id}`, { name: v, lang: LANG }); // only in this language
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
