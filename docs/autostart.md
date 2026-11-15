# Autostart and moving to another computer

The app is started with `python run.py` from its own folder. To make it start by itself
when the computer boots, there are recipes below for Linux (systemd) and Windows
(Task Scheduler). This part is optional: if you start the app by hand, nothing needs
to be set up.

In the examples the app lives in `~/Paint_accountant` (Linux) or `C:\Paint_accountant`
(Windows) and has a `.venv` environment, as in README → Quick start. If your paths are
different, substitute your own.

First make sure that starting by hand works: `.venv/bin/python run.py`
(on Windows `.venv\Scripts\python run.py`) and the page opens in a browser.

---

## Linux — systemd

### 1. The service file

Create `/etc/systemd/system/paint-accountant.service`:

```bash
sudo nano /etc/systemd/system/paint-accountant.service
```

```ini
[Unit]
Description=Paint Accountant
After=network-online.target
Wants=network-online.target

[Service]
# the user who owns the app folder
User=youruser
WorkingDirectory=/home/youruser/Paint_accountant
ExecStart=/home/youruser/Paint_accountant/.venv/bin/python run.py
Restart=on-failure
RestartSec=5
# print() output goes to the journal right away
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Replace `youruser` and `/home/youruser/...` with your own user and path. You can find
them with `whoami` and `pwd`, run in the app folder.

### 2. Enable it

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now paint-accountant
```

`enable` makes it start at boot, `--now` starts it right away.

### 3. Check and manage

```bash
systemctl status paint-accountant          # is it running, last lines of output
journalctl -u paint-accountant -f          # live log (Ctrl+C to leave)
sudo systemctl restart paint-accountant    # restart, e.g. after an update
sudo systemctl stop paint-accountant       # stop
sudo systemctl disable paint-accountant    # remove from autostart
```

### Updating

```bash
cd ~/Paint_accountant
git pull
.venv/bin/pip install -r requirements.txt          # if the dependencies changed
.venv/bin/pip install -r requirements-photo.txt    # if photo recognition is installed
sudo systemctl restart paint-accountant
```

### If the phone can't open the page

If the `ufw` firewall is on, open the port (8642 by default):

```bash
sudo ufw allow 8642/tcp
```

---

## Windows — Task Scheduler

### Option A: PowerShell (faster)

Open PowerShell **as administrator** and run this, with your own path:

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

- `pythonw.exe` runs without a black console window.
- `-AtStartup` together with `S4U` starts the app when the computer is switched on, even
  if nobody has logged in to Windows. If you want it to start only after logging in,
  replace the trigger with `New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME` and
  remove the `$principal` line and the `-Principal` parameter.
- `-ExecutionTimeLimit 0` removes the limit: by default Windows stops a task after
  3 days.

To start it right away, without a reboot:

```powershell
Start-ScheduledTask -TaskName "Paint Accountant"
```

### Option B: with the mouse

1. **Start → Task Scheduler → Create Task…** (exactly "Create Task", not
   "Create Basic Task").
2. **General:** name `Paint Accountant`; select "Run whether user is logged on or not"
   (or "Run only when user is logged on" if you want it to start after logging in).
3. **Triggers → New…:** "At startup" (or "At log on").
4. **Actions → New…:** "Start a program"
   - Program/script: `C:\Paint_accountant\.venv\Scripts\pythonw.exe`
   - Add arguments: `run.py`
   - Start in: `C:\Paint_accountant`
5. **Conditions:** clear "Start the task only if the computer is on AC power".
6. **Settings:** clear "Stop the task if it runs longer than…"; select
   "If the task fails, restart every: 1 minute".
7. OK. Windows may ask for the account password.

### Access from a phone: firewall rule

When you start the app by hand, Windows asks by itself whether to allow network access.
A task from the scheduler runs in the background and that window never appears, so the
rule has to be added once, in PowerShell as administrator:

```powershell
New-NetFirewallRule -DisplayName "Paint Accountant" -Direction Inbound -Protocol TCP -LocalPort 8642 -Action Allow -Profile Private
```

`-Profile Private` allows access on your home network only. The Wi‑Fi network must be
marked "Private" in Windows.

### Check and manage

```powershell
Get-ScheduledTask -TaskName "Paint Accountant" | Get-ScheduledTaskInfo   # last run and result
Stop-ScheduledTask  -TaskName "Paint Accountant"
Start-ScheduledTask -TaskName "Paint Accountant"
Unregister-ScheduledTask -TaskName "Paint Accountant" -Confirm:$false    # remove from autostart
```

Under `pythonw` there is no output to see. If the page doesn't open, stop the task and
start the app by hand with `.venv\Scripts\python run.py`: the error will show in the
console.

### Updating

```powershell
Stop-ScheduledTask -TaskName "Paint Accountant"
cd C:\Paint_accountant
git pull
.venv\Scripts\pip install -r requirements.txt
Start-ScheduledTask -TaskName "Paint Accountant"
```

---

## Moving the catalog to another computer

1. On the old computer: **⚙ Settings → Backup → ⬇ Full backup (.zip)**, or in a console
   `python run.py --dump`.
2. On the new one: install the app following the README, start it and open
   **⚙ Settings → Restore from a backup…**, or in a console
   `python run.py --restore paint-accountant-YYYY-MM-DD.zip`.

The backup holds all paints, storages, tags, manufacturers, photos and the login password
(if one was set). Before replacing, the new computer's current database is saved in the
`data/` folder as `paints.before-restore-<date>.sqlite3`.

The links in QR labels contain the computer's address. If the new computer has a different
address on the network, reprint the labels (**QR labels**) or reserve the old address for
it in the router settings.
