# Setting up Authentik for the gateway

The gateway runs its own OAuth 2.1 server for Claude, ChatGPT and other MCP
clients, but it doesn't handle passwords. When someone connects, it sends
their browser to **one** OpenID Connect (OIDC) provider in your Authentik to
sign in, then issues its own tokens.

This is the long version of [DEPLOY.md](DEPLOY.md) step 3: every Authentik
object the gateway needs, what to put in each field, why, and how to check
it.

Placeholders:

| Placeholder | Means |
| --- | --- |
| `https://mtg.example.com` | the gateway's public URL, the value of `MTG_PUBLIC_URL` |
| `https://auth.example.com` | your Authentik's public URL |
| `MTG Assistant Gateway Users` | the Authentik group whose members may use the gateway |

## Contents

1. [What the gateway needs from Authentik](#1-what-the-gateway-needs-from-authentik)
2. [Before you start](#2-before-you-start)
3. [Create the access group](#3-create-the-access-group)
4. [Create the OAuth2/OpenID provider](#4-create-the-oauth2openid-provider)
5. [Create the application and bind the group](#5-create-the-application-and-bind-the-group)
6. [Collect the issuer, client ID and client secret](#6-collect-the-issuer-client-id-and-client-secret)
7. [Gateway settings and the Docker secret](#7-gateway-settings-and-the-docker-secret)
8. [Add people: accounts, invitations, groups](#8-add-people-accounts-invitations-groups)
9. [Check the sign-in](#9-check-the-sign-in)
10. [Troubleshooting](#10-troubleshooting)
11. [Versions tested and how](#11-versions-tested-and-how)
12. [Optional: removed-member clean-up](#12-optional-removed-member-clean-up)

## 1. What the gateway needs from Authentik

This comes straight from the gateway's code (`src/mtg_gateway/oidc.py`,
`config.py`, `auth_provider.py`). Each row is something the steps below set
up.

| Need | What the gateway does | Where it's set in Authentik |
| --- | --- | --- |
| Discovery | Loads `<MTG_OIDC_ISSUER>/.well-known/openid-configuration` and requires the `issuer` inside to match (a trailing slash on either side doesn't matter) | The provider's **OpenID Configuration Issuer**, `https://auth.example.com/application/o/mtg-gateway/`. The slug in it is the **application's** slug |
| Sign-in flow | Authorization code with PKCE (`S256`). The redirect URI is `MTG_PUBLIC_URL` + `/auth/callback` | Provider → **Redirect URIs**: `https://mtg.example.com/auth/callback`, matching mode **Strict** |
| Client authentication | Sends the client ID and secret in the token request body (`client_secret_post`) | Provider → **Client type: Confidential**. Copy the **Client ID** and **Client Secret** |
| Scopes | Asks for `openid profile email offline_access` (`MTG_OIDC_SCOPES`; if you set it yourself, include `offline_access`) | Provider → **Scopes**: the four built-in mappings `authentik default OAuth Mapping: OpenID 'openid'`, `… 'email'`, `… 'profile'` and `… 'offline_access'`. **Don't skip `offline_access`**: without it Authentik silently issues no refresh token, and members are asked to sign in again every time Authentik's access token runs out (an hour by default) |
| ID token | Must be in the token response and signed with RS256, RS384, RS512, ES256, ES384, ES512 or PS256. HS256 is refused | Provider → **Signing Key**: a certificate, by default `authentik Self-signed Certificate` (RS256). Leave **Include claims in id_token** on |
| Identity | Reads `sub` (required), `email`, `name`, `preferred_username` and `groups` from the ID token, and fills any gaps from `/userinfo` | The default claims of the `email` and `profile` mappings. Authentik's `profile` mapping includes `groups` (checked by the end-to-end tests, section 11) |
| Stable identity | Stores people by `sub`. Linked Archidekt accounts, proposals and tokens all hang off it | Provider → **Subject mode**: `Based on the User's hashed ID` (the default). Change it after people have signed in and everyone becomes a new, empty user |
| Who gets in, first gate | Nothing. Authentik decides before the gateway ever sees the person | Application → **Policy / Group / User Bindings**: bind `MTG Assistant Gateway Users` |
| Who gets in, second gate | Anyone whose `groups` claim includes neither `MTG_REQUIRED_GROUP` nor `MTG_ADMIN_GROUP` (exactly) gets HTTP 403, and any gateway browser sessions they had are closed. The gateway won't start with it empty unless `MTG_ALLOW_ANY_IDP_USER=true` | The same group name in `MTG_REQUIRED_GROUP`. Exact, case-sensitive match |
| Live membership | Before serving any request that carries a browser session or a gateway token, asks Authentik's `/userinfo` for the person's groups as they are now, renewing Authentik's access token with its refresh token when needed. The answer is cached for `MTG_MEMBERSHIP_CHECK_TTL` seconds (5 by default; `0` asks every time); after that, a request within 60 seconds of Authentik last letting that sign-in in is answered at once while Authentik is asked in the background, and a page being read (outside `/admin`) is answered at once whenever Authentik let that sign-in in before, so the first page after a pause never waits; writes and admin pages past 60 seconds wait. Someone taken out of the group loses every gateway token and session and their Archidekt link on their next request. Someone deactivated or deleted shows up only as refused tokens: each of their sign-ins ends when next used, their AI apps keep their gateway tokens but are refused until a new sign-in (which Authentik won't allow), and their stored Archidekt session stays until an admin presses Disable or Unlink, it expires (about 40 days after linking), or the optional hourly clean-up in section 12 deletes it. If Authentik can't be reached (a dropped connection is tried once more first), requests get a 503 and nothing is revoked | The `offline_access` scope mapping (above). Nothing else |
| Sign-out | The gateway's **Sign out** button ends its own browser session (with `Clear-Site-Data`); **Sign out on all my devices** (on the `/logout` page) ends every browser and Android app session of that person. It doesn't call Authentik's end-session endpoint, so the Authentik session stays, but for the next hour a sign-in to the gateway's pages in that browser or app asks Authentik for the password again (`prompt=login`), so the next person on a shared device isn't signed straight back in | Provider → **Invalidation flow**: Authentik requires one, so use the default. Nothing else |
| Refresh at the provider | Keeps Authentik's access and refresh token from each sign-in (one set per browser or Android app, one for the member's AI apps), encrypted with the `mtg_fernet_key` secret, and uses them only for the live membership check; while the member is active it renews each set at least once a day. AI clients still get the gateway's own tokens, never Authentik's | Provider token lifetimes can stay at their defaults. Keep the refresh token validity at least as long as the gateway's **Assistant refresh** time (the Settings page's Sign-in rows, or `MTG_REFRESH_TOKEN_TTL`; both 30 days by default): when Authentik refuses an expired refresh token the gateway can't tell that from a deactivated account, so that sign-in ends and the member signs in again there (their other devices, AI apps' tokens and Archidekt link are kept) |

## 2. Before you start

- Authentik is reachable at `https://auth.example.com` with a certificate
  from a public CA. The gateway checks it against the CA bundle in the image
  (the `certifi` package), so a private CA won't work with the published
  image.
- You can get into the Authentik admin interface (`/if/admin/`).
- You know the gateway's final public URL. The redirect URI and the issuer
  depend on it and on the application slug, so changing either later means
  editing the provider.
- Authentik's default flows exist:
  `default-provider-authorization-implicit-consent` (or the
  `-explicit-consent` one) and `default-provider-invalidation-flow`. A fresh
  Authentik creates them from its default blueprints within a minute of
  first start (**Flows and Stages → Flows**).
- The gateway's and Authentik's clocks agree, 5 seconds apart at most (both
  machines set their time over NTP). The Android app's sign-in in the phone's
  browser checks when the person last signed in at Authentik against when that
  sign-in started, allowing 5 seconds of difference: with clocks further
  apart, that sign-in is refused and the app finishes it another way.

## 3. Create the access group

**Directory → Groups → Create**

| Field | Value |
| --- | --- |
| Name | `MTG Assistant Gateway Users` |
| Is superuser | off |
| Parent | none |

Do this first so the later steps can point at it. Add yourself now
(**Directory → Users → your user → Groups → Add to existing group**), or in
section 8 with everyone else.

Want a second tier, say people who can see Authentik's app page but **can't**
use the gateway? Make a second group and bind only the first one in section
5. The end-to-end tests do exactly that to prove the two gates are separate
(section 11).

## 4. Create the OAuth2/OpenID provider

**Applications → Providers → Create → OAuth2/OpenID Provider**

| Field | Value | Why |
| --- | --- | --- |
| Name | `MTG Assistant Gateway` | Shows on Authentik's consent and user pages |
| Authentication flow | leave empty (Authentik uses its default) | |
| Authorization flow | `default-provider-authorization-implicit-consent` | Signs people straight through once they're logged in to Authentik. The `-explicit-consent` flow works too; people then see an Authentik consent page listing the scopes each time |
| Invalidation flow | `default-provider-invalidation-flow` | Authentik requires one. The gateway never triggers it |
| Client type | **Confidential** | The gateway holds a client secret |
| Grant Types (Authentik 2026.5 and newer) | at least **Authorization Code** and **Refresh token**; you can untick the rest | Authentik refuses a sign-in whose grant type the provider doesn't list. The form ticks all of them by default, and an upgrade keeps an existing provider's. The gateway uses only these two |
| Client ID | keep the generated value | Goes in `MTG_OIDC_CLIENT_ID` (it's not a secret) |
| Client Secret | keep the generated value; you'll reveal it once in section 6 | Goes into the Docker secret `mtg_oidc_client_secret` and nowhere else |
| Redirect URIs | one entry, matching mode **Strict**, URL `https://mtg.example.com/auth/callback` | Exactly `MTG_PUBLIC_URL` plus `/auth/callback`, no trailing slash. Strict mode refuses anything else |
| Signing Key | `authentik Self-signed Certificate` (or any RSA/EC certificate you manage) | Gives RS256 ID tokens. With **no** signing key, Authentik signs with HS256 using the client secret, which the gateway refuses (reported from Authentik's behaviour; the symptom is in section 10) |
| Encryption Key | none | The gateway doesn't decrypt tokens |
| Advanced protocol settings → Scopes | `authentik default OAuth Mapping: OpenID 'openid'`, `… 'email'`, `… 'profile'` and `… 'offline_access'`. Nothing else needed | `openid` makes Authentik issue an ID token. `email` and `profile` carry the claims from section 1, and `profile` includes `groups`. `offline_access` makes Authentik issue a refresh token, which the gateway needs to keep checking membership after Authentik's access token runs out. Leave it out and Authentik says nothing; members just get asked to sign in again every hour |
| Subject mode | **Based on the User's hashed ID** | A stable, opaque `sub`. Never a username or email mode: people can change those themselves, and whoever picks up an old value inherits that gateway account. Don't change it after the first sign-in |
| Include claims in id_token | **on** (the default) | At sign-in the gateway reads the ID token first and only falls back to `/userinfo`. The live membership check always asks `/userinfo` |
| Issuer mode | **Each provider has a different issuer, based on the application slug** (the default, `per_provider`) | Gives you the issuer in section 6 |
| Access code, access token and refresh token validity | defaults (1 minute, 1 hour, 30 days) | A minute is plenty for the redirect. The gateway renews Authentik's access token with the refresh token when it runs out. Don't make the refresh token validity shorter than the gateway's **Assistant refresh** time (30 days by default); see *Refresh at the provider* in section 1 |

Save. If the Signing Key list is empty, make one first under **System →
Certificates → Generate**.

A note on labels: the field names above match the API fields the end-to-end
tests set (`client_type`, `redirect_uris[].matching_mode`, `signing_key`,
`property_mappings`, `sub_mode`, `include_claims_in_id_token`,
`authorization_flow`, `invalidation_flow`). The admin UI labels are
reported, not re-checked on a live screen. If one has moved, go by the
meaning in the *Why* column.

## 5. Create the application and bind the group

**Applications → Applications → Create**

| Field | Value | Why |
| --- | --- | --- |
| Name | `MTG Assistant Gateway` | Shows in Authentik's user portal |
| Slug | `mtg-gateway` | Becomes part of the issuer URL, so pick it once |
| Provider | `MTG Assistant Gateway` (section 4) | |
| Launch URL | `https://mtg.example.com/` | Optional. The tile in Authentik's portal opens the gateway's front page |

Save, then open the application and go to **Policy / Group / User Bindings →
Bind existing policy/group/user**:

| Field | Value |
| --- | --- |
| Type | **Group** |
| Group | `MTG Assistant Gateway Users` |
| Enabled | on |
| Order | `0` |

With at least one binding, Authentik only lets members of the bound groups
through. Everyone else gets Authentik's *Permission denied* page and never
reaches the gateway. **With no binding at all, every Authentik account can
sign in**, and `MTG_REQUIRED_GROUP` is your only gate. Set up both.

Authentik's newer **Create with Provider** wizard makes the same objects in
one go; the field values are the same.

## 6. Collect the issuer, client ID and client secret

Open **Applications → Providers → MTG Assistant Gateway**.

- **OpenID Configuration Issuer**:
  `https://auth.example.com/application/o/mtg-gateway/`. That's
  `MTG_OIDC_ISSUER`. Keep the trailing slash as shown (the gateway is fine
  either way). Sanity check in a browser:
  `https://auth.example.com/application/o/mtg-gateway/.well-known/openid-configuration`
  should return JSON whose `issuer` is that URL.
- **Client ID**: a long hex string. That's `MTG_OIDC_CLIENT_ID`.
- **Client Secret**: click the eye icon to reveal it, and paste it only into
  the command in section 7. Not into the stack's environment variables, not
  into a chat, not into a file in the repository.

## 7. Gateway settings and the Docker secret

On a Swarm manager, create the client-secret Docker secret. This reads it
without showing it on screen: paste, press Enter. It never lands in shell
history or the stack's environment.

```bash
read -rs S && printf %s "$S" | docker secret create mtg_oidc_client_secret - ; unset S
```

You can also run plain `docker secret create mtg_oidc_client_secret -`,
paste, press Enter, then Ctrl-D. That works, but what you pasted stays
visible in the terminal's scrollback, so clear the screen afterwards. A
trailing newline is fine either way; the gateway strips it.

**Rotating it later.** Docker secrets can't be changed, so create the new
value under a new name (say `mtg_oidc_client_secret_v2`) and map it to the
old name inside the container in the stack file, so nothing else has to
change:

```yaml
secrets:
  mtg_oidc_client_secret_v2:
    external: true
services:
  mtg-assistant-gateway:
    secrets:
      - source: mtg_oidc_client_secret_v2
        target: mtg_oidc_client_secret
```

Redeploy, make sure a sign-in works, then `docker secret rm` the old one.
Rotate it together with the Client Secret on the Authentik provider. The
other three, `mtg_fernet_key`, `mtg_session_secret` and `mtg_ntfy_token`, are covered in
[DEPLOY.md](DEPLOY.md) step 4.

Stack environment variables for the identity provider (the rest are in
[DEPLOY.md](DEPLOY.md) step 6):

| Variable | Value |
| --- | --- |
| `MTG_PUBLIC_URL` | `https://mtg.example.com` |
| `MTG_OIDC_ISSUER` | `https://auth.example.com/application/o/mtg-gateway/` |
| `MTG_OIDC_CLIENT_ID` | the Client ID from section 6 |
| `MTG_OIDC_CLIENT_SECRET_FILE` | `/run/secrets/mtg_oidc_client_secret` (already set in `deploy/portainer-stack.yml`) |
| `MTG_OIDC_SCOPES` | leave the default, `openid profile email offline_access` |
| `MTG_REQUIRED_GROUP` | `MTG Assistant Gateway Users`, exactly as the group is named in Authentik. Required: the gateway refuses to start with it empty unless `MTG_ALLOW_ANY_IDP_USER=true` |

`MTG_REQUIRED_GROUP` compares the name character for character with the
`groups` claim. Spaces are fine; case and punctuation have to match.

## 8. Add people: accounts, invitations, groups

Who can use the gateway is decided entirely in Authentik. The gateway has no
user list of its own. A person shows up in its database the first time they
sign in, and is known by their `sub` from then on.

**Make an account for them.** **Directory → Users → Create**. Fill in
Username, Name and Email (the gateway shows Name, or the email if Name is
empty). Save, open the user, and use **Set password** or **Create recovery
link**. Send them the link so they pick their own password. Then **Groups →
Add to existing group → MTG Assistant Gateway Users**.

**Let them sign up with an invitation.** This needs an enrollment flow with
an invitation stage, which a default Authentik doesn't ship turned on. If
you have one: **Directory → Invitations → Create**, choose that flow, set an
expiry and **Single use** (always both: an invitation that pre-fills the
group and can be reused is an open door for anyone who sees the link),
optionally pre-fill their groups in the invitation's fixed data, and send
them the link. They create their own
account. You still add them to `MTG Assistant Gateway Users` unless the invitation did
it. (Menu names reported from Authentik's docs; the end-to-end tests don't
cover invitations.)

**Social or external logins** (Google, GitHub, LDAP and so on) set up as
Authentik **Sources** work as-is. The gateway only ever sees the ID token
Authentik issues. Group membership still has to be given in Authentik, by
you: see the next part.

### MFA and who can join

Anyone who signs in as a member of `MTG Assistant Gateway Users` can change that
member's Archidekt decks through the gateway. So the group and the
passwords behind it are what protect those decks. Recommended:

- **Require a second factor.** Give the gateway an authentication flow that
  asks for one: either set the provider's **Authentication flow** to a flow
  of its own whose **Authenticator Validation** stage allows TOTP or WebAuthn
  and has **Not configured action** set to force the user to set one up, or
  change that setting on the default authentication flow if every
  Authentik application should require it. Add a **Reputation** policy to
  the identification stage to slow down password guessing.
- **Nothing adds people to the group by itself.** Enrollment flows, source
  property mappings and invitation fixed data must not put anyone into
  `MTG Assistant Gateway Users` unless that link is single use and expires. A Source
  that enrolls new users should use an enrollment flow that adds no groups,
  so a stranger with a Google or GitHub account gets an Authentik account
  at most, not gateway access.
- **Don't link accounts by email for sources that don't confirm emails.**
  A Source's **User matching mode** of linking to an existing user with the
  same email lets anyone who registers that email at the provider sign in
  as your user. Use identifier matching, or the email mode only for
  providers that confirm addresses.

(Menu names reported from Authentik's docs; the end-to-end tests don't
cover MFA or Sources.)

**Remove someone.** Take them out of `MTG Assistant Gateway Users`, or
deactivate or delete the user. That's all. The gateway asks Authentik on
every request; so that pages don't wait for Authentik, the identity
provider is asked again in the background (writes and admin pages wait
once 60 seconds have passed since it last let them in). Taken out of the
group, they lose their gateway tokens, browser sessions, the Authentik
tokens it kept and their Archidekt link, and are signed out everywhere. A
deactivated or deleted user shows up as refused tokens, so each of their
sign-ins ends when next used and their AI apps are refused (press
**Revoke tokens and sessions** or **Disable** to end those at once); the link stays until an admin presses
**Disable**, **Unlink Archidekt** or **Delete data** on the admin page, until
it expires (about 40 days after they linked; the hourly purge then deletes
it), or until the optional clean-up in section 12 removes it. Authentik
reports such a user only as a refused token, which looks the same as an
expired one. A new sign-in
then fails at Authentik's binding, or at the gateway's group check if the
binding lets them through. This was
tested live against Authentik 2026.2.2: taking someone out of a group
revokes nothing in Authentik itself (it keeps honouring their refresh
token), but its `/userinfo` shows the change at once, and deactivating or
deleting the user invalidates their Authentik tokens at once. Taking someone
out of `MTG_ADMIN_GROUP` likewise removes the admin page on their next
request. More in [OPERATIONS.md](OPERATIONS.md#revoking-access).

Send each person [ONBOARDING.md](ONBOARDING.md) along with the gateway's
hostname.

### Profile pictures

The account icon shows the same picture Authentik shows for the member,
following **System → Settings → Avatars** (Authentik tries each entry in
order), and keeps it up to date with the live membership check:

- **Gravatar** (`gravatar` in Avatars): Authentik sends its address in the
  `picture` claim, and the gateway fetches the picture itself, once per
  change, so members' browsers never contact Gravatar;
- **a picture the member uploaded to their Authentik profile** (an
  `attributes.…` entry in Avatars, filled by a file field in the user
  settings flow): see below;
- anything else, or nothing: the member's initials, drawn by the gateway.

Only PNG, JPEG, GIF and WebP pictures are kept (checked by their bytes, never
SVG), and only Gravatar addresses are fetched; other addresses are ignored.
The copy lives under the gateway's data folder and *Delete my data* removes
it.

**Uploaded pictures.** Authentik keeps an uploaded picture as a whole image
inside the user's attributes. Authentik 2026.8.0 to 2026.8.2 copied it into
the `picture` claim, which made every token as big as the image (a 1 MB
picture meant 1 MB tokens on every check); the gateway still copes with
that, but upgrade. Authentik 2026.8.3 and newer leave it out of `picture`,
so tokens stay small. To show uploaded pictures on 2026.8.3 and newer, add
this optional scope mapping **and select it on the provider**: both steps,
or nothing is sent. It puts only a 16-character fingerprint of the
picture in tokens; the gateway asks for the image itself only when the
fingerprint changes.

1. **Customization → Property Mappings → Create → Scope Mapping**.
   Name: `MTG Assistant Gateway: uploaded picture`. Scope name: `profile`.
   Expression:

<!-- uploaded-picture-mapping: tests/e2e/authentik_setup.py installs exactly this expression -->
```python
# MTG Assistant Gateway: a picture uploaded to the user's profile, kept out of tokens.
avatar = request.user.avatar or ""
if not avatar.startswith(("data:image/png", "data:image/jpeg", "data:image/gif", "data:image/webp")):
    return {}
http = request.http_request
if http is not None and http.GET.get("mtg_picture") == "1":
    return {"mtg_picture": avatar}
from hashlib import sha256

return {"mtg_picture_version": sha256(avatar.encode()).hexdigest()[:16]}
```
<!-- /uploaded-picture-mapping -->

2. **Applications → Providers → the gateway's provider → Edit → Advanced
   protocol settings → Scopes**: move `MTG Assistant Gateway: uploaded
   picture` from Available to Selected, next to the four built-in ones
   (`openid`, `email`, `profile`, `offline_access`), and save. This is the
   step that makes Authentik send it; creating the mapping alone does
   nothing, and with an uploaded picture first in **Avatars** the member gets
   no Gravatar either, so the gateway keeps drawing initials.

Nothing changes on the gateway, and nobody needs to sign in again: the
picture appears within a few seconds of the member's next page. Any other
application on the same provider that asks userinfo for `mtg_picture=1`
gets the member's own picture, nothing more.

## 9. Check the sign-in

1. Deploy the stack ([DEPLOY.md](DEPLOY.md) step 6). The service goes
   healthy without talking to Authentik; the first sign-in is the first
   contact.
2. In a browser, open `https://mtg.example.com/account`. You get sent to
   Authentik, sign in, and land back on the account page with your name and
   a **Sign out** button.
3. On the account page, open **What your sign-in shares**. It should show
   your username, email and a `groups` list containing
   `MTG Assistant Gateway Users`. Then connect Claude or ChatGPT
   ([CONNECT.md](CONNECT.md)) and call the `whoami` tool: it answers with
   your name only (assistants never get the email or groups).
4. Try step 2 with a second account that's **not** in the group. Authentik
   shows *Permission denied* (no binding), or, if that account gets through
   on another bound group, the gateway shows *Your account is not in the
   group that may use this service* (HTTP 403).
5. On a manager, `docker service logs <stack>_mtg-assistant-gateway` (with your
   Portainer stack name in front, `mtg` in DEPLOY.md) shows no
   `identity provider` warnings, and the audit log
   ([OPERATIONS.md](OPERATIONS.md#looking-at-proposals-snapshots-links-and-the-audit-log))
   has a `login_ok` entry for you and a `login_rejected_group` entry for the
   second account.

## 10. Troubleshooting

| What you see | Cause and fix |
| --- | --- |
| Authentik shows *Redirect URI Error* (or *Invalid redirect URI*) right after leaving the gateway | The provider's Redirect URI doesn't match `https://mtg.example.com/auth/callback`. In Strict mode the scheme, host, path, port and the lack of a trailing slash all count. Fix it on the provider, not the gateway |
| Authentik shows *Permission denied* | The account isn't in a group bound to the application (section 5). Add them to `MTG Assistant Gateway Users` |
| Gateway page *Your account is not in the group that may use this service* (HTTP 403) | Authentik let them through, but their `groups` claim includes neither `MTG_REQUIRED_GROUP` nor `MTG_ADMIN_GROUP`. Check the exact spelling, that they're actually a member, and that the provider still has the `profile` scope mapping (it's what carries `groups`) |
| Authentik's sign-in page stays blank or half-drawn behind the proxy, and the browser console shows *Mixed Content* errors for `http://` addresses | Authentik doesn't trust the proxy's address, so it ignores `X-Forwarded-Proto` and builds `http://` links (seen with 2026.8.3). By default it trusts only private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`). If your proxy reaches Authentik from another range, list it in `AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` on the Authentik server (comma-separated, keeping the defaults you need). The usual Docker networks are private, so most setups never see this |
| Sign-in bounces straight back to the gateway with an error (Authentik answers `invalid_request`, *The request is otherwise malformed*), and Authentik's log says `Invalid grant_type for provider` | The provider doesn't list **Authorization Code** under **Grant Types** (section 4). Tick it and **Refresh token** |
| Members are asked to sign in again about every hour | The provider is missing the `offline_access` scope mapping, so Authentik issues no refresh token and the gateway can't renew its check once Authentik's access token runs out. Add `authentik default OAuth Mapping: OpenID 'offline_access'` under **Advanced protocol settings → Scopes** (section 4); people sign in once more and it stops |
| Page *The sign-in service can't be reached to confirm your access* (HTTP 503; JSON `idp_unavailable` for apps); log says `membership check could not reach the identity provider` | Authentik is down, or the gateway container can't reach `auth.example.com` (DNS, firewall, certificate). The gateway refuses requests rather than guess, and revokes nothing; everything works again once Authentik answers. Check Authentik's health and that the gateway's network can reach it |
| Page *This account belongs to a different sign-in provider than the one this gateway uses now* | `MTG_OIDC_ISSUER` was changed (or pointed at another provider) and a new account has the same `sub` as an old one. The gateway refuses rather than hand over the old account's data. If you only moved Authentik to a new address (same provider, same users), list the old issuer URL in `MTG_OIDC_PREVIOUS_ISSUERS` instead. Otherwise, if it's the same person, an admin uses **Delete data** on the old account (admin page, Users), then they sign in again with an empty account |
| Gateway page *Sign-in could not be completed: the identity provider refused the gateway's client ID or client secret*; log says `identity provider rejected the code exchange (HTTP 401, invalid_client)` | Wrong client secret in the Docker secret (recreate it from section 6), wrong Client ID, or the provider got switched to the **Public** client type |
| Gateway page *… refused the sign-in code*; log says `… (HTTP 400, invalid_grant)` | The code was used already or took more than the access code validity (1 minute) to come back: try again. If it happens every time, check the Redirect URI (section 4) |
| Log says `identity-provider metadata issuer 'https://…' does not match configured '…'` | `MTG_OIDC_ISSUER` has a different slug or host from what the provider reports. Copy the **OpenID Configuration Issuer** from the provider page exactly |
| Log says `cannot load identity-provider metadata from …` | The gateway container can't reach `auth.example.com` (DNS, firewall, or an overlay network with no way out), or Authentik's certificate isn't from a public CA |
| Log says `identity provider returned no id_token; is the 'openid' scope enabled?` | The `openid` scope mapping was removed from the provider |
| Gateway page *… could not verify the identity provider's ID token*; log says `ID token validation failed: …` | `signed with HS256`: no **Signing Key** on the provider (section 4). Otherwise an **Encryption Key** is set (clear it), a key the gateway hasn't seen (it refreshes the key set once, then gives up), a Client ID that doesn't match the token's `aud`, or the two machines' clocks are more than a minute apart (`ExpiredTokenError` or `issued in the future`). `ExceededSizeError` on 0.6.1 or older: the token was over the old size limits (a large property mapping or many groups); upgrade to 0.6.2 or newer |
| Gateway page *This sign-in was started in a different browser* | The sign-in link from the AI client was opened in a different browser from the one the client used, or cookies are blocked for `mtg.example.com`. Finish in the same browser. If it happens to everyone, make sure people reach the gateway only at the exact host in `MTG_PUBLIC_URL`, over https, and that nothing in between strips cookies |
| Log says `identity provider issued unusually large tokens …; largest ID token claims: …` | A scope mapping on the provider adds a lot of data (an image, a long attribute, hundreds of groups). The gateway copes, but Authentik repeats it in the access token sent on every group check. Find the named claim under **Advanced protocol settings → Scopes** (or the user's or group's attributes) and trim it. The four mappings in section 4 are all the gateway needs |
| Log says `largest ID token claims: picture=…` with hundreds of KB, and pages say *The sign-in service can't be reached* (log: `answered userinfo with HTTP 400; the access token is … bytes`) | Authentik 2026.8.0 to 2026.8.2 put the user's avatar in the `picture` claim, embedded as a whole image when the avatar comes from a user attribute (**System → Settings → Avatars** set to `attributes.…`). Authentik copies the claims into its access token too, so it can grow past a megabyte. Fix it in Authentik: upgrade to 2026.8.3 or newer, whose default `profile` mapping leaves embedded images out; or move the `attributes.…` entry after `gravatar` or `initials` in **Avatars**. Then sign in again. To keep showing uploaded pictures after upgrading, add the optional mapping in [Profile pictures](#profile-pictures), which keeps them out of tokens. Since 0.6.6 the gateway sends a token that big in the body of its userinfo request, which proxies such as Nginx Proxy Manager accept, so it keeps working meanwhile, but every check still moves a megabyte |
| Everyone is suddenly a new user after a provider change | **Subject mode** was changed. Put it back; the gateway can't merge identities |
| **What your sign-in shares** on the account page shows no email | The account has no email in Authentik, or the `email` scope mapping was removed |

## 11. Versions tested and how

- **Checked by the end-to-end tests** (`tests/e2e`, run in CI by
  `.github/workflows/e2e.yml`). A real Authentik is started on a single-node
  Docker Swarm and set up through its REST API by
  `tests/e2e/authentik_setup.py`, with exactly the objects on this page: one
  confidential provider, strict redirect URI, the self-signed signing key,
  the four default scope mappings (`openid`, `email`, `profile`,
  `offline_access`), hashed-ID subject mode, claims in the ID token, a
  one-minute access token validity (so the gateway's renewal with the
  refresh token gets exercised), the default authorization and invalidation
  flows, one application with slug `mtg-gateway`, and two groups bound to
  it. Four users then sign in through Chromium:
  - two members of the bound and required group get separate identities,
    with their own names in `whoami`;
  - a member of a bound group that isn't `MTG_REQUIRED_GROUP` gets through
    Authentik and is refused by the gateway;
  - an account with no binding is refused by Authentik;
  - a member connected through an AI client is taken out of the group
    through Authentik's API, and a few seconds later the gateway refuses
    their access token (401) and their refresh token (`invalid_grant`).

  Sign-out, token and consent paths are covered by the same tests.
- **Authentik versions:** `2025.6.4` (what this guide was first written
  against), `2026.2.2` and `2026.8.3`. Pull requests run the tests against
  `2026.8.3`, the current release, including the uploaded-picture mapping
  from [Profile pictures](#profile-pictures), installed exactly as printed
  there; a manual run of the workflow covers `2025.6.4` and `2026.8.3`.
  `2025.6.4` and `2026.2.2` passed in October 2026, and the provider fields,
  flows and group bindings came out the same on each. For the current state, look at the
  latest **End-to-end** workflow run. Newer Authentik releases should work, but they
  aren't covered until that list is updated.
- **Reported, not re-checked for this page:** the admin UI menu paths and
  field labels (the tests use the API, whose field names are in section 4),
  Authentik's invitation and recovery-link features, and HS256 signing when
  no key is picked.
- **Not covered:** a private CA on Authentik, the explicit-consent
  authorization flow, and Authentik sources (social logins). The gateway
  code doesn't treat any of those differently.

## 12. Optional: removed-member clean-up

### What it does and why

When you take someone out of the gateway's group in Authentik, the gateway
deletes their stored Archidekt session the next time they (or one of their
apps) reach it. Someone who never comes back, or whose Authentik account
was deactivated or deleted, would keep that session on the server until an
admin presses **Disable** or **Unlink**, or it expires.

The clean-up closes that gap. Once an hour the gateway asks Authentik who
is in `MTG_REQUIRED_GROUP` and `MTG_ADMIN_GROUP` right now. It deletes the
stored Archidekt session of every linked member who is in neither group, or
whose account is deactivated, and writes an `archidekt_link_swept` entry in
the activity log. Members' Account pages say whether it is on.

It is optional and stays off until you give the gateway an Authentik API
token. The token belongs to a service account that may only **view** the
gateway's one or two groups. It can't add anyone to a group or change one.

### Before you start

- An Authentik admin account. Everything below was checked on Authentik
  2026.8.3: the objects through its API, the labels in its admin interface.
- The exact names of your gateway groups: the value of
  `MTG_REQUIRED_GROUP` (here `MTG Assistant Gateway Users`) and of
  `MTG_ADMIN_GROUP` if you set one.
- A shell on a Swarm manager (or in the folder of your Compose file).

Never paste the token into a chat, an issue or the stack's environment. It
goes only into a Docker secret, in step 6.

### Set it up

1. **Create a service account.** **Directory → Users → New User → Service
   Account → Next**. Username `mtg-gateway-sweep`. Switch **Create group**
   off and **Expiring** off (it is on by default). Press **Next → Review
   Credentials → Close**. Ignore the password it shows: it is an app
   password, and Authentik refuses it as an API token.
2. **Create a role with no permissions.** **Directory → Roles → New Role**,
   Role Name `mtg-gateway-sweep` → **Create Role**. Add nothing under its
   **Permissions** tab.
3. **Give the role to the service account.** **Directory → Groups → New
   Group**: Group Name `mtg-gateway-sweep`, move the role to **Selected
   Roles** → **Create Group**. Open the new group → **Users → Add Existing
   User → +**, select the service account → **Confirm → Assign**. The
   group's user list hides service accounts by default, so it may still say
   *No objects found*; the service account's own **Groups** tab shows it.
   Don't bind this group to the gateway's application.
4. **Let the role view only the gateway's groups.** **Directory → Groups**,
   open the group named in `MTG_REQUIRED_GROUP` → **Permissions → Assign
   Role Object Permission** → Role `mtg-gateway-sweep`, switch on **Can
   view Group** only → **Assign Role Object Permission**. Do the same on
   the group named in `MTG_ADMIN_GROUP`, if you set one. Never give the
   role **Can view Group** for all groups (a global permission, on the
   role's own Permissions tab): the token could then read every member of
   every group, admins included.

   The same through the API, with an admin token in `$A` and each group's
   UUID (from its page address):

   ```bash
   curl -sS -X POST -H "Authorization: Bearer $A" -H 'Content-Type: application/json' \
     https://auth.example.com/api/v3/rbac/permissions/assigned_by_roles/<role-uuid>/assign/ \
     -d '{"permissions":["authentik_core.view_group"],"model":"authentik_core.group","object_pk":"<group-uuid>"}'
   ```
5. **Create the token.** **Directory → Tokens and App passwords → New
   Token**: Identifier `mtg-gateway-sweep-api`, User `mtg-gateway-sweep`,
   Intent **API Token**, **Expiring** off (it is on by default; an expired
   token just stops the clean-up) → **Create Token**. Copy it with the row
   action **Copy token**.
6. **Store it as the `mtg_authentik_api_token` Docker secret.** The stack file always attaches
   this secret. Until you replace it, it holds the word `off` (DEPLOY.md step 4), which keeps the
   clean-up off.
   Docker secrets can't be changed, so on a Swarm manager take the old one out of the gateway,
   delete it and create it again with the token. Paste the token at the silent prompt and press
   Enter; nothing shows on screen, and the token doesn't land in your shell history:

   ```bash
   docker service update --secret-rm mtg_authentik_api_token mtg_mtg-assistant-gateway
   docker secret rm mtg_authentik_api_token
   read -rs T && printf %s "$T" | docker secret create mtg_authentik_api_token - ; unset T
   ```

   (Your stack's name in place of `mtg` in `mtg_mtg-assistant-gateway`.) If you are setting up a
   new gateway and haven't created the secret yet, the last line alone is enough.

   With plain Docker Compose, the token goes in `secrets/mtg_authentik_api_token.txt` next to
   `docker-compose.yml` in place of the word `off` that `make-secrets.sh` wrote there:

   ```bash
   read -rs T && printf %s "$T" > secrets/mtg_authentik_api_token.txt ; unset T
   ```

   Replacing the token later works the same way: the three Swarm lines above, or the Compose line.
7. **Redeploy.** The stack file and `docker-compose.yml` already attach the secret and point the
   gateway at it, so neither needs an edit: in Portainer, **Update the stack**; without Portainer,
   `docker stack deploy` again; with Compose, `docker compose up -d --force-recreate gateway` (the
   gateway reads the token when it starts, and a plain `up -d` doesn't restart it when only the
   file changed). There is nothing to set: the stack and Compose files point
   `MTG_AUTHENTIK_API_TOKEN_FILE` at `/run/secrets/mtg_authentik_api_token`, and the gateway reaches
   Authentik's API at the scheme and host of `MTG_OIDC_ISSUER` (`https://auth.example.com`).

8. **Check it on the admin page.** Open **Admin**. In the **System** card,
   the **Removed-member clean-up** row says what is happening and what to
   do next (table below). Right after the redeploy it says *On. The first
   check runs 2 minutes after the gateway starts*. Reload a few minutes later:
   it should start with *Working*.

### What the admin row says, and what to do

| The row says | What it means | What to do |
| --- | --- | --- |
| *Off. This optional hourly check deletes the saved Archidekt sign-in of people who no longer have access. To turn it on, follow docs/IDP-AUTHENTIK.md section 12.* | The `mtg_authentik_api_token` secret holds the word `off`, or `MTG_AUTHENTIK_API_TOKEN_FILE` is empty. | Nothing, if you don't want the clean-up. Otherwise follow the steps above. |
| *Off: the Authentik API token can't be read (…)* | The file named in `MTG_AUTHENTIK_API_TOKEN_FILE` is missing or empty. | Check that the secret exists (`docker secret ls`), that your stack file is the current one (one from before 0.7.37 has the secret's lines commented out), and that `MTG_AUTHENTIK_API_TOKEN_FILE` is unset or `/run/secrets/mtg_authentik_api_token`. Redeploy. |
| *Off: it needs MTG_REQUIRED_GROUP.* | There is a token but no required group to compare with. | Set `MTG_REQUIRED_GROUP` and redeploy. |
| *On. The first check runs 2 minutes after the gateway starts; reload this page then.* | It is set up and waiting for its first round. | Reload the page after two minutes. |
| *Working. Last check at …: N members with access; no one lost access. Nothing for you to do.* or *…; deleted the saved Archidekt sign-in of N people who lost access. Nothing for you to do.* | The last round worked. | Nothing. |
| *Not working. The last check at … failed and deleted nothing: Authentik could not be reached (…)* | The gateway can't reach Authentik's address. | Check that the gateway's network reaches `https://auth.example.com` (the scheme and host of `MTG_OIDC_ISSUER`). It tries again by itself. |
| *… failed and deleted nothing: Authentik answered HTTP 401* or *HTTP 403* | Authentik refused the token: it expired or was deleted, or the service account was deactivated. | Make a new token (step 5) and a new secret (step 6), then redeploy (step 7). |
| *… failed and deleted nothing: Authentik answered HTTP* (another number) | Authentik had a problem, or sent the request somewhere else. | Wait for the next try. If it stays, check that `MTG_OIDC_ISSUER` is Authentik's own address, not one that redirects. |
| *… failed and deleted nothing: group '…' found 0 times* | The group name doesn't match, or the role can't view that group. | Check the spelling of `MTG_REQUIRED_GROUP` and `MTG_ADMIN_GROUP` against Authentik, and step 4 on each group. |
| *… failed and deleted nothing: Authentik says nobody is in the groups* | Both groups came back without an active member. | Check that the groups have members, and step 4 on each group. |
| *… failed and deleted nothing: nobody the gateway knows is among the members* | The provider's subject mode or the group names are wrong, so everyone would look removed. | Set the provider's **Subject mode** to **Based on the User's hashed ID** (section 4), and check the group names. |
| *… failed and deleted nothing: it would delete N of M stored Archidekt sessions at once* | More than three sessions, and more than a quarter of them, would go in one round. | If you removed those people on purpose, press **Disable** for each on the admin **Users** page. Otherwise check the subject mode and the group names. |
| *… failed and deleted nothing:* anything else | Authentik's answer was not what the gateway expects. | Check that `MTG_OIDC_ISSUER` points at Authentik itself, then read the gateway's log. |

After a failure the next try waits longer: one hour, then two, four, up to
six. The gateway's log tells the same story: `removed-member clean-up is
on: hourly, asking https://auth.example.com about …` at start,
`removed-member clean-up ran: …` after every round, and `removed-member
clean-up skipped, nothing deleted: …` when a round is refused. The token is
never shown or logged.

### What the token can read

Checked on Authentik 2026.8.3 with the grant from step 4: the token lists
those two groups and nothing else (another group, including
`authentik Admins`, isn't listed), and Authentik refuses it the user list
(HTTP 403). For each member of the two groups it can read their username,
name, email, attributes, Authentik user ID and `uid`, last sign-in and
whether they are active, which is what the groups API returns. It can also
read its own user (`/core/users/me/`) and its own tokens.

The service account can create more API tokens for itself and read their
keys (checked: HTTP 201 and 200). So deleting the token is not enough to cut
it off: delete the service account. Deactivating it stops its tokens
(checked: HTTP 403), but activating it again brings them all back (checked:
HTTP 200); to keep a deactivated account, first delete every token it holds
under **Tokens and App passwords**.

**What it asks.** `GET /api/v3/core/groups/?name=<group>&include_users=true`
for each of the two groups. It uses each member's `uid` (the `sub` Authentik
gives the gateway in the default **Based on the User's hashed ID** subject
mode) and `is_active`. Only direct members count, the same as the `groups`
claim people sign in with in Authentik's default `profile` mapping. If you
changed that mapping to also list parent groups, people who get in only
through a child group look removed to the clean-up and lose their stored
session every hour: keep the default mapping, or add them to the group
directly.

**When it deletes nothing.** Every failure in the table above deletes
nothing. So do an answer split into pages, a member entry without `uid` or
`is_active`, and a redirect.

### Turning it off

Empty `MTG_AUTHENTIK_API_TOKEN_FILE` and redeploy. Then delete the service
account in Authentik (deleting only the token leaves the account able to
make another, and deactivating it lasts only until someone activates it).

### If you set it up with 0.7.7's steps

0.7.7 gave the role **Can view Group** as a global permission, so that token
could read every group, and the account could have made itself more tokens.
Delete that service account and follow the steps above with a new one and a
new token, then replace the `mtg_authentik_api_token` secret as described
in step 6.
