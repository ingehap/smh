I'll research the current state of X/Twitter data-collection tools before writing anything, 
since this landscape has changed a lot and recommending a broken tool would waste your time.
I have a clear picture of the 2026 landscape. Let me verify the exact current API of the 
strongest candidate before writing the script.Scweet v5.0 (released March 2026, last verified 
working March 2026) is the right tool. Let me install it in my sandbox to read the exact 
tweet-object schema from source — I want the field names to be correct rather than guessed. 
(I can't run a live scrape here since x.com isn't reachable from this environment, but the 
source is the ground truth for output structure.)Scweet 5.3 installed cleanly. Now let me read 
the actual source to get the exact `get_profile_tweets` signature and the tweet object schema.
`get_profile_tweets` returns a `ProfileResult` whose `items` are raw dicts, so I need the exact 
dict keys. Let me read the client class and the output serializer that knows those keys.Let me 
get the exact dict keys from `_tweet_to_dict` (the canonical per-tweet schema) and the 
save-format/error details.I now have the complete ground-truth schema. Let me confirm the exact 
per-tweet dict keys empirically by constructing the model.Schema confirmed empirically against 
Scweet 5.3. I have exactly what I need. Writing the script now, then I'll verify it parses, 
imports, and runs its CLI in the sandbox (live scraping can't be tested here because x.com is 
outside this environment's allowlist — but the logic, schema mapping, and CLI will be verified).
Now let me verify it compiles, the CLI works, the input/auth guards behave, and the Markdown 
rendering is correct against synthetic tweet records (everything except the live network call, 
which can't run here).CLI and guards work. Now the most important check — that the schema mapping 
and Markdown rendering produce correct output. I'll feed synthetic records matching Scweet's exact 
schema, including edge cases (empty text, leading Markdown characters, quoted text, media, missing 
date).Rendering is correct — Markdown escaping of leading block characters, media, quoted text, 
and dedupe all behave. One refinement: undated tweets currently sort to the top in newest-first 
mode; they should always go last. Let me fix that.The `_ts_key` helper is now unused; let me remove 
it to keep the script clean, then re-verify the full run end-to-end.Verified end-to-end. Copying 
to outputs and presenting.Here's `x_to_markdown.py`. I verified it in my sandbox: it compiles, the 
CLI and input/auth guards work, and the Markdown rendering and ordering are correct against synthetic 
records matching Scweet's exact schema. The one thing I couldn't exercise here is the live network 
call, because x.com isn't on this environment's domain allowlist — but the schema mapping is 
confirmed against `Scweet.models` (v5.3), so the output structure is correct.

**Tool choice and why.** The free-scraper era is over. snscrape broke after X's backend changes and 
twint is unmaintained, and the official API now costs $100/month minimum with the free tier being 
effectively useless. Everything that still works in 2026 authenticates with a logged-in session cookie 
and calls X's internal GraphQL API. I built on **Scweet** (MIT, PyPI `scweet`), which is purpose-built 
for pulling a user's full public timeline and was last verified working in March 2026 against X's 
current GraphQL API. Its own comparison table puts it alongside `twscrape` as the two libraries that 
still function, with Scweet adding built-in file output, resume, and a sync API — `twscrape` is the 
closest alternative if you ever want a fallback.

**The non-negotiable requirement: an `auth_token` cookie.** There's no username/password path — X's 
anti-automation defenses make programmatic login unreliable and likely to trigger account locks, so 
Scweet authenticates with the browser cookie instead. To get it: 
log into x.com → DevTools (F12) → Application → Cookies → `https://x.com` → copy `auth_token`. 
**Use a dedicated/throwaway account, never your personal one** 
— a single account sustains only a few hundred to a few thousand tweets/day before rate limits.

**Run it:**
```bash
python -m pip install -U Scweet
export X_AUTH_TOKEN="paste_your_auth_token_here"   # keeps it out of shell history
python x_to_markdown.py https://x.com/FrnkNlsn      # -> FrnkNlsn_tweets_YYYYMMDD.md
```
Useful flags: 
`--limit N`, 
`--oldest-first`, 
`--exclude-retweets`, 
`--json` (raw alongside the .md), 
`--resume` (Ctrl-C is safe; state lives in `scweet_state.db`), 
`--proxy`, 
`--cookies-file` 
for multi-account. The output is a single .md with YAML front matter (handle, profile, count, 
timestamp, tool version), an optional profile header, and one section per tweet (text as a faithful 
blockquote, engagement counts, media links, permalink).

**Honest caveats.** 
Only public accounts are reachable; protected ones are not. It rides undocumented endpoints that X 
changes every few weeks — `--self-heal` (on by default) refreshes query IDs at startup, but if a run 
starts failing, `pip install -U Scweet` is the first move. And X's ToS prohibits automated access 
without permission; as of 2026, accessing more than 1,000,000 posts in 24 hours via automated means 
without permission carries $15,000 in liquidated damages — single-account timeline archiving is far 
below that, but the compliance responsibility (and GDPR, if you store data on EU residents) is yours.

References: 
Scweet — github.com/Altimis/Scweet (PyPI `scweet`); twscrape — github.com/vladkens/twscrape; 
2026 landscape — scrapegraphai.com/blog/tweet-scraper, scrapfly.io/blog/posts/how-to-scrape-twitter.

