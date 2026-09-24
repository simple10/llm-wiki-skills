# circle.harvest

The sandbox for `channel-circle`'s `harvest` stage. A Circle.so community's
harvest: the SPA's pages, its signed HLS media and its asset CDN, rendered
in a persistent-profile Chrome.

## Bins

None declared. The capture drives Chrome, which the harvesting machine
supplies.

## Hosts

- `api.anthropic.com`, `api.openai.com`, `chatgpt.com`: the model endpoints.
  A slice needs one to run at all.
- `circle.so`: the platform apex.
- `*.circle.so`: every community subdomain, and the media and asset hosts
  under it (`cdn-media.circle.so`, `assets-v2.circle.so`).

## Credential

None declared (`requires.credential: false`). A members-only community
replays the per-domain login profile `scripts/login.py` minted, and a
spawned slice is granted no profile directory yet (llm-wiki-plugins#2282),
so an authenticated harvest exits 2 or 5 in a slice and runs only where
`whereami` says `spawn: none`.

## Customize

- A custom-domain community: its host is the ticket's own and joins the
  slice's egress at dispatch, so it needs no entry here.
- Wistia-embedded lessons: `fast.wistia.com`, `fast.wistia.net` and
  `embed-cloudfront.wistia.com` only if the operator wants those lessons
  fetched without a per-job widen.

## Never loosen

Do not widen `*.circle.so` to `*`, and add no host a lesson did not name in
a report's `missing[]`.

## Profile

```jsonc
// circle.harvest: the jail of channel-circle's harvest stage.
{
  "v": 2,
  "profile": {
    "meta": {
      "name": "channel-circle-harvest",
      "description": "channel-circle's harvest slice: the model endpoints and this venue's hosts."
    },
    "network": {
      // Allow-list mode: naming any host denies every other.
      "allow_domain": [
        // The model endpoints: a slice needs one to run at all.
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        // The venue.
        "circle.so",
        "*.circle.so"
      ]
    }
  }
}
```
