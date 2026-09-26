# frameio.harvest

The sandbox for `channel-frameio`'s `harvest` stage. A Frame.io share's
harvest: the share page, its HLS and document-proxy streams, and yt-dlp's
media pull.

## Bins

`yt-dlp`, recorded by `skills enable`; the floor read-grants the slice its
bin dir.

## Hosts

- `api.anthropic.com`, `api.openai.com`, `chatgpt.com`: the model endpoints.
  A slice needs one to run at all.
- `frame.io`: the apex.
- `*.frame.io`: the share app (`next.frame.io`), the HLS host
  (`sahls.frame.io`) and the proxy host (`assets.frame.io`).

## Filesystem

Read on Playwright's browser build (`$HOME/.cache/ms-playwright` on Linux,
`$HOME/Library/Caches/ms-playwright` on macOS), which the capture
scripts launch.

## Credential

None. A share link is its own authorization.

## Customize

- Nothing beyond the ticket's host, which joins at dispatch.

## Never loosen

Do not widen `*.frame.io` to a CDN wildcard; a host the proxy refused comes
back as `denied` and is the foreman's call.

## Profile

```jsonc
// frameio.harvest: the jail of channel-frameio's harvest stage.
{
  "v": 1,
  "profile": {
    "meta": {
      "name": "channel-frameio-harvest",
      "description": "channel-frameio's harvest slice: the model endpoints and this venue's hosts."
    },
    "filesystem": {
      // Playwright's browser build: Linux, then macOS. A path absent on
      // this machine grants nothing.
      "read": [
        "$HOME/.cache/ms-playwright",
        "$HOME/Library/Caches/ms-playwright"
      ]
    },
    "network": {
      // Allow-list mode: naming any host denies every other.
      "allow_domain": [
        // The model endpoints: a slice needs one to run at all.
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        // The venue.
        "frame.io",
        "*.frame.io"
      ]
    }
  }
}
```
