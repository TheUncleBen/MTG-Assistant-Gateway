# The Android app

MTG Assistant Gateway is also a small Android app that wraps your gateway. It
opens the gateway's own pages full screen, signs you in the same way the website
does, and adds the one thing a browser page can't give you when scanning cards:
a camera screen with torch brightness, zoom and exposure controls, which on a
foldable lays itself out around the hinge. Everything you see in the app
is served by the gateway, so the app and the website always match, and anyone
who runs their own gateway points the app at theirs.

It is free to use, free to distribute and free to build: today there is no app
store, no developer account and no paid service anywhere in the chain. Each
gateway hands the app out itself. (Google's developer-verification programme
may change that from 2027; see [section 10](#10-googles-developer-verification).)

This page covers using the app, how it is distributed, building and signing it,
and what Google's developer-verification programme means for it. It is honest
about what was and wasn't tested: the app has been built, unit-tested, its
page hand-off exercised in a headless browser, and installed and signed in on
the owner's phone; the camera scan and the fold layouts on a real device are
still the owner's test to report.

## Contents

- [1. Getting the app](#1-getting-the-app)
  - [Adding the app to a gateway](#adding-the-app-to-a-gateway)
- [2. Using it](#2-using-it)
- [3. Scanning with the phone camera](#3-scanning-with-the-phone-camera)
- [4. Foldables and large screens](#4-foldables-and-large-screens)
- [5. What it needs from the gateway](#5-what-it-needs-from-the-gateway)
- [6. How the app is distributed](#6-how-the-app-is-distributed)
- [7. Building the app](#7-building-the-app)
- [8. Signing: your release key](#8-signing-your-release-key)
- [9. Releasing: the CI build and the gateway image](#9-releasing-the-ci-build-and-the-gateway-image)
- [10. Google's developer verification](#10-googles-developer-verification)
- [11. The package ID](#11-the-package-id)
- [12. App Links: opening gateway links in the app](#12-app-links-opening-gateway-links-in-the-app)
- [13. Privacy and security notes](#13-privacy-and-security-notes)
- [14. Not done yet](#14-not-done-yet)

## 1. Getting the app

Open your gateway in the phone's browser, sign in, go to **Account**, then
**Get the Android app** (the `/app` page). It shows the version, the SHA-256
of the file, the SHA-256 fingerprint of the app's signing certificate, and one
download button. Then:

1. Compare the signing certificate SHA-256 on `/app` with the one published in
   the project's GitHub release for that version (in the release notes and in
   the release's `mtg-assistant-gateway.json`), or with one the gateway's owner gave you some
   other way. If they differ, don't install the app; tell the owner. Both values
   on `/app` come from the gateway, the same place as the file, so the file's
   SHA-256 only shows the download arrived intact, not who made it. For a
   stronger check, run `apksigner verify --print-certs mtg-assistant-gateway.apk` on a computer
   and compare the `certificate SHA-256 digest` line with the published value.
2. Open the downloaded `mtg-assistant-gateway.apk`. Android asks you to allow installs from
   that browser once. Allow it and tap **Install**.
3. Play Protect may warn that the developer is unknown or unverified, because
   the app is signed by whoever runs your gateway, not by an app store; the
   exact wording varies by phone and Android version. Only continue if you got
   the file from your gateway's `/app` page and the certificate fingerprint
   matched in step 1. If Play Protect says the app is harmful, or you are
   unsure, don't install it and ask the gateway's owner.
4. On first launch the app asks for your **gateway address**: type the hostname
   the owner gave you, for example `mtg.example.com`. The app checks that the
   address answers as a gateway, saves it, and opens the site. Sign in exactly
   as in a browser.

**Updates** are the same download again: install the newer file over the old
one. Settings and sign-in are kept as long as the gateway signs releases with
the same key ([section 8](#8-signing-your-release-key)). **Change gateway** in
the app's menu (the round button, bottom right) brings the address screen back.

If your gateway's `/app` page says there is no app file, the gateway's image
was built without one: the release build only adds the app when the
maintainers' signing secrets are set ([section 9](#9-releasing-the-ci-build-and-the-gateway-image)).
To check a version, open its
[GitHub release](https://github.com/TheUncleBen/MTG-Assistant-Gateway/releases):
a release that carries the app lists an "Android app signing certificate
SHA-256" line in its notes and has `mtg-assistant-gateway-release.apk` under
**Assets**. A release without them has no app, in the image or on GitHub.
Ask the gateway's owner, who can add the app by hand
([Adding the app to a gateway](#adding-the-app-to-a-gateway)).

### Adding the app to a gateway

For operators whose image has no app, or who build their own:

1. Get the two files: `mtg-assistant-gateway-release.apk` and
   `mtg-assistant-gateway.json`, either from a GitHub release that has them
   or from your own build in `android/out/` ([section 7](#7-building-the-app)).
2. On the node that runs the gateway, put them in a folder of their own as
   real files (not symlinks), with the APK renamed to
   `mtg-assistant-gateway.apk`, readable by the gateway's user:

   ```bash
   sudo mkdir -p /srv/mtg-gateway/app
   sudo cp mtg-assistant-gateway-release.apk /srv/mtg-gateway/app/mtg-assistant-gateway.apk
   sudo cp mtg-assistant-gateway.json /srv/mtg-gateway/app/
   sudo chown -R 1000:1000 /srv/mtg-gateway/app
   ```

3. Mount the folder into the gateway service read-only and point
   `MTG_APP_DIR` at it. In the stack file, under the gateway's `volumes:`:

   ```yaml
         - type: bind
           source: /srv/mtg-gateway/app
           target: /app-dist
           read_only: true
   ```

   and under its `environment:`, `MTG_APP_DIR: /app-dist`. Redeploy the stack.
4. Open `/app` signed in: it now shows the version, checksums and the
   download button. If you built the app yourself, give your users your
   signing certificate SHA-256 some way other than the gateway (step 1 of
   [Getting the app](#1-getting-the-app)), because no GitHub release
   publishes it.

Requirements: Android 10 or later. A camera is optional; without one the app
still works, it just can't scan.

## 2. Using it

The app shows the gateway's pages: home, account, proposals, scan, and whatever
else your gateway serves. A few things are native:

- **The App section of the account menu** (the person icon, top right):
  *Scan with phone camera*, *Reload*, *Open in browser*, *Change gateway*.
  The same four actions sit on a round button at the bottom right while a
  page that is not the gateway's is showing (the sign-in service, say); on
  the gateway's own pages the button stays out of the way of the bottom tab
  bar.
- **Layout.** The gateway's pages know they are in the app (the app adds
  `MTGAssistant/<version>` to the browser's user agent) and never show the
  website footer. Navigation follows Android's window size classes: under
  600 px wide (a phone, a folded foldable) the bottom tab bar; from 600 px (an
  unfolded foldable, a tablet, a wide split-screen pane) a navigation rail
  down the left edge with the same five sections. The rules track the live
  window, so folding, rotating, split screen and pop-up windows re-flow at
  once without a reload.
- **Back** goes back a page; on the first page it leaves the app.
- **Links to your gateway** from other apps open in the app: share a gateway
  link to it from the browser's share sheet, or, when the operator built the
  app with App Links ([section 12](#12-app-links-opening-gateway-links-in-the-app)),
  just tap the link.
- **Links to other sites** (Archidekt, Scryfall) open in your browser. Pages on
  the gateway, and on the one sign-in service the gateway's sign-in redirects
  you to, stay in the app. Links to other apps (`mailto:` and the like) open
  only when you tap them.
- **Sign-in** runs in the app's embedded web view, which suits Authentik's own
  login forms. If the sign-in service sends you on to another site (a "Sign in
  with Discord/GitHub/Google" source, say), that site opens in your browser
  rather than in the app, so a sign-in that chains through a third-party
  provider can't finish in the app; use *Open in browser* in that case and
  tell the gateway's owner. The same goes for sign-in services that hand
  you to another site with a form (SAML or other brokered logins): the app
  stops that page and opens it in the browser, where the form's data is
  lost. If your gateway's sign-in works that way, use the gateway in the
  phone's browser instead of the app.
- **Staying signed in.** The app's sign-in is a browser session like the
  website's, so it ends the same way: when the browser session runs out,
  when you use **Sign out on all my devices** on the `/logout` page, on your
  next tap after the owner removes you from the gateway's group (the
  gateway checks with the sign-in service every few seconds), and once
  after the owner upgrades the gateway to 0.6.1. If the sign-in service
  can't be reached, pages say so and ask you to try again shortly.
- **Downloads** (CSV export, the skill zip) land in your Downloads folder. If
  your sign-in has ended in the meantime, the app says so instead of saving a
  sign-in page; reload, sign in and download again.
- **File pickers** (photo and CSV import) open for the gateway's pages only.
- **The /scan page's own camera** works inside the app: the app asks for camera
  permission once, and grants it to the gateway's pages only.
- **Look and theme.** The app's own screens (setup, error, menu) use the pages'
  palette, and the status bar and navigation bar take the pages' top bar and
  bottom tab bar colours. The app follows the phone's Light / Dark setting;
  the pages follow it too unless you pick Light or Dark in the account menu
  on the page, which wins inside the app as well.

## 3. Scanning with the phone camera

The gateway's `/scan` page already reads cards from a photo (see
[SCANNING.md](SCANNING.md)). The app's camera screen just takes a better photo
than a browser can, then hands it to that page, which keeps running underneath:

1. Menu, **Scan with phone camera**, or the **Phone camera** button the scan
   page shows when it runs inside the app. The app opens `/scan` if you aren't
   on it.
2. Fill the dashed card outline with the card. Controls, each shown only when
   the camera reports it (the app uses CameraX, Google's camera library, and
   asks it what the camera can do):
   - **Torch** on/off.
   - **Brightness** slider for the torch. Shown when CameraX reports adjustable
     torch strength, which it does on Android 15 or later for cameras with more
     than one level. Earlier Android versions can only set torch strength while
     no app has the camera open, so with the preview running the slider isn't
     offered there.
   - **Zoom**, within the range the camera reports.
   - **Brightness** (exposure compensation).
3. Tap **Shoot**. The photo goes to the scan page, which finds the card, reads
   the name and the set line and looks it up, exactly as the page's Photo
   button would. The camera stays open: the result appears as a line over the
   viewfinder. A match waits on the page with **Add** and the printing picker;
   tap **Done** to see it.
4. Or turn **Auto** on and hold up one card after another. This is the page's
   continuous mode with the phone camera: a clean read of a name no other card's
   name begins with is added to the list without a tap and shown over the
   viewfinder with **Undo**; the same card is not added again while it stays in
   view; a shaky read is kept for a tap on the page (*Not sure*); a read with no
   match goes to the page's unidentified list. Each shot is taken as soon as the
   page has finished with the previous one. **Done** closes the camera; the
   list tab shows what was added, or the camera tab shows a read that waits.

The photo never leaves the phone except as the page's normal lookups to your
gateway: it's decoded in the page, which sends what its own camera would send,
the text it read plus the small art fingerprint that tells printings apart.

## 4. Foldables and large screens

- The app keeps the page alive across fold, unfold and rotation (no reload), and
  lets the system size it freely, so it follows the screen you're using.
- Layout of the pages is the gateway's responsibility: they are responsive, and
  wide windows (unfolded foldables, tablets) get the navigation rail and the
  pages' wide layouts, whatever the window's position or orientation.
- **Half-folded postures** are detected with Jetpack WindowManager. With the
  camera open, fold the phone half way and stand it on the table (tabletop
  posture): the viewfinder moves to the upper half and the controls to the flat
  lower half, so you can prop the phone up and feed cards under the lens. Held
  like a book (hinge upright), the viewfinder takes the left page and the
  controls the right. Flat or fully open, the controls sit over the bottom of
  the viewfinder as on any phone. A hinge too close to an edge of the panel is
  ignored rather than leaving a sliver of screen for one side.
- Written against the WindowManager API; the split geometry has unit tests
  (`FoldSplit`), and the half-folded layout still awaits a report from a real
  foldable.

## 5. What it needs from the gateway

The app talks only to pages and endpoints the gateway already serves:

| Used for | Endpoint |
|---|---|
| Checking the address at setup | `GET /healthz` (expects JSON with `"status": "ok"`) |
| Everything else | the normal pages, loaded in the app |
| Phone-camera scans | the `/scan` page's `window.__scan` hooks (`flattenCard`, `flatTitleRegion`, `infoRegionOf`, `scanRegion`, `showTab`, `noCardFrame`, `undoLastAdd`) and its `scan:*` events |
| App Links (optional) | `GET /.well-known/assetlinks.json`, served from `MTG_ANDROID_ASSETLINKS` ([section 12](#12-app-links-opening-gateway-links-in-the-app)) |

The pages work with any gateway version; the phone-camera hand-off needs a scan
page that has those hooks (the edge-detection scan page with continuous mode,
and later), and the app says so instead of retrying when they are missing, or
when the gateway has no `/scan` page at all. Pages the app does not know about
(deck pages, history, admin) are simply pages: whatever the gateway serves
shows in the app, and a gateway without them shows its own 404. The `/app`
download page is new in the gateway version that introduced the app.

The hand-off is covered by a test in the gateway's own suite
(`tests/test_scan_browser.py`, `test_android_glue_drives_the_real_page`): the
exact script the app installs, lifted from `ScanGlue.kt`, runs in headless
Chromium against the real page with synthetic card photos.

## 6. How the app is distributed

Nothing here costs money or needs an account with anyone:

- **From each gateway.** The gateway image ships the APK the release build
  produced and serves it at `/app/mtg-assistant-gateway.apk` behind the normal sign-in, with a
  `/app` page that shows the version, the file checksum and the signing
  certificate fingerprint. Self-hosters therefore give out
  the app from their own deployment. `MTG_APP_DIR` points the gateway at a
  different folder if you build your own ([DEPLOY.md](DEPLOY.md#environment-reference)).
- **From the GitHub release.** The release workflow attaches
  `mtg-assistant-gateway-release.apk`, `mtg-assistant-gateway-release.aab` and `mtg-assistant-gateway.json` to the version's
  GitHub release when the release signing secrets are set
  ([section 9](#9-releasing-the-ci-build-and-the-gateway-image)). Anyone can
  download them there once the repository is public.
- **Not Google Play.** Publishing there needs a developer account with a
  one-time fee, and the app's users all have accounts on a private gateway
  anyway. An app bundle is still produced in case that changes.
- **Not F-Droid.** As far as the maintainers know, F-Droid only accepts apps
  under a free and open-source licence, and this project's PolyForm
  Noncommercial licence isn't one. Verify against F-Droid's inclusion policy
  before relying on that.
- **Obtainium** (a free app that watches a URL for new APKs) can track the
  project's GitHub releases page once the repository is public. It can't track a
  gateway's `/app` page, because that page needs the gateway sign-in.

## 7. Building the app

The sources are in [`android/`](../android/): a standard Gradle project (Kotlin,
AndroidX, CameraX, WindowManager) with the Android Gradle plugin. The build
needs Java 17 or newer and the Android SDK; the script fetches the SDK for you
when none is installed.

### From the command line

```bash
android/scripts/build.sh debug                  # android/out/mtg-assistant-gateway-debug.apk
android/scripts/build.sh release --throwaway    # release APK + .aab, one-off key, sideloading only
android/scripts/test.sh                         # JVM unit tests
```

With no `ANDROID_HOME` (or `ANDROID_SDK_ROOT`, or `sdk.dir` in
`android/local.properties`) the script downloads Google's command-line tools
(pinned, SHA-256 checked) into `android/.tools/sdk`, accepts the SDK licences
on your behalf ([terms](https://developer.android.com/studio/terms)) and installs
platform 36 and build tools 36. The Gradle wrapper then downloads Gradle and the
libraries on first use. Expect a few hundred megabytes and a few minutes the
first time; later builds are quick. Output goes to `android/out/` (not
committed). A release build signed with your own key, which also writes the
`mtg-assistant-gateway.json` the gateway's `/app` page reads:

```bash
RELEASE_KEYSTORE=/path/to/mtg-assistant-release.jks RELEASE_KEY_ALIAS=mtgassistant \
RELEASE_STORE_PASS=... RELEASE_KEY_PASS=... android/scripts/build.sh release
```

Set `APP_LINKS_HOST=mtg.example.com` as well to build the App Links flavor
([section 12](#12-app-links-opening-gateway-links-in-the-app)).

The release build is shrunk with R8 (about 2 MB); the debug build is not.

### With Android Studio

Open the `android` folder in Android Studio and let it sync; the plugin
versions in `android/build.gradle.kts` are the ones the maintainers build with.
Release signing reads `android/keystore.properties` (ignored by git):

```
storeFile=/path/to/mtg-assistant-release.jks
storePassword=...
keyAlias=mtgassistant
keyPassword=...
```

The App Links flavor is `applinks`; pass `-PappLinksHost=mtg.example.com` in
the Gradle command line or `gradle.properties`, or build `plain`.

## 8. Signing: your release key

Android only installs an update over an existing app when both are signed with
the same key, so one key per release line is the rule: make it once, back it up,
and sign every release with it. Losing it means users must uninstall and
reinstall (and lose the app's saved gateway address, nothing more).

```bash
keytool -genkeypair -v -keystore mtg-assistant-release.jks -alias mtgassistant \
  -keyalg RSA -keysize 2048 -validity 10000
```

`keytool` comes with any Java install (on Windows, for example the free
Eclipse Temurin JDK; the command is the same in PowerShell on one line, without
the `\`). It asks for a password and a name; the name is printed in the
certificate, so a project name is enough. On current Java versions the file is
a PKCS12 keystore with one password for both the store and the key, so the
key password is the same as the store password. Keep the `.jks` file and its
password outside the repository, for example in a password manager, with a
backup copy somewhere else. Never commit a
keystore: `android/.gitignore` already excludes `*.jks` and `*.keystore`.

The certificate's SHA-256 fingerprint, which developer registration
([section 10](#10-googles-developer-verification)) and App Links
([section 12](#12-app-links-opening-gateway-links-in-the-app)) need (the
release build prints it too):

```bash
keytool -list -v -keystore mtg-assistant-release.jks -alias mtgassistant | grep SHA256
```

The sideload build in `android/out/` from `--throwaway` is signed with a key
generated at build time (`android/out/throwaway.keystore`). It's for trying the
app, not for a release line: a later build with your real key won't install over
it.

## 9. Releasing: the CI build and the gateway image

Every update to `main` is a release ([VERSIONS.md](VERSIONS.md)). The CI workflow
signs the app for each release (pushes to `main` and manual runs from `main`
only), in the job `android`, which runs in the GitHub **environment**
`android-release`. Keep the signing secrets in that environment, not as
repository secrets (Settings, Environments, `android-release`, Environment
secrets):

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_B64` | the keystore file, base64: `base64 -w0 mtg-assistant-release.jks` (Linux), `base64 -i mtg-assistant-release.jks` (macOS), or in Windows PowerShell `[Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\mtg-assistant-release.jks")) \| Set-Clipboard`, which copies it straight to the clipboard |
| `ANDROID_KEY_ALIAS` | the alias, `mtgassistant` above |
| `ANDROID_STORE_PASS` | the keystore password |
| `ANDROID_KEY_PASS` | the key password (the same as the keystore password for a keystore made as above) |

Paste each value straight into GitHub's secret form; never into a chat, an
issue or a file in the repository.

With the secrets set, a release build runs the unit tests, builds and signs the APK
with the runner's Android SDK, copies
the APK into `docker/app-dist/` so the published image serves it at `/app`, and
the release job attaches APK, bundle and `mtg-assistant-gateway.json` to the
version's GitHub release, with the signing certificate's SHA-256 in its notes. Without the
secrets the job prints a notice and the image ships without the app; the `/app`
page then says so. The job adds a few minutes of Actions time per release.

The job's `if:` keeps other branches out of the signing job, but it is not a
security boundary on its own: anyone with write access can push a branch whose
workflow file says something else. What actually protects the key is the
environment, so set it up like this:

- **Deployment branches and tags**: *Selected branches and tags*, with the
  branch rule `main`. Runs from any other ref then never receive the secrets.
- **Required reviewers** (optional): yourself (or whoever may release), so
  every signing run waits for an approval in the Actions tab. The release waits
  with it: the image and the GitHub release follow only once you approve, so
  leave it off if releases should publish without you.
- Delete any repository-level copies of the four `ANDROID_*` secrets, and
  protect `main` (Settings, Rules) so that only people you trust can change it.

If the environment doesn't exist yet, GitHub creates it unprotected on the
first run, so create and protect it before the first release.

Inside the job, the keystore is decoded, used and deleted in one step (a shell
`trap` removes it even when the build fails, and a following step checks it is
gone), and every action the workflow uses is pinned to a full commit SHA, so a
moved tag upstream can't run new code next to the key. The signing
certificate's SHA-256 is written into `mtg-assistant-gateway.json` as `cert_sha256`, shown on
the run's summary and appended to the GitHub release notes, which is the
independent value people compare `/app` against
([section 1](#1-getting-the-app)).

Pull requests that change `android/` (and aren't drafts) run a separate
`android-check` job: a debug build and the unit tests, with no secrets and a
read-only token, so build-script and Kotlin changes are exercised before they
are merged rather than first in the signing job. Pull requests that don't touch
`android/` skip it.

One optional repository **variable** (not secret), `ANDROID_APP_LINKS_HOST`,
set to your gateway's hostname, makes the release build the App Links flavor
([section 12](#12-app-links-opening-gateway-links-in-the-app)).

## 10. Google's developer verification

Google is introducing identity verification for the developers of apps installed
on certified Android devices (devices with Google Play services), including
apps installed outside Google Play. The points below are what Google's
"Android developer verification" pages on developer.android.com, its FAQ and
the account guides said when read on 2026-10-05. Google is still changing the
programme, so check those pages before relying on any of it:

- **Timeline.** From September 30, 2026, apps whose package names aren't
  registered to a verified developer can't be installed *from the participating
  app stores* in Brazil, Indonesia, Singapore and Thailand on certified devices
  running Android 7 or later. Google's FAQ says that deadline applies only to
  those stores: sideloading and other stores are not affected by it. The
  verification requirement is to be "expanded globally for all apps on
  certified Android devices in 2027".
- **Free option.** A **limited distribution account** in the Android Developer
  Console is free, needs a Google account with 2-step verification and a legal
  name and address but **no government ID**, and lets a developer share apps
  with **up to 20 devices** that their owners explicitly authorise (a QR code or
  link handshake). Google describes it for hobbyists sharing with family and
  friends with no commercial intent, which matches this project.
- **Paid option.** A **full distribution account** costs US$25 (one-time) and
  needs identity verification. Google Play's own registration fee is the same
  amount.
- **Power users.** Google says an "advanced flow", launched August 2026, lets a
  user choose to install apps from unverified developers after a one-time setup.
- **Registration** ties a package name to the SHA-256 fingerprint of its signing
  key. If several developers use the same package name with different keys,
  Google allocates the name by install counts.

**What this means for free distribution of MTG Assistant Gateway.** As Google described it on
that date, nothing changes today for users outside the four countries above,
and sideloading isn't affected anywhere until the 2027 global rollout. For 2027: a gateway operator who signs
the app with their own key can keep it free by registering the package name
under a limited distribution account (20 devices is the ceiling; friends of one
gateway fit, a public user base doesn't), or their users can turn on the
advanced flow. Because registration is per package name and per key, an
operator who builds and signs their own copy should also give it their own
package ID ([section 11](#11-the-package-id)) rather than share
`local.mtgassistantgateway.app` with other operators' keys. The maintainers will revisit
this when Google publishes the 2027 details.

## 11. The package ID

Every Android app has a package ID (also called application ID), a dotted
name like `local.mtgassistantgateway.app`. Android uses it as the app's permanent
identity: two apps with the same ID are the same app, so an update must keep it,
and two different apps can't share it on one phone. It is **not** shown to
users and has nothing to do with the display name (MTG Assistant Gateway) or the gateway
address.

The usual convention is a domain you control, reversed, plus a name. This app is
sideloaded, not published in a store, so it doesn't need one:
`local.mtgassistantgateway.app` is a neutral name that belongs to no domain or person.
It lives in one file, `android/app/application-id.txt`; the Gradle build reads it from
there. Change it and rebuild; nothing else needs editing. The Kotlin package name
(`local.mtgassistantgateway.app`, where the source lives) stays as it is: the manifest
names the app's screens in full, so they don't depend on the package ID. Operators who sign
their own builds should pick their own ID
(see [section 10](#10-googles-developer-verification)).

## 12. App Links: opening gateway links in the app

Android can hand https links to a site straight to an app, with no "open with"
prompt, once the site has vouched for the app. Because the gateway address is
typed in, not built in, the standard build claims no links: it is the same APK
for every gateway. An operator who wants their gateway's links to open in the
app builds the **applinks** flavor for their own host and publishes the
matching statement on the gateway:

1. Build with `APP_LINKS_HOST=mtg.example.com` (or set the
   `ANDROID_APP_LINKS_HOST` repository variable for the CI build). The
   resulting APK claims `https://mtg.example.com/...`.
2. Put the signing certificate's SHA-256 fingerprint
   ([section 8](#8-signing-your-release-key)) in `MTG_ANDROID_ASSETLINKS`
   ([DEPLOY.md](DEPLOY.md#environment-reference)), which the gateway serves at
   `/.well-known/assetlinks.json`:

   ```json
   [{"relation": ["delegate_permission/common.handle_all_urls"],
     "target": {"namespace": "android_app",
                "package_name": "local.mtgassistantgateway.app",
                "sha256_cert_fingerprints": ["D4:F7:32:...:97:1E"]}}]
   ```

   The package name must match `android/app/application-id.txt`.
3. Install the app. Android checks the statement at install time; after that a
   tap on a gateway link opens the page in the app. If verification fails
   (wrong fingerprint, the file not served, the gateway behind a sign-in wall
   for that path), the link opens in the browser as before, and the app's page
   in Settings, **Open by default**, says why.

Without App Links, a gateway link can still be shared to the app from the
browser's share sheet, and the app opens it. Links to other sites are ignored,
and so is any link that arrives before the app has been set up: the address
screen is shown without a prefill, so a link can't pick your gateway for you.

## 13. Privacy and security notes

- The app has no server of its own and no analytics. It stores one setting,
  the gateway address. It asks for two permissions, internet and camera; the
  network-state permission one of its libraries declares is removed from the
  manifest. Sign-in cookies live in the system WebView's cookie store
  for this app only. None of it is backed up or copied to a new phone: backup
  is off, and from Android 12 the data-extraction rules also exclude everything
  from cloud backup and device-to-device transfer, so a new phone signs in
  afresh.
- `https` is required for the gateway address, and cleartext traffic is off in
  the manifest.
- JavaScript runs only for pages the WebView loads, and the native bridge the
  app exposes to pages (`MtgNative`) answers only the gateway's own page: the
  app notes which page is showing when a call arrives and checks again before
  acting, so a page that calls on its way out can't act on the gateway page
  that replaces it (Android's framework WebView puts the object on every page;
  the origin-scoped alternative needs AndroidX). Camera permission and the file
  picker are for the gateway's origin only; file access from pages is off;
  mixed content is blocked.
- Pages from other sites never load inside the app, so a link can't put a
  look-alike page behind the app's chrome. The one exception is the sign-in
  service: a single https origin, the one the gateway advertises at
  `/.well-known/mtg-gateway` (the app reads it from your gateway each time it
  starts and keeps it with the gateway address). A gateway too old to have
  that page falls back to the redirect that directly follows a gateway
  `/login`. The sign-in service's pages stay in the app only during a
  sign-in; any further site it redirects to opens in the browser. On the page
  where you approve or deny connecting an AI application, Approve continues
  in the app only to that sign-in service, and Deny, which goes back to the
  application's own site, always opens in the browser. A page from another
  site that slips in without the app being asked (the redirect after a form)
  is stopped and handed to the browser, and the app goes back to the gateway;
  if that happens again within a few seconds the app unloads the page and
  shows Retry. A page can send at most one link to the browser every few
  seconds without a tap, so it can never open tab after tab. Once a sign-in finishes,
  the app forgets the pages it went through, so Back doesn't land on the
  sign-in service's old login page.
- Links to other apps leave the app only when tapped in the page itself (not
  in a frame, not from a redirect or a script), and only to apps that accept
  links from a browser, as in Chrome.
- Downloads are fetched by the app itself from the gateway, with the gateway's
  cookies and without following redirects, so the cookies never go to another
  host. (Android's DownloadManager would re-send them to wherever a redirect
  pointed.)
- The gateway serves the APK and its page behind the normal sign-in, so the file
  isn't an anonymous download. `/app` shows the file's SHA-256 and the signing
  certificate's SHA-256, but both come from the gateway, the same place as the
  file: the file checksum is only a download-integrity check, and someone who
  can replace the APK can replace both values too. The real check on a first
  install is comparing the certificate fingerprint with the one published in
  the GitHub release ([section 1](#1-getting-the-app)); after that, Android
  refuses updates signed with a different key.

## 14. Not done yet

- **Device coverage is thin.** The app has been installed and signed in on
  one phone; the camera controls, the photo hand-off and the fold layouts are
  how the code is written and unit-tested, not yet something observed working
  on a device.
- **Camera choice**: CameraX's default back camera. Phones with several back
  cameras may prefer another one.
- **Developer registration** for 2027 ([section 10](#10-googles-developer-verification))
  is the operator's step, not something the build can do.
- **A Trusted Web Activity** instead of the WebView was considered and left
  out: it would hand the pages to Chrome, where the app's camera panel, its
  photo hand-off and its bridge cannot reach them.
