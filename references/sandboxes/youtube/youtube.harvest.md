# youtube.harvest

The sandbox for `channel-youtube`'s `harvest` stage. A YouTube video's
metadata, captions and media, fetched by yt-dlp.

## Bins

`yt-dlp`, recorded by `skills enable`; the floor read-grants the slice its
bin dir.

## Hosts

- `api.anthropic.com`, `api.openai.com`, `chatgpt.com`: the model endpoints.
  A slice needs one to run at all.
- `youtube.com`: the apex.
- `*.youtube.com`: `www.youtube.com`, the watch page and the player.
- `youtu.be`: short links.
- `*.googlevideo.com`: the media streams.
- `*.ytimg.com`: thumbnails.

## Credential

None.

## Customize

- yt-dlp writes a cache under `$HOME/.cache/yt-dlp`, which this sandbox
  does not grant. Unverified whether a denied cache costs more than a
  warning; if a harvest fails on it, a `filesystem.allow` on that directory
  is the fix.

## Never loosen

Do not widen to `*.google.com` or `*`: yt-dlp's reach is these hosts.

## Profile

```jsonc
// youtube.harvest: the jail of channel-youtube's harvest stage.
{
  "v": 1,
  "profile": {
    "meta": {
      "name": "channel-youtube-harvest",
      "description": "channel-youtube's harvest slice: the model endpoints and this venue's hosts."
    },
    "network": {
      // Allow-list mode: naming any host denies every other.
      "allow_domain": [
        // The model endpoints: a slice needs one to run at all.
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        // The venue.
        "youtube.com",
        "*.youtube.com",
        "youtu.be",
        "*.googlevideo.com",
        "*.ytimg.com"
      ]
    }
  }
}
```
