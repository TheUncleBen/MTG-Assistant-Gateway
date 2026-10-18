#!/usr/bin/env bash
# Checks that a release build's merged AndroidManifest.xml files (every one the Android Gradle plugin
# writes for the variant) carry no debug-only test hook: the PushTestHooks receiver that
# push-test.sh --prepare writes into android/app/src/debug (an exported receiver any app on the phone
# could trigger). build.sh runs it after every release build.
#
# Usage: android/scripts/check-release-manifest.sh <android/app/build directory> <flavor, e.g. plain>
set -euo pipefail

BUILD="${1:?usage: $0 <app build directory> <flavor>}"
FLAVOR="${2:?usage: $0 <app build directory> <flavor>}"
found=0
while IFS= read -r -d '' manifest; do
  found=1
  if grep -q "PushTestHooks" "$manifest"; then
    echo "the release manifest declares the push test hook (PushTestHooks): $manifest" >&2
    exit 1
  fi
done < <(find "$BUILD/intermediates" -path "*/${FLAVOR}Release/*" -ipath "*manifest*" -name AndroidManifest.xml -print0 2>/dev/null)
if [ "$found" = 0 ]; then
  echo "no merged release manifest for the $FLAVOR flavor under $BUILD/intermediates" >&2
  exit 1
fi
