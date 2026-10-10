#!/usr/bin/env bash
# Builds the MTG Assistant Gateway Android app with Gradle and the Android SDK.
#
# With Android Studio installed, open android/ instead. Without it, this script fetches Google's
# command-line tools into android/.tools/sdk (about 200 MB with the platform and build tools) and
# accepts the SDK licences on your behalf (https://developer.android.com/studio/terms), then runs the
# Gradle wrapper, which downloads Gradle itself and the AndroidX libraries on first use.
#
# Usage:
#   android/scripts/build.sh debug                 -> android/out/mtg-assistant-gateway-debug.apk
#   android/scripts/build.sh release               -> android/out/mtg-assistant-gateway-release.apk + .aab,
#                                                     signed with $RELEASE_KEYSTORE / $RELEASE_KEY_ALIAS
#                                                     ($RELEASE_STORE_PASS, $RELEASE_KEY_PASS)
#   android/scripts/build.sh release --throwaway   -> same, signed with a fresh throwaway key written to
#                                                     android/out/throwaway.keystore (sideloading only)
#
# App Links: set APP_LINKS_HOST=mtg.example.com to build the applinks flavor, which claims https links
# to that one gateway (docs/ANDROID.md, "App Links"). Unset, the plain flavor is built.
# UPDATE_REPO=owner/name sets where the app looks for its own updates (docs/ANDROID.md, "Updates").
#
# Prerequisites: a JDK (17 or newer), curl, unzip, and keytool from the JDK for --throwaway.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$ROOT/out"
NAME="mtg-assistant-gateway"

MODE="${1:-debug}"
THROWAWAY=0
[ "${2:-}" = "--throwaway" ] && THROWAWAY=1
case "$MODE" in debug|release) ;; *) echo "usage: $0 debug|release [--throwaway]" >&2; exit 2;; esac

log() { printf '\n==> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing tool: $1 ($2)" >&2; exit 1; }; }
need java "install a JDK, 17 or newer"
need curl "install curl"
need unzip "install unzip"

# ---- the Android SDK ---------------------------------------------------------------------------
# Pinned command-line tools; the checksum was taken when first fetched and a mismatch stops the build.
CLT_URL="https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip"
CLT_SHA="7ec965280a073311c339e571cd5de778b9975026cfcbe79f2b1cdcb1e15317ee"
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$SDK" ] && [ -f "$ROOT/local.properties" ]; then
  SDK="$(sed -n 's/^sdk\.dir=//p' "$ROOT/local.properties" | head -1)"
fi
if [ -z "$SDK" ] || [ ! -d "$SDK" ]; then
  SDK="$ROOT/.tools/sdk"
  if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
    log "Downloading the Android command-line tools"
    mkdir -p "$SDK/cmdline-tools"
    curl -fsSL --retry 3 -o "$SDK/clt.zip" "$CLT_URL"
    echo "$CLT_SHA  $SDK/clt.zip" | sha256sum -c --quiet - || { echo "checksum mismatch for $CLT_URL" >&2; rm -f "$SDK/clt.zip"; exit 1; }
    rm -rf "$SDK/cmdline-tools/latest" "$SDK/clt-unpack"
    unzip -q "$SDK/clt.zip" -d "$SDK/clt-unpack"
    mv "$SDK/clt-unpack/cmdline-tools" "$SDK/cmdline-tools/latest"
    rm -rf "$SDK/clt-unpack" "$SDK/clt.zip"
    log "Accepting the Android SDK licences and installing platform 36 and build tools"
    yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" --licenses >/dev/null 2>&1 || true
    "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" "platforms;android-36" "build-tools;36.0.0" >/dev/null
  fi
fi
export ANDROID_HOME="$SDK"
unset ANDROID_SDK_ROOT

# ---- Gradle ------------------------------------------------------------------------------------
FLAVOR=plain
GRADLE_ARGS=()
if [ -n "${APP_LINKS_HOST:-}" ]; then
  FLAVOR=applinks
  GRADLE_ARGS+=("-PappLinksHost=$APP_LINKS_HOST")
fi
# UPDATE_REPO=owner/name: the GitHub repository whose releases the app updates itself from (unset:
# this project's; set but empty: no update check). See docs/ANDROID.md, "Updates".
if [ -n "${UPDATE_REPO+x}" ]; then
  GRADLE_ARGS+=("-PupdateRepo=$UPDATE_REPO")
fi
Flavor="$(tr '[:lower:]' '[:upper:]' <<< "${FLAVOR:0:1}")${FLAVOR:1}"
mkdir -p "$OUT"

if [ "$MODE" = "debug" ]; then
  log "Building the debug APK ($FLAVOR)"
  (cd "$ROOT" && ./gradlew -q ${GRADLE_ARGS[@]+"${GRADLE_ARGS[@]}"} ":app:assemble${Flavor}Debug")
  cp "$ROOT/app/build/outputs/apk/$FLAVOR/debug/app-$FLAVOR-debug.apk" "$OUT/$NAME-debug.apk"
  log "Done: $OUT/$NAME-debug.apk"
  exit 0
fi

# ---- release -----------------------------------------------------------------------------------
if [ "$THROWAWAY" = 1 ]; then
  need keytool "install a JDK"
  KS="$OUT/throwaway.keystore"; ALIAS=throwaway; STOREPASS="throwaway-$(date +%s)"; KEYPASS="$STOREPASS"
  rm -f "$KS"
  log "Creating a throwaway release key (sideloading only; never publish with it)"
  SIGN_STORE_PASS="$STOREPASS" SIGN_KEY_PASS="$KEYPASS" \
  keytool -genkeypair -keystore "$KS" -storepass:env SIGN_STORE_PASS -keypass:env SIGN_KEY_PASS -alias "$ALIAS" \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=MTG Assistant Gateway throwaway,O=MTG Assistant Gateway,C=US" >/dev/null 2>&1
  echo "$STOREPASS" > "$OUT/throwaway.keystore.password"
  export RELEASE_KEYSTORE="$KS" RELEASE_KEY_ALIAS="$ALIAS" RELEASE_STORE_PASS="$STOREPASS" RELEASE_KEY_PASS="$KEYPASS"
  unset SIGN_STORE_PASS SIGN_KEY_PASS
else
  : "${RELEASE_KEYSTORE:?set RELEASE_KEYSTORE to your release keystore (or pass --throwaway)}"
  : "${RELEASE_KEY_ALIAS:?set RELEASE_KEY_ALIAS}"
  : "${RELEASE_STORE_PASS:?set RELEASE_STORE_PASS}"
  export RELEASE_KEYSTORE RELEASE_KEY_ALIAS RELEASE_STORE_PASS RELEASE_KEY_PASS="${RELEASE_KEY_PASS:-$RELEASE_STORE_PASS}"
fi
[ -f "$RELEASE_KEYSTORE" ] || { echo "keystore not found: $RELEASE_KEYSTORE" >&2; exit 1; }
RELEASE_KEYSTORE="$(cd "$(dirname "$RELEASE_KEYSTORE")" && pwd)/$(basename "$RELEASE_KEYSTORE")" # Gradle resolves relative paths against its own project dir

log "Building and signing the release APK and app bundle ($FLAVOR)"
(cd "$ROOT" && ./gradlew -q ${GRADLE_ARGS[@]+"${GRADLE_ARGS[@]}"} ":app:assemble${Flavor}Release" ":app:bundle${Flavor}Release")
cp "$ROOT/app/build/outputs/apk/$FLAVOR/release/app-$FLAVOR-release.apk" "$OUT/$NAME-release.apk"
cp "$ROOT/app/build/outputs/bundle/${FLAVOR}Release/app-$FLAVOR-release.aab" "$OUT/$NAME-release.aab"
APKSIGNER="$(ls -d "$ANDROID_HOME"/build-tools/*/apksigner 2>/dev/null | sort -V | tail -1)"
[ -n "$APKSIGNER" ] || { echo "apksigner not found under $ANDROID_HOME/build-tools" >&2; exit 1; }
"$APKSIGNER" verify --min-sdk-version 29 "$OUT/$NAME-release.apk"
# The signing certificate's SHA-256, which the gateway's /app page and the release notes show so people
# can check who signed what they download (docs/ANDROID.md, section 1).
CERT_SHA="$("$APKSIGNER" verify --print-certs "$OUT/$NAME-release.apk" | sed -n 's/^Signer #1 certificate SHA-256 digest: \([0-9a-f]\{64\}\)$/\1/p' | head -n 1)"
[ -n "$CERT_SHA" ] || { echo "could not read the signing certificate's SHA-256 from apksigner" >&2; exit 1; }
log "Signed APK: $OUT/$NAME-release.apk"

# Metadata the gateway's /app page shows next to the download (see src/mtg_gateway/app_page.py).
VERSION_NAME="$(head -1 "$ROOT/../VERSION" | tr -d '[:space:]')"
VERSION_CODE="$(python3 -c 'import sys; a,b,c=map(int,sys.argv[1].split(".")); print(a*1000000+b*1000+c)' "$VERSION_NAME" 2>/dev/null \
  || awk -F. '{print $1*1000000+$2*1000+$3}' <<< "$VERSION_NAME")"
APK_SHA="$(sha256sum "$OUT/$NAME-release.apk" | cut -d' ' -f1)"
printf '{"version_name": "%s", "version_code": %s, "sha256": "%s", "cert_sha256": "%s", "size": %s, "min_sdk": 29, "target_sdk": 36, "flavor": "%s"}\n' \
  "$VERSION_NAME" "$VERSION_CODE" "$APK_SHA" "$CERT_SHA" "$(stat -c %s "$OUT/$NAME-release.apk")" "$FLAVOR" > "$OUT/$NAME.json"
log "App bundle: $OUT/$NAME-release.aab"

log "Certificate fingerprint (for assetlinks.json / MTG_ANDROID_ASSETLINKS)"
SIGN_STORE_PASS="$RELEASE_STORE_PASS" keytool -list -v -keystore "$RELEASE_KEYSTORE" -storepass:env SIGN_STORE_PASS -alias "$RELEASE_KEY_ALIAS" 2>/dev/null | grep -E 'SHA256:' || true
