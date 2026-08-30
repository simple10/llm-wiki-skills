# <Venue Type>

Every entry carries `(verified|unverified, YYYY-MM-DD, venue-observed-on)`.
Seeded entries from model knowledge stay unverified until a real run confirms
them. Librarian-only file — workers file field notes instead.

Playbooks live at `<ops dir>/tactics/<name>.md` and belong to this wiki —
one copy, edited in place, with nothing to resolve at read time. Copy this file
to start a new one; `<ops dir>/bin/llm-wiki-ops tactics install <name>` adds
one the machinery ships, and `... tactics refresh <name>` replaces this wiki's
copy with the machinery's when the upstream one has improved (including this
template — `refresh` is the only verb that can name it). Contributing a
playbook back upstream is a deliberate manual act, done by a human, never by
the librarian pass.

## Recognizing it

URL shapes, platform tells — when a lane should use this playbook.

## Finding content (search & discovery)

Best routes in: native search (and its quirks), external search operators
(`site:`, `inurl:`), archives/feeds/APIs, where the high-signal areas live
(pinned threads, digests, specific sections).

## Judging quality fast

What separates signal from noise on this venue type; what to skim first;
engagement/recency signals that actually correlate with quality.

## Capture notes

What to tell the scraper: sensible scope/filters for this venue type, auth
needs, anything that makes harvest cheap or expensive here.

## Quirks log

Dated one-liners that don't fit above, including superseded claims:

- YYYY-MM-DD (venue): observation
