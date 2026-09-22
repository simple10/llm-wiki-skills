# channel-frameio — customizing this skill

Ask which share link(s) this wiki watches and record them in the
installed SKILL.md under a "Watched shares" heading. Ask for a slug and a
content-named destination for each — share-link domains carry no source
identity, so a mechanically-derived one would be opaque
(`slug=<content-name> dest=sources/scrapes/<content-name>`).

Ask whether asset titles carry a share-wide suffix worth trimming — that
becomes `--title-strip` on the capture step (`harvest_share.py`, which
hands it to every leaf, and each leaf records it for the process step);
record the chosen value in the installed
SKILL.md. The share's author, group and tags go on the JOB, not in this
file — `meta.author=`, `meta.group=`, `meta.group_type=`, `meta.tags=`,
`meta.areas=` on the `pipeline add` at enable — and the host stamps them
onto every page the job lands.
