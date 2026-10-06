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
| Who gets in, second gate | Anyone whose `groups` claim doesn't include `MTG_REQUIRED_GROUP` exactly gets HTTP 403, and any gateway browser sessions they had are closed. The gateway won't start with it empty unless `MTG_ALLOW_ANY_IDP_USER=true` | The same group name in `MTG_REQUIRED_GROUP`. Exact, case-sensitive match |
| Live membership | Before serving any request that carries a browser session or a gateway token, asks Authentik's `/userinfo` for the person's groups as they are now, renewing Authentik's access token with its refresh token when needed. The answer is cached for `MTG_MEMBERSHIP_CHECK_TTL` seconds (5 by default). Someone taken out of the group, deactivated or deleted loses everything on their next request. If Authentik can't be reached, requests get a 503 and nothing is revoked | The `offline_access` scope mapping (above). Nothing else |
| Sign-out | The gateway's **Sign out** button ends its own browser session (with `Clear-Site-Data`); **Sign out on all my devices** (on the `/logout` page) ends every browser and Android app session of that person. It doesn't call Authentik's end-session endpoint, so the Authentik session stays, but for the next hour a sign-in to the gateway's pages in that browser or app asks Authentik for the password again (`prompt=login`), so the next person on a shared device isn't signed straight back in | Provider → **Invalidation flow**: Authentik requires one, so use the default. Nothing else |
| Refresh at the provider | Keeps Authentik's access and refresh token from each sign-in, encrypted with the `mtg_fernet_key` secret, and uses them only for the live membership check. AI clients still get the gateway's own tokens, never Authentik's | Provider token lifetimes can stay at their defaults. Keep the refresh token validity at least as long as `MTG_REFRESH_TOKEN_TTL` (both 30 days by default): when Authentik refuses an expired refresh token the gateway can't tell that from a deactivated account, so it treats it like a removal, and the person signs in again and relinks Archidekt |

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
| Client ID | keep the generated value | Goes in `MTG_OIDC_CLIENT_ID` (it's not a secret) |
| Client Secret | keep the generated value; you'll reveal it once in section 6 | Goes into the Docker secret `mtg_oidc_client_secret` and nowhere else |
| Redirect URIs | one entry, matching mode **Strict**, URL `https://mtg.example.com/auth/callback` | Exactly `MTG_PUBLIC_URL` plus `/auth/callback`, no trailing slash. Strict mode refuses anything else |
| Signing Key | `authentik Self-signed Certificate` (or any RSA/EC certificate you manage) | Gives RS256 ID tokens. With **no** signing key, Authentik signs with HS256 using the client secret, which the gateway refuses (reported from Authentik's behaviour; the symptom is in section 10) |
| Encryption Key | none | The gateway doesn't decrypt tokens |
| Advanced protocol settings → Scopes | `authentik default OAuth Mapping: OpenID 'openid'`, `… 'email'`, `… 'profile'` and `… 'offline_access'`. Nothing else needed | `openid` makes Authentik issue an ID token. `email` and `profile` carry the claims from section 1, and `profile` includes `groups`. `offline_access` makes Authentik issue a refresh token, which the gateway needs to keep checking membership after Authentik's access token runs out. Leave it out and Authentik says nothing; members just get asked to sign in again every hour |
| Subject mode | **Based on the User's hashed ID** | A stable, opaque `sub`. Never a username or email mode: people can change those themselves, and whoever picks up an old value inherits that gateway account. Don't change it after the first sign-in |
| Include claims in id_token | **on** (the default) | At sign-in the gateway reads the ID token first and only falls back to `/userinfo`. The live membership check always asks `/userinfo` |
| Issuer mode | **Each provider has a different issuer, based on the application slug** (the default, `per_provider`) | Gives you the issuer in section 6 |
| Access code, access token and refresh token validity | defaults (1 minute, 1 hour, 30 days) | A minute is plenty for the redirect. The gateway renews Authentik's access token with the refresh token when it runs out. Don't make the refresh token validity shorter than `MTG_REFRESH_TOKEN_TTL` (30 days); see *Refresh at the provider* in section 1 |

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
other two secrets, `mtg_fernet_key` and `mtg_session_secret`, are covered in
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
- **Don't link accounts by email for sources that don't verify emails.**
  A Source's **User matching mode** of linking to an existing user with the
  same email lets anyone who registers that email at the provider sign in
  as your user. Use identifier matching, or the email mode only for
  providers that verify addresses.

(Menu names reported from Authentik's docs; the end-to-end tests don't
cover MFA or Sources.)

**Remove someone.** Take them out of `MTG Assistant Gateway Users`, or
deactivate or delete the user. That's all. The gateway asks Authentik on
every request (at most `MTG_MEMBERSHIP_CHECK_TTL` seconds old, 5 by
default), so on their next request it revokes their gateway tokens, browser
sessions, the Authentik tokens it kept and their Archidekt link, and they
are signed out everywhere. A new sign-in then fails at Authentik's binding,
or at the gateway's group check if the binding lets them through. This was
tested live against Authentik 2026.2.2: taking someone out of a group
revokes nothing in Authentik itself (it keeps honouring their refresh
token), but its `/userinfo` shows the change at once, and deactivating or
deleting the user invalidates their Authentik tokens at once. Taking someone
out of `MTG_ADMIN_GROUP` likewise removes the admin page on their next
request. More in [OPERATIONS.md](OPERATIONS.md#revoking-access).

Send each person [ONBOARDING.md](ONBOARDING.md) along with the gateway's
hostname.

## 9. Check the sign-in

1. Deploy the stack ([DEPLOY.md](DEPLOY.md) step 6). The service goes
   healthy without talking to Authentik; the first sign-in is the first
   contact.
2. In a browser, open `https://mtg.example.com/account`. You get sent to
   Authentik, sign in, and land back on the account page with your name and
   a **Sign out** button.
3. Connect Claude or ChatGPT ([CONNECT.md](CONNECT.md)) and call the
   `whoami` tool. It should show your `sub`, `email`, `preferred_username`
   and a `groups` list containing `MTG Assistant Gateway Users`.
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
| Gateway page *Your account is not in the group that may use this service* (HTTP 403) | Authentik let them through, but their `groups` claim doesn't include `MTG_REQUIRED_GROUP`. Check the exact spelling, that they're actually a member, and that the provider still has the `profile` scope mapping (it's what carries `groups`) |
| Members are asked to sign in again about every hour | The provider is missing the `offline_access` scope mapping, so Authentik issues no refresh token and the gateway can't renew its check once Authentik's access token runs out. Add `authentik default OAuth Mapping: OpenID 'offline_access'` under **Advanced protocol settings → Scopes** (section 4); people sign in once more and it stops |
| Page *The sign-in service can't be reached to confirm your access* (HTTP 503; JSON `idp_unavailable` for apps); log says `membership check could not reach the identity provider` | Authentik is down, or the gateway container can't reach `auth.example.com` (DNS, firewall, certificate). The gateway refuses requests rather than guess, and revokes nothing; everything works again once Authentik answers. Check Authentik's health and that the gateway's network can reach it |
| Page *This account belongs to a different sign-in provider than the one this gateway uses now* | `MTG_OIDC_ISSUER` was changed (or pointed at another provider) and a new account has the same `sub` as an old one. The gateway refuses rather than hand over the old account's data. If it's the same person, an admin uses **Delete data** on the old account (admin page, Users), then they sign in again with an empty account |
| Gateway page *Sign-in could not be completed with the identity provider*; log says `identity provider rejected the code exchange (HTTP 400/401)` | Wrong client secret in the Docker secret (recreate it from section 6), wrong Client ID, or the provider got switched to the **Public** client type |
| Log says `identity-provider metadata issuer 'https://…' does not match configured '…'` | `MTG_OIDC_ISSUER` has a different slug or host from what the provider reports. Copy the **OpenID Configuration Issuer** from the provider page exactly |
| Log says `cannot load identity-provider metadata from …` | The gateway container can't reach `auth.example.com` (DNS, firewall, or an overlay network with no way out), or Authentik's certificate isn't from a public CA |
| Log says `identity provider returned no id_token; is the 'openid' scope enabled?` | The `openid` scope mapping was removed from the provider |
| Log says `ID token validation failed: …` | No **Signing Key** on the provider (so the token is HS256), a key the gateway hasn't seen (it refreshes the key set once, then gives up), a Client ID that doesn't match the token's `aud`, or the two machines' clocks are more than a minute apart |
| Gateway page *This sign-in was started in a different browser* | The sign-in link from the AI client was opened in a different browser from the one the client used, or cookies are blocked for `mtg.example.com`. Finish in the same browser. If it happens to everyone, make sure people reach the gateway only at the exact host in `MTG_PUBLIC_URL`, over https, and that nothing in between strips cookies |
| Everyone is suddenly a new user after a provider change | **Subject mode** was changed. Put it back; the gateway can't merge identities |
| `whoami` shows a name but no email | The account has no email in Authentik, or the `email` scope mapping was removed |

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
    with `groups` showing in `whoami`;
  - a member of a bound group that isn't `MTG_REQUIRED_GROUP` gets through
    Authentik and is refused by the gateway;
  - an account with no binding is refused by Authentik;
  - a member connected through an AI client is taken out of the group
    through Authentik's API, and a few seconds later the gateway refuses
    their access token (401) and their refresh token (`invalid_grant`).

  Sign-out, token and consent paths are covered by the same tests.
- **Authentik versions:** `2025.6.4` (what this guide was first written
  against) and `2026.2.2`. Pull requests run the tests against `2026.2.2`
  only; a manual run of the workflow covers both. Both passed when this page
  was written (October 2026), and the provider fields, flows and group
  bindings came out the same on each. For the current state, look at the
  latest `e2e` workflow run. Newer Authentik releases should work, but they
  aren't covered until that list is updated.
- **Reported, not re-checked for this page:** the admin UI menu paths and
  field labels (the tests use the API, whose field names are in section 4),
  Authentik's invitation and recovery-link features, and HS256 signing when
  no key is picked.
- **Not covered:** a private CA on Authentik, the explicit-consent
  authorization flow, and Authentik sources (social logins). The gateway
  code doesn't treat any of those differently.
