# notion-tasks.harvest

The sandbox for `channel-notion-tasks`'s `harvest` stage. One Notion
workspace's task pull through the account's Notion connector.

## Bins

None.

## Hosts

- `api.anthropic.com`, `api.openai.com`, `chatgpt.com`: the model endpoints.
  A slice needs one to run at all.
- `mcp.notion.com`: Notion's hosted MCP endpoint. Unverified: no Notion
  connector is configured on the machine this was written on, so confirm
  with `claude mcp list` before binding.

## Credential

None declared. The connector's auth is the session's. Whether a jailed
`claude -p` loads account connectors is unmeasured (llm-wiki-plugins#2282),
so harvest is expected to run where `whereami` says `spawn: none`.

## Customize

- The connector's endpoint, if `claude mcp list` names a different one.

## Never loosen

Add no `notion.so` host: the connector is the only source, and a direct
fetch is improvising another.

## Profile

```jsonc
// notion-tasks.harvest: the jail of channel-notion-tasks's harvest stage.
{
  "v": 2,
  "profile": {
    "meta": {
      "name": "channel-notion-tasks-harvest",
      "description": "channel-notion-tasks's harvest slice: the model endpoints and this venue's hosts."
    },
    "network": {
      // Allow-list mode: naming any host denies every other.
      "allow_domain": [
        // The model endpoints: a slice needs one to run at all.
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        // The venue.
        "mcp.notion.com"
      ]
    }
  }
}
```
