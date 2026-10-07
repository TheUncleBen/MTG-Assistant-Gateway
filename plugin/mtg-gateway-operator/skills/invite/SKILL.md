---
name: invite
description: Give a person access to the MTG Assistant Gateway: create or enable their Authentik account, add them to the gateway group, and send them the one-link install instructions. Use when the operator says "invite", "add a user", "give X access" or "onboard" someone to the gateway.
---

# Invite a user to the MTG Assistant Gateway

Access is decided only in Authentik; the gateway has no user list. The
person needs (a) an Authentik account in the group bound to the MTG Assistant Gateway
application and (b) the gateway address. Nothing else: no client ID, no API
key.

## Ask for

- The person's name or email, and whether they already have an Authentik
  account.
- The group bound to the MTG Assistant Gateway application (default
  `MTG Assistant Gateway Users`; it was chosen at deploy time).

## Steps for the operator (Authentik admin, browser)

1. Directory, Users: create the user, or send an invitation if an
   enrollment flow exists. The new user sets their own password; the operator
   never knows it.
2. Directory, Groups: add the user to the gateway's group (the one in
   `MTG_REQUIRED_GROUP`; normally also the group bound to the application).
3. Done. Nothing to restart.

Authentik's exact menu labels have not been checked on a live 2026.2 admin
screen; look under Directory.

## What to send the new user

Draft a short message for the operator to send. It contains only the
gateway address and the install page, which has the steps for every app:

> You have an MTG Assistant Gateway account. Open https://<gateway host>/install and
> follow the steps for the app you use (Claude, Claude Code, ChatGPT or
> Codex). Sign in with the account I made for you; you set your own
> password there. Nothing to download for Claude or ChatGPT.

For a user on Claude Code, the whole install is:

```
claude plugin marketplace add https://<gateway host>/plugin/marketplace.json
claude plugin install mtg-gateway@mtg-gateway
```

then `/mcp` to sign in. They can also paste the install page into Claude
Code and say "install this plugin".

## Removing access later

Remove the user from the group (or deactivate the user) in Authentik.
That's enough: the gateway asks Authentik on every request (cached for
`MTG_MEMBERSHIP_CHECK_TTL` seconds, 5 by default), so on their next request
their gateway tokens and browser sessions are revoked. Removal from the
group also revokes their Archidekt link; a deactivated user keeps it until
the operator uses **Delete data** on the admin page. If
the provider is missing the `offline_access` scope mapping, fix that first
(`docs/IDP-AUTHENTIK.md`). Details in `docs/OPERATIONS.md`, "Revoking
access".
