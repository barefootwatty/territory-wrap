#!/usr/bin/env python3
"""Render the STATIC dashboard, grouped into sections. Branding (title, tagline,
footer) is pulled from engine/config.json — edit that, not this file, to rebrand.

Usage:
  python3 build_site.py                       -> builds site/index.html from site/data.json (latest edition)
  python3 build_site.py <edition.json> <out>  -> builds site/<out> from a specific edition file

Cards are baked into HTML (no fetch). Article photos load from source CDNs with a
styled gradient fallback. A per-device read/archive "inbox" (localStorage) lets GW
tick items off once used; archived items hide but can be shown again.
"""
import os, json, html, sys, datetime

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
SITE = os.path.join(ROOT, "site")
CONFIG = json.load(open(os.path.join(ENGINE, "config.json")))

# Section order + colours. Category strings must match the data exactly.
SECTIONS = [
    ("Director & Brand Watch", "linear-gradient(135deg,#9a3b2e,#5e1f16)"),
    ("Top End (NT local)",     "linear-gradient(135deg,#c75b12,#7a2f08)"),
    ("Rec Fishing & the Bodies","linear-gradient(135deg,#1f6f8b,#0e3d4d)"),
    ("International & Destinations","linear-gradient(135deg,#2e7d5b,#123a2a)"),
    ("Fly Fishing",            "linear-gradient(135deg,#2f8f86,#12433f)"),
    ("Social & Catches",       "linear-gradient(135deg,#b5892a,#6e4d0e)"),
    ("Lighter Side",           "linear-gradient(135deg,#7a4ea8,#3d2559)"),
    ("Conservation",           "linear-gradient(135deg,#3f7d3a,#1c3d1a)"),
]
GRAD = dict(SECTIONS)


def esc(t):
    return html.escape(str(t), quote=True)


def fmt_dur(s):
    if not s:
        return ""
    s = int(s)
    return f"{s//60} min {s%60:02d} sec"


# Belt-and-braces image filter. Curation (the daily task) is the first line of
# defence, but drop anything here that smells like a generic banner/logo/social
# card rather than an article-specific photo, so a bad URL never reaches a card.
_BAD_IMG_BITS = (
    "logo", "sprite", "placeholder", "opengraph-image", "og-image", "og_image",
    "og-default", "default-", "-default", "banner", "fb_header", "_header",
    "facebook", "social-card", "share-image", "sharing", "favicon", "avatar",
    "/icons/", "icon-", "spacer", "blank.", "no-image", "noimage",
)


def clean_image(url):
    """Return url if it looks like a real article photo, else '' (styled fallback)."""
    if not url:
        return ""
    u = url.lower()
    # Self-hosted images (downloaded by the Action into site/img/) always pass —
    # they're same-origin and already vetted at curation time.
    if u.startswith("img/"):
        return url
    if not u.startswith(("http://", "https://")):
        return ""
    if any(bit in u for bit in _BAD_IMG_BITS):
        return ""
    return url


def card(s, date):
    cat = s.get("category", "")
    grad = GRAD.get(cat, "linear-gradient(135deg,#8a5a2b,#4a2f12)")
    cid = f"{date}-{s.get('id','x')}"
    url = s.get("url", "")
    img = clean_image(s.get("image", ""))
    imgtag = (
        f'<img src="{esc(img)}" alt="" loading="lazy" '
        f'onload="this.classList.add(\'on\')" onerror="this.style.display=\'none\'">'
        if img else ""
    )
    sample = '<span class="sample">sample</span>' if s.get("sample") else ""
    # Headline links straight to the source article.
    head_html = esc(s["headline"])
    if url:
        head_html = f'<a class="headlink" href="{esc(url)}" target="_blank" rel="noopener">{esc(s["headline"])}</a>'
    extra = ""
    if s.get("link_label"):
        extra = f'<p class="extra"><a href="{esc(url)}" target="_blank" rel="noopener">{esc(s["link_label"])}</a></p>'
    readmore = ""
    if url:
        readmore = f'<p class="readmore"><a href="{esc(url)}" target="_blank" rel="noopener">Read the full article →</a></p>'
    return f"""      <article class="card" data-id="{esc(cid)}" data-section="{esc(cat)}">
        <div class="hero" style="background:{grad}">
          <span class="chip">{esc(cat)}</span>{sample}
          <div class="fallback">{esc(s['headline'])}</div>
          {imgtag}
        </div>
        <div class="body">
          <h3>{head_html}</h3>
          <p>{esc(s['summary'])}</p>
          {extra}
          {readmore}
          <div class="cardfoot">
            <button class="done" onclick="markDone('{esc(cid)}')">✓ Mark used / archive</button>
            <span class="src">Source: <a href="{esc(url)}" target="_blank" rel="noopener">{esc(s['source'])}</a></span>
          </div>
        </div>
      </article>"""


def render(edition, out_name, is_preview=False):
    date = edition.get("date", "")
    stories = edition.get("stories", [])
    by_cat = {}
    for s in stories:
        by_cat.setdefault(s.get("category", "Other"), []).append(s)

    # known sections in order, then any leftover categories (e.g. legacy) so
    # nothing silently disappears from the dashboard
    ordered = [c for c, _ in SECTIONS] + [c for c in by_cat if c not in GRAD]
    blocks = []
    for cat in ordered:
        items = by_cat.get(cat, [])
        if not items:
            continue
        cards = "\n".join(card(s, date) for s in items)
        blocks.append(f'    <section class="sec-group" data-group="{esc(cat)}">\n'
                       f'      <h2 class="sec">{esc(cat)}</h2>\n{cards}\n    </section>')
    body_sections = "\n".join(blocks)

    edition_label = edition.get("edition", date)
    dur = fmt_dur(edition.get("duration"))
    mp3 = edition.get("mp3", "")
    if mp3:
        player = (f'<section class="player"><h2>Listen to today\'s wrap</h2>'
                  f'<p class="dur">{esc(dur)}</p>'
                  f'<audio controls preload="none" src="{esc(mp3)}"></audio>'
                  f'<div class="subrow"><a class="btn primary" href="feed.xml">Subscribe in your podcast app</a></div></section>')
    else:
        player = ('<section class="player"><h2>Morning audio bulletin</h2>'
                  '<p class="dur">Generated fresh each morning — plays here and in your podcast app.</p></section>')

    banner = ('<div class="framebar"><strong>Framework preview.</strong> '
              'Cards marked <em>sample</em> are placeholders to show the layout &amp; sections — '
              'real items replace them once GW signs off.</div>') if is_preview else ""

    doc = f"""<!DOCTYPE html>
<html lang="en-au">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(CONFIG['podcast_title'])}</title>
<link rel="manifest" href="manifest.json">
<meta name="theme-color" content="#f6efe6">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{esc(CONFIG['podcast_title'])}">
<link rel="apple-touch-icon" href="cover.jpg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700&family=Open+Sans:wght@400;600&display=swap" rel="stylesheet">
<style>
  :root{{--sand:#f6efe6;--card:#fffdf9;--ink:#2a2320;--muted:#7a6f66;
    --accent:#8a5a2b;--line:#e4d8c7;--chip:#efe4d3;--shadow:0 1px 6px rgba(80,50,20,.06)}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--sand);color:var(--ink);font-family:'Open Sans',system-ui,sans-serif;line-height:1.55}}
  .wrap{{max-width:760px;margin:0 auto;padding:0 18px 60px}}
  header.mast{{padding:24px 2px 14px;border-bottom:2px solid var(--ink);margin-bottom:16px}}
  .mast .row{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}}
  .mast h1{{font-family:'Montserrat';font-weight:600;font-size:27px;letter-spacing:-.01em;margin:0}}
  .mast .date{{font-family:'Montserrat';font-weight:500;font-size:13px;color:var(--muted);white-space:nowrap}}
  .mast .tag{{font-size:13px;color:var(--muted);margin:6px 0 0}}
  .framebar{{background:#fff3e2;border:1px solid #e7c9a0;border-radius:10px;padding:10px 13px;font-size:13px;margin:14px 0 4px}}
  .toolbar{{display:flex;justify-content:flex-end;margin:8px 0 0}}
  .toolbar button{{font-family:'Montserrat';font-weight:600;font-size:12px;background:none;border:none;color:var(--accent);cursor:pointer}}
  .player{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;box-shadow:var(--shadow);margin:10px 0 4px}}
  .player h2{{font-family:'Montserrat';font-weight:600;font-size:15px;margin:0 0 3px}}
  .player .dur{{color:var(--muted);font-size:12px;margin:0}}
  audio{{width:100%;height:38px;margin-top:8px}}
  .subrow{{margin-top:10px}}
  .btn{{font-family:'Montserrat';font-weight:600;font-size:12px;text-decoration:none;border-radius:9px;padding:8px 13px;border:1px solid var(--line);background:#fff;color:var(--ink)}}
  .btn.primary{{background:var(--accent);color:#fff;border-color:var(--accent)}}
  .sec{{font-family:'Montserrat';font-weight:700;font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);margin:26px 2px 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:14px;box-shadow:var(--shadow);overflow:hidden}}
  .card.archived{{display:none}}
  .hero{{position:relative;aspect-ratio:16/9;overflow:hidden}}
  .hero img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .4s}}
  .hero img.on{{opacity:1}}
  .hero .fallback{{position:absolute;inset:0;display:flex;align-items:flex-end;font-family:'Montserrat';font-weight:700;font-size:19px;color:#fff;padding:15px;text-shadow:0 1px 6px rgba(0,0,0,.4)}}
  .hero .chip{{position:absolute;top:11px;left:11px;z-index:2;background:rgba(255,255,255,.94);color:#333;font-family:'Montserrat';font-weight:600;font-size:10px;letter-spacing:.04em;text-transform:uppercase;padding:4px 9px;border-radius:11px}}
  .hero .sample{{position:absolute;top:11px;right:11px;z-index:2;background:#2a2320;color:#fff;font-family:'Montserrat';font-weight:600;font-size:9px;letter-spacing:.1em;text-transform:uppercase;padding:4px 8px;border-radius:11px;opacity:.85}}
  .body{{padding:14px 16px 14px}}
  .body h3{{font-family:'Montserrat';font-weight:700;font-size:18px;margin:0 0 7px;line-height:1.3}}
  .body h3 .headlink{{color:inherit;text-decoration:none}}
  .body h3 .headlink:hover{{color:var(--accent);text-decoration:underline}}
  .body p{{margin:0 0 9px;font-size:15px;color:#3a322d}}
  .extra a{{font-family:'Montserrat';font-weight:600;font-size:13px;color:var(--accent)}}
  .readmore{{margin:0 0 10px}}
  .readmore a{{font-family:'Montserrat';font-weight:700;font-size:13px;color:var(--accent);text-decoration:none}}
  .readmore a:hover{{text-decoration:underline}}
  .cardfoot{{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-top:4px}}
  .done{{font-family:'Montserrat';font-weight:600;font-size:12px;cursor:pointer;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:9px;padding:7px 11px}}
  .done:hover{{background:var(--chip)}}
  .src{{font-size:12px;color:var(--muted)}}
  .src a{{color:var(--accent)}}
  footer{{text-align:center;color:var(--muted);font-size:12px;margin-top:34px;line-height:1.7}}
</style>
</head>
<body>
<div class="wrap">
  <header class="mast">
    <div class="row"><h1>{esc(CONFIG['podcast_title'])}</h1><span class="date">{esc(edition_label)}</span></div>
    <p class="tag">{esc(CONFIG['podcast_subtitle'])}</p>
  </header>
  {banner}
  {player}
  <div class="toolbar"><button id="toggleArch" onclick="toggleArchived()">Show archived</button></div>
{body_sections}
  <footer>{esc(CONFIG['podcast_title'])} · Bees Creek, NT · {esc(CONFIG['podcast_subtitle'])}</footer>
</div>
<script>
  const KEY='tw_read';
  function readSet(){{try{{return new Set(JSON.parse(localStorage.getItem(KEY)||'[]'))}}catch(e){{return new Set()}}}}
  function save(set){{localStorage.setItem(KEY,JSON.stringify([...set]))}}
  let showArch=false;
  function apply(){{
    const set=readSet();
    document.querySelectorAll('.card').forEach(c=>{{
      const done=set.has(c.dataset.id);
      c.classList.toggle('archived', done && !showArch);
      const b=c.querySelector('.done');
      if(b) b.textContent = done ? '↩ Unarchive' : '✓ Mark used / archive';
    }});
    document.querySelectorAll('.sec-group').forEach(g=>{{
      const anyVisible=[...g.querySelectorAll('.card')].some(c=>!c.classList.contains('archived'));
      g.style.display = anyVisible ? '' : 'none';
    }});
    document.getElementById('toggleArch').textContent = showArch ? 'Hide archived' : 'Show archived';
  }}
  function markDone(id){{const set=readSet(); set.has(id)?set.delete(id):set.add(id); save(set); apply();}}
  function toggleArchived(){{showArch=!showArch; apply();}}
  apply();
</script>
</body>
</html>
"""
    open(os.path.join(SITE, out_name), "w").write(doc)
    print(f"Built site/{out_name}: {len(stories)} cards across "
          f"{len([1 for c,_ in SECTIONS if by_cat.get(c)])} sections")


def main():
    if len(sys.argv) >= 3:
        edition = json.load(open(sys.argv[1]))
        render(edition, sys.argv[2], is_preview="sample" in sys.argv[1] or "framework" in sys.argv[1])
        return
    data = json.load(open(os.path.join(SITE, "data.json")))
    eps = data.get("episodes", [])
    if not eps:
        print("no episodes"); return
    render(eps[0], "index.html")


if __name__ == "__main__":
    main()
