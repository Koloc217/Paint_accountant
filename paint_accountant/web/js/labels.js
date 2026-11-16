/* QR labels for storages: a printable sheet and a PNG at 300 dpi. */

import { t } from '../i18n.js';
import { api, ApiError, h, toast } from './core.js';
import { pageHead } from './shared.js';

/** Link to a storage that works from a phone: the LAN address if this page was opened as localhost. */
export function storageLink(info, id) {
  const local = ['localhost', '127.0.0.1', '::1', '[::1]'].includes(location.hostname);
  const base = local && info.lan_url ? info.lan_url : location.origin;
  return `${base}/storage/${id}`;
}

export function qrImg(link, cls = 'qr') {
  const img = h('img', { class: cls, alt: t('QR-код'), src: `/api/qr.svg?data=${encodeURIComponent(link)}` });
  img.addEventListener('error', () => img.replaceWith(h('div', { class: cls + ' qr-missing muted small' },
    t('QR не получился — обновите зависимости: pip install -r requirements.txt'))));
  return img;
}

const LABEL_SIZES = [['s', t('3 см')], ['m', t('4 см')], ['l', t('5,5 см')]];
export const getLabelSize = () => {
  let size = null;
  try { size = localStorage.getItem('pa.labelSize'); } catch { /* storage blocked */ }
  return LABEL_SIZES.some(([k]) => k === size) ? size : 'm';  // a value from an older version -> default
};

// the same geometry as the printed sheet (style.css): QR side in mm for each size
const LABEL_QR_MM = { s: 22, m: 30, l: 44 };
const IMAGE_DPI = 300;

/** PNG of labels [{name, link}] at real size: 300 dpi, written into the file so it prints 1:1. */
async function labelsImage(items, size) {
  const px = mm => Math.round(mm * IMAGE_DPI / 25.4);
  const qr = LABEL_QR_MM[size] || 30;
  const w = Math.max(qr + 8, 30), padX = 4, padY = 3, gap = 1.5;
  const fontMm = qr * 0.11 + 5 * 25.4 / 72, lineMm = fontMm * 1.15;
  const cols = Math.max(1, Math.min(items.length, Math.floor(190 / w)));  // fits an A4 page

  const codes = await Promise.all(items.map(async it => {
    const res = await fetch(`/api/qr.svg?data=${encodeURIComponent(it.link)}`);
    if (!res.ok) throw new ApiError(res.status, t('QR не получился — обновите зависимости: pip install -r requirements.txt'));
    const url = URL.createObjectURL(await res.blob());
    const img = new Image();
    await new Promise((ok, fail) => { img.onload = ok; img.onerror = fail; img.src = url; });
    return { img, url };
  }));

  const ctx0 = document.createElement('canvas').getContext('2d');
  const font = `600 ${px(fontMm)}px system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`;
  ctx0.font = font;
  const wrap = text => {  // break the name into lines that fit the label
    const lines = [];
    let line = '';
    for (const word of text.split(/\s+/)) {
      const tryLine = line ? `${line} ${word}` : word;
      if (!line || ctx0.measureText(tryLine).width <= px(w - 2 * padX)) line = tryLine;
      else { lines.push(line); line = word; }
    }
    if (line) lines.push(line);
    return lines;
  };
  const texts = items.map(it => wrap(it.name));
  const rows = [];
  for (let i = 0; i < items.length; i += cols) rows.push(texts.slice(i, i + cols));
  const rowH = rows.map(r => padY * 2 + qr + gap + Math.max(...r.map(l => l.length)) * lineMm);

  const canvas = document.createElement('canvas');
  canvas.width = px(w * cols) + 2;
  canvas.height = px(rowH.reduce((a, b) => a + b, 0)) + 2;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  let y = 0;
  rows.forEach((row, r) => {
    row.forEach((lines, c) => {
      const i = r * cols + c, x = c * w;
      ctx.strokeStyle = '#999';
      ctx.lineWidth = 2;
      ctx.setLineDash([px(1.2), px(0.8)]);
      ctx.strokeRect(px(x) + 1, px(y) + 1, px(w), px(rowH[r]));
      ctx.drawImage(codes[i].img, px(x + (w - qr) / 2) + 1, px(y + padY) + 1, px(qr), px(qr));
      ctx.fillStyle = '#000';
      ctx.font = font;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      lines.forEach((ln, k) => ctx.fillText(ln, px(x + w / 2) + 1, px(y + padY + qr + gap + k * lineMm) + 1));
    });
    y += rowH[r];
  });
  codes.forEach(c => URL.revokeObjectURL(c.url));
  const blob = await new Promise(ok => canvas.toBlob(ok, 'image/png'));
  return withDpi(new Uint8Array(await blob.arrayBuffer()), IMAGE_DPI);
}

/** Put a pHYs chunk (pixels per metre) into a PNG, so image viewers print it at real size. */
function withDpi(png, dpi) {
  const ppm = Math.round(dpi / 0.0254);
  const chunk = new Uint8Array(21);
  const view = new DataView(chunk.buffer);
  view.setUint32(0, 9);
  chunk.set([0x70, 0x48, 0x59, 0x73], 4);  // "pHYs"
  view.setUint32(8, ppm);
  view.setUint32(12, ppm);
  chunk[16] = 1;  // unit: metre
  view.setUint32(17, crc32(chunk.subarray(4, 17)));
  const at = 33;  // right after the signature (8) and IHDR (25)
  const out = new Uint8Array(png.length + chunk.length);
  out.set(png.subarray(0, at), 0);
  out.set(chunk, at);
  out.set(png.subarray(at), at + chunk.length);
  return new Blob([out], { type: 'image/png' });
}

function crc32(bytes) {
  let c = ~0;
  for (const b of bytes) {
    c ^= b;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xEDB88320 & -(c & 1));
  }
  return ~c >>> 0;
}

// browsers may replace a non-Latin download name with "download", so names are transliterated
const TRANSLIT = Object.fromEntries('а:a б:b в:v г:g д:d е:e ё:e ж:zh з:z и:i й:j к:k л:l м:m н:n о:o п:p р:r с:s т:t у:u ф:f х:h ц:c ч:ch ш:sh щ:sch ъ: ы:y ь: э:e ю:yu я:ya'
  .split(' ').map(pair => pair.split(':')));

function asciiFileName(name) {
  const latin = [...name].map(ch => {
    const lower = ch.toLowerCase();
    if (!(lower in TRANSLIT)) return ch;
    const tr = TRANSLIT[lower];
    return ch !== lower ? tr.charAt(0).toUpperCase() + tr.slice(1) : tr;
  }).join('');
  return latin.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '') || 'qr';
}

/** «💾 Сохранить картинку»: downloads labelsImage() of getItems() at getSize(). */
export function saveImageButton(getItems, getSize, fileName, showSize = false) {
  const label = () => (showSize ? `${t('💾 Сохранить картинку')} · ${LABEL_SIZES.find(([k]) => k === getSize())[1]}` : t('💾 Сохранить картинку'));
  const b = h('button', { type: 'button', class: 'btn', id: 'save-image' }, label());
  b.addEventListener('click', async () => {
    const items = getItems();
    if (!items.length) { toast(t('Не выбрано ни одного хранилища'), 'err'); return; }
    b.disabled = true;
    try {
      const blob = await labelsImage(items, getSize());
      const a = h('a', { href: URL.createObjectURL(blob), download: `${asciiFileName(typeof fileName === 'function' ? fileName() : fileName)}.png` });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    } catch (err) {
      toast(err instanceof ApiError ? err.message : t('Не получилось сохранить картинку'), 'err');
    } finally {
      b.disabled = false;
    }
  });
  return b;
}

export async function viewLabels(mount, preselected) {
  const [storages, info] = await Promise.all([api('GET', '/storages'), api('GET', '/info').catch(() => ({}))]);
  const chosen = new Set(preselected ? preselected.filter(id => storages.some(s => s.id === id)) : storages.map(s => s.id));
  let size = getLabelSize();

  const sheet = h('div', { class: 'label-sheet' });
  const drawSheet = () => {
    sheet.className = `label-sheet size-${size}`;
    const picked = storages.filter(s => chosen.has(s.id));
    sheet.replaceChildren(...(picked.length
      ? picked.map(s => h('div', { class: 'qr-label' },
        qrImg(storageLink(info, s.id), 'qr-label-code'),
        h('div', { class: 'qr-label-name' }, s.name)))
      : [h('div', { class: 'empty no-print' }, t('Отметьте хранилища, для которых нужны метки'))]));
    count.textContent = picked.length ? t('Меток: {0}', picked.length) : '';
  };

  const count = h('span', { class: 'muted small' });
  const checks = storages.map(s => {
    const box = h('input', { type: 'checkbox', checked: chosen.has(s.id) });
    box.addEventListener('change', () => { box.checked ? chosen.add(s.id) : chosen.delete(s.id); drawSheet(); });
    return h('label', { class: 'check' }, box, s.name);
  });
  const all = h('button', { type: 'button', class: 'btn small' }, t('Все'));
  const none = h('button', { type: 'button', class: 'btn small' }, t('Ни одного'));
  const setAll = on => {
    storages.forEach(s => (on ? chosen.add(s.id) : chosen.delete(s.id)));
    checks.forEach(l => { l.querySelector('input').checked = on; });
    drawSheet();
  };
  all.addEventListener('click', () => setAll(true));
  none.addEventListener('click', () => setAll(false));

  const seg = h('div', { class: 'seg' }, LABEL_SIZES.map(([key, label]) => {
    const b = h('button', { type: 'button', 'aria-pressed': String(key === size) }, label);
    b.addEventListener('click', () => {
      size = key;
      try { localStorage.setItem('pa.labelSize', key); } catch { /* private mode */ }
      seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
      drawSheet();
    });
    return b;
  }));

  const printBtn = h('button', { type: 'button', class: 'btn primary' }, t('🖨 Печать'));
  printBtn.addEventListener('click', () => {
    if (!chosen.size) { toast(t('Не выбрано ни одного хранилища'), 'err'); return; }
    window.print();
  });

  const base = storageLink(info, 0).replace(/\/storage\/0$/, '');
  drawSheet();
  mount(h('div', null,
    h('div', { class: 'no-print' },
      pageHead(t('QR-метки'), '#/storages'),
      h('div', { class: 'panel' },
        h('div', { class: 'field' }, h('label', null, t('Ширина метки')), seg),
        h('div', { class: 'field' },
          h('label', null, t('Хранилища '), h('span', { class: 'label-picks' }, all, none)),
          h('div', { class: 'label-checks' }, checks)),
        h('div', { class: 'row', style: 'align-items:center' }, printBtn,
          saveImageButton(() => storages.filter(s => chosen.has(s.id)).map(s => ({ name: s.name, link: storageLink(info, s.id) })),
            () => size, () => (chosen.size === 1 ? `qr-${storages.find(s => chosen.has(s.id)).name}` : 'qr-labels')),
          count)),
      h('p', { class: 'muted small' },
        t('Метки ведут на адрес '), h('code', null, base), t('. Если у компьютера сменится IP, напечатанные метки перестанут открываться — '),
        t('закрепите за ним адрес в настройках роутера (резервирование DHCP). '),
        t('Печатайте в масштабе 100% («Фактический размер»), режьте по пунктиру, сверху можно заклеить скотчем.')),
      h('h2', null, t('Предпросмотр'))),
    sheet));
}
