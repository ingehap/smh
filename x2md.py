#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
x_to_markdown.py — Collect a public X/Twitter account's timeline into a Markdown file.

Given an account (a bare handle, "@handle", or a full https://x.com/handle URL),
this tool fetches the account's public tweet timeline and writes a single,
human-readable Markdown (.md) archive.

------------------------------------------------------------------------------
WHY THIS TOOL / THE 2026 REALITY
------------------------------------------------------------------------------
X (formerly Twitter) shut down free API access in February 2023; the official
API now starts at roughly USD 100/month (Basic, ~10k tweets/month) and rises
steeply. The classic free scrapers are dead: `twint` is unmaintained (since
2023) and `snscrape` broke after X's backend changes. The approaches that still
work in 2026 all authenticate with a logged-in session cookie and call X's
internal GraphQL API directly.

This script is built on **Scweet** (MIT-licensed, actively maintained — v5.x as
of 2026), which is purpose-built for exactly this: pulling a user's full public
timeline via cookie + GraphQL, with a self-healing manifest, rate-limiting and
resume support.

    Scweet ........ https://github.com/Altimis/Scweet  (PyPI: `scweet`)
    Alternatives .. twscrape (https://github.com/vladkens/twscrape) — async-only,
                    no built-in file output; closest active competitor.

------------------------------------------------------------------------------
INSTALL
------------------------------------------------------------------------------
    python -m pip install -U Scweet

------------------------------------------------------------------------------
AUTHENTICATION (REQUIRED)
------------------------------------------------------------------------------
Scweet needs your `auth_token` cookie from a logged-in x.com session. There is
no username/password login: X's anti-automation defenses make programmatic
login unreliable and likely to lock the account.

To obtain it:
    1. Log into https://x.com in a browser.
    2. Open DevTools (F12) -> Application -> Cookies -> https://x.com
    3. Copy the value of the `auth_token` cookie.

Provide it to this script via EITHER:
    * the X_AUTH_TOKEN environment variable (recommended — keeps it out of your
      shell history), or
    * the --auth-token flag, or
    * a --cookies-file cookies.json (Scweet multi-account format).

  >> Use a dedicated / throwaway account, never your personal one. <<
  A single account typically sustains a few hundred to a few thousand tweets
  per day before hitting X's rate limits.

------------------------------------------------------------------------------
USAGE EXAMPLES
------------------------------------------------------------------------------
    export X_AUTH_TOKEN="paste_your_auth_token_here"

    # Whole available timeline -> FrnkNlsn_tweets_YYYYMMDD.md
    python x_to_markdown.py https://x.com/FrnkNlsn

    # Cap at 500 tweets, oldest-first, custom output name
    python x_to_markdown.py @FrnkNlsn --limit 500 --oldest-first -o frank.md

    # Resume an interrupted run (state is kept in scweet_state.db)
    python x_to_markdown.py FrnkNlsn --resume

    # Also dump the raw tweet records as JSON alongside the Markdown
    python x_to_markdown.py FrnkNlsn --json

------------------------------------------------------------------------------
LIMITATIONS & ETHICS
------------------------------------------------------------------------------
* Only PUBLIC content is reachable; protected/private accounts are not.
* It relies on undocumented X web endpoints; X changes them roughly every few
  weeks. --self-heal (default on) refreshes query IDs at startup, but breakage
  is always possible. Pin or upgrade Scweet if a run starts failing.
* X's Terms of Service prohibit automated access without permission, and as of
  2026 impose liquidated damages for very-high-volume access (>1,000,000 posts
  in 24h). Single-account timeline archiving is far below that, but you are
  responsible for complying with the ToS and applicable law (e.g. GDPR if you
  store data about EU residents — minimise PII, keep a retention policy).

------------------------------------------------------------------------------
Author note: written as a self-contained CLI; standard library only, apart from
Scweet. Tested against Scweet 5.3 (schema confirmed against Scweet.models).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Scweet import (deferred-friendly, with a helpful message if missing)
# --------------------------------------------------------------------------- #
try:
    from Scweet import Scweet
    from Scweet.exceptions import (
        AccountPoolExhausted,
        AuthError,
        EngineError,
        NetworkError,
        ProxyError,
        RateLimitError,
        RunFailed,
        ScweetError,
    )
    try:
        from Scweet import __version__ as SCWEET_VERSION
    except Exception:  # pragma: no cover
        SCWEET_VERSION = "unknown"
except ModuleNotFoundError:
    sys.stderr.write(
        "ERROR: the 'Scweet' package is not installed.\n"
        "Install it with:\n\n    python -m pip install -U Scweet\n\n"
    )
    sys.exit(2)


# --------------------------------------------------------------------------- #
# Handle / input normalisation
# --------------------------------------------------------------------------- #
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def normalize_handle(raw: str) -> str:
    """Turn '@handle', 'handle', or any x.com/twitter.com URL into a bare handle."""
    s = raw.strip()
    # Strip a URL down to its first path segment.
    m = re.search(r"(?:twitter\.com|x\.com)/([^/?#]+)", s, flags=re.IGNORECASE)
    if m:
        s = m.group(1)
    s = s.lstrip("@").strip("/").strip()
    # Drop a trailing query/fragment if any slipped through.
    s = re.split(r"[?#]", s)[0]
    if not _HANDLE_RE.match(s):
        raise ValueError(
            f"'{raw}' does not look like a valid X handle. Expected something "
            "like 'FrnkNlsn', '@FrnkNlsn', or 'https://x.com/FrnkNlsn'."
        )
    return s


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #
# Characters that, at the start of a line, would start a Markdown block. We
# escape only these (leading position) to preserve tweet text fidelity while
# keeping the document readable.
_LEADING_BLOCK_RE = re.compile(r"^(\s*)([#>\-+*]|\d+[.)])(\s)")


def _escape_md_line(line: str) -> str:
    return _LEADING_BLOCK_RE.sub(lambda m: f"{m.group(1)}\\{m.group(2)}{m.group(3)}", line)


def as_blockquote(text: Optional[str]) -> str:
    """Render multi-line text as a Markdown blockquote, faithfully."""
    if not text:
        return "> *(no text)*"
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join("> " + _escape_md_line(ln) if ln else ">" for ln in lines)


def yaml_quote(value: Any) -> str:
    """Safely quote a scalar for the YAML front matter."""
    s = "" if value is None else str(value)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fmt_timestamp(ts: Optional[str]) -> str:
    """Pretty 'YYYY-MM-DD HH:MM UTC' from an ISO timestamp; fall back to raw."""
    if not ts:
        return "(no date)"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return str(ts)


# --------------------------------------------------------------------------- #
# Tweet -> Markdown
# --------------------------------------------------------------------------- #
def tweet_to_md(tweet: dict) -> str:
    """Render one Scweet tweet dict to a Markdown section.

    Schema (Scweet.models.TweetRecord, confirmed v5.3):
        tweet_id, user{screen_name,name}, timestamp, text, embedded_text,
        emojis, comments, likes, retweets, media{image_links[]}, tweet_url, raw
    """
    parts: list[str] = []
    parts.append(f"### {fmt_timestamp(tweet.get('timestamp'))}")
    parts.append("")
    parts.append(as_blockquote(tweet.get("text")))

    embedded = tweet.get("embedded_text")
    if embedded and embedded.strip() and embedded.strip() != (tweet.get("text") or "").strip():
        parts.append("")
        parts.append("> **Quoted / embedded:**")
        parts.append(as_blockquote(embedded))

    parts.append("")
    metrics = (
        f"Likes: {tweet.get('likes', 0)} "
        f"\u00b7 Retweets: {tweet.get('retweets', 0)} "
        f"\u00b7 Replies: {tweet.get('comments', 0)}"
    )
    parts.append(metrics)

    media = (tweet.get("media") or {}).get("image_links") or []
    if media:
        links = " \u00b7 ".join(f"[{i + 1}]({u})" for i, u in enumerate(media))
        parts.append("")
        parts.append(f"Media: {links}")

    url = tweet.get("tweet_url")
    tid = tweet.get("tweet_id", "")
    footer = f"[Open on X]({url})" if url else "(no link)"
    if tid:
        footer += f" \u00b7 ID `{tid}`"
    parts.append("")
    parts.append(footer)
    parts.append("")
    return "\n".join(parts)


def build_markdown(handle: str, tweets: list[dict], profile: Optional[dict]) -> str:
    profile = profile or {}
    profile_name = profile.get("name") or (tweets[0]["user"].get("name") if tweets else None)
    profile_url = f"https://x.com/{handle}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # YAML front matter -------------------------------------------------------
    fm = [
        "---",
        f"account: {yaml_quote('@' + handle)}",
        f"profile_name: {yaml_quote(profile_name)}",
        f"profile_url: {profile_url}",
        f"tweets_collected: {len(tweets)}",
        f"collected_utc: {now}",
        f"collected_with: {yaml_quote('Scweet ' + str(SCWEET_VERSION))}",
        f"note: {yaml_quote('Public timeline via X GraphQL endpoint; not guaranteed exhaustive.')}",
        "---",
        "",
    ]

    out: list[str] = ["\n".join(fm)]
    out.append(f"# Tweets by @{handle}")
    if profile_name:
        out.append(f"\n**{profile_name}** \u2014 [{profile_url}]({profile_url})")

    # Optional profile block (best-effort) -----------------------------------
    if profile:
        desc = (profile.get("description") or "").strip()
        if desc:
            out.append("\n" + as_blockquote(desc))
        stat_bits = []
        if profile.get("followers_count") is not None:
            stat_bits.append(f"Followers: {profile['followers_count']}")
        if profile.get("following_count") is not None:
            stat_bits.append(f"Following: {profile['following_count']}")
        if profile.get("statuses_count") is not None:
            stat_bits.append(f"Posts: {profile['statuses_count']}")
        if profile.get("location"):
            stat_bits.append(f"Location: {profile['location']}")
        if stat_bits:
            out.append("\n" + " \u00b7 ".join(stat_bits))

    out.append(f"\n*Collected {now} with Scweet {SCWEET_VERSION}. "
               f"{len(tweets)} tweet(s).*\n")
    out.append("---\n")

    if not tweets:
        out.append("*No tweets were returned.*\n")
    else:
        out.extend(tweet_to_md(t) for t in tweets)

    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #
def fetch_profile(client: "Scweet", handle: str) -> Optional[dict]:
    """Best-effort profile metadata; never fatal."""
    try:
        info = client.get_user_info([handle])
        if info:
            return info[0]
    except Exception as exc:  # noqa: BLE001 — metadata is optional
        sys.stderr.write(f"[warn] could not fetch profile metadata: {exc}\n")
    return None


def dedupe_preserve(tweets: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for t in tweets:
        tid = t.get("tweet_id")
        if tid and tid in seen:
            continue
        if tid:
            seen.add(tid)
        out.append(t)
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="x_to_markdown.py",
        description="Collect a public X/Twitter account timeline into a Markdown file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Auth: set X_AUTH_TOKEN or pass --auth-token / --cookies-file. "
               "Use a dedicated account, not your personal one.",
    )
    p.add_argument("account",
                   help="Handle, @handle, or full x.com/twitter.com profile URL.")
    p.add_argument("-o", "--output",
                   help="Output .md path (default: <handle>_tweets_<YYYYMMDD>.md).")
    p.add_argument("--limit", type=int, default=None,
                   help="Max tweets to collect (default: all available — runs "
                        "until the account's daily cap is hit).")
    p.add_argument("--oldest-first", action="store_true",
                   help="Order the archive oldest-to-newest (default: newest first).")
    p.add_argument("--exclude-retweets", action="store_true",
                   help="Heuristically drop retweets (text beginning with 'RT @').")
    p.add_argument("--json", dest="dump_json", action="store_true",
                   help="Also write the raw tweet records as <output>.json.")

    auth = p.add_argument_group("authentication")
    auth.add_argument("--auth-token", default=os.environ.get("X_AUTH_TOKEN"),
                      help="x.com auth_token cookie (or set X_AUTH_TOKEN).")
    auth.add_argument("--cookies-file",
                      help="Scweet cookies.json (multi-account) instead of a single token.")

    adv = p.add_argument_group("advanced")
    adv.add_argument("--proxy",
                     help="Proxy URL, e.g. http://user:pass@host:port.")
    adv.add_argument("--db", default="scweet_state.db",
                     help="Scweet state DB path (used for resume; default: scweet_state.db).")
    adv.add_argument("--resume", action="store_true",
                     help="Resume an interrupted collection for this account.")
    adv.add_argument("--no-self-heal", action="store_true",
                     help="Disable startup manifest refresh (self-heal is on by default).")
    adv.add_argument("-q", "--quiet", action="store_true",
                     help="Suppress progress messages.")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    def log(msg: str) -> None:
        if not args.quiet:
            sys.stderr.write(msg + "\n")

    # 1. Resolve handle ------------------------------------------------------
    try:
        handle = normalize_handle(args.account)
    except ValueError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2

    # 2. Auth check ----------------------------------------------------------
    if not args.auth_token and not args.cookies_file:
        sys.stderr.write(
            "ERROR: no credentials. Set the X_AUTH_TOKEN environment variable, "
            "or pass --auth-token, or --cookies-file.\n\n"
            "Get auth_token: log into x.com -> DevTools (F12) -> Application -> "
            "Cookies -> https://x.com -> copy the 'auth_token' value.\n"
            "Use a dedicated/throwaway account, never your personal one.\n"
        )
        return 2

    # 3. Build client --------------------------------------------------------
    log(f"[info] Scweet {SCWEET_VERSION} | target @{handle} | "
        f"limit={args.limit if args.limit is not None else 'all'}")
    try:
        client = Scweet(
            auth_token=args.auth_token,
            cookies_file=args.cookies_file,
            proxy=args.proxy,
            db_path=args.db,
            manifest_scrape_on_init=not args.no_self_heal,
        )
    except AuthError as exc:
        sys.stderr.write(f"ERROR: authentication failed: {exc}\n"
                         "Your auth_token may be expired — grab a fresh one.\n")
        return 1
    except (NetworkError, ProxyError) as exc:
        sys.stderr.write(f"ERROR: network/proxy problem during setup: {exc}\n")
        return 1
    except ScweetError as exc:
        sys.stderr.write(f"ERROR: Scweet failed to initialise: {exc}\n")
        return 1

    # 4. Fetch ---------------------------------------------------------------
    log("[info] fetching profile metadata...")
    profile = fetch_profile(client, handle)

    log("[info] fetching timeline (this can take a while; Ctrl-C is safe with --resume)...")
    try:
        tweets = client.get_profile_tweets(
            [handle], limit=args.limit, resume=args.resume, save=False,
        )
    except AuthError as exc:
        sys.stderr.write(f"ERROR: authentication failed: {exc}\n"
                         "Refresh your auth_token and retry.\n")
        return 1
    except RateLimitError as exc:
        sys.stderr.write(f"ERROR: rate-limited by X: {exc}\n"
                         "Wait and retry with --resume, or add more accounts via "
                         "--cookies-file.\n")
        return 1
    except AccountPoolExhausted as exc:
        sys.stderr.write(f"ERROR: all accounts exhausted/cooling down: {exc}\n")
        return 1
    except (NetworkError, ProxyError) as exc:
        sys.stderr.write(f"ERROR: network/proxy problem: {exc}\n")
        return 1
    except (EngineError, RunFailed) as exc:
        sys.stderr.write(
            f"ERROR: scrape engine failure: {exc}\n"
            "X may have changed its endpoints. Upgrade Scweet "
            "(`pip install -U Scweet`) and retry.\n")
        return 1
    except ScweetError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\n[info] interrupted. Re-run with --resume to continue.\n")
        return 130

    # 5. Post-process --------------------------------------------------------
    tweets = dedupe_preserve(tweets or [])
    if args.exclude_retweets:
        tweets = [t for t in tweets if not (t.get("text") or "").lstrip().startswith("RT @")]
    # Sort dated tweets by timestamp; keep any undated ones at the end either way.
    dated = [t for t in tweets if t.get("timestamp")]
    undated = [t for t in tweets if not t.get("timestamp")]
    dated.sort(key=lambda t: t["timestamp"], reverse=not args.oldest_first)
    tweets = dated + undated
    log(f"[info] collected {len(tweets)} tweet(s).")

    # 6. Write ---------------------------------------------------------------
    out_path = args.output or f"{handle}_tweets_{datetime.now():%Y%m%d}.md"
    markdown = build_markdown(handle, tweets, profile)
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(markdown)
    except OSError as exc:
        sys.stderr.write(f"ERROR: could not write '{out_path}': {exc}\n")
        return 1
    log(f"[done] wrote {out_path}")

    if args.dump_json:
        json_path = (out_path[:-3] if out_path.endswith(".md") else out_path) + ".json"
        try:
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(tweets, fh, ensure_ascii=False, indent=2)
            log(f"[done] wrote {json_path}")
        except OSError as exc:
            sys.stderr.write(f"[warn] could not write JSON '{json_path}': {exc}\n")

    if not tweets:
        log("[warn] no tweets returned — the account may be empty/protected, or "
            "your token may lack access.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
