# Updating the gateway

This page is for every update: what to do before it, the update itself, and the extra steps a few
versions need. The deployment guides ([DEPLOY.md](DEPLOY.md) for Swarm and Portainer,
[DEPLOY-COMPOSE.md](DEPLOY-COMPOSE.md) for Docker Compose) always describe the current version
from a fresh start.

The gateway never updates itself. Twice a day it asks GitHub whether a newer release is out and, if
so, shows its admins a notice with these steps ([OPERATIONS.md](OPERATIONS.md#restart-or-update)).

## Before every update

1. **Read the release notes** of every version between yours and the new one: the
   [CHANGELOG](../CHANGELOG.md), or the project's GitHub releases, which carry the same text. A
   version whose update needs more than the steps below says so under **Upgrade notes** and has a
   section in [Upgrade notes by version](#upgrade-notes-by-version) here. Do those steps for every
   version you skip over, oldest first.
2. **Take a backup.** The gateway upgrades its database when it starts, and an older image refuses
   to start on a database a newer one upgraded. Going back means restoring the backup you took
   before updating and running the old image on it ([OPERATIONS.md](OPERATIONS.md#backups)).
   - Swarm, on the node that runs the gateway:
     `docker exec --user 1000:1000 $(docker ps -q -f name=mtg_mtg-assistant-gateway) mtg-gateway backup`
   - Docker Compose: `docker compose exec --user 1000:1000 gateway mtg-gateway backup`

   (Use your own `PUID:PGID` in place of `1000:1000`.)

## Update steps

### Portainer

**Stacks** → `mtg`. If `MTG_TAG` names a version, change it to the new one first
([VERSIONS.md](VERSIONS.md)); with `MTG_TAG=latest` the update pulls the newest version. If the
release notes say the stack file changed, replace the stack's text with the new
[`deploy/portainer-stack.yml`](../deploy/portainer-stack.yml) (with the **Repository** build
method, the update brings it). Then click **Update the stack** (with the Repository build method
the button is called **Pull and redeploy**), and in the box that asks “Are you sure?” make sure
**Re-pull image and redeploy** is on, then click **Update**.

### Swarm without Portainer

Set `MTG_TAG` in your variables file if it is pinned, update your copy of the repository
(`git pull`), load the variables into a new shell and run `docker stack deploy` again, as in
[DEPLOY.md](DEPLOY.md#without-portainer).

### Docker Compose

```bash
# change MTG_TAG in .env to the new release if it is pinned, then:
docker compose pull
docker compose up -d
```

If the release notes say the Compose file changed, get the new `docker-compose.yml` first (see
[0.7.29](#0729-included-in-0730) below for how, and what to carry over).

## After updating

`curl -s https://mtg.example.com/healthz` answers `{"status":"ok"}`, and the admin page's
**System** card shows the new version. Updates are `stop-first`, so expect a few seconds of
downtime; signed-in assistants keep working.

## Upgrade notes by version

Only versions that need more than the update steps are listed, newest first. A version not listed
here needs nothing extra.

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

0.7.29 was not released on its own. Anyone updating to 0.7.30 or later from 0.7.26 or earlier does
these steps as part of the 0.7.30 update, where its steps point here.

#### Swarm or Portainer stack

0.7.29 adds the admin Settings page. A stack file from before 0.7.29 gives 24
of the page's 47 settings a value of its own, so the page would show them
locked (greyed out) even with no variable set.

1. **If you pasted the stack file into Portainer's Web editor:** first note
   your own edits to the old text: the optional secret lines you removed the
   `# ` from (`mtg_authentik_api_token`, `mtg_ntfy_token`, each in two
   places) and any line you added under `environment:`. Then, in
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
3. If `MTG_TAG` names a version, change it to `0.7.30` first (in the stack's
   variables, or in `mtg.env` without Portainer); otherwise you redeploy the
   old version with the new stack file. Click **Update the stack** (if your
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
`environment:`. Then download the new file (if you cloned the repository, run
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
