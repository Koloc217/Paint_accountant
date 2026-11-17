/* The DOM helper, small utilities and the call to the REST API under /api. */

import { LANG, localizeBuiltins, serverText, t } from '../i18n.js';
import { goToLogin } from './settings.js';

export const viewEl = document.getElementById('view');

export function h(tag, attrs, ...children) {
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

export const countPaints = n => (LANG === 'en' ? `${n} ${n === 1 ? 'paint' : 'paints'}`
  : `${n} ${plural(n, 'краска', 'краски', 'красок')}`);
export const countPots = n => (LANG === 'en' ? `${n} ${n === 1 ? 'pot' : 'pots'}`
  : `${n} ${plural(n, 'банка', 'банки', 'банок')}`);

export function debounce(fn, ms) {
  let tg;
  return (...args) => { clearTimeout(tg); tg = setTimeout(() => fn(...args), ms); };
}

export function toast(message, kind = '', action = null) {
  const el = document.getElementById('toast');
  el.replaceChildren(h('span', null, message));
  if (action) {
    el.append(h('button', {
      type: 'button', class: 'toast-action',
      onclick: async () => { el.className = ''; await action.onClick(); },
    }, action.label));
  }
  el.className = 'show ' + kind + (action ? ' has-action' : '');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.className = ''; }, action ? 8000 : kind === 'err' || kind === 'warn' ? 5000 : 2500);
}

export function normHex(value) {
  const v = (value || '').trim();
  if (!v) return '';
  const m = v.match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!m) return null;
  let d = m[1].toLowerCase();
  if (d.length === 3) d = [...d].map(c => c + c).join('');
  return '#' + d;
}

export async function copyText(text) {
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

export class ApiError extends Error {
  constructor(status, message, detail = null) { super(message); this.status = status; this.detail = detail; }
}

export async function api(method, path, body) {
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
    let msg = t('Ошибка {0}', res.status);
    if (typeof d === 'string') msg = serverText(d);
    else if (Array.isArray(d)) msg = d.map(e => (e.loc ? e.loc.slice(1).join('.') + ': ' : '') + e.msg).join('; ');
    else if (d && typeof d === 'object') msg = serverText(d.message) || msg;
    if (res.status === 401 && d && d.code === 'auth_required') goToLogin();
    throw new ApiError(res.status, msg, d && typeof d === 'object' && !Array.isArray(d) ? d : null);
  }
  return localizeBuiltins(data);
}

export function explain(err, conflictText) {
  if (!(err instanceof ApiError)) return t('Нет связи с сервером');
  if (err.status === 409 && conflictText) return conflictText;
  if (err.status === 404) return t('Не найдено — возможно, уже удалено');
  if (err.status === 422) return t('Проверьте поля: ') + err.message;
  return err.message;
}
