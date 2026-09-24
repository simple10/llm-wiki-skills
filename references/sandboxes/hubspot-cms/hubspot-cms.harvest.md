# hubspot-cms.harvest

The sandbox for `channel-hubspot-video`'s `harvest` stage. A HubSpot CMS
page whose content is a HubSpot Video: the page renders in Chromium through
Playwright, and the Mux stream behind the player is fetched.

## Bins

None declared. Playwright's Chromium must be installed on the harvesting
machine first (`references/enable.md` step 1).

## Hosts

- `api.anthropic.com`, `api.openai.com`, `chatgpt.com`: the model endpoints.
  A slice needs one to run at all.
- `play.hubspotvideo.com`: the lazy player iframe.
- `image.mux.com`: the storyboard that names the playback id.
- `stream.mux.com`: the unsigned master playlist.
- `*.mux.com`: the rendition hosts behind the master
  (`manifest-*.edgemv.mux.com`); the exact set is unverified.

## Credential

None.

## Customize

- The site's own host is the ticket's and joins at dispatch.
- Playwright's browser cache (`$HOME/.cache/ms-playwright` on Linux,
  `$HOME/Library/Caches/ms-playwright` on macOS): a `filesystem.read` entry,
  if the machine layer does not already grant it. Unverified that a render
  needs more than a read.

## Never loosen

Add no site host here: the platform's hosts are the same for every HubSpot
customer, and a site is the ticket's.

## Profile

```jsonc
// hubspot-cms.harvest: the jail of channel-hubspot-video's harvest stage.
{
  "v": 2,
  "profile": {
    "meta": {
      "name": "channel-hubspot-video-harvest",
      "description": "channel-hubspot-video's harvest slice: the model endpoints and this venue's hosts."
    },
    "network": {
      // Allow-list mode: naming any host denies every other.
      "allow_domain": [
        // The model endpoints: a slice needs one to run at all.
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        // The venue.
        "play.hubspotvideo.com",
        "image.mux.com",
        "stream.mux.com",
        "*.mux.com"
      ]
    }
  }
}
```
