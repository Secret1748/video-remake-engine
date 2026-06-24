---
name: video-variants
description: Spin ONE video into N variants that look the same to a person but that an Instagram-class duplicate detector no longer links to the source — then recommend which to post first and a drip schedule for the rest. Use whenever the user wants to "repost a video fresh", "make N different versions of a video", "beat duplicate/dedupe detection on my own content", "freshen a clip for reposting", or pick the best of several auto-generated cuts. Drives the repo's spin.py (ffmpeg, measure-driven) + verify.py (pHash). For the user's OWN content on their OWN channels only.
---

# video-variants

Drive `spin.py` to produce N metadata-stripped, perceptually-distinct-but-human-identical
variants of one video, then help the user choose and schedule them.

## When to use
- "Turn this video into 10 versions I can post over time."
- "Make my product video look fresh each repost without it being flagged as a duplicate."
- "Give me variants and tell me which is best for the first post."

## What the user must know first (state it plainly, don't oversell)
- Platforms dedupe on the **decoded perceptual fingerprint** (frame pHash + audio), not
  file metadata. The levers that actually move a robust frame-hash while staying "the same
  video" are **horizontal mirror** (invisible, but flips on-screen text/logos) and
  **off-center reframe/zoom** (mildly visible). Color/grain/speed beat the metadata,
  exact-hash, audio and temporal matchers — not the frame-hash. `spin.py` measures this and
  reports a PASS/FAIL per variant; trust the number, not vibes.
- This is for the user's **own** content. Don't help launder others' content, impersonate,
  or spam. Reposting your own clip fresh = fine; making stolen content "original" = no.

## Workflow

1. **Locate the input** and confirm it exists. Ask the one decision that matters:
   **does this clip have on-screen text, captions, or a logo burned into the frame?**
   - No → run with `--allow-mirror` (strongest, invisible).
   - Yes / unsure → run **without** it (text-safe; relies on reframe/zoom).

2. **Run the spinner** from the repo root:
   ```bash
   python3 spin.py "<input>" --out ./out                 # text-safe
   python3 spin.py "<input>" --out ./out --allow-mirror   # text-free clip
   ```
   It renders in parallel and prints a per-variant PASS/near line.

3. **Read `out/manifest.json`** (and `out/REVIEW.md`). For each variant note: `name`,
   `look`, `lever`, `phash_distance`, `passes_detection`, `file`, `thumb`.

4. **Eyeball the thumbnails.** Optionally build a contact sheet to compare at a glance:
   ```bash
   cd out/thumbs && ffmpeg -y -loglevel error -pattern_type glob -i '*.jpg' \
     -vf "scale=384:-1,tile=5x2:padding=6:color=white" ../montage.jpg
   ```
   View `out/montage.jpg`. Confirm each still reads as the same video (watch for an
   accidental mirror on a clip that *did* have text — flag it if so).

5. **Recommend a pick + a drip plan.** Write a short summary:
   - **Post first:** the `PASS` variant whose look is strongest for the platform/brand
     (usually high-distance + flattering grade; call out *why*).
   - **Drip the rest:** propose a cadence (e.g., one every 2–4 weeks), ordered so
     consecutive posts differ the most (alternate mirrored/non-mirrored, warm/cool) to
     maximize "freshness" between postings. Give concrete dates if the user wants.
   - **Flag** any `⚠️ still close` variant: suggest `--allow-mirror` (if text-free) or
     raising that profile's `base_zoom_pct`.

6. If the user wants different looks, **edit `profiles.json`** (see README "Add or tune a
   profile") and re-run — don't hand-write ffmpeg commands; the engine handles escalation
   and measurement.

## Optional: agentic art-direction
If asked to go further, propose per-profile tweaks (a brand-matched grade, a specific
reframe) by editing `profiles.json`, re-run, and re-read the manifest to confirm each still
PASSes. Always re-verify after editing — a stronger grade won't change the distance, but a
changed `reframe`/`base_zoom_pct` will.

## Guardrails
- Never claim a variant is "undetectable" — say it "clears an open-source frame-hash at the
  current threshold." Encourage also varying caption / cover frame / audio.
- Respect `--allow-mirror` as a per-clip safety: if unsure whether a clip has text, leave it
  off.
- The tool only edits its own `out/` and reads the input; it does not upload anywhere.
