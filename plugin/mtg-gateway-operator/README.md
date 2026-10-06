# MTG Assistant Gateway operator plugin

Skills for whoever deploys and runs the gateway. It bundles no MCP server.

- `skills/deploy/`: walks through `docs/DEPLOY.md` step by step, generates
  the random secrets on the operator's machine, and verifies the result.
- `skills/invite/`: adds a user in Authentik and sends them the install
  link.

Install (needs read access to this repository, which `gh auth login` or
your git credentials provide):

```
claude plugin marketplace add TheUncleBen/MTG-Assistant-Gateway
claude plugin install mtg-gateway-operator@mtg-gateway
```
