/* A paint's page: adding a new one and editing. */

import { t } from '../i18n.js';
import { api, ApiError, copyText, countPots, explain, h, normHex, toast } from './core.js';
import {
  addPots, askAboutDuplicate, confirmDialog, deleteButton, isDuplicate, isPaintExists, locationsEditor,
  mergeDialog, pageHead, placementBody, placementSelect, setSwatch, showNotice, swatch, toggleChip,
} from './shared.js';
import { goBack } from './router.js';
import { lastManufacturer, photoRecognizer, rememberManufacturer } from './photo.js';

export async function viewPaintForm(mount, id, prefillStorage = null) {
  const [tags, storages, makers, paint, photo, info] = await Promise.all([
    api('GET', '/tags'),
    api('GET', '/storages'),
    api('GET', '/manufacturers'),
    id ? api('GET', `/paints/${id}`) : Promise.resolve(null),
    id ? Promise.resolve(null) : api('GET', '/recognize').catch(() => null),
    api('GET', '/info').catch(() => ({})),
  ]);
  const lookupOn = !!(info.lookup && info.lookup.upcitemdb);
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

  // ---- online lookup of the barcode (UPCitemdb; a setting, the request is made by the server)
  const lookupBtn = h('button', { type: 'button', class: 'icon-btn lookup-btn', title: t('Найти штрихкод в UPCitemdb'), 'aria-label': t('Найти штрихкод в UPCitemdb') }, '🔎');
  const lookupBox = h('div', { class: 'panel lookup-box', hidden: true });
  let lookupTags = [];
  function proposeMaker(text) {
    maker.value = NEW;
    newMakerBox.hidden = false;
    newMakerInput.value = text;
    newMakerInput.focus();
  }
  function lookupProblem(err) {
    const c = err instanceof ApiError && err.detail ? err.detail.code : null;
    if (c === 'lookup_limit') return t('Бесплатные запросы UPCitemdb на сегодня закончились (100 в день)');
    if (c === 'lookup_too_fast') return t('Слишком часто — UPCitemdb просит подождать минуту');
    if (c === 'lookup_disabled') return t('Поиск в UPCitemdb выключен в настройках');
    if (c === 'lookup_bad_barcode') return t('Штрихкод для поиска — 8, 12, 13 или 14 цифр');
    if (c === 'lookup_unreachable') return t('Запрос не ушёл: {0}', err.message);
    if (c === 'lookup_config') return t('Настройка прокси неверна: {0}', err.message);
    return explain(err);
  }
  /** Ask UPCitemdb about the barcode (the one given, or the one in the field) and fill empty fields. */
  async function lookupOnline(scanned) {
    if (scanned && !barcode.value.trim()) barcode.value = scanned;
    const value = (scanned || barcode.value).trim();
    if (!value) { barcode.focus(); toast(t('Сначала введите или отсканируйте штрихкод'), 'warn'); return; }
    lookupBtn.disabled = true;
    lookupBox.hidden = false;
    lookupBox.replaceChildren(h('span', { class: 'muted' }, t('Ищу {0} в UPCitemdb…', value)));
    lookupBox.scrollIntoView({ block: 'nearest' });
    try {
      const data = await api('GET', `/lookup/upcitemdb?barcode=${encodeURIComponent(value)}`);
      const left = data.remaining == null ? null
        : h('div', { class: 'muted small' }, t('Осталось запросов на сегодня: {0}', data.remaining));
      if (!data.found) {
        // replaceChildren() would print a missing part as the word "null"
        lookupBox.replaceChildren(...[h('b', null, t('UPCitemdb не знает штрихкод {0}', data.barcode)),
          h('div', { class: 'muted small' }, t('Заполните поля вручную или по фото этикетки.')), left].filter(Boolean));
        return;
      }
      const s = data.suggestion;
      const filled = [];
      lookupTags.forEach(tg => setTag(tg, false));
      lookupTags = [];
      let otherName = null;
      if (s.name && !name.value.trim()) { name.value = s.name; filled.push(t('название')); }
      else if (s.name && s.name !== name.value.trim()) otherName = s.name;
      if (s.manufacturer && (isNew || !maker.value)) {
        if (!maker.querySelector(`option[value="${s.manufacturer.id}"]`)) {
          maker.insertBefore(h('option', { value: String(s.manufacturer.id) }, s.manufacturer.name), maker.querySelector(`option[value="${NEW}"]`));
        }
        selectMaker(s.manufacturer.id);
        filled.push(t('производитель'));
      } else if (s.new_manufacturer && isNew) {
        proposeMaker(s.new_manufacturer);
      }
      if (s.code && !code.value.trim()) { code.value = s.code; filled.push(t('код')); }
      lookupTags = s.tag_ids.filter(tg => tagChips.has(tg) && !selectedTags.has(tg));
      lookupTags.forEach(tg => setTag(tg, true));
      if (lookupTags.length) filled.push(lookupTags.length === 1 ? t('тег') : t('теги'));
      const item = data.item;
      lookupBox.replaceChildren(...[
        h('b', null, filled.length ? t('Заполнено: ') + filled.join(', ') : t('Нашлось, но новых полей не заполнено')),
        h('div', { class: 'small' }, 'UPCitemdb: ', item.title || '—', item.brand ? ` · ${item.brand}` : ''),
        s.range_only ? h('div', { class: 'muted small' }, t('Это объявление на всю линейку — название и теги по нему не определить, впишите сами.')) : null,
        s.new_manufacturer && isNew
          ? h('div', { class: 'muted small' }, t('Производителя «{0}» ещё нет в списке — добавьте его или выберите другого.', s.new_manufacturer)) : null,
        otherName ? h('button', { type: 'button', class: 'btn small-btn', style: 'margin-top:6px',
          onclick: e => { name.value = otherName; e.target.remove(); } }, t('Подставить название «{0}»', otherName)) : null,
        h('div', { class: 'muted small' }, t('Проверьте поля: название из магазина бывает неточным.')),
        rawAnswer(data.barcode), left].filter(Boolean));
    } catch (err) {
      lookupBox.replaceChildren(h('b', null, t('Не получилось найти')), h('div', { class: 'muted small' }, lookupProblem(err)));
    } finally {
      lookupBtn.disabled = false;
    }
  }
  /** The answer as it came, for when a pot comes out wrong: costs no request. */
  function rawAnswer(barcode) {
    const box = h('pre', { class: 'raw-answer' });
    const copy = h('button', { type: 'button', class: 'btn small-btn', hidden: true,
      onclick: async () => toast(await copyText(box.textContent) ? t('Скопировано') : t('Не получилось скопировать'),
        'ok') }, t('Скопировать'));
    const details = h('details', { class: 'raw-details' }, h('summary', null, t('Ответ UPCitemdb')), box, copy);
    details.addEventListener('toggle', async () => {
      if (!details.open || box.textContent) return;
      box.textContent = t('Загружаю…');
      try {
        const url = `/lookup/upcitemdb/raw?barcode=${encodeURIComponent(barcode)}`;
        box.textContent = JSON.stringify(await api('GET', url), null, 1);
        copy.hidden = false;
      } catch (err) { box.textContent = explain(err); }
    }, { once: false });
    return details;
  }

  lookupBtn.addEventListener('click', () => lookupOnline());

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
      proposeMaker,
      lookup: lookupOn ? lookupOnline : null,
      setTag,
      placement: () => placementBody(placement.value),
      clearForNext() {
        name.value = ''; code.value = ''; barcode.value = ''; hexText.value = ''; setSwatch(preview, null); qty.value = '1';
        lookupTags = []; lookupBox.hidden = true;
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
          if (isPaintExists(err) && body.quantity > 0) {
            // the paint is already in the catalog (maybe in another storage): offer to add pots to it
            const where = placement.selectedOptions[0].textContent.replace(' ✋', '').trim();
            const ok = await confirmDialog({
              title: t('Такая краска уже есть в каталоге'),
              text: t('Добавить {0} → {1}?', countPots(body.quantity), where),
              confirmLabel: t('Добавить'), kind: 'primary',
            });
            if (!ok) return; // cancelled — stay on the form
            const r = await addPots({ id: err.detail.paint_id, name: body.name },
              { count: body.quantity, storage_id: body.storage_id });
            if (!r) return; // cancelled in the "at hand" question
            created = { ...r.paint, notice: r.notice };
          } else if (!isDuplicate(err)) {
            throw err;
          } else {
            const extra = await askAboutDuplicate(body.name, err.detail, body.quantity);
            if (!extra) return; // cancelled — stay on the form
            created = await api('POST', '/paints', { ...body, ...extra });
          }
        }
        rememberManufacturer(created.manufacturer_id);
        if (created.notice) showNotice(created, created.notice);
        else toast(t('«{0}» добавлена (0 банок)', created.name), 'ok');
        if (andAnother) {
          // keep manufacturer, storage and tags — usually the next pot is from the same batch
          name.value = ''; code.value = ''; barcode.value = ''; hexText.value = ''; setSwatch(preview, null); qty.value = '1';
          if (photoBlock) photoBlock.reset();
          lookupTags = []; lookupBox.hidden = true;
          // the next pot goes straight to the camera; otherwise (computer) to the name field
          if (!(photoBlock && photoBlock.startCamera())) name.focus();
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
      h('div', { class: 'field' }, h('label', { for: 'f-barcode' }, t('Штрихкод')),
        lookupOn ? h('div', { class: 'lookup-row' }, barcode, lookupBtn) : barcode)),
    lookupBox,
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
      isNew ? null : h('button', { type: 'button', class: 'btn', title: t('Слить дубликат в эту краску'), onclick: async () => {
        try {
          const merged = await mergeDialog(p);
          if (!merged) return;
          toast(t('Краски объединены'), 'ok');
          goBack();
        } catch (err) { toast(explain(err), 'err'); }
      } }, t('Объединить')),
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
