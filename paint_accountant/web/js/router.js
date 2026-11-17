/* Screens are switched by the URL hash; this decides which one to draw. */

import { t } from '../i18n.js';
import { explain, h, viewEl } from './core.js';
import { paintWatchers } from './shared.js';
import { viewPaints } from './paints.js';
import { viewPaintForm } from './paint-form.js';
import { viewStorage, viewStorages } from './storages.js';
import { viewLabels } from './labels.js';
import { viewCatalogs } from './catalogs.js';
import { viewStats } from './stats.js';
import { viewLogin, viewSettings } from './settings.js';

const routes = [
  [/^#\/?$/, (m, mount) => viewPaints(mount)],
  [/^#\/paint\/new(?:\?storage=(\d+))?$/, (m, mount) => viewPaintForm(mount, null, m[1] ? Number(m[1]) : null)],
  [/^#\/paint\/(\d+)$/, (m, mount) => viewPaintForm(mount, Number(m[1]))],
  [/^#\/storages$/, (m, mount) => viewStorages(mount)],
  [/^#\/storage\/(\d+)$/, (m, mount) => viewStorage(mount, Number(m[1]))],
  [/^#\/catalogs$/, (m, mount) => viewCatalogs(mount)],
  [/^#\/labels(?:\?ids=([\d,]+))?$/, (m, mount) => viewLabels(mount, m[1] ? m[1].split(',').map(Number) : null)],
  [/^#\/tags$/, () => location.replace('#/catalogs')],
  [/^#\/stats$/, (m, mount) => viewStats(mount)],
  [/^#\/settings$/, (m, mount) => viewSettings(mount)],
  [/^#\/login$/, (m, mount) => viewLogin(mount)],
];

let renderSeq = 0;
let prevHash = null;
let currentHash = location.hash || '#/';
let cleanups = [];

/** Run when the current screen is replaced (remove listeners etc.). */
export function onLeave(fn) { cleanups.push(fn); }

function highlightTab(hash) {
  const tab = hash.startsWith('#/storage') || hash.startsWith('#/labels') ? 'storages'
    : hash.startsWith('#/catalogs') ? 'catalogs' : hash.startsWith('#/stats') ? 'stats'
    : hash.startsWith('#/settings') ? 'settings' : 'paints';
  document.body.classList.toggle('signed-out', hash === '#/login');
  document.querySelectorAll('.tabs a').forEach(a => a.classList.toggle('active', a.dataset.tab === tab));
}

export async function render() {
  cleanups.forEach(fn => fn());
  cleanups = [];
  paintWatchers.clear();
  const hash = location.hash || '#/';
  highlightTab(hash);
  const seq = ++renderSeq;
  // each screen gets its own mount(): if the user has already moved on,
  // a slow screen finishing late doesn't overwrite the new one
  const mount = node => { if (seq === renderSeq) viewEl.replaceChildren(node); };
  for (const [re, view] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    try {
      await view(m, mount);
    } catch (e) {
      mount(h('div', { class: 'empty' }, explain(e), h('br'), h('a', { class: 'btn', href: '#/' }, t('К списку красок'))));
    }
    return;
  }
  location.replace('#/');
}

window.addEventListener('hashchange', () => {
  prevHash = currentHash;
  currentHash = location.hash || '#/';
  render();
});

/** After saving a form go back where the user came from (list, storage page…). */
export function goBack(fallback = '#/') {
  let target = prevHash;
  if (!target || target.startsWith('#/paint/') || target === currentHash) target = fallback;
  if (target === (location.hash || '#/')) render();
  else location.hash = target;
}
