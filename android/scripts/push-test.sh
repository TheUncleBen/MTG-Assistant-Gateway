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
  # The push tests' hook: removes the app's task as Recents does (see the Recents scenario). Debug
  # source set only, written here in CI: a release build never has it.
  mkdir -p "$ROOT/app/src/debug/java/local/mtgassistantgateway/app"
  cat > "$ROOT/app/src/debug/java/local/mtgassistantgateway/app/PushTestHooks.kt" <<'KT'
package local.mtgassistantgateway.app

import android.app.ActivityManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Push tests only: removes the app's tasks, as removing it from Recents does. */
class PushTestHooks : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        context.getSystemService(ActivityManager::class.java).appTasks.forEach { it.finishAndRemoveTask() }
    }
}
KT
  cat > "$ROOT/app/src/debug/AndroidManifest.xml" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application android:networkSecurityConfig="@xml/push_test_network">
        <receiver android:name="local.mtgassistantgateway.app.PushTestHooks" android:exported="true" />
    </application>
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
hits() { ctl state | python3 -c "import json,sys; print(json.load(sys.stdin)['pages'].get('$1', 0))"; }
top() { adb shell dumpsys activity activities 2>/dev/null | tr -d '\r' | grep -E 'topResumedActivity|mResumedActivity'; }
# Tap the on-screen element whose text is $1 (the app's own views and its web page alike)
tap_text() {
  adb shell uiautomator dump /sdcard/ui.xml >/dev/null 2>&1
  local at
  at=$(adb shell cat /sdcard/ui.xml | tr -d '\r' | python3 -c '
import re, sys
want = sys.argv[1]
for m in re.finditer(r"<node [^>]*>", sys.stdin.read()):
    n = m.group(0)
    t = re.search(r"text=\"([^\"]*)\"", n)
    d = re.search(r"content-desc=\"([^\"]*)\"", n)
    if want in ((t and t.group(1)) or "", (d and d.group(1)) or ""):
        b = re.search(r"bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", n)
        print("%d %d" % ((int(b[1]) + int(b[3])) // 2, (int(b[2]) + int(b[4])) // 2))
        break' "$1")
  [ -n "$at" ] || return 1
  # shellcheck disable=SC2086
  adb shell input tap $at
}
send() { ctl send -d "{\"id\":\"$1\",\"title\":\"$2\",\"message\":\"$3\",\"sequence_id\":\"${4:-}\",\"click\":\"https://localhost:$PORT/proposals\"}" >/dev/null; }
notes() { adb shell dumpsys notification --noredact 2>/dev/null | tr -d '\r'; }
result() { if [ "$2" = ok ]; then echo "PASS  $1"; else echo "FAIL  $1: $3"; FAILED=1; fi; }
shot() { adb exec-out screencap -p > "$OUT/$1.png" 2>/dev/null || true; }
# Wait up to $2 seconds for the command in $1 to succeed. The explicit return 0 matters: a loop that
# ran its body ends with the body's last status, which here is the failed timeout test.
until_ok() { local t=0; until eval "$1" >/dev/null 2>&1; do [ "$t" -ge "$2" ] && return 1; sleep 2; t=$((t + 2)); done; return 0; }
# With pipefail, grep -q stopping early makes the writer fail (SIGPIPE) and so the whole check: the
# greps below read all their input instead.
shows() { notes | grep -F "$1" >/dev/null; }
running() { adb shell dumpsys activity services "$PKG" 2>/dev/null | grep NotifyService >/dev/null; }
prefs() { adb shell run-as "$PKG" cat shared_prefs/mtgassistant.xml 2>/dev/null | tr -d '\r'; }
key_stored() { prefs | grep -F "<string name=\"push_key\">$KEY</string>" >/dev/null; }
# The app's tasks in Recents, one line each: the task's own line and what dumpsys says about whether
# it is shown. A task marked hidden, excluded or not in Recents is left out, since the launcher
# doesn't show it and no gesture can remove it.
recents_tasks() {
  # Each "* Recent #" task ends at the next line indented no deeper than its own header, so a
  # task never takes in what dumpsys prints after it (such as the launcher's fallback home task).
  adb shell dumpsys activity recents 2>/dev/null | tr -d '\r' | python3 -c '
import re, sys
pkg = sys.argv[1]
tasks, cur, depth = [], None, 0
for line in sys.stdin.read().splitlines():
    indent = len(line) - len(line.lstrip())
    if re.match(r"\s*\* Recent #", line):
        cur, depth = [line], indent
        tasks.append(cur)
    elif cur is not None and line.strip() and indent > depth:
        cur.append(line)
    elif line.strip():
        cur = None
for t in tasks:
    own = "\n".join(t)
    # Without -a, dumpsys prints only the task headers, which name the app as A=<uid>:<affinity>
    # (the package, as the app sets no taskAffinity) or I=<package>/<activity>.
    p = re.escape(pkg)
    hit = next((l.strip() for l in t if re.search(r"realActivity=\{?" + p + r"/|\bA=\d+:" + p + r"(?=[\s}])|\bI=\{?" + p + "/", l)), None)
    if hit is None or re.search(r"inRecents=false|isAvailable=false|excludeFromRecents|mHidden=true|hidden=true", own):
        continue
    flags = " ".join(sorted(set(re.findall(r"\b(?:inRecents|isAvailable|mHidden|hidden)=\w+", own))))
    print(t[0].strip() + ("  [" + flags + "]" if flags else "") + "  matched: " + hit)' "$PKG"
}
in_recents() { [ -n "$(recents_tasks)" ]; }
# For a failed check: every task header dumpsys lists, and the whole listing in the run's files.
recents_headers() {
  adb shell dumpsys activity recents 2>/dev/null | tr -d '\r' | tee "$OUT/recents.txt" | grep '\* Recent #' | sed 's/^ *//' | tr '\n' ';'
}
connected() { [ "$(state connections)" -ge 1 ]; }
boot() {
  adb wait-for-device
  until_ok '[ "$(adb shell getprop sys.boot_completed | tr -d "\r")" = 1 ]' 300
  adb reverse "tcp:$PORT" "tcp:$PORT"
}

PUSH_TEST_KEY="$KEY" PUSH_TEST_KEEPALIVE=5 PUSH_TEST_STATIC="$ROOT/../src/mtg_gateway/static" python3 "$HERE/push_test_gateway.py" "$CERTS/leaf.pem" "$CERTS/leaf.key" "$PORT" \
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
# adb shell joins its arguments into one command line, so the device's sh -c gets one quoted string.
adb shell "run-as $PKG sh -c 'mkdir -p shared_prefs && cat > shared_prefs/mtgassistant.xml'" <<XML
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="gateway_origin">https://localhost:$PORT</string>
    <string name="push_key">$KEY</string>
</map>
XML
if key_stored && prefs | grep -F "<string name=\"gateway_origin\">https://localhost:$PORT</string>" >/dev/null; then
  result "set up with the key and the gateway address" ok
else
  result "set up with the key and the gateway address" no "the app's settings file was not written"
  echo "stopping: every scenario after this needs them"; exit 1
fi
# The service isn't exported, so adb can't start it; opening the app starts it, as on a phone.
adb shell am start -W -n "$PKG/local.mtgassistantgateway.app.MainActivity" >/dev/null; sleep 3
adb shell input keyevent KEYCODE_HOME
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
  echo "note: the app's Menu button was not found; it shows only over pages that are not the gateway's (see 01-connected.png)"
fi
adb shell input keyevent KEYCODE_HOME

send m001AbCdEf "Test one" "The first message"
if until_ok 'shows "Test one"' 30; then result "a message shows" ok; else result "a message shows" no "not posted"; fi
shot 02-message

# -- one message per deck: replaced as the count changes, removed when nothing waits
send m002AbCdEf "1 change to Atraxa is waiting for you" "Open it." deck-42
if ! until_ok 'shows "1 change to Atraxa is waiting for you"' 30; then
  result "a deck's message is replaced" no "the deck's first message never showed"
else
  send m003AbCdEf "2 changes to Atraxa are waiting for you" "Open it." deck-42
  # Waits for the end state: while Android swaps the message, dumpsys can list the new one before the
  # old one is gone.
  if until_ok 'shows "2 changes to Atraxa are waiting for you" && ! shows "1 change to Atraxa is waiting for you"' 30; then
    result "a deck's message is replaced" ok
  else
    result "a deck's message is replaced" no "both or neither in dumpsys"
  fi
fi
shot 03-grouped
if ! shows "2 changes to Atraxa are waiting for you"; then
  result "a withdrawn message goes" no "the deck's message was not shown to begin with"
else
  ctl delete -d '{"sequence_id":"deck-42"}' >/dev/null
  if until_ok '! shows "2 changes to Atraxa are waiting for you"' 30; then result "a withdrawn message goes" ok; else result "a withdrawn message goes" no "still shown"; fi
fi

# -- the app swiped away from Recents: the service keeps running
adb shell am start -W -n "$PKG/local.mtgassistantgateway.app.MainActivity" >/dev/null
sleep 3
in_recents_before=no; until_ok in_recents 10 && in_recents_before=yes
size=$(adb shell wm size | tr -d '\r' | awk '{print $NF}')
w=${size%x*}; h=${size#*x}
# Up to three tries: Recents can still be opening when the first swipe lands. Removing the task is
# not instant, so each try waits a little for it to go.
how="swipe"
for _ in 1 2 3; do
  adb shell input keyevent KEYCODE_APP_SWITCH
  sleep 3
  shot recents
  adb shell input swipe $((w / 2)) $((h * 6 / 10)) $((w / 2)) $((h / 20)) 200
  until_ok '! in_recents' 6 && break
done
# The swipe gesture is not reliable on the emulator, so if it didn't take, the debug build's test
# hook (PushTestHooks, written by --prepare into the debug source set only) removes the app's task
# with ActivityManager.AppTask.finishAndRemoveTask, which goes through Android's own task removal as
# a swipe does (read from AOSP, not checked on a device), except that a swipe may also try to end
# the app's process.
if in_recents; then
  echo "note: still in Recents after the swipes: $(recents_tasks | tr '\n' ';')"
  how="test hook (not a launcher swipe)"
  adb shell input keyevent KEYCODE_HOME
  adb shell am broadcast -n "$PKG/local.mtgassistantgateway.app.PushTestHooks" >/dev/null
fi
adb shell input keyevent KEYCODE_HOME
send m004AbCdEf "After swipe" "Still here"
if [ "$in_recents_before" = no ]; then
  result "swiped away from Recents" no "the app was not in Recents to begin with; tasks listed: $(recents_headers)"
elif ! until_ok '! in_recents' 10; then
  result "swiped away from Recents" no "the app was still in Recents after the $how: $(recents_tasks | tr '\n' ';')"
elif running && until_ok 'shows "After swipe"' 30; then
  result "swiped away from Recents (removed by $how)" ok
else
  result "swiped away from Recents" no "service gone or message missing"
fi

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
if [ -z "$bounds" ]; then
  result "ongoing notification dismissed" no "the Always connected notification was not found to swipe away"
elif running && until_ok 'shows "After dismiss"' 30; then
  result "ongoing notification dismissed" ok
else
  result "ongoing notification dismissed" no "service gone or message missing (swipe target found: ${bounds:-no})"
fi

# -- Doze: the phone unplugged and idle; the app is allowed to run in the background
adb shell dumpsys battery unplug
adb shell dumpsys deviceidle force-idle >/dev/null
if [ "$(adb shell dumpsys deviceidle get deep | tr -d '\r')" != IDLE ]; then
  result "Doze" no "the phone did not go idle"
else
  send m006AbCdEf "In Doze" "Arrived while idle"
  if until_ok 'shows "In Doze"' 60; then result "Doze" ok; else result "Doze" no "not delivered while idle"; fi
fi
adb shell dumpsys deviceidle unforce >/dev/null
adb shell dumpsys battery reset

# -- an app update (what Update does: install over the app): it starts again by itself
before=$(state connects)
adb install -r "$APK" >/dev/null
if until_ok '[ "$(state connects)" -gt '"$before"' ] && connected' 90; then result "after an app update" ok; else result "after an app update" no "no new connection"; fi
send m007AbCdEf "After update" "Still here"
if until_ok 'shows "After update"' 30; then result "message after an update" ok; else result "message after an update" no "missing"; fi

# -- a restart of the phone
before=$(state connects)
adb reboot
boot
if until_ok '[ "$(state connects)" -gt '"$before"' ] && connected' 180; then result "after a reboot" ok; else result "after a reboot" no "no connection after boot"; fi
send m008AbCdEf "After reboot" "Still here"
if until_ok 'shows "After reboot"' 30; then result "message after a reboot" ok; else result "message after a reboot" no "missing"; fi
shot 04-after-reboot

# -- missed while offline: since= brings it back once. The emulator reaches the stand-in through adb
# reverse, not its network, so the test cuts that and ends the stream as well as turning the network off.
adb shell svc wifi disable; adb shell svc data disable
adb reverse --remove "tcp:$PORT"
ctl drop -d '{}' >/dev/null
if ! until_ok '[ "$(state connections)" -eq 0 ]' 30; then
  result "missed while offline" no "the app stayed connected"
else
  send m009AbCdEf "While offline" "Missed"
  adb reverse "tcp:$PORT" "tcp:$PORT"
  adb shell svc wifi enable; adb shell svc data enable
  if until_ok 'shows "While offline"' 120 && [ "$(state last_since)" != None ]; then
    result "missed while offline" ok
  else
    result "missed while offline" no "not caught up"
  fi
fi
adb reverse "tcp:$PORT" "tcp:$PORT" 2>/dev/null

# -- Stop (force stop, as Android's task manager does): stays stopped until the app is opened
until_ok connected 60
was_running=no; running && connected && was_running=yes
adb shell am force-stop "$PKG"
sleep 5
if [ "$was_running" = no ]; then
  result "stopped stays stopped" no "the service was not running and connected before Stop"
elif ! running && until_ok '[ "$(state connections)" -eq 0 ]' 30; then result "stopped stays stopped" ok; else result "stopped stays stopped" no "still running"; fi
adb shell am start -W -n "$PKG/local.mtgassistantgateway.app.MainActivity" >/dev/null
if until_ok connected 60; then result "opening the app starts it again" ok; else result "opening the app starts it again" no "no connection"; fi

# -- the gateway says notifications are off: the app stops and forgets the key
if ! key_stored || ! running; then
  result "stops when switched off" no "the key was not stored or the service not running before the switch-off"
elif ctl refuse -d '{"status":403,"error":"off"}' >/dev/null && until_ok '! running' 120 && ! key_stored; then
  result "stops when switched off" ok
else
  result "stops when switched off" no "still running or key kept"
fi
shot 05-end

# -- the page's App menu items (the account menu's App section, on a gateway page): each reaches the app
adb shell pm grant "$PKG" android.permission.CAMERA
app() { adb shell am start -W -n "$PKG/local.mtgassistantgateway.app.MainActivity" >/dev/null; sleep 4; }
# Each item is pressed with a tap on its label. If that has no effect (the label not found on screen,
# or the tap missed), the stand-in page clicks the same button itself, which goes through the same
# click handler and bridge call; the result line says which press worked.
# menu <label> <bridge method> <check> <what failed>
menu() {
  local how
  if tap_text "$1" && until_ok "$3" 15; then how="tapped"
  else
    ctl press -d "{\"name\":\"$2\"}" >/dev/null
    if until_ok "$3" 15; then how="clicked by the page; the tap did nothing"; else how=""; fi
  fi
  if [ -n "$how" ]; then result "menu: $1 ($how)" ok
  else result "menu: $1" no "$4 (the page saw: $(state seen))"; fi
}
app
before=$(hits /)
menu "Reload" reload '[ "$(hits /)" -gt '"$before"' ]' "no new page load"
sleep 2
menu "Open in browser" openInBrowser 'a=$(top) && ! printf "%s" "$a" | grep -F "$PKG/" >/dev/null' "the app stayed in front"
shot menu-open-in-browser
app
before=$(hits /scan)
menu "Scan with the app's camera" openCamera '[ "$(hits /scan)" -gt '"$before"' ]' "the scan page was not opened"
sleep 3
shot menu-scan
adb shell input keyevent KEYCODE_BACK; sleep 1
# The app opens no https links itself, so it is opened afresh on the gateway's page.
adb shell am force-stop "$PKG"; app
menu "Change gateway" changeGateway 'top | grep SetupActivity >/dev/null' "the setup screen did not open"
shot menu-change-gateway

echo "battery: a 24 hour battery test needs a real phone; not run here."
exit "$FAILED"
