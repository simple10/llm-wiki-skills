# gmail.harvest

The sandbox for `channel-gmail`'s `harvest` stage. One mailbox's pull
through the account's Gmail connector. The slice fetches nothing else.

## Bins

None.

## Hosts

- `api.anthropic.com`, `api.openai.com`, `chatgpt.com`: the model endpoints.
  A slice needs one to run at all.
- `gmailmcp.googleapis.com`: the Gmail connector's MCP endpoint (`claude mcp
  list`), and the host this unit's credential is spent at.

## Credential

`requires.credential: true`. The binding's payload file is granted per
spawn, and it may be spent only at `gmailmcp.googleapis.com`, the exact
`host:` keyword the manifest claims. Whether a jailed `claude -p` loads
account connectors at all is unmeasured (llm-wiki-plugins#2282), so harvest
is expected to run where `whereami` says `spawn: none`.

## Customize

- Nothing. Every mailbox reaches the same endpoint.

## Never loosen

Add no Google host besides the connector's: `mail.google.com` is fetched by
no step.

## Profile

```jsonc
// gmail.harvest: the jail of channel-gmail's harvest stage.
{
  "v": 2,
  "profile": {
    "meta": {
      "name": "channel-gmail-harvest",
      "description": "channel-gmail's harvest slice: the model endpoints and this venue's hosts."
    },
    "network": {
      // Allow-list mode: naming any host denies every other.
      "allow_domain": [
        // The model endpoints: a slice needs one to run at all.
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        // The venue.
        "gmailmcp.googleapis.com"
      ]
    }
  }
}
```
