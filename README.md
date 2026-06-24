# video-remake-engine

Turn **one** video into **N** variants that read as the *same* video to a person, but
that an Instagram-class duplicate detector no longer links to the source — so you can post
the best one and drip the rest over time without each repost being flagged as a copy of
the last.

It is **measure-driven** and **quality-first**: it doesn't apply cosmetic tweaks and hope.
It measures each variant against a real perceptual hash, applies only the *minimum* change
that actually moves the needle, adds **no grain and no softening**, and re-encodes
**visually-losslessly** — reporting a PASS/FAIL *and* the detail retained for every variant.

> **Use & ethics — read this.** This is for **your own content on your own channels**
> (reposting your product video fresh, A/B-picking the best cut). It is **not** for
> laundering someone else's content, impersonation, spam at scale, or coordinated
> inauthentic behavior. Platforms penalize *unoriginal/reposted* content on purpose; this
> tool makes a genuinely-yours clip read as a fresh original — it does not make stolen
> content original. You are responsible for following each platform's terms.

---

## The honest premise (why most "video spinner" tools are theater)

Instagram/Meta do **not** dedupe by file metadata — every upload is re-encoded, so metadata
is stripped on their end regardless. Detection runs on the **decoded perceptual
fingerprint**: a robust **frame perceptual-hash** (the pHash/PDQ family) plus **audio
fingerprinting**. Meta states it matches duplicates with *"video fingerprinting, visual
similarity, and audio matching"* and demotes reposts (originals get ~40–60% more reach;
10+ reposts in 30 days can drop an account from recommendations).

Robust perceptual hashing is **designed to survive** cosmetic edits. Measured on a real
clip with this repo's own [`verify.py`](verify.py) (64-bit pHash, mean min-Hamming distance
to the nearest source frame; higher = harder to link; ≥12 ≈ "won't link"):

| Transform | pHash distance | Visible to a person? | Useful vs frame-hash? |
|---|---|---|---|
| Heavy color / contrast / grade | ~2 | yes | ❌ none |
| Rotate 2° | ~4 | no | ❌ weak |
| **Centered** zoom (any amount) | ~3–4 | no | ❌ none (composition unchanged) |
| **Off-center** reframe/zoom ~20% | ~13–17 | slightly (tighter frame) | ✅ works |
| **Horizontal mirror** | ~27 | no — *unless on-screen text/logo* | ✅ strongest **& lossless** |

So the only levers that move a robust frame-hash **while staying "the same video"** are
**mirror** (huge, invisible, and *geometrically lossless* — but it flips on-screen
text/logos) and **substantial off-center reframing** (works, mildly visible as tighter
framing). Color, grain, light rotation, and speed do *not* beat the frame-hash — but they
still matter, because they beat the **metadata**, **exact-hash**, **audio**, and
**temporal** matchers. The engine uses all of them; it just doesn't pretend a color tweak
fools a frame matcher.

---

## Quality: verified, not promised

"Don't degrade the video" is a hard constraint here, measured with SSIM/PSNR:

- **Mirror variants are lossless geometry.** When you pass `--allow-mirror`, the strongest
  lever is also the cleanest: a horizontal flip retains **100% of the resolution** and
  clears detection (~27 distance) with **zero** zoom.
- **Re-encode is visually lossless.** Default **CRF 16** measured **PSNR 40.3 dB / SSIM
  0.987** against a mathematically-lossless render of the *identical* transform — on a
  mandelbrot, the worst case for an encoder; real footage scores higher. Want zero
  mathematical loss? `--lossless` (x264 `qp 0`).
- **No degradation is *added*.** Profiles add **no grain** and **never soften** (negative
  sharpen is clamped to 0). Any scaling uses **lanczos** (the sharpest common filter).
- **The one real cost is reframing a same-resolution master.** Beating the frame-hash
  *without* mirror requires an off-center crop, which upscales ~15% on a same-res source
  (≈83–91% detail retained, mildly soft). The engine **measures and reports** this per
  variant, and you can make it **100%** by either using `--allow-mirror` (text-free clips)
  or supplying a higher-resolution master with `--target-height` (then the reframe is a
  downscale — free).

Every run prints, and `manifest.json`/`REVIEW.md` record, a **detail-retained %** per
variant so nothing is hidden.

---

## How it works

For each profile the engine:

1. **applies a distinct look** — color personality (warm/cool/moody/airy…) + a reframe
   direction — so the variants look different *to you* for picking;
2. **escalates the minimum lever** until a real pHash can no longer match the variant to
   the source, then **stops**:
   - with `--allow-mirror` (you've confirmed the clip has **no on-screen text/logo**),
     mirror-preferring profiles flip — invisible, lossless, ~27 distance;
   - otherwise it ramps **off-center crop/reframe** (crop-then-lanczos, never upscaling the
     whole frame) just past the detection line;
3. **strips all metadata** (`-map_metadata -1`, chapters, bitexact) and **re-encodes**
   visually-losslessly (H.264 / yuv420p / +faststart — universally accepted);
4. **adjusts time + audio** — small speed change (video+audio kept in sync), optional
   micro-trim and pitch — to also defeat the temporal and audio matchers;
5. **re-measures the encoded file** and records pHash distance + PASS/FAIL + detail %.

---

## Requirements

- **ffmpeg** and **ffprobe** on `PATH` (`brew install ffmpeg` / `apt install ffmpeg`).
- **Python 3.10+** (standard library only — no `pip install`).
- *Optional:* **Chromaprint** (`brew install chromaprint`, gives `fpcalc`) to also score an
  **audio**-fingerprint distance; without it, audio is reported as "not measured".

## Install & quick start

```bash
git clone https://github.com/<you>/video-remake-engine
cd video-remake-engine
python3 spin.py examples/real_sample.mp4 --out ./out --allow-mirror
open out/REVIEW.md   # the table you skim to pick the best
```

## Usage

```bash
# text-safe (default): no mirror — safe for clips WITH on-screen text/logo
python3 spin.py product.mp4 --out ./out

# clip has NO on-screen text/logo -> enable the invisible, lossless, strongest lever
python3 spin.py broll.mp4 --out ./out --allow-mirror

# quality knobs
python3 spin.py in.mp4 --out ./out \
  --crf 14 \            # even higher quality (default 16, visually lossless)
  --lossless \          # mathematically lossless (x264 qp 0; large files)
  --audio-kbps 320 \    # AAC bitrate (default 256, transparent)
  --target-height 1080  # if < source height, reframes consume master headroom losslessly

# other knobs
python3 spin.py in.mp4 --out ./out \
  --count 6 --jobs 8 --threshold 12 --margin 2 --no-thumbs
```

`--allow-mirror` is a **per-video** decision: only set it when the clip has no burned-in
captions, logos, or readable text (a mirror reads backwards). For mixed libraries, leave it
off by default and flip it on for the clips you know are clean.

### Exit codes
- `0` — all variants rendered, byte-distinct, and cleared detection.
- `1` — rendered, but a variant didn't clear detection (see `REVIEW.md`) or a render failed.

## Outputs (in `--out`)

```
<name>__v01_<profile>.mp4 … <name>__v10_<profile>.mp4   the variants
thumbs/<name>__v0X_<profile>.jpg                        one mid-frame each (eyeball grid)
manifest.json     machine-readable: lever, pHash distance, detail %, sha256, ffmpeg cmd
REVIEW.md         human review sheet: a table you skim to pick the best
```

`REVIEW.md` is the file you open. Per variant it shows the look, the lever used
(`mirror` / `12% (ur)`), the measured pHash distance, a `✅ PASS / ⚠️ still close` badge, and
the detail retained. Pick a `PASS` whose thumbnail you like, post it, drip the rest.

## The 10 built-in profiles

Defined in [`profiles.json`](profiles.json). Five are `prefer_mirror` (used only with
`--allow-mirror`); the other five always reframe. Each has a distinct color personality and
reframe direction so the ten look different from the source **and** each other.

| # | Profile | Look | Mirror? |
|---|---------|------|---------|
| 1 | Warm Up-Right | warm, framed upper-right | no |
| 2 | Cool Left | cooler/crisper, framed left | yes |
| 3 | Punch Up-Right | saturated, high-contrast | no |
| 4 | Cinematic Down-Left | filmic + vignette | yes |
| 5 | Airy Right | bright, lifted | no |
| 6 | Moody Down-Right | dark, dramatic | yes |
| 7 | Vivid Up | vivid, +3% pace | no |
| 8 | Filmic Up-Left | soft, muted, low-contrast | yes |
| 9 | Neutral Down | near-neutral baseline | no |
| 10 | Soft Mirror | relaxed, slightly slower | yes |

### Add or tune a profile

Edit `profiles.json`. Each profile carries a **look** (`hue_deg`, `saturation`,
`brightness`, `contrast`, `gamma`, `warmth`, `sharpen` *(positive only — softening is
ignored)*, `vignette`) and the **levers** (`reframe` ∈
`center/left/right/up/down/ul/ur/dl/dr`, `base_zoom_pct`, `prefer_mirror`, `speed_factor`,
`trim_head_ms`, `trim_tail_ms`, `audio_semitones`, `audio_gain_db`). Notes:

- **`reframe: "center"` cannot beat a frame-hash** (composition is unchanged); the engine
  auto-substitutes an off-center direction when it must clear detection — but prefer a real
  direction for an intentional look.
- The engine *raises* `base_zoom_pct` automatically if a profile won't clear; you rarely
  need to hand-tune it.
- Color is for *your eye* (variety), not for evasion — it won't move the distance.

## Verify anything by hand

```bash
python3 verify.py source.mp4 variant.mp4
# video pHash mean-min=26.7  median-min=28  min=24  (src 30 / var 20 frames)
#   -> DISTINCT to a frame matcher ✅ (threshold 12)
# audio chromaprint differing-bits fraction=0.41  (sufficiently different)   # if fpcalc present
```

## Limitations (so nobody is surprised)

- **No tool can *guarantee* permanent evasion** of a detector that keeps improving, and Meta
  combines frame + audio + behavioral signals (same caption, same audio, posting cadence).
  Treat the PASS badge as "clears an open-source frame-hash at our threshold today," not a
  warranty. Vary your caption, cover frame, and ideally audio too.
- **Text/logo clips** can't use mirror, so their best lever is off-center reframe (~13–17
  distance, 83–91% detail on a same-res master) — real, but a matcher with a lower
  threshold may still associate them. Supply a higher-res master (`--target-height`) to keep
  100% detail.
- The pHash here is a faithful, deliberately-conservative *proxy* for platform matchers
  (PDQ/TMK), not the exact algorithm — so passing it is a strong sign, not a certificate.

## Repo layout

```
spin.py                         the engine (CLI)
verify.py                       pure-Python pHash perceptual scorer (also a CLI)
profiles.json                   the 10 variant recipes (edit freely)
examples/real_sample.mp4        a demo clip
.claude/skills/video-variants/  a Claude Code skill that drives the tool + recommends a pick/drip plan
```

## License

MIT — see [LICENSE](LICENSE).
