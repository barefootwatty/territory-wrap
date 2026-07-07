#!/usr/bin/env python3
"""Build one podcast episode (script + MP3 + meta) from a curated stories JSON.

Usage: python3 build_episode.py <path-to-stories.json>

Composes a spoken script (title read, up-front headline summary, then section by
section), writes it to episodes/<date>.script.txt, and renders audio via the
pluggable voice provider (ElevenLabs/Hannah at the hosting layer; espeak fallback).
"""
import sys, os, json, re, subprocess, datetime, shutil
import tts_provider

# Phonetic respellings applied to the SPOKEN script ONLY, so tricky place names
# read correctly in the audio. The headlines/summaries in the data and on the
# dashboard stay spelled correctly — this only rewrites what Emma actually says.
# Extend freely: {correct spelling: how it should sound}.
PRONUNCE = {
    "Kiritimati": "Kiriss-mass",
    "Bensbach": "Bens-bahk",
    "Nhulunbuy": "Nool-un-boy",
    "Maningrida": "Manning-greeda",
    "Dhipirri": "Dip-er-ree",
    "Arnhem": "Arn-em",
    "Tiwi": "Tee-wee",
    "Kakadu": "Kacka-doo",
    "saratoga": "sarra-toe-ga",
    "saratogas": "sarra-toe-gas",
    "barramundi": "barra-mundi",
}


def apply_pronunciation(text):
    """Respell known tricky words for the ear. Whole-word, case-insensitive."""
    for word, say in PRONUNCE.items():
        text = re.sub(rf"\b{re.escape(word)}\b", say, text, flags=re.IGNORECASE)
    return text

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
EPISODES = os.path.join(ROOT, "episodes")
SITE_EP = os.path.join(ROOT, "site", "episodes")
CONFIG = json.load(open(os.path.join(ENGINE, "config.json")))
os.makedirs(EPISODES, exist_ok=True)
os.makedirs(SITE_EP, exist_ok=True)

# Spoken running order of sections.
SECTION_ORDER = [
    "Director & Brand Watch", "Top End (NT local)", "Rec Fishing & the Bodies",
    "International & Destinations", "Fly Fishing", "Social & Catches",
    "Lighter Side", "Conservation",
    # legacy categories still read if present
    "NT & Lifestyle", "Aus Rec Fishing", "International & Biz", "Advocacy",
]


def episode_title(data):
    if data.get("title"):
        return data["title"]
    return f"{CONFIG['podcast_title']} — {data.get('edition', data['date'])}"


def compose_script(data):
    parts = []
    parts.append(episode_title(data) + ".")
    parts.append(data.get("intro", f"Good morning Watty. Here's {CONFIG['podcast_title']}."))
    # up-front rundown of everything in the dashboard
    heads = [s["headline"] for s in data["stories"]]
    if heads:
        parts.append("Here's what's in today's wrap. " + "; ".join(heads) + ".")
    # group by section, in running order
    by_cat = {}
    for s in data["stories"]:
        by_cat.setdefault(s.get("category", "Other"), []).append(s)
    seen = set()
    for cat in SECTION_ORDER + list(by_cat.keys()):
        if cat in seen or cat not in by_cat:
            continue
        seen.add(cat)
        parts.append(f"{cat}.")
        for s in by_cat[cat]:
            parts.append(s.get("spoken") or s.get("summary", ""))
    parts.append(data.get("outro", "That's your wrap. Have a cracker of a day. Tight lines."))
    return apply_pronunciation("\n\n".join(p.strip() for p in parts if p.strip()))


def ffprobe_duration(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
        return round(float(out.strip()), 1)
    except Exception:
        return None


def main():
    data = json.load(open(sys.argv[1]))
    date = data["date"]

    # PUBLIC/PRIVATE SPLIT. The full data file (all sections, incl. Director &
    # Brand Watch) stays on GW's disk and feeds the private cockpit. Everything
    # that becomes public — the spoken MP3, the podcast feed and the hosted site
    # (both built downstream from this meta) — is built from the PUBLIC subset,
    # so family/brand items are never read aloud or served on the open web.
    exclude = set(CONFIG.get("public_exclude_categories", []))
    pub = dict(data)
    pub["stories"] = [s for s in data.get("stories", []) if s.get("category") not in exclude]

    script = compose_script(pub)
    open(os.path.join(EPISODES, f"{date}.script.txt"), "w").write(script)

    mp3 = os.path.join(EPISODES, f"{date}.mp3")
    used = tts_provider.synth(script, mp3, CONFIG)
    shutil.copy2(mp3, os.path.join(SITE_EP, f"{date}.mp3"))

    meta = {
        "date": date,
        "edition": data.get("edition", date),
        "title": episode_title(data),
        "duration": ffprobe_duration(mp3),
        "filesize": os.path.getsize(mp3),
        "mp3": f"episodes/{date}.mp3",
        "voice": used,
        "intro": data.get("intro", ""),
        "stories": pub["stories"],
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    json.dump(meta, open(os.path.join(EPISODES, f"{date}.json"), "w"), indent=2)
    print(f"Built {mp3}  voice={used}  duration={meta['duration']}s  size={meta['filesize']}B")


if __name__ == "__main__":
    main()
