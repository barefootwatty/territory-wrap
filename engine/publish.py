#!/usr/bin/env python3
"""Cloud publish step for the Barefoot Daily News Bulletin — runs in the GitHub
Action, NOT the walled daily sandbox.

The daily builder (sandbox) can't reach ElevenLabs, so it pushes the day's spoken
SCRIPT (episodes/<date>.script.txt) + meta + the site to GitHub. This script then
runs on GitHub's runners (open internet + the ELEVENLABS_API_KEY secret) and:

  1. Finds the newest episode script that has no rendered MP3 yet.
  2. Renders it in whichever voice is configured in engine/config.json (voice
     block) via ElevenLabs (espeak-ng CLI fallback). To try a different voice,
     edit config.json's voice_name/voice_id and push — nothing in this script
     needs to change.
  3. Probes the real duration + filesize and patches episodes/<date>.json.
  4. Rebuilds feed.xml + data.json + index.html so the podcast + site carry the
     correct audio, duration and size.

The Action then commits the regenerated text files + the MP3.

FUTURE (Supabase, pre-wired): to stop MP3s accumulating in git, set
config media_base_url to a public Supabase bucket and uncomment the upload block
below; feed/site already read config.base_url for media, so only that + the
upload call change.
"""
import os, sys, json, glob, shutil, subprocess, datetime, urllib.request

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
EPISODES = os.path.join(ROOT, "episodes")
SITE = os.path.join(ROOT, "site")
SITE_EP = os.path.join(SITE, "episodes")
IMG_DIR = os.path.join(SITE, "img")
CONFIG = json.load(open(os.path.join(ENGINE, "config.json")))
sys.path.insert(0, ENGINE)
import tts_provider  # noqa: E402

os.makedirs(SITE_EP, exist_ok=True)

_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/pjpeg": ".jpg",
        "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
        "image/avif": ".avif"}


def _download_image(url, dest_noext):
    """Fetch a remote image to dest_noext+<ext>. Returns the site-relative path,
    or '' if it isn't a real image. News CDNs often block hotlinking from another
    origin, so we copy the photo here and serve it same-origin from site/img/."""
    req = urllib.request.Request(url, headers={
        "User-Agent": f"Mozilla/5.0 (compatible; BarefootDailyNews/1.0; +{CONFIG.get('base_url', '')})",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": url,
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        data = r.read()
    if not ctype.startswith("image/") or len(data) < 3000:
        return ""  # HTML error page, tracking pixel, or tiny sprite -> reject
    dest = dest_noext + _EXT.get(ctype, ".jpg")
    with open(dest, "wb") as f:
        f.write(data)
    return os.path.relpath(dest, SITE).replace(os.sep, "/")


def localize_images(meta, date):
    """Rewrite each story's remote image to a downloaded same-origin copy so the
    dashboard + feed always render it. Anything that fails to fetch -> '' (the
    card shows its styled fallback tile, which beats a broken image)."""
    day_dir = os.path.join(IMG_DIR, date)
    os.makedirs(day_dir, exist_ok=True)
    for s in meta.get("stories", []):
        img = s.get("image", "")
        if img.startswith("img/"):
            continue  # already localised
        if not img.startswith(("http://", "https://")):
            s["image"] = ""
            continue
        try:
            local = _download_image(img, os.path.join(day_dir, str(s.get("id", "x"))))
        except Exception as ex:
            print(f"publish: image id={s.get('id')} failed ({ex}); dropping")
            local = ""
        s["image"] = local
        print(f"publish: image id={s.get('id')} -> {local or 'dropped (fallback tile)'}")


def prune_images(keep):
    """Best-effort: drop image folders older than the retention window."""
    if not os.path.isdir(IMG_DIR):
        return
    cutoff = datetime.date.today() - datetime.timedelta(days=int(keep))
    for name in os.listdir(IMG_DIR):
        try:
            d = datetime.datetime.strptime(name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            shutil.rmtree(os.path.join(IMG_DIR, name), ignore_errors=True)


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

    shutil.copy2(mp3, os.path.join(SITE_EP, f"{date}.mp3"))

    # Patch the episode meta with the real numbers, self-host its images, then
    # rebuild feed + site.
    meta_path = os.path.join(EPISODES, f"{date}.json")
    meta = json.load(open(meta_path))
    meta["duration"] = ffprobe_duration(mp3)
    meta["filesize"] = os.path.getsize(mp3)
    meta["voice"] = used
    meta["built_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    localize_images(meta, date)
    prune_images(CONFIG.get("retention_days", 30))
    json.dump(meta, open(meta_path, "w"), indent=2)

    subprocess.run([sys.executable, os.path.join(ENGINE, "build_feed.py")], check=True)
    subprocess.run([sys.executable, os.path.join(ENGINE, "build_site.py")], check=True)
    print(f"publish: feed + site rebuilt for {date} "
          f"({meta['voice']}, {meta['duration']}s, {meta['filesize']}B)")


if __name__ == "__main__":
    main()
