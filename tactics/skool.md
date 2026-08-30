# Skool (communities)

Every entry carries `(verified|unverified, YYYY-MM-DD, venue-observed-on)`.
Librarian-only file — workers file field notes instead.

## Recognizing it

`skool.com/<community-slug>` URLs; gated communities with classroom + feed +
calendar tabs.

## Finding content (search & discovery)

- Most communities are join-gated; content is only reachable with the wiki
  owner's membership session (scraper auth storage-state). Without membership,
  the About page is the only public surface — judge from it whether joining is
  worth recommending, don't scrape around the gate (unverified seed, 2026-07-10).
- High-signal areas: pinned posts, the Classroom tab (structured courses), and
  recurring digest threads; the main feed is chatty and low-density
  (unverified seed, 2026-07-10).
- Web-search `site:skool.com <topic>` surfaces public About pages of relevant
  communities — a discovery route for venues, not content
  (unverified seed, 2026-07-10).

## Judging quality fast

- Member count means little; look at post cadence in the last 30 days and
  whether the owner still posts substantively (unverified seed, 2026-07-10).

## Capture notes

- Classroom courses map to the scraper's `scope=section` course handling;
  needs the member session in the credential store. Licensed-content boundary
  applies: only communities the wiki owner has actually joined
  (unverified seed, 2026-07-10).

## Quirks log

- (empty)
