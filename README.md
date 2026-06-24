# video-remake-engine

[![ci](https://github.com/Secret1748/video-remake-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Secret1748/video-remake-engine/actions/workflows/ci.yml)

![the drag-and-drop UI](docs/ui.png)

Turn **one** video into **N** variants that read as the *same* video to a person, but where
**every variant is unlinkable to the source _and_ to each other** on the two layers an
Instagram-class system dedupes on — the **video frame-fingerprint** (pHash/PDQ) and the
**audio fingerprint** (Chromaprint/AcoustID). So you can post the best one now and drip the
rest over time without any of them being flagged as a copy.

It is **measure-driven** and **quality-first**: it doesn't apply cosmetic tweaks and hope.
It searches per clip for a mutually-distinct *set* of transforms, applies them, then runs a
**GREEN gate** that re-measures the finished files on both layers and tells you, per variant,
whether it's truly distinct — with numbers, not vibes.

> **Use & ethics — read this.** For **your own content on your own channels** (reposting
> your video fresh, A/B-picking the best cut). **Not** for laundering someone else's content,
> impersonation, spam, or coordinated inauthentic behavior. Platforms penalize
> *unoriginal/reposted* content on purpose; this makes a genuinely-yours clip read as a fresh
> original — it does not make stolen content original. Follow each platform's terms.

---

## "GREEN" — the bar

A run is **🟢 GREEN** when all N variants, measured on the *encoded* output files, are:

- **video** pHash distance ≥ `12` vs the source **and** vs every other variant, **and**
- **audio** Chromaprint distance ≥ `0.22` vs the source **and** vs every other variant.

(`0.22` is comfortably in "different recording" territory — same audio lightly processed
scores <0.15, genuinely different content scores 0.40+.) If a variant falls short on either
layer, the report flags it ⚠️ and tells you why. Exit code is `0` only when fully GREEN.

---

## The honest premise (why most "video spinner" tools are theater)

Instagram/Meta do **not** dedupe by file metadata — every upload is re-encoded, so metadata
is stripped on their end regardless. Detection runs on the **decoded perceptual
fingerprint**: a robust **frame perceptual-hash** (pHash/PDQ) plus **audio fingerprinting**.
Meta matches duplicates using *"video fingerprinting, visual similarity, and audio matching"*
and demotes reposts (originals get ~40–60% more reach; 10+ reposts in 30 days can drop an
account from recommendations).

Robust fingerprinting is **designed to survive** cosmetic edits. Measured with this repo's
own `verify.py`:

| Layer | What survives (useless) | What actually moves it (while staying "the same video") |
|---|---|---|
| **Video** pHash | colour grade (~2), light rotation (~4), centered zoom (~3) | **horizontal mirror** (~27, lossless, but flips on-screen text), **off-center reframe/zoom** (~13–17) |
| **Audio** Chromaprint | volume, EQ, light reverb (~0.0–0.08) | **tempo** (+3% ≈ 0.33), **pitch** (±0.6–1.0 st), combined |

So the engine uses the levers that work — geometry for video, pitch+tempo for audio — and
proves it. Colour/grain don't beat fingerprints; they're just there so the variants look
different *to you* when you pick.

---

## Quality: verified, not promised

"Don't degrade the video" is a hard constraint, measured with SSIM/PSNR:

- **Mirror is lossless geometry** (100% detail) and the strongest video lever — used first
  when `--allow-mirror` is set (clips with no on-screen text/logo).
- **Re-encode is visually lossless.** Default **CRF 16** ≈ **PSNR 40 dB / SSIM 0.99** vs a
  mathematically-lossless render of the same transform. `--lossless` for x264 `qp 0`.
- **No degradation is added** — no grain, no softening; **lanczos** scaling; subtle audio
  (≤0.7 semitone pitch).
- **The one real cost** is reframing a same-resolution master without mirror (needed to make
  video pairwise-distinct): ~77–91% detail retained, **measured and reported** per variant.
  Keep it 100% by supplying a higher-res master and passing `--target-height`, or by using
  `--allow-mirror` on text-free clips.

---

## How it works

For a clip and N colour "looks" (`profiles.json`), the engine:

1. **selects a mutually-distinct set of framings** — measures a grid of reframe
   directions × zooms (+ a lossless mirror when `--allow-mirror`) against the source and
   pairwise, greedily picking N that are all distinct (detail-first, so it prefers the
   shallowest zoom / the lossless mirror);
2. **selects a mutually-distinct set of audio settings** — same idea over a grid of subtle
   pitch × tempo (skipped if the clip is silent or `fpcalc` isn't installed);
3. **renders** each variant = a distinct framing + distinct audio + a distinct colour look,
   strips all metadata, re-encodes visually-lossless, keeping A/V in sync;
4. **green-gates** — re-measures the finished files' full pairwise + vs-source matrix on
   both layers and reports 🟢/🔴 per variant and overall.

The perceptual scoring lives in [`verify.py`](verify.py) — a pure-Python DCT pHash plus a
Chromaprint wrapper — so the tool proves its claims with measured numbers.

---

## Requirements

- **ffmpeg** + **ffprobe** on `PATH` (`brew install ffmpeg`).
- **Python 3.10+** (standard library only).
- **Chromaprint** for the audio layer (`brew install chromaprint` → gives `fpcalc`). Without
  it, the engine still runs but gates **video only** and says so.

## Install & quick start

```bash
git clone https://github.com/<you>/video-remake-engine
cd video-remake-engine
brew install ffmpeg chromaprint           # chromaprint optional but recommended
python3 spin.py examples/real_sample.mp4 --out ./out --allow-mirror
open out/REVIEW.md
```

## Usage

```bash
# text-safe (default): no mirror — safe for clips WITH on-screen text/logo
python3 spin.py product.mp4 --out ./out

# clip has NO on-screen text/logo -> enable the lossless mirror lever
python3 spin.py broll.mp4 --out ./out --allow-mirror

# quality / sizing knobs
python3 spin.py in.mp4 --out ./out \
  --crf 14 \             # higher quality (default 16, visually lossless)
  --lossless \           # mathematically lossless (x264 qp 0; big files)
  --audio-kbps 320 \     # AAC bitrate (default 256)
  --target-height 1080   # if < source height, reframes consume master headroom losslessly

# other
python3 spin.py in.mp4 --out ./out --count 6 --jobs 8 --no-thumbs
```

`--allow-mirror` is a **per-video** decision — only set it when the clip has no burned-in
text/logo (a mirror reads backwards).

**Exit codes:** `0` = fully GREEN; `1` = a variant fell short on a layer (see `REVIEW.md`) or
a render failed.

## Drag-and-drop web UI

Prefer not to touch a terminal? Run the bundled local UI — same engine, no extra installs:

```bash
python3 serve.py            # -> http://127.0.0.1:8000   (--port to change)
```

Drag a video on, tick *"no on-screen text"* to enable the mirror lever, and it renders the 10
variants with inline players, download buttons, and the per-variant GREEN badges. Everything
runs locally — nothing is uploaded anywhere.

## Outputs (in `--out`)

```
<name>__v01_<look>.mp4 … <name>__v10_<look>.mp4   the variants
thumbs/…                                          one mid-frame each
manifest.json   machine-readable: framing, audio, per-layer distances, GREEN, sha256, cmd
REVIEW.md       human sheet: GREEN banner + a row per variant (Vsrc/Vmin/Asrc/Amin/detail)
```

`REVIEW.md` columns: **Vsrc/Vmin** = video distance to source / nearest variant; **Asrc/Amin**
= audio distance to source / nearest variant; **Detail** = % resolution retained; **Status** =
✅ distinct or ⚠️ too close.

## Profiles = colour looks

[`profiles.json`](profiles.json) is now just **10 colour looks** (Warm, Cool, Punchy,
Cinematic, Airy, Moody, Vivid, Filmic, Neutral, Soft) — for *your eye*, so the variants are
easy to tell apart. The distinctness levers (framing, mirror, audio) are chosen **per clip**
by the engine to guarantee GREEN. Edit colours freely; add/remove entries to change how many
variants you get. (`sharpen` is enhancement-only; negatives are ignored — no softening.)

## Verify anything by hand

```bash
python3 verify.py source.mp4 variant.mp4
# video pHash mean-min=19.7 … -> DISTINCT to a frame matcher ✅ (threshold 12)
# audio chromaprint differing-bits fraction=0.45  (sufficiently different)
```

## Limitations (so nobody is surprised)

- **No tool can *guarantee* permanent evasion** of a system that keeps improving and also
  weighs behavioral signals (same caption, same cover frame, posting cadence). GREEN means
  "clears two open-source, conservative fingerprint proxies at our thresholds today." Still
  vary captions/cover frames and space out your posts.
- **Text/logo clips** can't use mirror, so video distinctness comes from reframing — which
  costs some detail (measured/reported). Supply a higher-res master (`--target-height`) to
  keep 100%.
- The fingerprints here (DCT pHash, Chromaprint) are faithful, deliberately-conservative
  *proxies* for platform matchers (PDQ/TMK, Content-ID-style audio) — passing them is strong
  evidence, not a certificate.
- The bundled `examples/real_sample.mp4` is **silent** (it demonstrates the video layer); the
  audio layer engages automatically on real clips that have audio.

## Repo layout

```
spin.py        the engine (CLI)
serve.py       a local drag-and-drop web UI (stdlib http.server)
verify.py      pure-Python pHash + Chromaprint perceptual scorer (also a CLI)
profiles.json  the 10 colour looks
examples/      a demo clip (silent)
.github/workflows/ci.yml         CI smoke test (asserts the demo renders GREEN)
.claude/skills/video-variants/   a Claude Code skill that drives it + recommends a pick/drip plan
```

## License

MIT — see [LICENSE](LICENSE).
