# web-page — after enabling

1. Set `[pipeline] harvest = "web-page"` in `.llm-wiki.toml`, and commit it:

   ```sh
   llm-wiki-ops git commit .llm-wiki.toml message="pipeline: web-page is the plain-url harvester"
   ```

   `jobs add <url>` reads this key for a job that names no `skill=` of its
   own — the plain-url case, as opposed to a channel unit's job.

2. `pipeline jobs ls` and, for any job still naming the old
   `llm-wiki:harvest-page` skill, repoint it:

   ```sh
   llm-wiki-ops pipeline jobs edit <slug> harvest.skill=web-page
   ```
