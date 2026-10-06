#!/usr/bin/env bash
# Runs the app's JVM unit tests (android/app/src/test) through Gradle. Fetches the Android SDK the
# same way scripts/build.sh does when none is installed.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$SDK" ] && [ -f "$ROOT/local.properties" ]; then SDK="$(sed -n 's/^sdk\.dir=//p' "$ROOT/local.properties" | head -1)"; fi
if [ -z "$SDK" ] || [ ! -d "$SDK" ]; then
  [ -x "$ROOT/.tools/sdk/cmdline-tools/latest/bin/sdkmanager" ] || "$HERE/build.sh" debug >/dev/null
  SDK="$ROOT/.tools/sdk"
fi
export ANDROID_HOME="$SDK"
unset ANDROID_SDK_ROOT
cd "$ROOT" && ./gradlew -q :app:testPlainDebugUnitTest
for f in app/build/test-results/testPlainDebugUnitTest/*.xml; do
  sed -n 's/.*<testsuite name="\([^"]*\)" tests="\([0-9]*\)" skipped="[0-9]*" failures="\([0-9]*\)" errors="\([0-9]*\)".*/\1: \2 tests, \3 failures, \4 errors/p' "$f"
done
