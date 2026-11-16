/* Adding a paint by photo: upload or live camera, barcode and label text. */

import { localizeBuiltins, serverText, t } from '../i18n.js';
import { api, ApiError, copyText, countPots, explain, h, toast } from './core.js';
import { addPots, locationsText, showNotice, swatch } from './shared.js';
import { onLeave } from './router.js';
import { goToLogin } from './settings.js';

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

export function photoRecognizer({ status, fill }) {
  const el = h('div', { class: 'photo-block' });
  if (!status || !status.available) {
    el.append(h('div', { class: 'panel muted small' },
      t('Распознавание по фото не установлено. На компьютере с приложением выполните '),
      h('code', null, (status && status.install) || 'pip install -r requirements-photo.txt'),
      t(' и перезапустите его.')));
    return { el, buttons: '', reset() {}, startCamera: () => false };
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
  // The aim is wide for a barcode and tall for a label. In label mode the snapshot is cut to the aim,
  // so the keyboard, the mat and other pots around never reach the text recognition.
  const aim = h('div', { class: 'stream-aim' });
  const frame = h('div', { class: 'stream-frame', title: t('Тап по кадру — рамка для этикетки или штрихкода') }, video, aim);
  const aimHint = h('div', { class: 'muted small' });
  let labelAim = false;
  function setLabelAim(on) {
    labelAim = on;
    aim.classList.toggle('label', on);
    aimHint.textContent = on
      ? t('Этикетка в рамке — распознаю только её. Тап по кадру вернёт рамку штрихкода.')
      : t('Тап по кадру — рамка для этикетки.');
  }
  frame.addEventListener('click', () => setLabelAim(!labelAim));
  const shutter = h('button', { type: 'button', class: 'btn primary' }, t('📸 Снимок этикетки'));
  const closeStream = h('button', { type: 'button', class: 'btn' }, t('Закрыть'));
  const streamBox = h('div', { class: 'panel stream-box', hidden: true },
    frame,
    streamStatus,
    aimHint,
    h('div', { class: 'row' }, shutter, closeStream));
  let stream = null;
  let scanning = false;
  let scanTimer = null;
  let lastCode = null;
  let detector = null;

  closeStream.addEventListener('click', () => stopStream());
  shutter.addEventListener('click', async () => {
    const blob = await frameBlob(1600, 0.9, labelAim ? aimCrop() : null);
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
    setLabelAim(false);
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

  /** The aim's area in video pixels (the video is shown with object-fit: cover), with a small margin. */
  function aimCrop() {
    const vw = video.videoWidth, vh = video.videoHeight;
    const f = frame.getBoundingClientRect();
    const a = aim.getBoundingClientRect();
    if (!vw || !f.width || !a.width) return null;
    const k = Math.max(f.width / vw, f.height / vh); // screen px per video px
    const offX = (f.width - vw * k) / 2, offY = (f.height - vh * k) / 2;
    const pad = 0.08;
    const x0 = (a.left - f.left - offX) / k - a.width / k * pad;
    const y0 = (a.top - f.top - offY) / k - a.height / k * pad;
    const x1 = (a.right - f.left - offX) / k + a.width / k * pad;
    const y1 = (a.bottom - f.top - offY) / k + a.height / k * pad;
    const sx = Math.max(0, x0), sy = Math.max(0, y0);
    const sw = Math.min(vw, x1) - sx, sh = Math.min(vh, y1) - sy;
    return sw > 0 && sh > 0 ? { sx, sy, sw, sh } : null;
  }

  async function frameBlob(maxSide, quality, crop = null) {
    if (!video.videoWidth) return null;
    const c = crop || { sx: 0, sy: 0, sw: video.videoWidth, sh: video.videoHeight };
    const k = Math.min(1, maxSide / Math.max(c.sw, c.sh));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(c.sw * k);
    canvas.height = Math.round(c.sh * k);
    canvas.getContext('2d').drawImage(video, c.sx, c.sy, c.sw, c.sh, 0, 0, canvas.width, canvas.height);
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
      setLabelAim(true);
      streamStatus.replaceChildren(
        h('b', null, t('Новый штрихкод {0}', code)),
        h('div', { class: 'muted small' }, t('Его ещё нет в каталоге. Поставьте этикетку в рамку и нажмите «Снимок этикетки» — заполню остальное.')));
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
      if (!res.ok) {
        const d = data && data.detail;
        if (res.status === 401 && d && d.code === 'auth_required') goToLogin();
        throw new ApiError(res.status, serverText(typeof d === 'string' ? d : d && d.message) || t('Ошибка {0}', res.status));
      }
      show(localizeBuiltins(data));
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
      // text far from the label (keyboard keys, the mat) is folded away, but still one tap away
      const near = chips.filter((c, i) => lines[i].kind !== 'background');
      const far = chips.filter((c, i) => lines[i].kind === 'background');
      let more = null;
      if (far.length) {
        const farBox = h('div', { class: 'tag-picker far-chips', hidden: true }, far);
        const toggle = h('button', { type: 'button', class: 'link-btn small' }, t('Ещё {0} — похоже на фон', far.length));
        toggle.addEventListener('click', () => {
          farBox.hidden = !farBox.hidden;
          toggle.textContent = farBox.hidden ? t('Ещё {0} — похоже на фон', far.length) : t('Скрыть фон');
        });
        more = h('div', { class: 'far-block' }, toggle, farBox);
      }
      parts.push(h('div', { class: 'photo-section' },
        h('b', null, t('Текст с этикетки')),
        h('div', { class: 'muted small' }, t('В название куски добавляются по порядку, повторный тап убирает. Код и производитель — замена.')),
        seg,
        near.length ? h('div', { class: 'tag-picker' }, near) : null,
        more));
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

  /** Open the camera without a tap of its own — after "+ ещё". False when there's no camera here. */
  function startCamera() {
    if (!touch) return false;
    if (mode === 'stream') { startStream(); return true; }
    // the camera app opens only right after a tap; saving takes a moment, and if the tap has
    // "expired" by then (a slow server, a duplicate question) the browser would silently ignore it
    if (navigator.userActivation && !navigator.userActivation.isActive) {
      button.focus({ preventScroll: true });
      button.scrollIntoView({ block: 'nearest' });
      return true;
    }
    cameraInput.click();
    return true;
  }

  el.append(modeSeg || '', cameraInput, input, streamBox, panel);
  const buttons = h('div', { class: 'photo-buttons' }, button, galleryButton);
  return { el, buttons, reset, startCamera };
}

/** Camera mode on this device: 'photo' or 'stream' (remembered per browser). */
export function cameraMode(set) {
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
export function lastManufacturer(makers) {
  let id = null;
  try { id = Number(localStorage.getItem('pa.lastManufacturerId')); } catch { /* storage blocked */ }
  return makers.some(m => m.id === id) ? id : null;
}

export function rememberManufacturer(id) {
  try { localStorage.setItem('pa.lastManufacturerId', String(id)); } catch { /* private mode */ }
}
