# Updating the gateway

An update is a fresh install on top of your data: take the newest stack file and the newest
example variables file, put your own settings in, and deploy, as on the first day
([DEPLOY.md](DEPLOY.md) for Swarm and Portainer, [DEPLOY-COMPOSE.md](DEPLOY-COMPOSE.md) for
Docker Compose). You don't need older release notes, and you don't take a backup first: when the
new version has to upgrade the database, the gateway saves a copy of it before it changes
anything ([below](#what-the-gateway-does-by-itself)).

The gateway never updates itself. Twice a day it asks GitHub whether a newer release is out and, if
so, shows its admins a notice with these steps ([OPERATIONS.md](OPERATIONS.md#restart-or-update)).

**What stays as it is:** the data folder (`MTG_DATA_DIR`), the backup folder (`MTG_BACKUP_DIR`),
the Docker secrets you already made, your identity provider, the overlay network and the reverse
proxy. **What you take new each time:** the stack file (or `docker-compose.yml`), which you use
unchanged, and the example variables file, into which you copy your own values.

## Update steps

### Portainer

1. **Your values.** Save your current variables as `mtg.env.old`: your old `mtg.env`, or the
   stack's **Environment variables** in **Advanced mode** copied out of Portainer. Then download
   the newest [`deploy/stack.env.example`](../deploy/stack.env.example) and save it as `mtg.env`.
   Copy into it every value your old file sets: the lines marked `REQUIRED`, and any optional
   line you changed. To list the lines your old file sets, run
   `grep -v -e '^#' -e '=$' -e '^$' mtg.env.old`, or read through it. A variable the new file
   doesn't list any more has no effect: leave it out. The comments in the new file explain every
   line, so this is also the moment to see what is new.
2. **Secrets.** Do [DEPLOY.md step 4](DEPLOY.md#4-docker-secrets-once) again. It only adds a
   secret that is missing, and the commands there skip any secret you already have (in
   Portainer, skip the ones the **Secrets** list already shows). Your old secrets stay as they
   are.
3. **The stack.** **Stacks** → `mtg` → **Editor**. Replace the whole text with the newest
   [`deploy/portainer-stack.yml`](../deploy/portainer-stack.yml), unchanged. (With the
   **Repository** build method the update brings the newest file itself.) Under **Environment
   variables**, switch to **Advanced mode** and replace everything there with your new `mtg.env`.
   Then click **Update the stack** (with the Repository build method the button is called
   **Pull and redeploy**), and in the box that asks “Are you sure?” make sure **Re-pull image and
   redeploy** is on, then click **Update**.

### Swarm without Portainer

On a Swarm manager, in your copy of the repository:

```bash
git pull                                  # the newest stack file and example
cp mtg.env mtg.env.old
cp deploy/stack.env.example mtg.env       # then copy your values in, as in Portainer step 1
```

Do [DEPLOY.md step 4](DEPLOY.md#4-docker-secrets-once) again for any secret that is missing.
Then open a new shell (a variable you removed stays set in the old one) and load the variables
and deploy: the `while ... done < mtg.env` line and the `docker stack deploy ...` line under
[Without Portainer](DEPLOY.md#without-portainer).

### Docker Compose

In the folder with `docker-compose.yml`, get the newest files (if you cloned the repository, run
`git pull` in it instead of the three `curl` lines):

```bash
base=https://raw.githubusercontent.com/TheUncleBen/MTG-Assistant-Gateway/main/deploy/compose
curl -fsSLO "$base/docker-compose.yml"
curl -fsSLO "$base/make-secrets.sh" && chmod +x make-secrets.sh
cp .env .env.old
curl -fsSL "$base/.env.example" -o .env
```

**Now edit `.env`** and copy your values in from `.env.old`, as in Portainer step 1 (in
particular `MTG_DATA_DIR`, `MTG_BACKUP_DIR` and `MTG_SECRETS_DIR` if you changed them, so the
gateway finds your data and secrets). Then:

```bash
./make-secrets.sh          # adds only a secret file that is missing
docker compose pull
docker compose up -d
```

## What the gateway does by itself

When a new version first starts on your database, it checks whether the database needs
upgrading. When it does, the gateway:

1. saves a full copy of the database in `before-upgrade`, a folder inside your backup folder
   (`<MTG_BACKUP_DIR>/before-upgrade/`, or inside the data folder when `MTG_BACKUP_DIR` isn't
   set), named like `mtg-gateway-before-upgrade-20261021T030000Z-schema15.sqlite`;
2. checks the copy (it runs SQLite's own check on it and makes sure it holds the same database
   version it copied) before it keeps it;
3. upgrades the database in one step: the upgrade happens whole or not at all, so one that fails
   or is cut off leaves the database exactly as it was;
4. keeps the newest 3 copies and deletes older ones. A start that keeps failing saves its copy
   once, not on every try. Every copy is also deleted once it is older than
   `MTG_BACKUP_KEEP_DAYS` (14 days unless you changed it), as the nightly backups are.

If it can't save the copy (for example the disk is full or the folder can't be written), it
doesn't upgrade, changes nothing and doesn't start. Its log says why, with a line starting
`database error: the database was not upgraded`, such as `... not enough free space in
/backups/before-upgrade: it needs about 40 MB and has 12 MB`. Fix that and update the stack
again, or go back to the version you ran before, which still works on the unchanged database.

The copy is the database exactly as it was, so going back needs nobody to relink Archidekt. That
also means it holds members' Archidekt sessions and the identity provider's tokens, still
encrypted with the Fernet key, which is never in the file. So it is readable by the gateway's own
user only, stays in the backup folder, and is never copied to `MTG_BACKUP_COPY_DIR`. (The nightly
backups leave those out, as before: [OPERATIONS.md](OPERATIONS.md#backups).) When a member
unlinks Archidekt or presses **Delete my data**, or an admin disables, unlinks or deletes a
member, the gateway makes the same change in every copy in the folder at once; a copy it can't
change is deleted.

A version that doesn't change the database saves no copy; the release notes say when a version
changes it.

## After updating

`curl -s https://mtg.example.com/healthz` answers `{"status":"ok"}`, and the admin page's
**System** card shows the new version and, under **Newest before-upgrade copy**, the newest
copy. Updates are `stop-first`: the old gateway stops before the new one starts, so it is
unreachable for a short while; signed-in assistants keep working.

## Going back to an older version

**To 0.7.37 or newer:** change `MTG_TAG` to that version and update the stack with that
version's stack file and example file (each version's files are on its branch,
`release/X.Y.Z`, see [VERSIONS.md](VERSIONS.md)). If a newer version had upgraded the database,
the older one finds its copy in `before-upgrade` and puts it back by itself when it starts:

- First it saves the newer database in the same folder as a set-aside copy,
  `mtg-gateway-set-aside-...sqlite`, so nothing is lost. The newest 3 set-aside copies are kept,
  each for at most `MTG_BACKUP_KEEP_DAYS`, like the before-upgrade copies.
- Members disabled, Archidekt sessions deleted and members' **Delete my data** since the upgrade
  stay so, and each member keeps the Archidekt link and phone connection they have now (going
  back to 0.7.39 or newer). Every other change made since the upgrade (proposals, snapshots,
  settings, someone you enabled again) is only in the set-aside copy: enable such a member again
  on the Users page.
- It ends every sign-in, so everyone signs in once more, and the admin page says what happened.

If there is no copy for it, it doesn't start, and its log says so: run the newer version again.
That is also the case once the copy was older than `MTG_BACKUP_KEEP_DAYS` and has been deleted,
so going back this way works only within that many days of the upgrade (the nightly backups from
before the upgrade are gone by then too). To keep the way back open longer, raise
`MTG_BACKUP_KEEP_DAYS` before you update.

**To 0.7.36 or older:** these versions don't go back by themselves. A version that didn't change
the database needs nothing more than the `MTG_TAG` change. If the newer version upgraded the
database (its release notes say so), put the copy it saved back by hand first: the newest file in
`before-upgrade` whose name starts `mtg-gateway-before-upgrade-`. On the gateway's node, with
your folders and your `PUID` (`1000` here):

```bash
docker service scale mtg_mtg-assistant-gateway=0       # Compose: docker compose stop gateway
sudo ls -t <MTG_BACKUP_DIR>/before-upgrade              # the newest copy is listed first
sudo -u '#1000' sh -c 'umask 077 && cd <MTG_DATA_DIR> && mkdir newer-1 &&
  mv mtg-gateway.sqlite* newer-1/ && cp <MTG_BACKUP_DIR>/before-upgrade/<the copy> mtg-gateway.sqlite'
```

Put the file name `ls` listed in place of `<the copy>`. The `newer-1` folder keeps the newer
database; if one is already there from an earlier time, use another name. Then end every sign-in
in the copy with the command under **Restore** in [OPERATIONS.md](OPERATIONS.md#backups) (that
page also says what a restore by hand brings back, such as members you disabled since), and
update the stack with the old version's `MTG_TAG` and files.

**Going forward again with the set-aside database:** to get back the changes made after the
upgrade, stop the gateway as above, and with the same `sudo -u '#1000' sh -c '...'` line move the
`mtg-gateway.sqlite*` files into a new folder and copy the `mtg-gateway-set-aside-...` file in as
`mtg-gateway.sqlite`. Then update the stack to the newer version again.

- **Every sign-in ends.** The set-aside copy still holds the sign-ins from before you went back,
  including ones revoked while the older version ran. A set-aside copy made by 0.7.39 or newer
  is marked, and the newer version ends every sign-in by itself at its first start on it. One
  made by 0.7.37 or 0.7.38 isn't: before you update the stack, end every sign-in in the file with the
  command under **Restore** in [OPERATIONS.md](OPERATIONS.md#backups).
- **What happened while the older version ran isn't in it.** Look through the Users page and
  disable, unlink or delete again any member you disabled, unlinked or deleted while the older
  version ran. A member who
  unlinked Archidekt or pressed **Delete my data** in that time does it again.

## Coming from an older version

The update steps above bring every stack, variable and secret change up to now. A few changes
reach past the stack, so if your old version is older than the one named, also:

- **0.7.29 or older:** settings you keep in your variables lock them on the gateway's Settings
  page (greyed out with that value). Leave out of `mtg.env` (or `.env`) any setting you'd rather
  change on the page.
- **0.7.29 or older:** members' notifications come in the gateway's Android app, not the ntfy
  app. Each member who wants them ticks **Send me notifications** on their Account page in the
  app and presses **Save notifications**. Members on an app older than 0.7.12 install the new app
  from the gateway's Install page (uninstalling the old one first). If you ran an ntfy server of
  your own for the gateway, you can stop it, and revoke the old token there.
- **0.7.32 or older:** the gateway sends at most 30 Archidekt requests a minute by default
  instead of 40. To keep 40, set "Archidekt requests per minute" on the Settings page.
- **0.7.36 or older:** the stack file and `docker-compose.yml` now always attach the
  `mtg_authentik_api_token` secret, so you no longer remove the `# ` from its lines. The secrets
  step above (Portainer step 2) creates it with the word `off` if you don't have it; with Compose,
  `./make-secrets.sh` does.

Every version's full notes are in the [CHANGELOG](../CHANGELOG.md), and the older update steps
are kept below for reference.

## Older update notes

These are the extra steps the versions from 0.7.29 to 0.7.33 needed when an update meant keeping
your old stack file and variables. The update steps above already cover them, and the steps
below that point “above” mean the update steps of those versions, which kept the old stack file.

### 0.7.33

The stack file and the Compose file name a new Forge image, `2.0.15-mag4`, which can play up to 16
games at once (the older `2.0.15-mag3` plays at most 4).

1. Take the new stack file or Compose file as the update steps above describe. If your variables
   set `FORGE_TAG`, Forge stays on that version: change it to `2.0.15-mag4`, or remove it to follow
   the stack file. With the older image the gateway still works, and a "Forge games played at
   once" above 4 plays at most 4 at once.
2. If you turned on the optional `mtg_authentik_api_token` lines in the old file, turn them on
   again in the new one, as for [0.7.32](#0732) step 3.
3. The gateway now sends at most 30 Archidekt requests a minute by default instead of 40, which
   makes large changes take a little longer. To keep 40, set "Archidekt requests per minute" on the
   Settings page, or `MTG_ARCHIDEKT_MAX_PER_MINUTE=40` in your variables (which locks it on the
   page).

### 0.7.32

The stack file and the Compose file now pass 27 more variables to the gateway and Forge
themselves: the 21 settings `MTG_AUTO_BIG_EDIT_ROWS`, `MTG_PROPOSAL_EXPIRY_DAYS`,
`MTG_ARCHIDEKT_BACKUP_COPIES`, `MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT`, `MTG_SNAPSHOTS_PER_DECK`,
`MTG_FORGE_PARALLEL_GAMES` and the 15 `MTG_SCAN_*` settings, and also `MTG_ANDROID_PACKAGE`,
`MTG_ANDROID_ASSETLINKS`, `MTG_PLUGIN_DIR`, `MTG_APP_DIR`, `FORGE_MAX_METASPACE` and
`FORGE_OOM_SCORE_ADJ`. The Compose file passes `MTG_ARCHIDEKT_BASE` too. Before 0.7.32 the guides
said to add a line under `environment:` in the stack file for these.

1. When you make your own edits again in the new file (the update steps above), **don't copy a
   line you added under `environment:` for any of these variables.** The new file already has
   that name there, and Docker refuses a file that names a variable twice under `environment:`.
   Put the value in your variables instead: Portainer's variables, `mtg.env`, or `.env` with
   Docker Compose.
2. Any of the 21 settings that your variables already set now reaches the gateway, and it locks
   that setting on the Settings page (greyed out with that value). To change the setting on the
   page instead, remove it from your variables.
3. If you turned on the optional `mtg_authentik_api_token` lines in the old file, turn them on
   again in the new one, as in
   [IDP-AUTHENTIK.md](IDP-AUTHENTIK.md#12-optional-removed-member-clean-up) step 7. The new file
   has them commented out. If your Portainer stack uses the **Repository** build method, the
   stack file comes from the repository as it is, so the lines stay commented out: use the
   **Web editor** method, or a copy of the repository you keep yourself with the lines turned
   on. Without them the admin page says the removed-member clean-up is off because the token
   can't be read.
4. **Check the values of those 21 settings in your variables before you deploy.** The 0.7.31
   stack file didn't pass them, so a value there had no effect. Now it reaches the gateway, which
   checks it when it starts: a value outside its range, or one that isn't a number (or isn't a
   whole number where the range is in whole numbers), stops the gateway from starting. Each start
   then logs one line naming the first such setting, such as `configuration error:
   MTG_SNAPSHOTS_PER_DECK must be between 5 and 100`, and the gateway keeps restarting (Swarm
   restarts the service; Compose restarts the container). To fix it, change the value to one in
   range, or remove it from your variables to manage the setting on the Settings page, then deploy
   the stack again. The allowed values:

   | Variable | Allowed |
   |---|---|
   | `MTG_AUTO_BIG_EDIT_ROWS` | 1 to 100 |
   | `MTG_PROPOSAL_EXPIRY_DAYS` | 1 to 30 |
   | `MTG_ARCHIDEKT_BACKUP_COPIES` | 1 to 50 |
   | `MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT` | `true` or `false` |
   | `MTG_SNAPSHOTS_PER_DECK` | 5 to 100 |
   | `MTG_FORGE_PARALLEL_GAMES` | 1 to 16 |
   | `MTG_SCAN_FUZZY_MIN_SIMILARITY` | 0.3 to 1.0 |
   | `MTG_SCAN_FUZZY_CONFIDENT_SIMILARITY` | 0.3 to 1.0 |
   | `MTG_SCAN_AMBIGUITY_MARGIN` | 0.0 to 0.5 |
   | `MTG_SCAN_AUTO_ADD_CONFIDENCE` | 0.0 to 100.0 |
   | `MTG_SCAN_FOIL_STAR_INK_RATIO` | 0.01 to 0.9 |
   | `MTG_SCAN_GLARE_RATIO` | 0.005 to 0.9 |
   | `MTG_SCAN_MIN_OCR_CONFIDENCE` | 0.0 to 100.0 |
   | `MTG_SCAN_ART_ENABLED` | `true` or `false` |
   | `MTG_SCAN_ART_MAX_DISTANCE` | 0 to 1024 |
   | `MTG_SCAN_ART_MIN_MARGIN` | 0 to 1024 |
   | `MTG_SCAN_ART_MAX_PRINTINGS` | 1 to 2000 |
   | `MTG_SCAN_ART_IMAGE_INTERVAL` | 0.05 to 5.0 |
   | `MTG_SCAN_ART_IMAGES_PER_HOUR` | 1 to 36000 |
   | `MTG_SCAN_ART_MAX_GALLERIES` | 1 to 100000 |
   | `MTG_SCAN_ART_GALLERY_IDLE_DAYS` | 1 to 3650 |

   For `true` or `false`, the gateway reads `1`, `true` or `yes` (any case) as true and any other
   value as false (an empty one keeps the default), so a mistyped value there doesn't stop it: it
   turns the setting off.

   The table gives the values the current version accepts: 0.7.32 itself accepted 1 to 4 for
   `MTG_FORGE_PARALLEL_GAMES`, and 0.7.33 raised the top to 16.

### 0.7.30

#### Swarm or Portainer stack

0.7.30 brings its own notification server (ntfy) into the stack, and the
gateway's Android app gets the members' notifications from it through the
gateway's own address.

1. **Before you update the stack**, create the `mtg_ntfy_token` secret
   ([DEPLOY.md](DEPLOY.md#4-docker-secrets-once) step 4: the fourth command in Option B, or Option A). The new stack file
   needs it; without it the stack won't deploy. **If you already have an
   `mtg_ntfy_token` secret** (it held the token for an ntfy server of your
   own), make a new one instead, so the stack's ntfy server doesn't accept a
   token your old server accepts too: Swarm won't remove a secret a service
   still uses, so first run
   `docker service update --secret-rm mtg_ntfy_token mtg_mtg-assistant-gateway`,
   then `docker secret rm mtg_ntfy_token`, then that step 4 command. The
   gateway can't start without its token file in between, so do this just
   before you update the stack. Revoke the old token on your old ntfy server
   as well.
2. If your stack's environment has `MTG_NTFY_URL` or `MTG_NTFY_TOKEN_FILE`
   with a value, delete those lines (empty is fine): the stack file now
   points the gateway at its own ntfy server. If you ran an ntfy server of
   your own for the gateway, you can stop it after the update, and delete
   the proxy host you made for it: the new ntfy server needs none.
3. **If Forge is on:** the new stack file pulls Forge image `2.0.15-mag3`.
   If your variables file was copied from an older
   `stack.env.example`, it still sets `FORGE_TAG=2.0.15-mag2`, which keeps
   the old Forge image: change it to `FORGE_TAG=2.0.15-mag3`, or delete the
   line. Delete its `FORGE_GAME_SECONDS=600` line too, and
   `FORGE_MAX_PARALLEL=1` if it has one, to get the new defaults (a
   four-minute game limit, and Forge working out how many games fit at
   once). Its `MTG_FORGE_GAMES=10` keeps 10 games a report, locked on the
   Settings page; delete it for the new default of 4.
4. Update the stack with the new file, as in the 0.7.29 steps below (your
   old `mtg_ntfy_token` lines, if you had them, are already in it).
5. Tell the members: notifications now come in the gateway's own Android
   app, not the ntfy app, and members need app 0.7.30 to get them. Each
   member who wants them opens their Account page in the app, ticks **Send
   me notifications** and presses **Save notifications**, which also connects
   that phone (**Get notifications on this phone** stays on the card while
   a phone isn't connected). Their old subscriptions in the ntfy
   app stop getting messages and can be deleted. There are no notifications
   on iPhone.
6. **Members on an app older than 0.7.12 must update the app:** in those
   apps, sign-in no longer starts by itself. They install the new app from the
   gateway's Install page (uninstalling the old one first, since it was
   signed with a different key).

#### Docker Compose

0.7.30 adds the stack's own notification server (ntfy). Get the new
`docker-compose.yml` as in the 0.7.29 steps below, and the new helper with
`curl -fsSLO "$base/make-secrets.sh"` (same `base`). Then run
`./make-secrets.sh` again before `docker compose up -d`: it adds
`secrets/mtg_ntfy_token.txt` and leaves your other files alone. If that file
already exists (it held the token for an ntfy server of your own), delete it
first so a new token is made, and revoke the old token on your old ntfy
server. If your `.env`
has `MTG_NTFY_URL` or `MTG_NTFY_TOKEN_FILE` with a value, delete those lines;
an ntfy server you ran for the gateway can go, with its proxy host.
If Forge is on, the new file pulls Forge image `2.0.15-mag3`. Then follow
the 0.7.29 steps below, but skip the
`mtg_ntfy_token` edits: the new file already has those lines.
Notifications now come in the gateway's Android app (members tick **Send
me notifications** and press **Save notifications** on their Account page in the app), not the
ntfy app, and members need app 0.7.30 to get them. **Members on an app older than 0.7.12 must
update the app.**

### 0.7.29 (included in 0.7.30)

0.7.29 was not released on its own. If you are updating from 0.7.26 or earlier to 0.7.30 or later,
do these steps as part of the 0.7.30 steps above, which point here.

#### Swarm or Portainer stack

0.7.29 adds the admin Settings page. A stack file from before 0.7.29 gives 24
of the page's 47 settings a value of its own, so the page would show them
locked (greyed out) even with no variable set.

1. **If you pasted the stack file into Portainer's Web editor:** first note
   your own edits to the old text: the optional secret lines you removed the
   `# ` from (`mtg_authentik_api_token`, `mtg_ntfy_token`, each in two
   places) and any line you added under `environment:` (leave out the ones
   [0.7.32](#0732) lists). Then, in
   **Stacks** → `mtg` → **Editor**, replace the whole text with the new
   [`deploy/portainer-stack.yml`](../deploy/portainer-stack.yml) and make
   the same edits in it. Without the Authentik lines the removed-member
   clean-up turns off (the admin page says so); without the ntfy lines the
   gateway won't start. With the **Repository** build method and this
   repository, the update brings the new file. If Portainer pulls your own
   copy of the repository instead, bring that copy up to date first and keep
   your edits in it.
   **Without Portainer:** in your copy of the repository, keep a copy of your
   edited file (`cp deploy/portainer-stack.yml
   deploy/portainer-stack.old.local.yml`). Then run
   `git checkout deploy/portainer-stack.yml` (this undoes your edits to it),
   then `git pull`. Make the same edits in the new
   `deploy/portainer-stack.yml` (your saved copy shows what they were).
2. **Optional:** for each setting you want to change on the Settings page,
   delete its variable. A variables file made from the older
   `stack.env.example` sets 19 of them, such as `MTG_ARCHIDEKT_BACKUPS=true`
   and `MTG_DECK_SYNC_MINUTES=30`. Any variable you keep stays locked: the
   Settings page shows it greyed out with its value, and you change it in
   the stack and redeploy.
3. If `MTG_TAG` names a version, change it to the version you are updating
   to (0.7.30 or later) first, in the stack's variables or, without Portainer,
   in `mtg.env`. Otherwise you redeploy the old version with the new stack file. Click **Update the stack** (if your
   stack uses the Repository build method, the button is called **Pull and
   redeploy**). In the box that asks “Are you sure?”, make sure **Re-pull
   image and redeploy** is on, then click **Update**. Without Portainer: open a new shell (a variable you
   deleted from `mtg.env` stays set in the old one), go to your copy of the
   repository (`cd` into it), and run the last two lines under
   [Without Portainer](DEPLOY.md#without-portainer) again.

#### Docker Compose

Before the update commands ([Docker Compose](#docker-compose) above), note your own edits to the old `docker-compose.yml`:
the optional secret lines you removed the `# ` from (`mtg_authentik_api_token`,
`mtg_ntfy_token`, each in two places) and any line you added under
`environment:` (leave out the ones [0.7.32](#0732) lists). Then download the new file (if you cloned the repository, run
`git checkout docker-compose.yml`, which undoes your edits to it, then
`git pull`, instead):

```bash
base=https://raw.githubusercontent.com/TheUncleBen/MTG-Assistant-Gateway/main/deploy/compose
curl -fsSLO "$base/docker-compose.yml"
```

This replaces your old file, so make the same edits in the new one. Without
the Authentik lines the removed-member clean-up turns off (the admin page says
so); without the ntfy lines the gateway won't start.

The old file gives 24 of the Settings page's 47 settings a value of its own,
which locks them on the page (greyed out with that value). An `.env` made
from the older `.env.example` sets 17 of them. **Optional:** delete the lines
of the ones you want to change on the Settings page. Any you keep stay
locked: you change them in `.env`, then run `docker compose up -d`.
