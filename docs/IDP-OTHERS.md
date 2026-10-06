# Using another identity provider

Authentik is the identity provider the guides walk through and the one the
end-to-end tests run against ([IDP-AUTHENTIK.md](IDP-AUTHENTIK.md)). The
gateway itself doesn't depend on Authentik: it talks plain OpenID Connect,
so any provider that meets the checklist below should work.

**How sure is this page?** The checklist comes from the gateway's code
(`src/mtg_gateway/oidc.py`). The per-provider notes are from each project's
documentation as I understand it and **have not been tested with the
gateway**. Menu names move between versions. If you get one working, a pull
request with what you did is very welcome.

## The checklist

Set up one **confidential** OIDC client (also called a "web application" or
"client with a secret") for the gateway, with:

| Setting | Value | Notes |
| --- | --- | --- |
| Redirect URI | `https://mtg.example.com/auth/callback` | Your `MTG_PUBLIC_URL` plus `/auth/callback`. The only one the gateway uses |
| Grant type | Authorization code | PKCE (`S256`) is always sent, so providers that require it are fine |
| Client authentication | **`client_secret_post`** by default | The gateway sends the client ID and secret in the form body. For a client set to HTTP Basic, set `MTG_OIDC_TOKEN_AUTH_METHOD=client_secret_basic` |
| Scopes | `openid profile email`, plus whatever makes your provider send groups | Set `MTG_OIDC_SCOPES` if you need more, for example `openid profile email groups` |
| ID token signing | RS256/384/512, ES256/384/512 or PS256 | HS256 (shared-secret signing) and EdDSA are refused |
| Issuer | HTTPS, and exactly what the provider's `/.well-known/openid-configuration` says in `issuer` | A trailing slash difference is tolerated, anything else isn't |
| Subject (`sub`) | Stable per person | People are stored by `sub`. If it changes, everyone becomes a new, empty user |
| Certificate | From a public CA | The image checks the provider against the standard CA bundle |
| Groups | By default a claim named **`groups`**, in the ID token or the userinfo response. Change the name with `MTG_OIDC_GROUPS_CLAIM`, a dot-path such as `realm_access.roles` | It may be a list of strings, a single string, or an object whose keys are the group names. Any other shape counts as no groups. Matching is exact and case-sensitive |

Then set `MTG_OIDC_ISSUER`, `MTG_OIDC_CLIENT_ID` and the
`mtg_oidc_client_secret` secret, as in the deploy guides.

### About the group check

`MTG_REQUIRED_GROUP` is the gateway's own gate on top of whatever your
provider enforces. Groups are read at each sign-in; the groups recorded then
are checked again on every token refresh and every browser page, so a
removal at the provider takes effect at the person's next sign-in (within
`MTG_REAUTH_INTERVAL` for assistants).

- **Leave it empty and set `MTG_ALLOW_ANY_IDP_USER=true`** and the gateway
  lets in anyone your provider signs in for this client. (Empty without that
  flag, the gateway refuses to start, so a forgotten group can't open it to
  everyone.) That's fine if the provider itself only allows the right
  people into this application (an access policy, a group binding, an
  allowed-users list). It is **not** fine with a provider anyone can sign up
  to, like a public Google account.
- **Set it** and the value must match one of the groups in the claim
  (`MTG_OIDC_GROUPS_CLAIM`, default `groups`) exactly. Some providers send group paths (`/MTG Assistant Gateway Users`), IDs
  (GUIDs) or `name@domain` forms; use whatever yours actually sends.

**To see what your provider sends**, leave `MTG_REQUIRED_GROUP` empty and set
`MTG_ALLOW_ANY_IDP_USER=true` for a moment, connect an assistant, and ask it to call `whoami`. It shows the
groups the gateway received. Put the right one in `MTG_REQUIRED_GROUP` and
remove `MTG_ALLOW_ANY_IDP_USER`, and redeploy.

## Keycloak

- Issuer: `https://kc.example.com/realms/<realm>` (older versions had
  `/auth` before `/realms`). Behind a reverse proxy, set Keycloak's hostname
  (`KC_HOSTNAME`) so its issuer is the public `https://` URL.
- Create an **OpenID Connect** client with **Client authentication** on
  (confidential) and **Standard flow** on. Add the redirect URI. The secret
  is on the client's **Credentials** tab.
- Keycloak's default client authenticator ("Client Id and Secret") accepts
  the secret in the request body.
- Groups: Keycloak doesn't send them by default. Add a mapper of type
  **Group Membership** to the client's dedicated scope, with token claim
  name `groups`, **Add to ID token** on, and **Full group path** off (with it
  on, values look like `/MTG Assistant Gateway Users` and `MTG_REQUIRED_GROUP` needs
  the leading slash). Realm roles work too: set
  `MTG_OIDC_GROUPS_CLAIM=realm_access.roles`, put the role in
  `MTG_REQUIRED_GROUP`, and turn on **Add to ID token** (or **Add to
  userinfo**) for the *realm roles* mapper of the `roles` client scope; the
  gateway doesn't read the access token.

## Authelia

- Issuer: Authelia's own URL, for example `https://auth.example.com`.
- Add a client under `identity_providers.oidc.clients` with
  `public: false`, the redirect URI, the scopes `openid`, `profile`, `email`
  and `groups`, and either **`token_endpoint_auth_method: client_secret_post`**
  or Authelia's Basic default together with
  `MTG_OIDC_TOKEN_AUTH_METHOD=client_secret_basic`. Store the client secret hashed in Authelia's config, as its
  docs describe, and the plain value in the gateway's secret.
- Set `MTG_OIDC_SCOPES=openid profile email groups`.
- Recent Authelia versions keep most claims out of the ID token and serve
  them from userinfo. The gateway asks userinfo when the ID token has no
  groups or no email, so that's fine.
- Authelia's own `authorization_policy` (for example `two_factor`) and
  access control rules decide who gets in before the gateway does.

## Zitadel

- Issuer: your instance's domain, for example
  `https://my-instance.zitadel.cloud` or your self-hosted URL.
- Create a **Web** application in a project, with authentication method
  **POST** (that's `client_secret_post`) and the redirect URI.
- Groups: Zitadel sends project **roles** as an object in its own claim.
  Either:
  - set `MTG_OIDC_GROUPS_CLAIM=urn:zitadel:iam:org:project:roles` and put the
    role's name in `MTG_REQUIRED_GROUP` (the gateway reads the object's keys as
    group names; turn on the project's setting that puts roles in the ID
    token or userinfo); or
  - leave `MTG_REQUIRED_GROUP` empty, set `MTG_ALLOW_ANY_IDP_USER=true`, and
    use the project's **check authorization on authentication** setting so only
    users granted a role can sign in.

## Pocket ID

- Issuer: Pocket ID's URL.
- Create an OIDC client with the redirect URI. Pocket ID signs in with
  passkeys only, which is a nice fit for a small group of friends.
- Add `groups` to `MTG_OIDC_SCOPES` and use Pocket ID's user groups. You can
  also restrict the client to certain groups in Pocket ID itself and leave
  `MTG_REQUIRED_GROUP` empty.

## Kanidm

- Issuer: `https://idm.example.com/oauth2/openid/<client name>`.
- Create a basic (confidential) OAuth2 resource server for the gateway with
  the redirect URI, and a scope map granting `openid`, `profile`, `email` and
  `groups` to the group that should get in. Only members of a scope-mapped
  group can sign in at all, so the scope map is already an access gate.
- Kanidm signs ID tokens with ES256 by default, which the gateway accepts.
- Group names may arrive in `name@domain` form. Check with `whoami`.

## Microsoft Entra ID

- Issuer: `https://login.microsoftonline.com/<tenant id>/v2.0`.
- Register an app with a web redirect URI and a client secret.
- Group claims are object IDs (GUIDs), not names, so `MTG_REQUIRED_GROUP`
  would be the group's object ID. Alternatively, require user assignment on
  the enterprise app, leave the group check empty and set
  `MTG_ALLOW_ANY_IDP_USER=true`.

## Google

Possible but not recommended. Google sends no groups at all, so
`MTG_REQUIRED_GROUP` has to stay empty (with `MTG_ALLOW_ANY_IDP_USER=true`),
and then **any Google account can sign in** unless your OAuth app is limited (an Internal app in a Workspace
organisation, or a testing-mode app with a list of test users). If you want
Google sign-in, put a provider that can federate it (Authentik, Keycloak,
Zitadel) in between and do the group check there.

## Switching providers later

People are stored by the provider's `sub`. A new provider (or a changed
subject setting) means everyone signs in as a new, empty user: their
Archidekt link, proposals, snapshots and scan sessions stay with the old
identity. Pick a provider before you invite people, or plan for everyone to
relink.
