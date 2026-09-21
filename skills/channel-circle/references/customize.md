# channel-circle — customizing this skill

Circle communities run on `*.circle.so` **or their own custom domain**, but
a custom-domain community still renders Circle's own uniform chrome — so
ONE installed copy normally serves every community this wiki watches, with
one watch per community.

Ask which community this wiki watches (the `*.circle.so` domain or the
custom domain fronting it) and record it in the installed SKILL.md under
a "Watched communities" heading — the skill body is wiki-owned. For a
custom-domain community, also add that host to the installed copy's
`manifest.json` `requires.network` (keep `circle.so`) so
`skills search <domain>` answers from the wiki's copy directly, and the
harvest slice is granted that host.
There is no second copy to install: identity is the JOB's slug, so two
communities are two jobs on this one skill, each under its own heading
here.
