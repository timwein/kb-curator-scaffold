# Seed Templates

These are *empty* templates showing the file shape the agents expect at
`/workspace/seed/*` (mounted from your uploaded files).

To produce real seed files for yourself:

1. **Export your past Claude.ai conversations.** Use the Chrome extension's
   "export all conversations" or the API. Save as `claude_messages_clean.md`.

2. **Extract URLs.** Run `scripts/url_sources.py claude_messages_clean.md` to
   produce `url_sources.json` and `url_sources.md` — your URL corpus
   classified by source type (substack, blog, lab, arxiv, etc.).

3. **Generate the topic taxonomy.** Feed `claude_messages_clean.md` to Claude
   with the prompt in `EXAMPLE_topic_taxonomy.md`. Save the output as
   `topic_taxonomy.md`.

4. **Sweep your inbox for subscriptions.** Filter Gmail for newsletter senders
   over the last 14 days. Group by your topic taxonomy. Save as
   `subscriptions.md`.

5. **Write a 1-page interest profile.** Use `EXAMPLE_interests_seed.md` as a
   template. Save as `interests_seed.md`.

The agents read all of these every run. The conversation export is grep-only
(it's huge). The taxonomy and interests are read in full.
