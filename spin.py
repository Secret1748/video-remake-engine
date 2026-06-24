#!/usr/bin/env python3
"""
video-remake-engine — turn ONE video into N variants that read as the SAME video to a
person but that an Instagram-class duplicate detector no longer links to the source.

Quality is a first-class constraint: the engine adds NO grain, NO softening, scales with
the sharpest filter (lanczos), re-encodes visually-losslessly (CRF 16; `--lossless` for
mathematically lossless), and prefers the one transform that is geometrically LOSSLESS and
also the strongest perceptual lever — a horizontal mirror — whenever you allow it. It
reports the measured perceptual distance AND the detail retained for every variant.

What actually defeats perceptual duplicate detection (measured, see README):
  * Robust frame perceptual-hashing (pHash/PDQ — what Meta uses) SURVIVES color, grain,
    light rotation and speed. Those barely move the hash.
  * The levers that move a robust frame-hash while staying "the same video" are MIRROR
    (huge + lossless, but flips on-screen text/logos) and SUBSTANTIAL OFF-CENTER REFRAME.
  * Color / speed / trim / metadata-strip still matter — they beat the metadata,
    exact-hash, audio and temporal matchers — just not the frame-hash.

Measure-driven: for each variant it applies a distinct LOOK, escalates the MINIMUM lever
needed (mirror first when allowed; else just enough off-center reframe) until a real pHash
can't match it to the source, STOPS, strips all metadata, re-encodes, and re-measures.

Pure stdlib + ffmpeg/ffprobe. Sibling module verify.py does the perceptual scoring.

    python spin.py product.mp4 --out ./out                  # text-safe (no mirror)
    python spin.py broll.mp4   --out ./out --allow-mirror    # clip has no text -> lossless+strongest

For YOUR content on YOUR channels. See README "Use & ethics".
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, fields
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify  # noqa: E402

DEFAULT_PROFILES = HERE / "profiles.json"

# reframe direction -> unit offset of the crop window within the frame
DIRS = {
    "center": (0, 0), "left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
    "ul": (-1, -1), "ur": (1, -1), "dl": (-1, 1), "dr": (1, 1),
}


def _f(x: float) -> str:
    return f"{x:.5f}".rstrip("0").rstrip(".")


def _even(n: float) -> int:
    return max(2, int(round(n)) // 2 * 2)


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        sys.exit(f"error: `{tool}` not found on PATH. Install ffmpeg (e.g. `brew install ffmpeg`).")
    return path


# --------------------------------------------------------------------------- #
@dataclass
class Probe:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool
    sample_rate: int


def probe(input_path: Path) -> Probe:
    out = subprocess.run(
        [_require("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(input_path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if v is None:
        sys.exit("error: input has no video stream.")

    def _fps(s) -> float:
        num, _, den = s.get("avg_frame_rate", "0/0").partition("/")
        try:
            return float(num) / float(den) if float(den) else 30.0
        except (ValueError, ZeroDivisionError):
            return 30.0

    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0.0)
    return Probe(int(v["width"]), int(v["height"]), duration, _fps(v),
                 a is not None, int(a["sample_rate"]) if a and a.get("sample_rate") else 48000)


# --------------------------------------------------------------------------- #
@dataclass
class Profile:
    id: int
    name: str
    look: str = ""
    # look / personality (for your eye; these do NOT meaningfully move a frame-hash)
    hue_deg: float = 0.0
    saturation: float = 1.0
    brightness: float = 0.0
    contrast: float = 1.0
    gamma: float = 1.0
    warmth: float = 0.0
    sharpen: float = 0.0       # enhancement only; negatives (softening) are clamped to 0
    vignette: bool = False
    # geometry (what actually defeats the frame-hash)
    reframe: str = "center"
    base_zoom_pct: float = 6.0
    prefer_mirror: bool = False
    # time + audio (defeat the temporal + audio matchers; keep A/V in sync)
    speed_factor: float = 1.0
    trim_head_ms: int = 0
    trim_tail_ms: int = 0
    audio_semitones: float = 0.0
    audio_gain_db: float = 0.0
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        known = {fld.name for fld in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def region_for(zoom_pct: float, W: int, H: int) -> tuple[int, int]:
    """Source sub-region (a punch-in) for a given zoom. zoom<=0 -> the whole frame."""
    if zoom_pct and zoom_pct > 0:
        z = 1.0 + zoom_pct / 100.0
        return _even(W / z), _even(H / z)
    return W, H


def detail_pct(zoom_pct: float, W: int, out_w: int) -> float:
    """Detail retained: 100 = no upscaling. Cropping a region then scaling it to the
    output only loses detail when the region has fewer pixels than the output."""
    rw, _ = region_for(zoom_pct, W, 100)
    return round(min(100.0, rw / out_w * 100.0), 1)


def build_visual_chain(p: Profile, zoom_pct: float, mirror: bool, reframe: str,
                       W: int, H: int, out_w: int, out_h: int) -> str:
    """Crop-region (punch-in, never upscales the whole frame) -> mirror -> scale to output
    with lanczos -> color personality. No setpts. This is exactly what we MEASURE."""
    parts: list[str] = []
    rw, rh = region_for(zoom_pct, W, H)
    if (rw, rh) != (W, H):
        mx, my = W - rw, H - rh
        dx, dy = DIRS.get(reframe, (0, 0))
        x = min(max(int(round(mx / 2 * (1 + dx * 0.8))), 0), mx)
        y = min(max(int(round(my / 2 * (1 + dy * 0.8))), 0), my)
        parts.append(f"crop={rw}:{rh}:{x}:{y}")
    if mirror:
        parts.append("hflip")
    if (rw, rh) != (out_w, out_h):
        parts.append(f"scale={out_w}:{out_h}:flags=lanczos")
    if p.hue_deg:
        parts.append(f"hue=h={_f(p.hue_deg)}")
    if any([p.brightness, p.contrast != 1.0, p.saturation != 1.0, p.gamma != 1.0]):
        parts.append(f"eq=brightness={_f(p.brightness)}:contrast={_f(p.contrast)}:"
                     f"saturation={_f(p.saturation)}:gamma={_f(p.gamma)}")
    if p.warmth:
        parts.append(f"colorbalance=rm={_f(p.warmth)}:bm={_f(-p.warmth)}")
    if p.sharpen and p.sharpen > 0:   # enhancement only; never soften (that degrades)
        parts.append(f"unsharp=5:5:{_f(p.sharpen)}:5:5:0")
    if p.vignette:
        parts.append("vignette=PI/5")
    return ",".join(parts) or "null"


def build_audio_chain(p: Profile, pr: Probe) -> str:
    parts: list[str] = []
    r = 2.0 ** (p.audio_semitones / 12.0) if p.audio_semitones else 1.0
    tempo = (1.0 / r) * (p.speed_factor or 1.0)
    if r != 1.0:
        parts.append(f"asetrate={int(round(pr.sample_rate * r))}")
        parts.append(f"aresample={pr.sample_rate}")
    if abs(tempo - 1.0) > 1e-6:
        t = tempo
        while t > 2.0:
            parts.append("atempo=2.0"); t /= 2.0
        while t < 0.5:
            parts.append("atempo=0.5"); t /= 0.5
        parts.append(f"atempo={_f(t)}")
    if p.audio_gain_db:
        parts.append(f"volume={_f(p.audio_gain_db)}dB")
    return ",".join(parts) or "anull"


# --------------------------------------------------------------------------- #
@dataclass
class Lever:
    zoom_pct: float
    mirror: bool
    reframe: str
    mean_min: float
    cleared: bool


def escalate(src_hashes: list[int], source_path: Path, p: Profile, W: int, H: int,
             out_w: int, out_h: int, mirror_allowed: bool, threshold: int, margin: int) -> Lever:
    """Find the MINIMUM lever (least human-visible / least lossy first) that clears the
    detection threshold. Mirror-only is invisible AND geometrically lossless, so it's the
    first thing tried for mirror-preferring profiles."""
    base = p.base_zoom_pct
    if mirror_allowed and p.prefer_mirror:
        ladder = [(0, True), (base, True), (base + 6, True)]          # mirror-only first = lossless
    else:
        ladder = [(base, False), (base + 5, False), (base + 10, False),
                  (base + 15, False), (base + 20, False), (base + 26, False)]
        if mirror_allowed:
            ladder.append((0, True))                                  # lossless last-resort

    best: Lever | None = None
    for zoom, mirror in ladder:
        dir_ = p.reframe if (mirror or p.reframe != "center") else "ur"
        vf = build_visual_chain(p, zoom, mirror, dir_, W, H, out_w, out_h)
        cand = verify.frame_hashes(str(source_path), fps=2.0, pre_vf=vf)
        v = verify.distance_from(src_hashes, cand, threshold)
        lev = Lever(zoom, mirror, dir_, v.mean_min, v.mean_min >= threshold + margin)
        if best is None or lev.mean_min > best.mean_min:
            best = lev
        if lev.cleared:
            return lev
    return best  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
@dataclass
class Result:
    profile: Profile
    out_path: Path
    lever: Lever | None = None
    cmd: list[str] = None  # type: ignore[assignment]
    ok: bool = False
    error: str = ""
    sha256: str = ""
    size_bytes: int = 0
    duration: float = 0.0
    thumb: str = ""
    final_mean_min: float = 0.0
    passed: bool = False
    detail: float = 100.0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render_one(ffmpeg: str, input_path: Path, out_dir: Path, stem: str, p: Profile,
               pr: Probe, src_hashes: list[int], out_w: int, out_h: int, mirror_allowed: bool,
               threshold: int, margin: int, crf: int, lossless: bool, audio_kbps: int,
               thumbs: bool) -> Result:
    safe = "".join(c if c.isalnum() else "-" for c in p.name).strip("-").lower()
    out_path = out_dir / f"{stem}__v{p.id:02d}_{safe}.mp4"
    res = Result(profile=p, out_path=out_path)
    try:
        lever = escalate(src_hashes, input_path, p, pr.width, pr.height, out_w, out_h,
                         mirror_allowed, threshold, margin)
        res.lever = lever
        res.detail = detail_pct(lever.zoom_pct, pr.width, out_w)

        vchain = build_visual_chain(p, lever.zoom_pct, lever.mirror, lever.reframe,
                                    pr.width, pr.height, out_w, out_h)
        if p.speed_factor and p.speed_factor != 1.0:
            vchain = f"{vchain},setpts={_f(1.0 / p.speed_factor)}*PTS"

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        head_s, tail_s = max(0.0, p.trim_head_ms / 1000.0), max(0.0, p.trim_tail_ms / 1000.0)
        if pr.duration >= 2.0 and (head_s or tail_s):
            keep = pr.duration - head_s - tail_s
            if keep >= 0.5 * pr.duration and keep > 0.5:
                if head_s:
                    cmd += ["-ss", _f(head_s)]
                cmd += ["-t", _f(keep)]
        cmd += ["-i", str(input_path)]

        if pr.has_audio:
            cmd += ["-filter_complex", f"[0:v]{vchain}[v];[0:a]{build_audio_chain(p, pr)}[a]",
                    "-map", "[v]", "-map", "[a]"]
        else:
            cmd += ["-filter_complex", f"[0:v]{vchain}[v]", "-map", "[v]", "-an"]

        cmd += ["-map_metadata", "-1", "-map_chapters", "-1",
                "-fflags", "+bitexact", "-flags:v", "+bitexact", "-c:v", "libx264"]
        cmd += (["-qp", "0"] if lossless else ["-crf", str(crf)])
        cmd += ["-preset", "medium", "-pix_fmt", "yuv420p"]
        if pr.has_audio:
            cmd += ["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-flags:a", "+bitexact"]
        cmd += ["-movflags", "+faststart", str(out_path)]
        res.cmd = cmd

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            tail = proc.stderr.strip().splitlines()
            res.error = tail[-1] if tail else "ffmpeg failed"
            return res

        res.ok = True
        res.sha256 = _sha256(out_path)
        res.size_bytes = out_path.stat().st_size
        op = probe(out_path)
        res.duration = op.duration

        fv = verify.video_distance(str(input_path), str(out_path), threshold=threshold)
        res.final_mean_min = fv.mean_min
        res.passed = fv.distinct

        if thumbs:
            tdir = out_dir / "thumbs"
            tdir.mkdir(exist_ok=True)
            tpath = tdir / f"{out_path.stem}.jpg"
            tp = subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", _f(op.duration / 2.0),
                 "-i", str(out_path), "-frames:v", "1", "-q:v", "2", str(tpath)],
                capture_output=True, text=True)
            if tp.returncode == 0:
                res.thumb = str(tpath.relative_to(out_dir))
    except Exception as exc:  # noqa: BLE001
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# --------------------------------------------------------------------------- #
def write_reports(out_dir: Path, stem: str, src: Path, pr: Probe, out_w: int, out_h: int,
                  results: list[Result], threshold: int, mirror_allowed: bool,
                  lossless: bool, crf: int) -> None:
    quality = "mathematically lossless (qp 0)" if lossless else f"visually lossless (CRF {crf})"
    manifest = {
        "source": str(src), "source_geometry": f"{pr.width}x{pr.height}",
        "output_geometry": f"{out_w}x{out_h}", "encode": quality,
        "source_duration_s": round(pr.duration, 3), "mirror_allowed": mirror_allowed,
        "pass_threshold_phash": threshold, "variant_count": len(results),
        "variants": [{
            "id": r.profile.id, "name": r.profile.name, "look": r.profile.look,
            "file": r.out_path.name if r.ok else None, "ok": r.ok, "error": r.error or None,
            "sha256": r.sha256 or None, "size_bytes": r.size_bytes or None,
            "duration_s": round(r.duration, 3) if r.duration else None, "thumb": r.thumb or None,
            "lever": ({"zoom_pct": r.lever.zoom_pct, "mirror": r.lever.mirror,
                       "reframe": r.lever.reframe} if r.lever else None),
            "phash_distance": r.final_mean_min, "passes_detection": r.passed,
            "detail_retained_pct": r.detail,
            "ffmpeg_cmd": " ".join(r.cmd) if r.cmd else None,
        } for r in results],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    lines = [
        f"# Variant review — {stem}", "",
        f"Source `{src}` {pr.width}x{pr.height} · output {out_w}x{out_h} · encode {quality} · "
        f"mirror {'ALLOWED (clip declared text-free)' if mirror_allowed else 'off (text-safe)'}",
        "",
        f"`PASS` = a perceptual frame-hash (the family Meta uses) can no longer link the variant "
        f"to the source (mean-min Hamming ≥ {threshold}). **Detail** = % of original resolution "
        f"retained (100 = no upscaling; mirror-only and reframes with master headroom stay 100). "
        f"Eyeball `thumbs/` to confirm each still reads as the same video.",
        "",
        "| # | Profile | Look | Lever | pHash dist | Detection | Detail | File |",
        "|---|---------|------|-------|-----------|-----------|--------|------|",
    ]
    for r in results:
        if r.ok:
            lev = (f"{'mirror' if r.lever and r.lever.mirror else ''}"
                   f"{' + ' if r.lever and r.lever.mirror and r.lever.zoom_pct else ''}"
                   f"{(f'{r.lever.zoom_pct:.0f}% ({r.lever.reframe})' if r.lever and r.lever.zoom_pct else '')}"
                   or "re-encode only")
            badge = "✅ PASS" if r.passed else "⚠️ still close"
            lines.append(f"| {r.profile.id:02d} | **{r.profile.name}** | {r.profile.look} | {lev} | "
                         f"{r.final_mean_min} | {badge} | {r.detail:.0f}% | `{r.out_path.name}` |")
        else:
            lines.append(f"| {r.profile.id:02d} | **{r.profile.name}** | {r.profile.look} | — | — | "
                         f"_FAILED: {r.error}_ | — | — |")
    ok = [r for r in results if r.ok]
    passed = sum(1 for r in ok if r.passed)
    distinct = len({r.sha256 for r in ok})
    min_detail = min((r.detail for r in ok), default=100)
    lines += [
        "",
        f"**{len(ok)}/{len(results)} rendered · {distinct} distinct files · "
        f"{passed}/{len(ok)} clear detection · lowest detail retained {min_detail:.0f}%.**",
        "",
    ]
    if passed < len(ok) and not mirror_allowed:
        lines.append("> Some variants stay close because mirror is off. If this clip has **no "
                     "on-screen text/logo**, re-run with `--allow-mirror` — a mirror is invisible, "
                     "geometrically lossless, and reliably clears detection.")
    if min_detail < 100 and not mirror_allowed:
        lines.append("> Detail < 100% means a reframe upscaled a same-resolution master. To keep "
                     "100%: supply a higher-resolution master and set `--target-height`, or use "
                     "`--allow-mirror` on text-free clips.")
    lines += [
        "",
        "_All metadata stripped (`-map_metadata -1`, bitexact). File metadata is moot once uploaded "
        "(platforms re-encode); detection is on the perceptual fingerprint above._",
    ]
    (out_dir / "REVIEW.md").write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
def load_profiles(path: Path) -> list[Profile]:
    if not path.exists():
        sys.exit(f"error: profiles file not found: {path}")
    data = json.loads(path.read_text())
    raw = data["profiles"] if isinstance(data, dict) else data
    return [Profile.from_dict(d) for d in raw]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Remake one video into N metadata-stripped, fresh-looking variants that an "
                    "Instagram-class perceptual matcher no longer links to the source — without "
                    "degrading quality.")
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, default=Path("./out"))
    ap.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    ap.add_argument("--count", type=int, default=0, help="how many variants (default: all profiles)")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--allow-mirror", action="store_true",
                    help="permit horizontal mirror (ONLY for clips with no on-screen text/logo) — "
                         "invisible, lossless, strongest")
    ap.add_argument("--crf", type=int, default=16, help="x264 CRF; lower=higher quality (default 16, visually lossless)")
    ap.add_argument("--lossless", action="store_true", help="mathematically lossless encode (x264 qp 0; large files)")
    ap.add_argument("--audio-kbps", type=int, default=256, help="AAC bitrate (default 256, transparent)")
    ap.add_argument("--target-height", type=int, default=0,
                    help="output height; if < source, reframes consume master headroom losslessly")
    ap.add_argument("--threshold", type=int, default=verify.PASS_THRESHOLD)
    ap.add_argument("--margin", type=int, default=2)
    ap.add_argument("--no-thumbs", action="store_true")
    args = ap.parse_args(argv)

    ffmpeg = _require("ffmpeg"); _require("ffprobe")
    if not args.input.exists():
        sys.exit(f"error: input not found: {args.input}")

    profiles = load_profiles(args.profiles)
    if args.count and args.count > 0:
        profiles = profiles[: args.count]
    if not profiles:
        sys.exit("error: no profiles to render.")

    pr = probe(args.input)
    out_h = args.target_height if (args.target_height and args.target_height < pr.height) else pr.height
    out_w = _even(pr.width * out_h / pr.height)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem
    jobs = args.jobs or min(os.cpu_count() or 4, len(profiles))

    enc = "lossless(qp0)" if args.lossless else f"CRF{args.crf}"
    print(f"remaking {len(profiles)} variants of {args.input.name} "
          f"({pr.width}x{pr.height} -> {out_w}x{out_h}, {pr.duration:.1f}s) · {jobs} jobs · "
          f"mirror {'ON' if args.allow_mirror else 'off'} · {enc} · pass≥{args.threshold} pHash")
    src_hashes = verify.frame_hashes(str(args.input), fps=3.0)

    results: list[Result] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(render_one, ffmpeg, args.input, args.out, stem, p, pr, src_hashes,
                          out_w, out_h, args.allow_mirror, args.threshold, args.margin,
                          args.crf, args.lossless, args.audio_kbps, not args.no_thumbs)
                for p in profiles]
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            results.append(r)
            if r.ok:
                lev = f"{'mirror' if r.lever and r.lever.mirror else ''}{('+%.0f%%' % r.lever.zoom_pct) if r.lever and r.lever.zoom_pct else ('%.0f%%' % (r.lever.zoom_pct if r.lever else 0))}"
                print(f"  [{'PASS' if r.passed else 'near'}] v{r.profile.id:02d} "
                      f"{r.profile.name:<20} dist={r.final_mean_min:<5} detail={r.detail:.0f}% lever={lev}")
            else:
                print(f"  [FAIL] v{r.profile.id:02d} {r.profile.name:<20} {r.error}")

    results.sort(key=lambda r: r.profile.id)
    write_reports(args.out, stem, args.input, pr, out_w, out_h, results,
                  args.threshold, args.allow_mirror, args.lossless, args.crf)

    ok = [r for r in results if r.ok]
    passed = sum(1 for r in ok if r.passed)
    distinct = len({r.sha256 for r in ok})
    min_detail = min((r.detail for r in ok), default=100)
    print(f"\ndone: {len(ok)}/{len(results)} rendered · {distinct} distinct · "
          f"{passed}/{len(ok)} clear detection · lowest detail {min_detail:.0f}%.")
    print(f"  review:   {args.out / 'REVIEW.md'}")
    print(f"  manifest: {args.out / 'manifest.json'}")
    return 0 if ok and passed == len(ok) and distinct == len(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
