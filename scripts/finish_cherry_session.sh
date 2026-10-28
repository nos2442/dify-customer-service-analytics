#!/bin/sh
set -eu

appdir=/home/dify/apps/cherry-studio/squashfs-root
cache=/home/dify/.cache/dify-project-cherry
test "$(id -u)" -eq 0
session=$(loginctl list-sessions --no-legend | while read -r sid uid name rest; do
  if [ "$name" = dify ] && [ "$(loginctl show-session "$sid" -p State --value)" = active ] &&
     [ "$(loginctl show-session "$sid" -p Type --value)" = x11 ]; then echo "$sid"; fi
done)
test -n "$session"
test "$(printf '%s\n' "$session" | wc -l)" -eq 1
display=$(loginctl show-session "$session" -p Display --value)
test -n "$display"
test -r /run/lightdm/dify/xauthority
quit_performed=false
if pgrep -u dify -f "^$appdir/cherrystudio" >/dev/null; then
  python3 - <<'PY'
import os
main = []
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        args = [x.decode() for x in open('/proc/' + pid + '/cmdline', 'rb').read().split(b'\0') if x]
        if args and args[0] == '/home/dify/apps/cherry-studio/squashfs-root/cherrystudio' and not any(x.startswith('--type=') for x in args):
            assert os.stat('/proc/' + pid).st_uid == 1000
            assert '--user-data-dir=/home/dify/.config/CherryStudio' in args
            assert '--remote-debugging-address=127.0.0.1' in args
            assert '--remote-debugging-port=9222' in args
            main.append(pid)
    except OSError:
        continue
assert len(main) == 1, 'Expected one verified project CDP main process'
PY
  sudo -u dify env ELECTRON_RUN_AS_NODE=1 "$appdir/cherrystudio" \
    "$cache/configure_cherry_provider.cjs" quit >"$cache/final-quit-result.json"
  quit_performed=true
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if ! pgrep -u dify -f "^$appdir/cherrystudio" >/dev/null; then break; fi
    sleep 1
  done
  if pgrep -u dify -f "^$appdir/cherrystudio" >/dev/null; then
    echo 'Project process did not exit after application quit' >&2
    exit 1
  fi
fi
if ss -lnt | grep -q ':9222 '; then
  echo 'CDP port already in use; no process changed' >&2
  exit 1
fi

nohup sudo -u dify env HOME=/home/dify USER=dify LOGNAME=dify \
  APPDIR="$appdir" DISPLAY="$display" XAUTHORITY=/run/lightdm/dify/xauthority \
  XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  "$appdir/AppRun" --disable-setuid-sandbox --user-data-dir=/home/dify/.config/CherryStudio \
  >"$cache/normal-final-vmtools.log" 2>&1 </dev/null &

sleep 15
export CHERRY_QUIT_PERFORMED="$quit_performed"
export CHERRY_SESSION="$session" CHERRY_DISPLAY="$display"
python3 - <<'PY' > "$cache/final-normal-session.json"
import datetime, json, os, socket

executable = '/home/dify/apps/cherry-studio/squashfs-root/cherrystudio'
profile_arg = '--user-data-dir=/home/dify/.config/CherryStudio'
main = []
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        if os.stat('/proc/' + pid).st_uid != 1000:
            continue
        args = [x.decode() for x in open('/proc/' + pid + '/cmdline', 'rb').read().split(b'\0') if x]
        if args and args[0] == executable and profile_arg in args and not any(x.startswith('--type=') for x in args):
            main.append((int(pid), args))
    except OSError:
        continue
assert len(main) == 1, 'Expected exactly one project main process owned by dify'
pid, args = main[0]
assert not any(x.startswith('--remote-debugging') for x in args), 'Debug flag remains'
try:
    s = socket.create_connection(('127.0.0.1', 9222), timeout=1)
    s.close()
    closed = False
except ConnectionRefusedError:
    closed = True
assert closed, 'CDP port remains open'
print(json.dumps({
    'observed_at': datetime.datetime.now().astimezone().isoformat(),
    'method': 'VMware Tools, without SSH',
    'normal_pid': pid,
    'owner': 'dify',
    'profile': '/home/dify/.config/CherryStudio',
    'normal_process_running': True,
    'remote_debugging_flags_absent': True,
    'cdp_port_9222_closed': True,
    'previous_cdp_instance_quit_via_application': os.environ['CHERRY_QUIT_PERFORMED'] == 'true',
    'session': os.environ['CHERRY_SESSION'],
    'display': os.environ['CHERRY_DISPLAY'],
    'boot_id': open('/proc/sys/kernel/random/boot_id').read().strip()
}, indent=2))
PY
