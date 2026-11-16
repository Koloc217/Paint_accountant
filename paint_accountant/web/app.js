/* Paint Accountant web UI — plain JS modules, no build step, no external libraries.
   This file starts the app; the screens live in js/:
     core.js        DOM helper h(), toast, the API call
     shared.js      pieces used by several screens: dialogs, paint rows, pot placement
     router.js      which screen the URL hash shows
     paints.js      the paint list with search and filters   #/
     paint-form.js  a paint's page                           #/paint/new, #/paint/ID
     photo.js       adding a paint by photo or live camera
     storages.js    storages and a storage's page            #/storages, #/storage/ID
     labels.js      printable QR labels                      #/labels
     catalogs.js    manufacturers and tags                   #/catalogs
     stats.js       statistics                               #/stats
     settings.js    settings, backup, sign-in                #/settings, #/login
   i18n.js holds the interface texts.                                          */

import { render } from './js/router.js';

// On phones the keyboard shrinks the page (see the viewport meta). Keep the field being edited
// in the middle of what's left, clear of the sticky bottom bar — otherwise it hides under it.
if (window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0) {
  const keepFocusedVisible = () => {
    const el = document.activeElement;
    if (el && el.matches('input:not([type=checkbox]):not([type=color]):not([type=file]), textarea') && el.closest('main')) {
      el.scrollIntoView({ block: 'center' });
    }
  };
  window.addEventListener('resize', keepFocusedVisible);
  if (window.visualViewport) window.visualViewport.addEventListener('resize', keepFocusedVisible);
  document.addEventListener('focusin', () => setTimeout(keepFocusedVisible, 350)); // after the keyboard slides in
}

render();
