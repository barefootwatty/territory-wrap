#!/usr/bin/env python3
"""Rebuild the podcast RSS feed + dashboard data.json from all episode metas.

Usage: python3 build_feed.py
Prunes episodes older than retention_days, then writes site/feed.xml and
site/data.json referencing config.base_url.
"""
import os, json, glob, html, datetime, email.utils

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
EPISODES = os.path.join(ROOT, "episodes")
SITE = os.path.join(ROOT, "site")
SITE_EP = os.path.join(SITE, "episodes")
CONFIG = json.load(open(os.path.join(ENGINE, "config.json")))
os.makedirs(SITE_EP, exist_ok=True)


def esc(t):
    return html.escape(str(t), quote=True)


def rfc2822(date_str, hour=6):
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=hour, minute=30, tzinfo=datetime.timezone(datetime.timedelta(hours=9, minutes=30))
    )
    return email.utils.format_datetime(d)


def prune(metas, keep):
    # Feed/dashboard show only the last `keep` days. Physical deletion is
    # best-effort: user folders block unlink, so old MP3s simply stop being
    # listed rather than erroring the run.
    cutoff = datetime.date.today() - datetime.timedelta(days=keep)
    kept = []
    for m in metas:
        d = datetime.datetime.strptime(m["date"], "%Y-%m-%d").date()
        if d < cutoff:
            for f in (os.path.join(EPISODES, f"{m['date']}.mp3"),
                      os.path.join(EPISODES, f"{m['date']}.json"),
                      os.path.join(SITE_EP, f"{m['date']}.mp3")):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except OSError:
                    pass
        else:
            kept.append(m)
    return kept


def is_sample(m):
    """Never publish framework/sample editions (placeholder cards, '#' links)."""
    label = (str(m.get("edition", "")) + " " + str(m.get("title", ""))).lower()
    if "sample" in label or "framework" in label or "preview" in label:
        return True
    stories = m.get("stories", [])
    return bool(stories) and all(s.get("sample") for s in stories)


def main():
    base = CONFIG["base_url"].rstrip("/")
    metas = [json.load(open(p)) for p in glob.glob(os.path.join(EPISODES, "*.json"))]
    metas = [m for m in metas if not is_sample(m)]
    metas.sort(key=lambda m: m["date"], reverse=True)
    metas = prune(metas, int(CONFIG.get("retention_days", 30)))

    items = []
    for m in metas:
        url = f"{base}/{m['mp3']}"
        stories = m.get("stories", [])
        desc_lines = [f"{s['headline']} ({s.get('source','')})" for s in stories]
        desc = "In this edition: " + "; ".join(desc_lines)
        # Clickable show notes: headline links to the source article.
        note_items = "".join(
            f'<li><a href="{esc(s.get("url",""))}">{esc(s["headline"])}</a>'
            f' — {esc(s.get("source",""))}</li>'
            for s in stories if s.get("url")
        )
        notes_html = (f"<p>In this edition:</p><ul>{note_items}</ul>"
                      if note_items else esc(desc))
        items.append(f"""    <item>
      <title>{esc(m['title'])}</title>
      <description>{esc(desc)}</description>
      <content:encoded><![CDATA[{notes_html}]]></content:encoded>
      <pubDate>{rfc2822(m['date'])}</pubDate>
      <enclosure url="{esc(url)}" length="{m.get('filesize',0)}" type="audio/mpeg"/>
      <guid isPermaLink="false">territory-wrap-{m['date']}</guid>
      <itunes:duration>{int(m.get('duration') or 0)}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
    </item>""")

    now = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc))
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{esc(CONFIG['podcast_title'])}</title>
    <link>{esc(base)}</link>
    <language>{esc(CONFIG['podcast_language'])}</language>
    <description>{esc(CONFIG['podcast_description'])}</description>
    <itunes:author>{esc(CONFIG['podcast_author'])}</itunes:author>
    <itunes:subtitle>{esc(CONFIG['podcast_subtitle'])}</itunes:subtitle>
    <itunes:summary>{esc(CONFIG['podcast_description'])}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:explicit>false</itunes:explicit>
    <itunes:image href="{esc(base)}/cover.jpg"/>
    <itunes:owner>
      <itunes:name>{esc(CONFIG['podcast_owner_name'])}</itunes:name>
      <itunes:email>{esc(CONFIG['podcast_owner_email'])}</itunes:email>
    </itunes:owner>
    <itunes:category text="{esc(CONFIG['podcast_category'])}">
      <itunes:category text="{esc(CONFIG['podcast_subcategory'])}"/>
    </itunes:category>
    <image>
      <url>{esc(base)}/cover.jpg</url>
      <title>{esc(CONFIG['podcast_title'])}</title>
      <link>{esc(base)}</link>
    </image>
    <lastBuildDate>{now}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
    open(os.path.join(SITE, "feed.xml"), "w").write(feed)

    # dashboard data
    data = {
        "podcast_title": CONFIG["podcast_title"],
        "base_url": base,
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "episodes": metas,
    }
    json.dump(data, open(os.path.join(SITE, "data.json"), "w"), indent=2)
    print(f"Feed rebuilt: {len(metas)} episode(s). base_url={base}")


if __name__ == "__main__":
    main()
