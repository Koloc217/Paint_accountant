/* Settings, backup and sign-in. */

import { LANG, LANG_SETTING, LANGS, serverText, setLang, t } from '../i18n.js';
import { api, ApiError, explain, h, toast } from './core.js';
import { confirmDialog, pageHead, segButtons } from './shared.js';
import { render } from './router.js';
import { filters, rememberSort, saveSort } from './paints.js';
import { cameraMode } from './photo.js';

/** Sign-in is on and this device isn't signed in: show the login screen, then come back here. */
export function goToLogin() {
  if (location.hash === '#/login') return;
  try { sessionStorage.setItem('pa.next', location.hash || '#/'); } catch { /* private mode */ }
  location.hash = '#/login';
}

export async function viewLogin(mount) {
  const state = await api('GET', '/auth');
  const next = () => {
    let to = '#/';
    try { to = sessionStorage.getItem('pa.next') || '#/'; sessionStorage.removeItem('pa.next'); } catch { /* */ }
    location.replace(to === '#/login' ? '#/' : to);
  };
  if (state.signed_in) { next(); return; }
  const password = h('input', { class: 'input', id: 'l-password', type: 'password', autocomplete: 'current-password', required: true });
  const error = h('div', { class: 'login-error', role: 'alert' });
  const form = h('form', { class: 'panel login' },
    h('h1', null, 'Paint Accountant'),
    h('div', { class: 'field' }, h('label', null, t('Пользователь')), h('input', { class: 'input', value: state.user, readonly: true, autocomplete: 'username' })),
    h('div', { class: 'field' }, h('label', { for: 'l-password' }, t('Пароль')), password),
    error,
    h('button', { type: 'submit', class: 'btn primary' }, t('Войти')));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    error.textContent = '';
    try {
      await api('POST', '/auth/login', { password: password.value });
      next();
    } catch (err) {
      const d = err.detail || {};
      error.textContent = d.code === 'wrong_password' ? t('Неверный пароль')
        : d.code === 'too_many_attempts' ? t('Слишком много попыток — подождите {0} с', d.wait) : explain(err);
      password.select();
    }
  });
  mount(h('div', { class: 'login-wrap' }, form,
    h('p', { class: 'muted small' }, t('Забыли пароль? На компьютере с приложением выполните '),
      h('code', null, 'python run.py --reset-auth'), t(' — вход отключится, новый пароль можно задать в настройках.'))));
  password.focus();
}

function passwordPair(onSubmit, buttonLabel, askCurrent = false) {
  const current = askCurrent
    ? h('input', { class: 'input', type: 'password', id: 'p-current', autocomplete: 'current-password', required: true })
    : null;
  const first = h('input', { class: 'input', type: 'password', id: 'p-new', autocomplete: 'new-password', minlength: 4, required: true });
  const again = h('input', { class: 'input', type: 'password', id: 'p-again', autocomplete: 'new-password', minlength: 4, required: true });
  const form = h('form', { class: 'form' },
    current ? h('div', { class: 'field' }, h('label', { for: 'p-current' }, t('Текущий пароль')), current) : null,
    h('div', { class: 'field' }, h('label', { for: 'p-new' }, t('Новый пароль')), first,
      h('div', { class: 'hint' }, t('Не короче 4 символов'))),
    h('div', { class: 'field' }, h('label', { for: 'p-again' }, t('Ещё раз')), again),
    h('div', { class: 'form-actions' }, h('button', { type: 'submit', class: 'btn primary' }, buttonLabel)));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (first.value !== again.value) { toast(t('Пароли не совпадают'), 'err'); again.focus(); return; }
    if (first.value.length < 4) { toast(t('Пароль короче 4 символов'), 'err'); first.focus(); return; }
    try { await onSubmit(first.value, current ? current.value : undefined); } catch (err) {
      toast(explain(err), 'err');
      if (current && err instanceof ApiError && err.status === 403) { current.value = ''; current.focus(); }
    }
  });
  return form;
}

function backupPanel() {
  const fileInput = h('input', { type: 'file', accept: '.zip,.sqlite3,application/zip', hidden: true });
  const restore = h('button', { type: 'button', class: 'btn danger' }, t('Восстановить из копии…'));
  restore.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = '';
    if (!file) return;
    const ok = await confirmDialog({
      title: t('Заменить каталог копией «{0}»?', file.name),
      text: t('Все краски, хранилища, теги и настройки станут такими, как в копии. Текущая база сохранится рядом с ней в папке данных — её можно вернуть.'),
      confirmLabel: t('Заменить'),
    });
    if (!ok) return;
    restore.disabled = true;
    try {
      const res = await fetch('/api/backup/restore', { method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const d = data && data.detail;
        throw new Error(typeof d === 'string' ? serverText(d) : t('Ошибка {0}', res.status));
      }
      toast(t('Каталог восстановлен: {0} красок', data.counts.paint), 'ok');
      setTimeout(() => { location.hash = '#/'; location.reload(); }, 900);
    } catch (err) {
      toast(t('Не получилось восстановить: {0}', err.message), 'err');
    } finally {
      restore.disabled = false;
    }
  });
  return h('div', { class: 'panel form' },
    h('div', { class: 'field' },
      h('a', { class: 'btn', href: `/api/backup/paints.csv?lang=${LANG}`, download: '' }, t('⬇ Список красок (CSV)')),
      h('div', { class: 'hint' }, t('По производителям, с цветом, если он задан. Открывается в Excel и Google Таблицах. Только для чтения — обратно не загружается.'))),
    h('div', { class: 'field' },
      h('a', { class: 'btn', href: '/api/backup/dump', download: '' }, t('⬇ Полная копия (.zip)')),
      h('div', { class: 'hint' }, t('Вся база — чтобы перенести каталог на другой компьютер или вернуть после сбоя. Пароль входа тоже внутри.'))),
    h('div', { class: 'field' },
      h('div', null, restore, fileInput),
      h('div', { class: 'hint' }, t('Загрузите .zip, скачанный выше (здесь или на другом компьютере). Из консоли: '),
        h('code', null, t('python run.py --restore копия.zip')))));
}

export async function viewSettings(mount) {
  const [state, info] = await Promise.all([api('GET', '/auth'), api('GET', '/info').catch(() => ({}))]);
  const touch = window.matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0;

  // "auto" (the browser's language) stays possible in config.ini, but is not offered here:
  // the list shows the language actually in use, and picking one saves it for every device
  const langSel = h('select', { class: 'input', 'aria-label': t('Язык'), onchange: async e => {
    const code = e.target.value;
    if (code === LANG_SETTING) return;
    try { await setLang(code); } catch (err) {
      e.target.value = LANG;
      toast(t('Не получилось сохранить: {0}', err.message), 'err');
    }
  } }, LANGS.map(([code, name]) => h('option', { value: code }, name)));
  langSel.value = LANG;
  const language = h('div', { class: 'panel form' },
    h('div', { class: 'field' }, langSel,
      h('div', { class: 'hint' }, t('Сохраняется в config.ini и действует на всех устройствах.'))));
  const device = h('div', { class: 'panel form' },
    !touch ? null : h('div', { class: 'field' }, h('span', { class: 'label' }, t('Камера при добавлении краски')),
      segButtons(t('Режим камеры'), [['photo', t('Фото')], ['stream', t('Поток + фото')]], cameraMode(), value => {
        cameraMode(value);
        render();
      }),
      h('div', { class: 'hint' }, t('«Фото» открывает приложение камеры. «Поток» ловит штрихкоды в живом видео, но без HTTPS требует флаг Chrome.'))),
    h('div', { class: 'field' }, h('span', { class: 'label' }, t('Запоминать сортировку')),
      segButtons(t('Запоминать сортировку'), [['off', t('Нет')], ['on', t('Да')]], rememberSort() ? 'on' : 'off', value => {
        rememberSort(value === 'on');
        if (value === 'on') saveSort(filters.sort);
        render();
      }),
      h('div', { class: 'hint' }, t('Выбранная сортировка списка сохранится после перезагрузки страницы.'))));

  const look = info.lookup || {};
  const lookupPanel = h('div', { class: 'panel form' },
    h('div', { class: 'field' }, h('span', { class: 'label' }, 'UPCitemdb'),
      segButtons('UPCitemdb', [['off', t('Нет')], ['on', t('Да')]], look.upcitemdb ? 'on' : 'off', async value => {
        try {
          const r = await api('PUT', '/settings/lookup', { enabled: value === 'on' });
          if (r.overridden_by_env) toast(t('Сохранено, но сейчас действует переменная окружения PAINT_LOOKUP'), 'warn');
          render();
        } catch (err) { toast(t('Не получилось сохранить: {0}', err.message), 'err'); }
      }),
      h('div', { class: 'hint' }, t('Кнопка 🔎 у штрихкода спрашивает UPCitemdb, что это за товар, и заполняет форму краски. Отправляется только штрихкод и только по нажатию. Бесплатно: 100 запросов в день.')),
      h('div', { class: 'hint' },
        look.via === 'proxy' ? t('Запросы идут через прокси.')
          : look.via === 'interface' ? t('Запросы идут через интерфейс {0}.', look.interface)
            : t('Запросы идут напрямую, без прокси.'),
        ' ', t('Прокси или сетевой интерфейс задаются в config.ini, раздел [lookup].'))));

  let signIn;
  if (!state.enabled) {
    signIn = h('div', { class: 'panel' },
      h('p', { style: 'margin-top:0' }, t('Сейчас вход выключен: приложение открывается без пароля у всех в вашей сети.')),
      h('p', { class: 'muted small' }, t('Включите вход, чтобы спрашивать пароль. Пользователь — {0}. Этот телефон или компьютер останется в системе, на остальных устройствах нужно будет войти один раз.', state.user)),
      passwordPair(async pw => {
        await api('POST', '/auth/password', { password: pw });
        toast(t('Вход по паролю включён'), 'ok');
        render();
      }, t('Включить вход')));
  } else {
    const logout = h('button', { type: 'button', class: 'btn' }, t('Выйти'));
    logout.addEventListener('click', async () => {
      await api('POST', '/auth/logout').catch(() => null);
      location.hash = '#/login';
    });
    const off = h('button', { type: 'button', class: 'btn danger' }, t('Отключить вход'));
    off.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: t('Отключить вход по паролю?'),
        text: t('Приложение снова будет открываться без пароля у всех в вашей сети.'),
        confirmLabel: t('Отключить'),
      });
      if (!ok) return;
      try {
        await api('POST', '/auth/disable');
        toast(t('Вход отключён'), 'ok');
        render();
      } catch (err) { toast(explain(err), 'err'); }
    });
    signIn = h('div', { class: 'panel' },
      h('p', { style: 'margin-top:0' }, t('Вход включён. Пользователь — {0}.', state.user)),
      h('div', { class: 'row' }, logout, off),
      h('details', { class: 'sub-details' }, h('summary', null, t('Сменить пароль')),
        h('p', { class: 'muted small' }, t('После смены на остальных устройствах нужно будет войти заново.')),
        passwordPair(async (pw, current) => {
          await api('POST', '/auth/password', { password: pw, current });
          toast(t('Пароль изменён'), 'ok');
          render();
        }, t('Сменить пароль'), true)));
  }

  mount(h('div', null,
    pageHead(t('Настройки')),
    h('h2', null, t('Язык')), language,
    h('h2', null, t('Резервная копия')), backupPanel(),
    h('h2', null, t('Поиск штрихкода в интернете')), lookupPanel,
    h('h2', null, t('На этом устройстве')), device,
    h('h2', null, t('Вход по паролю')), signIn,
    h('p', { class: 'muted small' },
      t('Пароль передаётся по сети без шифрования (http), поэтому не используйте пароль от важных аккаунтов. '),
      t('Забыли пароль? На компьютере с приложением выполните '), h('code', null, 'python run.py --reset-auth'), '.'),
    info.version ? h('p', { class: 'muted small' }, `Paint Accountant ${info.version}`) : null));
}
