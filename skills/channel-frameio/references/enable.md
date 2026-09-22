# channel-frameio — after enabling

1. Check a real Chrome is on the harvesting machine, and that the worker
   there can launch it — the harvest step's, and a confined worker that
   cannot reach the browser captures nothing; tell the operator if it is
   missing. The process step needs neither: it reads the bytes harvest left
   and writes the page.
2. No auth walkthrough: guest share links authorize themselves.
3. Declare the job, naming the skill: `llm-wiki-ops pipeline add
   <share-url> slug=<content-name>
   description="<what this is>" skill=channel-frameio
   dest=sources/scrapes/<content-name>` — the skill's manifest supplies
   `every=once`, `harvest.scope=domain` and `harvest.assets=download`.
   **`download` is what keeps the video**: the only reference Frame.io
   offers is a signed HLS URL that expires within hours, and a video that was
   never fetched is never transcribed. Pass `harvest.assets=reference` only
   to take a share's documents and none of its media — and know that a
   share is often many GB under `download`. **Scope MUST stay `domain`**: a
   share's leaves sit under the share host, not under the watched URL, so
   `page` and (for a FOLDER inside the share) `section` both exclude them.
   Name `dest` for the content now: moving it later means re-running this
   `add` with a new `dest=`, since `pipeline edit` refuses that key. `dest`
   is also the one directory the skill's process step writes: it is handed to
   the page builder verbatim, off each process ticket.
