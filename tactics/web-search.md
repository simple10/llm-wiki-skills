# Web Search

Every entry carries `(verified|unverified, YYYY-MM-DD, venue-observed-on)`.
Librarian-only file — workers file field notes instead.

## Recognizing it

Default lane type when Phase 0 has no venue to target — the lane's job is to
*find* venues and primary sources, not just answers.

## Finding content (search & discovery)

- Use whatever search tools the session has (Tavily, Exa, Perplexity, SearXNG,
  built-in WebSearch) — check availability first, don't assume one
  (unverified seed, 2026-07-10).
- Semantic/neural indexes (Exa-style) find "pages like this" and obscure
  blogs; keyword engines with operators (`site:`, `intitle:`, `after:`)
  verify and enumerate. Use both when both exist (unverified seed, 2026-07-10).
- Query the *disagreement*, not just the topic: "X criticism", "X vs", "X
  postmortem" surface higher-signal sources than "what is X"
  (unverified seed, 2026-07-10).
- Date-restrict aggressively for fast-moving topics; a research lane's default
  should match the run goal's freshness needs (unverified seed, 2026-07-10).
- Answer-engine summaries (Perplexity-style) are leads, not findings — always
  chase the cited primary source before writing a finding
  (unverified seed, 2026-07-10).

## Judging quality fast

- Prefer primary sources (the announcement, the paper, the repo, the author's
  post) over coverage of them (unverified seed, 2026-07-10).
- An author with a stake or track record beats an SEO content farm; check who
  wrote it before reading all of it (unverified seed, 2026-07-10).

## Capture notes

- Single articles: `scope=page`, `assets=reference` is usually enough.
  Recommend `continual` only for sources whose *future* output matters
  (unverified seed, 2026-07-10).

## Quirks log

- (empty)
