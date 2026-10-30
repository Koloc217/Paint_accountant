# Автозапуск и перенос на другой компьютер

Приложение запускается командой `python run.py` из своей папки. Чтобы оно стартовало
само после включения компьютера, ниже есть рецепты для Linux (systemd, подходит и для
Raspberry Pi) и для Windows (Планировщик заданий). Это необязательная часть: если
запускать вручную, ничего настраивать не нужно.

В примерах приложение лежит в `~/Paint_accountant` (Linux) или `C:\Paint_accountant`
(Windows), и в нём создано окружение `.venv`, как в README → Quick start. Если пути
другие, подставьте свои.

Сначала убедитесь, что ручной запуск работает: `.venv/bin/python run.py`
(в Windows `.venv\Scripts\python run.py`) и страница открывается в браузере.

---

## Linux / Raspberry Pi — systemd

### 1. Файл службы

Создайте `/etc/systemd/system/paint-accountant.service`:

```bash
sudo nano /etc/systemd/system/paint-accountant.service
```

```ini
[Unit]
Description=Paint Accountant
After=network-online.target
Wants=network-online.target

[Service]
# пользователь, которому принадлежит папка приложения
User=pi
WorkingDirectory=/home/pi/Paint_accountant
ExecStart=/home/pi/Paint_accountant/.venv/bin/python run.py
Restart=on-failure
RestartSec=5
# вывод print() сразу попадает в журнал
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Замените `pi` и `/home/pi/...` на своего пользователя и путь. Узнать их можно командами
`whoami` и `pwd`, запущенными в папке приложения.

### 2. Включить

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now paint-accountant
```

`enable` включает запуск при загрузке, `--now` запускает сразу.

### 3. Проверить и управлять

```bash
systemctl status paint-accountant          # работает ли, последние строки вывода
journalctl -u paint-accountant -f          # журнал в реальном времени (Ctrl+C — выйти)
sudo systemctl restart paint-accountant    # перезапуск, например после обновления
sudo systemctl stop paint-accountant       # остановить
sudo systemctl disable paint-accountant    # убрать из автозапуска
```

### Обновление

```bash
cd ~/Paint_accountant
git pull
.venv/bin/pip install -r requirements.txt          # если поменялись зависимости
.venv/bin/pip install -r requirements-photo.txt    # если стоит распознавание по фото
sudo systemctl restart paint-accountant
```

### Если телефон не открывает страницу

Если включён файрвол `ufw`, откройте порт (по умолчанию 8642):

```bash
sudo ufw allow 8642/tcp
```

---

## Windows — Планировщик заданий

### Вариант A: через PowerShell (быстрее)

Откройте PowerShell **от имени администратора** и выполните, подставив свой путь:

```powershell
$app = "C:\Paint_accountant"

$action  = New-ScheduledTaskAction -Execute "$app\.venv\Scripts\pythonw.exe" -Argument "run.py" -WorkingDirectory $app
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName "Paint Accountant" -Action $action -Trigger $trigger `
                       -Settings $settings -Principal $principal
```

- `pythonw.exe` запускает без чёрного окна консоли.
- `-AtStartup` вместе с `S4U` означает запуск при включении компьютера, даже если в
  Windows никто не вошёл. Если нужно запускать только после входа в систему, замените
  триггер на `New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME` и уберите строку
  `$principal` и параметр `-Principal`.
- `-ExecutionTimeLimit 0` снимает ограничение: по умолчанию Windows останавливает
  задание через 3 дня.

Запустить сразу, не дожидаясь перезагрузки:

```powershell
Start-ScheduledTask -TaskName "Paint Accountant"
```

### Вариант B: мышкой

1. **Пуск → Планировщик заданий → Создать задачу** (именно «Создать задачу», а не
   «Создать простую задачу»).
2. **Общие:** имя `Paint Accountant`; отметьте «Выполнять для всех пользователей»
   (или «Выполнять только для пользователей, вошедших в систему», если нужен запуск
   после входа).
3. **Триггеры → Создать:** «При запуске» (или «При входе в систему»).
4. **Действия → Создать:** «Запуск программы»
   - Программа: `C:\Paint_accountant\.venv\Scripts\pythonw.exe`
   - Аргументы: `run.py`
   - Рабочая папка: `C:\Paint_accountant`
5. **Условия:** снимите «Запускать только при питании от электросети».
6. **Параметры:** снимите «Останавливать задачу, выполняемую дольше…»; включите
   «При сбое перезапускать каждые 1 мин».
7. OK. Windows может спросить пароль учётной записи.

### Доступ с телефона: правило брандмауэра

При ручном запуске Windows сама спрашивает, разрешить ли доступ к сети. Задание из
планировщика работает в фоне, и этого окна не будет, поэтому правило нужно добавить
один раз в PowerShell от администратора:

```powershell
New-NetFirewallRule -DisplayName "Paint Accountant" -Direction Inbound -Protocol TCP -LocalPort 8642 -Action Allow -Profile Private
```

`-Profile Private` разрешает доступ только в домашней сети. Сеть Wi‑Fi в Windows должна
быть помечена как «Частная».

### Проверить и управлять

```powershell
Get-ScheduledTask -TaskName "Paint Accountant" | Get-ScheduledTaskInfo   # последний запуск и результат
Stop-ScheduledTask  -TaskName "Paint Accountant"
Start-ScheduledTask -TaskName "Paint Accountant"
Unregister-ScheduledTask -TaskName "Paint Accountant" -Confirm:$false    # убрать из автозапуска
```

Под `pythonw` вывода не видно. Если страница не открывается, остановите задание и
запустите вручную через `.venv\Scripts\python run.py`: ошибка появится в консоли.

### Обновление

```powershell
Stop-ScheduledTask -TaskName "Paint Accountant"
cd C:\Paint_accountant
git pull
.venv\Scripts\pip install -r requirements.txt
Start-ScheduledTask -TaskName "Paint Accountant"
```

---

## Перенос каталога на другой компьютер

1. На старом компьютере: **⚙ Настройки → Резервная копия → ⬇ Полная копия (.zip)**,
   или в консоли `python run.py --dump`.
2. На новом: установите приложение по README, запустите и откройте
   **⚙ Настройки → Восстановить из копии…**, или в консоли
   `python run.py --restore paint-accountant-ГГГГ-ММ-ДД.zip`.

В копии лежат все краски, хранилища, теги, производители, фото и пароль входа (если он
был включён). Текущая база нового компьютера перед заменой сохраняется в папке `data/`
как `paints.before-restore-<дата>.sqlite3`.

Ссылки в QR-метках содержат адрес компьютера. Если у нового компьютера другой адрес в
сети, перепечатайте метки (**QR-метки**) или закрепите за ним прежний адрес в
настройках роутера.
