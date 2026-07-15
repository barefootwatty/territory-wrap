#!/usr/bin/env python3
"""Autonomous daily curator — runs unattended in the GitHub Action (cron).

Replaces the old Cowork scheduled task. Does the ONE job that used to need a
human-driven Claude: pick the day's stories and write them up. Everything
downstream (voice render, images, feed, site, deploy) is the existing pipeline,
untouched.

Flow:
  1. Fetch a candidate pool from the tested feeds (interests.json + sources.json).
  2. Ask Claude (with server-side web search + web fetch) to pick ~10-12 across
     the 8 sections, confirm each fact from the article body, vet the photo, and
     write summary + broadcast "spoken" copy to the editorial rules.
  3. Write data/<date>.json (full, private) + episodes/<date>.script.txt +
     episodes/<date>.json (public meta, no audio yet).
The Action then commits, and the existing publish step renders the voice, self-
hosts images, and rebuilds the feed + site.

Needs ANTHROPIC_API_KEY (curation) in env. Fails LOUDLY (non-zero exit, nothing
written) rather than publishing a broken edition — the previous day stays live.
"""
import os, sys, json, re, glob, urllib.parse, urllib.request, datetime
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import timezone, timedelta

import anthropic

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
import build_episode as be  # reuse compose_script + public-split logic

CONFIG = json.load(open(os.path.join(ENGINE, "config.json")))
INTERESTS = json.load(open(os.path.join(ENGINE, "interests.json")))
SOURCES = json.load(open(os.path.join(ENGINE, "sources.json")))

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
NOW_UTC = datetime.datetime.now(timezone.utc)
NT = NOW_UTC.astimezone(timezone(timedelta(hours=9, minutes=30)))  # ACST, Watty's clock
DATE = NT.strftime("%Y-%m-%d")
EDITION = NT.strftime("%A %-d %B %Y")
MODEL = os.environ.get("CURATION_MODEL", CONFIG.get("curation_model", "claude-opus-4-8"))

CATEGORIES = [
    "Director & Brand Watch", "Top End (NT local)", "Rec Fishing & the Bodies",
    "International & Destinations", "Fly Fishing", "Social & Catches",
    "Lighter Side", "Conservation",
]


# ---------- 1. Candidate pool (deterministic, plain Python) ----------
def _fetch(url):
    try:
        r = urllib.request.Request(url, headers=UA)
        return urllib.request.urlopen(r, timeout=25).read().decode("utf-8", "ignore")
    except Exception:
        return ""


def _parse(xml_text):
    i = xml_text.find("<?xml")
    if i > 0:
        xml_text = xml_text[i:]
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        dt = None
        if pub:
            try:
                dt = parsedate_to_datetime(pub)
            except Exception:
                dt = None
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out.append({"title": title, "url": link, "date": dt, "source": source})
    return out


def _recent_headlines():
    seen = set()
    for f in sorted(glob.glob(os.path.join(ROOT, "episodes", "20*.json")))[-6:]:
        try:
            for s in json.load(open(f)).get("stories", []):
                seen.add(s.get("headline", "").lower()[:60])
        except Exception:
            pass
    return seen


def candidate_pool():
    seen, pool = _recent_headlines(), []
    fmt = SOURCES["google_news_rss"]["url_format"]
    cat_fresh = {si["category"]: si["freshness_days"] for si in INTERESTS["standing_interests"]}

    def add(cat, item, fresh_days, via):
        t, dt = item["title"], item["date"]
        if not t or dt is None:
            return
        age = (NOW_UTC - dt).days
        if age > fresh_days or age < -2 or t.lower()[:60] in seen:
            return
        seen.add(t.lower()[:60])
        pool.append({"category": cat, "headline": t, "url": item["url"],
                     "source": item["source"], "date": dt.strftime("%Y-%m-%d"), "via": via})

    for si in INTERESTS["standing_interests"]:
        cat, fd = si["category"], si["freshness_days"]
        for q in si["queries"][:3]:  # a couple of good queries per category is plenty
            url = fmt.replace("{QUERY}", urllib.parse.quote(f"{q} when:{fd}d"))
            for item in _parse(_fetch(url)):
                add(cat, item, fd, f"search:{q}")
    for b in INTERESTS.get("temporary_boosts", []):
        if "_example" in b:
            continue
        exp = b.get("expires")
        if exp and datetime.date.fromisoformat(exp) < NT.date():
            continue
        cat, fd = b.get("category", "Lighter Side"), 14
        url = fmt.replace("{QUERY}", urllib.parse.quote(f"{b['keywords']} when:{fd}d"))
        for item in _parse(_fetch(url)):
            add(cat, item, fd, f"boost:{b['keywords']}")
    for feed in SOURCES["direct_feeds"]:
        cat = feed["category"]
        for item in _parse(_fetch(feed["url"])):
            add(cat, item, cat_fresh.get(cat, 10), f"feed:{feed['name']}")
    return pool


# ---------- 2. Claude curates + writes the edition ----------
RULES = """You are the editor of Glenn "Watty" Watt's daily GOOD-NEWS bulletin — recreational fishing, the outdoors, and Northern Territory news, with war/crime/death/disaster/politics-conflict left OUT.

Write and read like a professional news broadcast: measured, clear, confident. Australian English spelling, but NO slang ("mate", "ripper", "have a squiz"), no direct address by name, no forced humour. SCRIPT QUALITY: no unexplained fishing jargon (explain or drop); do NOT open every story with a formulaic tag ("A big one from...", "Some good news for..."); vary sentence construction; don't reuse the same dateline phrase twice.

The 8 EXACT category strings (use verbatim):
1. Director & Brand Watch (PRIVATE — genuine coverage of Angling Adventures / Barefoot Fishing Safaris / the Watt family in business, or Watty's lodges Bensbach/Christmas Island/Lake Murray/Tiwi/Arnhem Land; leave empty if nothing genuine)
2. Top End (NT local)
3. Rec Fishing & the Bodies
4. International & Destinations
5. Fly Fishing (MANDATORY: at least one item; saltwater/barramundi/saratoga preferred; no trout-fly-pattern as the sole item)
6. Social & Catches
7. Lighter Side
8. Conservation

FRESHNESS (from today's date): Top End / Rec Fishing / Director / Social = at most 3 days old; Conservation / Fly Fishing / International / Lighter = at most 10 days. Confirm the date on the article page — never trust a feed/snippet date.

RULES:
- Pick 10-12 strong stories across the sections. Always include >=1 Fly Fishing and always scan Director & Brand Watch. A genuine Top End (NT) item is highly valued.
- Use web_fetch to open each chosen story and CONFIRM the facts from the article body; use web_search to resolve a Google News link to the real publisher or to find a better source. Every fact in "summary" must come from the article body, not the feed title.
- NEVER source from Watty's own domains (anglingadventures.com.au/.net.au, barefootfishingsafaris.com.au, barefootfishingacademy). External coverage of his lodges IS wanted.
- IMAGE: use the article's og:image only if it clearly shows THIS story's subject; reject logos/banners/opengraph-cards/generic stock — set "image" to "" (a styled tile is better than a wrong photo).
- Drop off-topic keyword-match noise and anything not good-news.

OUTPUT: return ONLY a JSON object (no prose, no markdown fence) with this exact shape:
{"date":"<YYYY-MM-DD>","edition":"<Weekday D Month YYYY>","title":"Barefoot Daily News Bulletin — <edition>","intro":"<1-2 sentence professional open naming the day/date>","outro":"<short professional sign-off>","stories":[{"id":1,"category":"<one of the 8 exact strings>","headline":"...","summary":"<2-3 factual sentences from the body>","spoken":"<same story rewritten for the ear: broadcast style, numbers spelled out, no slang, varied openings>","source":"<publisher>","url":"<article url>","image":"<og:image url or empty string>"}]}"""


def curate(pool):
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    pool_txt = "\n".join(
        f"- [{c['category']}] {c['headline']} | {c['source']} | {c['date']} | {c['url']}"
        for c in pool)
    prompt = (f"{RULES}\n\nToday is {EDITION} ({DATE}).\n\n"
              f"Here is today's pre-dated candidate pool ({len(pool)} items). Choose from it, "
              f"but you may web_search for a better source or a fresh Top End / Fly Fishing item "
              f"if a section is thin. Confirm every chosen story by fetching its page.\n\n"
              f"CANDIDATES:\n{pool_txt}\n\nNow produce the edition JSON for {DATE}.")
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 20},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 25},
    ]
    messages = [{"role": "user", "content": prompt}]
    for _ in range(12):  # bound the server-tool agentic loop (handles pause_turn)
        resp = client.messages.create(model=MODEL, max_tokens=16000, tools=tools, messages=messages)
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        break
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise RuntimeError("curation produced no JSON:\n" + text[:500])
    edition = json.loads(m.group(0))
    edition["date"], edition["edition"] = DATE, EDITION
    edition.setdefault("title", f"{CONFIG['podcast_title']} — {EDITION}")
    for bad in [s for s in edition.get("stories", []) if s.get("category") not in CATEGORIES]:
        raise RuntimeError(f"story has an invalid category: {bad.get('category')!r}")
    return edition


# ---------- 3. Write the files the existing pipeline expects ----------
def write_edition(edition):
    for i, s in enumerate(edition["stories"], 1):
        s["id"] = i
    date = edition["date"]
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    json.dump(edition, open(os.path.join(ROOT, "data", f"{date}.json"), "w"),
              indent=2, ensure_ascii=False)  # full + private (data/ is git-ignored)

    exclude = set(CONFIG.get("public_exclude_categories", []))
    pub = dict(edition)
    pub["stories"] = [s for s in edition["stories"] if s.get("category") not in exclude]
    script = be.compose_script(pub)
    open(os.path.join(ROOT, "episodes", f"{date}.script.txt"), "w").write(script)
    meta = {"date": date, "edition": edition.get("edition", date),
            "title": be.episode_title(edition), "duration": None, "filesize": 0,
            "mp3": f"episodes/{date}.mp3", "voice": "pending",
            "intro": edition.get("intro", ""), "stories": pub["stories"],
            "built_at": datetime.datetime.now(timezone.utc).isoformat(timespec="seconds")}
    json.dump(meta, open(os.path.join(ROOT, "episodes", f"{date}.json"), "w"),
              indent=2, ensure_ascii=False)
    return len(pub["stories"])


def main():
    pool = candidate_pool()
    print(f"autobuild: {len(pool)} candidates for {DATE}")
    if len(pool) < 8:
        print("autobuild: WARNING pool is thin — continuing, Claude may broaden via web_search")
    edition = curate(pool)
    n = write_edition(edition)
    print(f"autobuild: wrote {DATE} — {n} public stories across "
          f"{len({s['category'] for s in edition['stories']})} sections")
    if n < 8:
        print(f"autobuild: NOTE only {n} public stories — honest short day")


if __name__ == "__main__":
    main()
