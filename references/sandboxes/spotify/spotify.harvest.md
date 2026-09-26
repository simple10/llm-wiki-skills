# spotify.harvest

The sandbox for `channel-spotify`'s `harvest` stage. A Spotify show, episode
or playlist's metadata, its cover art, and the keyless iTunes lookup that
finds a show's open RSS feed.

## Bins

None.

## Hosts

- `api.anthropic.com`, `api.openai.com`, `chatgpt.com`: the model endpoints.
  A slice needs one to run at all.
- `spotify.com`: the apex.
- `*.spotify.com`: the Web API, the token endpoint and the embed page.
- `*.scdn.co`: cover art (`mosaic.scdn.co`, `i.scdn.co`).
- `*.spotifycdn.com`: cover art (`image-cdn-*.spotifycdn.com`).
- `itunes.apple.com`: the keyless feed lookup.

## Credential

`requires.credential: "optional"`. A job with a binding on this machine
is granted that one payload and spends it at `api.spotify.com` and
`accounts.spotify.com`. The manifest's exact `host:spotify.com` keyword is
the credential's claim, and this snippet reaches it. A job with none runs
keyless.

## Customize

- A show's own feed and enclosure hosts, if the operator wants its audio
  fetched in a slice rather than by a widen (`references/enable.md`, the
  known limitation).

## Never loosen

Never add a DRM media host: music and Spotify-exclusive audio are captured
as references and never ripped.

## Profile

```jsonc
// spotify.harvest: the jail of channel-spotify's harvest stage.
{
  "v": 1,
  "profile": {
    "meta": {
      "name": "channel-spotify-harvest",
      "description": "channel-spotify's harvest slice: the model endpoints and this venue's hosts."
    },
    "network": {
      // Allow-list mode: naming any host denies every other.
      "allow_domain": [
        // The model endpoints: a slice needs one to run at all.
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        // The venue.
        "spotify.com",
        "*.spotify.com",
        "*.scdn.co",
        "*.spotifycdn.com",
        "itunes.apple.com"
      ]
    }
  }
}
```
