#!/usr/bin/env python3
"""Pluggable TTS provider for The Territory Wrap.

synth(text, out_mp3, config) picks a voice provider from config["voice"]:
  - "elevenlabs": premium voice (needs ELEVENLABS_API_KEY in env + open internet).
                  Runs at the hosting/publish layer, not the walled daily sandbox.
  - "espeak":     offline fallback (robotic) so a run never fails with no audio.

Designed as a provider seam so the same engine can serve multiple profiles/voices
if this ever becomes a multi-user product.
"""
import os, json, wave, subprocess, tempfile, ctypes as C, urllib.request

ENGINE = os.path.dirname(os.path.abspath(__file__))
TTS_DIR = os.path.join(ENGINE, "tts")


def _elevenlabs(text, out_mp3, v):
    key = os.environ.get(v.get("api_key_env", "ELEVENLABS_API_KEY"), "")
    if not key:
        raise RuntimeError("no ElevenLabs API key in env")
    voice_id = v["voice_id"]
    url = (f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
           f"?output_format=mp3_44100_128")
    body = json.dumps({
        "text": text,
        "model_id": v.get("model_id", "eleven_multilingual_v2"),
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "xi-api-key": key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        audio = r.read()
    with open(out_mp3, "wb") as f:
        f.write(audio)
    return "elevenlabs"


def _espeak_cli(text, out_mp3, v):
    """Fallback-of-the-fallback: use a system-installed espeak-ng CLI (e.g. the
    GitHub Action runner does `apt-get install espeak-ng`). Bundled lib not needed."""
    wav = os.path.join(tempfile.gettempdir(), "tw-espeak-cli.wav")
    subprocess.run(["espeak-ng",
                    "-v", v.get("espeak_voice", "en-gb"),
                    "-s", str(int(v.get("espeak_rate_wpm", 148))),
                    "-p", str(int(v.get("espeak_pitch", 45))),
                    "-w", wav, text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                    "-codec:a", "libmp3lame", "-b:a", "96k", out_mp3], check=True)
    try:
        os.remove(wav)
    except OSError:
        pass
    return "espeak"


def _espeak(text, out_mp3, v):
    lib = None
    for name in ("libespeak-ng.so", "libespeak-ng.so.1"):
        p = os.path.join(TTS_DIR, name)
        if os.path.exists(p):
            lib = p
            break
    if not lib:
        # No bundled voice data (e.g. the cloud publish runner) — try the CLI.
        return _espeak_cli(text, out_mp3, v)
    e = C.CDLL(lib)
    rate = e.espeak_Initialize(1, 0, TTS_DIR.encode(), 0)  # AUDIO_OUTPUT_RETRIEVAL
    if rate < 0:
        raise RuntimeError("espeak init failed")
    buf = bytearray()
    CB = C.CFUNCTYPE(C.c_int, C.POINTER(C.c_short), C.c_int, C.c_void_p)

    def _cb(w, n, ev):
        if w and n > 0:
            buf.extend(C.cast(w, C.POINTER(C.c_char))[: n * 2])
        return 0

    cb = CB(_cb)
    e.espeak_SetSynthCallback(cb)
    e.espeak_SetVoiceByName(v.get("espeak_voice", "en-gb").encode())
    e.espeak_SetParameter(1, int(v.get("espeak_rate_wpm", 148)), 0)
    e.espeak_SetParameter(2, 100, 0)
    e.espeak_SetParameter(3, int(v.get("espeak_pitch", 45)), 0)
    b = text.encode("utf-8")
    e.espeak_Synth(b, len(b) + 1, 0, 0, 0, 1, None, None)  # espeakCHARS_UTF8
    e.espeak_Synchronize()
    wav = os.path.join(tempfile.gettempdir(), "tw-espeak.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(bytes(buf))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                    "-codec:a", "libmp3lame", "-b:a", "96k", out_mp3], check=True)
    try:
        os.remove(wav)
    except OSError:
        pass
    return "espeak"


def synth(text, out_mp3, config):
    """Generate speech to out_mp3. Returns the provider actually used."""
    v = config.get("voice", {}) if isinstance(config.get("voice"), dict) else {}
    provider = v.get("provider", "espeak")
    if provider == "elevenlabs":
        try:
            return _elevenlabs(text, out_mp3, v)
        except Exception as ex:
            print(f"[tts] ElevenLabs unavailable ({ex}); falling back to {v.get('fallback','espeak')}")
    return _espeak(text, out_mp3, v)
