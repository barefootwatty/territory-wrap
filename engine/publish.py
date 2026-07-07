#!/usr/bin/env python3
"""Cloud publish step for The Territory Wrap — runs in the GitHub Action, NOT the
walled daily sandbox.

The daily builder (sandbox) can't reach ElevenLabs, so it pushes the day's spoken
SCRIPT (episodes/<date>.script.txt) + meta + the site to GitHub. This script then
runs on GitHub's runners (open internet + the ELEVENLABS_API_KEY secret) and:

  1. Finds the newest episode script that has no rendered MP3 yet.
  2. Renders it in Hannah's voice via ElevenLabs (espeak-ng CLI fallback).
  3. Probes the real duration + filesize and patches episodes/<date>.json.
  4. Rebuilds feed.xml + data.json + index.html so the podcast + site carry the
     correct Hannah audio, duration and size.

The Action then commits the regenerated text files + the MP3.

FUTURE (Supabase, pre-wired): to stop MP3s accumulating in git, set
config media_base_url to a public Supabase bucket and uncomment the upload block
below; feed/site already read config.base_url for media, so only that + the
upload call change.
"""
import os, sys, json, glob, subprocess, datetime

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
EPISODES = os.path.join(ROOT, "episodes")
SITE_EP = os.path.join(ROOT, "site", "episodes")
CONFIG = json.load(open(os.path.join(ENGINE, "config.json")))
sys.path.insert(0, ENGINE)
import tts_provider  # noqa: E402

os.makedirs(SITE_EP, exist_ok=True)


def ffprobe_duration(path):
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
        return round(float(out.strip()), 1)
    except Exception:
        return None


def newest_pending():
    """Newest date whose script exists but whose site MP3 is missing."""
    dates = sorted(
        os.path.basename(p)[:-len(".script.txt")]
        for p in glob.glob(os.path.join(EPISODES, "*.script.txt"))
    )
    force = os.environ.get("RENDER_DATE")  # manual override for a re-run
    if force:
        return force if force in dates else None
    for d in reversed(dates):
        if not os.path.exists(os.path.join(SITE_EP, f"{d}.mp3")):
            return d
    return dates[-1] if dates else None  # nothing pending -> re-render newest


def main():
    date = newest_pending()
    if not date:
        print("publish: nothing to render")
        return
    script = open(os.path.join(EPISODES, f"{date}.script.txt")).read()
    mp3 = os.path.join(EPISODES, f"{date}.mp3")

    used = tts_provider.synth(script, mp3, CONFIG)
    print(f"publish: rendered {date} voice={used}")

    # FUTURE Supabase upload (pre-wired — uncomment + set env to enable):
    # import urllib.request
    # sb_url = os.environ["SUPABASE_URL"]; sb_key = os.environ["SUPABASE_SERVICE_KEY"]
    # bucket = CONFIG.get("media_bucket", "wrap-audio")
    # dest = f"{sb_url}/storage/v1/object/{bucket}/{date}.mp3"
    # req = urllib.request.Request(dest, data=open(mp3,'rb').read(), method="POST",
    #     headers={"Authorization": f"Bearer {sb_key}", "Content-Type": "audio/mpeg",
    #              "x-upsert": "true"})
    # urllib.request.urlopen(req, timeout=120)

    import shutil
    shutil.copy2(mp3, os.path.join(SITE_EP, f"{date}.mp3"))

    # Patch the episode meta with the real numbers, then rebuild feed + site.
    meta_path = os.path.join(EPISODES, f"{date}.json")
    meta = json.load(open(meta_path))
    meta["duration"] = ffprobe_duration(mp3)
    meta["filesize"] = os.path.getsize(mp3)
    meta["voice"] = used
    meta["built_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    json.dump(meta, open(meta_path, "w"), indent=2)

    subprocess.run([sys.executable, os.path.join(ENGINE, "build_feed.py")], check=True)
    subprocess.run([sys.executable, os.path.join(ENGINE, "build_site.py")], check=True)
    print(f"publish: feed + site rebuilt for {date} "
          f"({meta['voice']}, {meta['duration']}s, {meta['filesize']}B)")


if __name__ == "__main__":
    main()
