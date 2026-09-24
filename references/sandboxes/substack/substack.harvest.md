# substack.harvest

The sandbox for `channel-substack`'s `harvest` stage. A Substack
publication's archive API, its posts, and the images on its CDN.

## Bins

None.

## Hosts

- `api.anthropic.com`, `api.openai.com`, `chatgpt.com`: the model endpoints.
  A slice needs one to run at all.
- `substack.com`: the apex.
- `*.substack.com`: each publication's subdomain and `api.substack.com`.
- `substackcdn.com`: post images.

## Credential

None. Paid posts are recorded as paywalled, never fetched signed-in.

## Customize

- A custom-domain publication: its host is the ticket's own and joins at
  dispatch.

## Never loosen

Add no host a post did not name in a report's `missing[]`.

## Profile

```jsonc
// substack.harvest: the jail of channel-substack's harvest stage.
{
  "v": 2,
  "profile": {
    "meta": {
      "name": "channel-substack-harvest",
      "description": "channel-substack's harvest slice: the model endpoints and this venue's hosts."
    },
    "network": {
      // Allow-list mode: naming any host denies every other.
      "allow_domain": [
        // The model endpoints: a slice needs one to run at all.
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        // The venue.
        "substack.com",
        "*.substack.com",
        "substackcdn.com"
      ]
    }
  }
}
```
