#!/usr/bin/env bash
# The app's notification tests on a running emulator (the android-push-test workflow; docs/ANDROID.md).
#
#   android/scripts/push-test.sh <debug.apk> <out-dir>
#
# Needs: adb with one emulator booted, openssl and python3. The debug APK must be built with the test CA
# that this script writes first (--prepare <dir>), so the app trusts the stand-in gateway's https
# certificate. A release build never trusts it: the files go only into the debug source set, and only in CI.
#
# Each scenario prints PASS or FAIL with what it saw; the script exits 1 if any failed. Screenshots and
# the logs go to <out-dir>.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PKG="$(tr -d '[:space:]' < "$ROOT/app/application-id.txt")"
SERVICE="$PKG/local.mtgassistantgateway.app.NotifyService"
KEY="mtg-PushTestKeyAbCdEfGhIjK" # the app takes only mtg- and 22 letters, digits, - or _ (Push.isKey)
printf %s "$KEY" | grep -Eqx "mtg-[A-Za-z0-9_-]{22}" || { echo "the test key is not one the app accepts"; exit 2; }
PORT=8443

if [ "${1:-}" = "--prepare" ]; then
  # The stand-in gateway's certificates, and the debug-only network config that trusts the test CA.
  dir="$2"
  mkdir -p "$dir" "$ROOT/app/src/debug/res/xml" "$ROOT/app/src/debug/res/raw"
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$dir/ca.key" -out "$dir/ca.pem" -days 2 -subj "/CN=Push test CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -keyout "$dir/leaf.key" -out "$dir/leaf.csr" -subj "/CN=localhost" 2>/dev/null
  printf 'subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n' > "$dir/ext"
  openssl x509 -req -in "$dir/leaf.csr" -CA "$dir/ca.pem" -CAkey "$dir/ca.key" -CAcreateserial -out "$dir/leaf.pem" \
    -days 2 -extfile "$dir/ext" 2>/dev/null
  cp "$dir/ca.pem" "$ROOT/app/src/debug/res/raw/push_test_ca.pem"
  cat > "$ROOT/app/src/debug/res/xml/push_test_network.xml" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <debug-overrides>
        <trust-anchors>
            <certificates src="system" />
            <certificates src="@raw/push_test_ca" />
        </trust-anchors>
    </debug-overrides>
</network-security-config>
XML
  cat > "$ROOT/app/src/debug/AndroidManifest.xml" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application android:networkSecurityConfig="@xml/push_test_network" />
</manifest>
XML
  exit 0
fi

APK="$1"
OUT="$2"
CERTS="${PUSH_TEST_CERTS:-$OUT/certs}"
mkdir -p "$OUT"
FAILED=0
ctl() { curl -sS --cacert "$CERTS/ca.pem" "https://localhost:$PORT/control/$1" "${@:2}"; }
state() { ctl state | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
send() { ctl send -d "{\"id\":\"$1\",\"title\":\"$2\",\"message\":\"$3\",\"sequence_id\":\"${4:-}\",\"click\":\"https://localhost:$PORT/proposals\"}" >/dev/null; }
notes() { adb shell dumpsys notification --noredact 2>/dev/null | tr -d '\r'; }
result() { if [ "$2" = ok ]; then echo "PASS  $1"; else echo "FAIL  $1: $3"; FAILED=1; fi; }
shot() { adb exec-out screencap -p > "$OUT/$1.png" 2>/dev/null || true; }
# Wait up to $2 seconds for the command in $1 to succeed.
until_ok() { local t=0; while ! eval "$1" >/dev/null 2>&1; do sleep 2; t=$((t + 2)); [ "$t" -ge "$2" ] && return 1; done; }
shows() { notes | grep -qF "$1"; }
running() { adb shell dumpsys activity services "$PKG" 2>/dev/null | grep -q NotifyService; }
connected() { [ "$(state connections)" -ge 1 ]; }
boot() {
  adb wait-for-device
  until_ok '[ "$(adb shell getprop sys.boot_completed | tr -d "\r")" = 1 ]' 300
  adb reverse "tcp:$PORT" "tcp:$PORT"
}

PUSH_TEST_KEY="$KEY" PUSH_TEST_KEEPALIVE=5 python3 "$HERE/push_test_gateway.py" "$CERTS/leaf.pem" "$CERTS/leaf.key" "$PORT" \
  > "$OUT/gateway.log" 2>&1 &
GW=$!
trap 'kill $GW 2>/dev/null; adb logcat -d > "$OUT/logcat.txt" 2>/dev/null' EXIT
sleep 1
boot

# -- for the card: the launcher name and the long-press shortcuts at phone and Fold widths (dp at 420 dpi)
adb install -r "$APK" >/dev/null
for size in phone:1080x2400 fold344:903x2092 fold673:1767x2208; do
  name=${size%%:*}
  adb shell wm size "${size#*:}"; adb shell wm density 420
  adb shell input keyevent KEYCODE_HOME; sleep 2
  adb shell input swipe 500 1800 500 300 300; sleep 2 # the app drawer
  adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
  icon=$(adb shell cat /sdcard/ui.xml | tr -d '\r' | python3 -c '
import re, sys
m = re.search(r"text=\"MTG Gateway\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", sys.stdin.read())
print("%d %d" % ((int(m[1]) + int(m[3])) // 2, (int(m[2]) + int(m[4])) // 2) if m else "")')
  shot "launcher-$name"
  if [ -n "$icon" ]; then
    set -- $icon
    adb shell input swipe "$1" "$2" "$1" "$2" 1200; sleep 2 # a long press
    shot "launcher-$name-shortcuts"
    adb shell input keyevent KEYCODE_BACK
  else
    echo "note: MTG Gateway not found in the app drawer at $name (see launcher-$name.png)"
  fi
done
adb shell wm size reset; adb shell wm density reset
adb shell input keyevent KEYCODE_HOME

# -- set up as a member who pressed Get notifications on this phone (the page part is tested on the gateway)
adb install -r "$APK" >/dev/null
adb shell pm grant "$PKG" android.permission.POST_NOTIFICATIONS
adb shell dumpsys deviceidle whitelist "+$PKG" >/dev/null
adb shell run-as "$PKG" sh -c "mkdir -p shared_prefs && cat > shared_prefs/mtgassistant.xml" <<XML
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="gateway_origin">https://localhost:$PORT</string>
    <string name="push_key">$KEY</string>
</map>
XML
adb shell am start-foreground-service -n "$SERVICE" >/dev/null
if until_ok connected 60; then result "connects with the key" ok; else result "connects with the key" no "no connection"; fi
if until_ok 'shows "Always connected"' 30; then result "quiet Always connected notification" ok; else result "quiet Always connected notification" no "not in dumpsys"; fi
shot 01-connected

# -- for the card: the app's own menu, with its Scan entry
adb shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1; sleep 4
adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
fab=$(adb shell cat /sdcard/ui.xml | tr -d '\r' | python3 -c '
import re, sys
m = re.search(r"content-desc=\"Menu\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", sys.stdin.read())
print("%d %d" % ((int(m[1]) + int(m[3])) // 2, (int(m[2]) + int(m[4])) // 2) if m else "")')
if [ -n "$fab" ]; then
  set -- $fab
  adb shell input tap "$1" "$2"; sleep 2
  shot app-menu
  adb shell input keyevent KEYCODE_BACK
else
  echo "note: the app's Menu button was not found (see 01-connected.png)"
fi
adb shell input keyevent KEYCODE_HOME

send m001AbCdEf "Test one" "The first message"
if until_ok 'shows "Test one"' 30; then result "a message shows" ok; else result "a message shows" no "not posted"; fi
shot 02-message

# -- one message per deck: replaced as the count changes, removed when nothing waits
send m002AbCdEf "1 change to Atraxa is waiting for you" "Open it." deck-42
until_ok 'shows "1 change to Atraxa is waiting for you"' 30
send m003AbCdEf "2 changes to Atraxa are waiting for you" "Open it." deck-42
if until_ok 'shows "2 changes to Atraxa are waiting for you"' 30 && ! shows "1 change to Atraxa is waiting for you"; then
  result "a deck's message is replaced" ok
else
  result "a deck's message is replaced" no "both or neither in dumpsys"
fi
shot 03-grouped
ctl delete -d '{"sequence_id":"deck-42"}' >/dev/null
if until_ok '! shows "2 changes to Atraxa are waiting for you"' 30; then result "a withdrawn message goes" ok; else result "a withdrawn message goes" no "still shown"; fi

# -- the app swiped away from Recents: the service keeps running
adb shell am start -W -n "$PKG/local.mtgassistantgateway.app.MainActivity" >/dev/null
sleep 3
adb shell input keyevent KEYCODE_APP_SWITCH
sleep 2
size=$(adb shell wm size | tr -d '\r' | awk '{print $NF}')
w=${size%x*}; h=${size#*x}
adb shell input swipe $((w / 2)) $((h / 2)) $((w / 2)) $((h / 10)) 300
sleep 2
adb shell input keyevent KEYCODE_HOME
send m004AbCdEf "After swipe" "Still here"
if running && until_ok 'shows "After swipe"' 30; then result "swiped away from Recents" ok; else result "swiped away from Recents" no "service gone or message missing"; fi

# -- the ongoing notification dismissed (Android 14+ lets people swipe it away): messages still arrive
adb shell cmd statusbar expand-notifications
sleep 2
adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
bounds=$(adb shell cat /sdcard/ui.xml | tr -d '\r' | python3 -c '
import re, sys
x = sys.stdin.read()
m = re.search(r"text=\"Always connected\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", x)
print(" ".join(m.groups()) if m else "")')
if [ -n "$bounds" ]; then
  set -- $bounds
  adb shell input swipe "$1" $((($2 + $4) / 2)) "$w" $((($2 + $4) / 2)) 300
fi
adb shell cmd statusbar collapse
send m005AbCdEf "After dismiss" "Still here"
if running && until_ok 'shows "After dismiss"' 30; then
  result "ongoing notification dismissed" ok
else
  result "ongoing notification dismissed" no "service gone or message missing (swipe target found: ${bounds:-no})"
fi

# -- Doze: the phone unplugged and idle; the app is allowed to run in the background
adb shell dumpsys battery unplug
adb shell dumpsys deviceidle force-idle >/dev/null
send m006AbCdEf "In Doze" "Arrived while idle"
if until_ok 'shows "In Doze"' 60; then result "Doze" ok; else result "Doze" no "not delivered while idle"; fi
adb shell dumpsys deviceidle unforce >/dev/null
adb shell dumpsys battery reset

# -- an app update (what Update does: install over the app): it starts again by itself
before=$(state connects)
adb install -r "$APK" >/dev/null
if until_ok '[ "$(state connects)" -gt '"$before"' ] && connected' 90; then result "after an app update" ok; else result "after an app update" no "no new connection"; fi
send m007AbCdEf "After update" "Still here"
until_ok 'shows "After update"' 30 || result "message after an update" no "missing"

# -- a restart of the phone
adb reboot
boot
if until_ok connected 180; then result "after a reboot" ok; else result "after a reboot" no "no connection after boot"; fi
send m008AbCdEf "After reboot" "Still here"
if until_ok 'shows "After reboot"' 30; then result "message after a reboot" ok; else result "message after a reboot" no "missing"; fi
shot 04-after-reboot

# -- missed while offline: since= brings it back once
adb shell svc wifi disable; adb shell svc data disable
sleep 5
send m009AbCdEf "While offline" "Missed"
adb shell svc wifi enable; adb shell svc data enable
if until_ok 'shows "While offline"' 120; then result "missed while offline" ok; else result "missed while offline" no "not caught up"; fi

# -- Stop (force stop, as Android's task manager does): stays stopped until the app is opened
adb shell am force-stop "$PKG"
sleep 5
if ! running && until_ok '[ "$(state connections)" -eq 0 ]' 30; then result "stopped stays stopped" ok; else result "stopped stays stopped" no "still running"; fi
adb shell am start -W -n "$PKG/local.mtgassistantgateway.app.MainActivity" >/dev/null
if until_ok connected 60; then result "opening the app starts it again" ok; else result "opening the app starts it again" no "no connection"; fi

# -- the gateway says notifications are off: the app stops and forgets the key
ctl refuse -d '{"status":403,"error":"off"}' >/dev/null
if until_ok '! running' 120 && ! adb shell run-as "$PKG" cat shared_prefs/mtgassistant.xml | grep -q push_key; then
  result "stops when switched off" ok
else
  result "stops when switched off" no "still running or key kept"
fi
shot 05-end

echo "battery: a 24 hour battery test needs a real phone; not run here."
exit "$FAILED"
