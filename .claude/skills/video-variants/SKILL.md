---
name: video-variants
description: Remake ONE video into N variants that look the same to a person but that an Instagram-class duplicate detector no longer links to the source OR to each other — distinct on BOTH the video frame-fingerprint and the audio fingerprint (the "GREEN" gate) — then recommend which to post first and a drip schedule for the rest. Use whenever the user wants to "repost a video fresh", "make N different versions of a video", "beat duplicate/dedupe detection on my own content", "freshen a clip for reposting", or pick the best of several auto-generated cuts. Drives the repo's spin.py (ffmpeg, measure-driven) + verify.py (pHash + Chromaprint). For the user's OWN content on their OWN channels only.
---

# video-variants

Drive `spin.py` to produce N metadata-stripped, perceptually-distinct-but-human-identical
variants of one video, confirm they're **GREEN**, then help the user choose and schedule them.

## When to use
- "Turn this video into 10 versions I can post over time."
- "Make my product video look fresh each repost without it being flagged as a duplicate."
- "Give me variants and tell me which is best for the first post."

## What "GREEN" means (state it plainly, don't oversell)
A run is GREEN when every variant, measured on the encoded files, is distinct **from the
source AND from every other variant** on BOTH layers a platform dedupes on:
- **video** frame perceptual-hash (pHash/PDQ) ≥ 12, and
- **audio** Chromaprint fingerprint ≥ 0.22.
The engine searches per clip for a mutually-distinct set of framings (direction/zoom, plus a
lossless mirror when allowed) and audio settings (subtle pitch+tempo), then GREEN-gates the
result. Trust the printed numbers, not vibes.

Honest caveats to relay: GREEN clears two conservative open-source fingerprint proxies — not
a guarantee against an evolving system that also weighs behavioral signals (same caption,
cover frame, posting cadence). Advise varying caption/cover and spacing posts out. And this
is for the user's OWN content — not laundering others' clips.

## Workflow

1. **Locate the input** and confirm it exists. Ask the one decision that matters:
   **does this clip have on-screen text, captions, or a logo burned into the frame?**
   - No → run with `--allow-mirror` (mirror is invisible, lossless, strongest).
   - Yes / unsure → run **without** it (text-safe; reframe + audio do the work).
   Mention `brew install chromaprint` enables the audio layer (else it gates video only).

2. **Run** from the repo root:
   ```bash
   python3 spin.py "<input>" --out ./out                 # text-safe
   python3 spin.py "<input>" --out ./out --allow-mirror   # text-free clip
   ```
   It prints a 🟢/🔴 line per variant and an overall GREEN/NOT-GREEN verdict (exit 0 = GREEN).

3. **If not GREEN**, read the ⚠️ rows in `out/REVIEW.md`. Typical fixes:
   - reds on audio + clip is silent / no `fpcalc` → install Chromaprint or accept video-only;
   - reds on video for a text clip → that clip's footage is very uniform; deeper reframe is
     needed (it already escalates) — or supply a higher-res master + `--target-height`.

4. **Eyeball the thumbnails** (optionally a contact sheet:
   `cd out/thumbs && ffmpeg -y -loglevel error -pattern_type glob -i '*.jpg' -vf "scale=384:-1,tile=5x2:padding=6:color=white" ../montage.jpg`).
   Confirm each still reads as the same video (watch for an accidental mirror on a clip that
   DID have text).

5. **Recommend a pick + drip plan** from `manifest.json`:
   - **Post first:** the variant whose look is strongest for the platform/brand.
   - **Drip the rest:** propose a cadence (one every 2–4 weeks), ordered so consecutive posts
     differ most (alternate mirrored/non-mirrored, warm/cool) — and remind them to change the
     caption/cover frame each time.

6. To change looks, edit the colour entries in `profiles.json` (framing/audio are engine-
   chosen) and re-run. Don't hand-write ffmpeg — the engine handles selection + the gate.

## Guardrails
- Never claim "undetectable." Say it "clears two conservative fingerprint proxies at the
  current thresholds." Encourage varying caption/cover/cadence too.
- Respect `--allow-mirror` as a per-clip safety: if unsure whether a clip has text, leave off.
- The tool only writes its own `out/` and reads the input; it uploads nothing.
