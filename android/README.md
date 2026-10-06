# MTG Assistant Gateway Android app

The Android companion app for the gateway: the gateway's pages full screen,
plus a phone-camera scan panel (CameraX) with torch brightness, zoom, exposure
and a continuous Auto mode, laid out around the hinge on a half-folded phone
(Jetpack WindowManager).

- Using, building, signing and distributing it: [docs/ANDROID.md](../docs/ANDROID.md)
- `scripts/build.sh debug|release [--throwaway]`: builds with Gradle, fetching the
  Android SDK into `.tools/` when none is installed
- `scripts/test.sh`: JVM unit tests
- `settings.gradle.kts`, `app/build.gradle.kts`: the Gradle project, which Android
  Studio opens directly

Output goes to `out/`, downloaded tools to `.tools/`; both are ignored by git,
as is every keystore and `local.properties`.
