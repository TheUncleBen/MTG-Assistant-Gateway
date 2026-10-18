# MTG Assistant Gateway operator plugin

Skills for whoever deploys and runs the gateway. It bundles no MCP server.

- `skills/deploy/`: walks through `docs/DEPLOY.md` step by step, generates
  the random secrets on the operator's machine, and checks the result.
- `skills/invite/`: walks the operator through adding a user in Authentik
  and drafts the message with the install link for the operator to send.

Install:

```
claude plugin marketplace add TheUncleBen/MTG-Assistant-Gateway
claude plugin install mtg-gateway-operator@mtg-gateway
```
