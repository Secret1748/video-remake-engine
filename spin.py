#!/usr/bin/env python3
"""
video-remake-engine — turn ONE video into N variants that read as the SAME video to a
person, but where every variant is unlinkable to the source AND to each other on BOTH
layers an Instagram-class system dedupes on: the video frame-fingerprint (pHash/PDQ) and
the audio fingerprint (Chromaprint/AcoustID family).

"GREEN" = all N variants, measured on the encoded files, are:
    * video pHash distance >= threshold vs the source AND vs every other variant, and
    * audio Chromaprint distance >= threshold vs the source AND vs every other variant.

How it gets there (measure-driven, not assumed):
    1. SELECT a mutually-distinct SET of framings (direction + zoom, plus a lossless mirror
       when --allow-mirror) by measuring a candidate grid against the source and pairwise.
    2. SELECT a mutually-distinct SET of audio settings (subtle pitch + tempo) the same way.
    3. RENDER each variant = a distinct framing + distinct audio + a distinct colour look;
       strip all metadata; re-encode visually-lossless (CRF 16; --lossless for qp 0).
    4. GREEN-GATE: re-measure the encoded files' full pairwise + vs-source matrix on both
       layers and report GREEN / which variant+layer fell short.

Quality-first: mirror is lossless geometry (100% detail); no grain, no softening; lanczos
scaling; visually-lossless encode. Reframing a same-res master is the only detail cost and
is measured + reported (use --target-height with a hi-res master to keep 100%).

Pure stdlib + ffmpeg/ffprobe (+ optional Chromaprint `fpcalc` for the audio layer).
verify.py does the perceptual scoring. For YOUR content on YOUR channels — see README.
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
from dataclasses import dataclass, field, fields
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import verify  # noqa: E402

DEFAULT_PROFILES = HERE / "profiles.json"

DIRS = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1),
        "ul": (-1, -1), "ur": (1, -1), "dl": (-1, 1), "dr": (1, 1), "center": (0, 0)}
DIR_LIST = ["ur", "ul", "dr", "dl", "up", "down", "left", "right"]

# gate (what counts as GREEN on the encoded files). Chromaprint scale: same recording
# lightly processed ≈ <0.15 differing bits, genuinely different content ≈ 0.40+, so a 0.22
# pairwise floor is clearly in "not a match" territory.
V_GATE = 12        # video pHash mean-min distance
A_GATE = 0.22      # audio chromaprint differing-bits fraction
# selection targets (higher than the gate, to absorb encode drift)
V_PICK_SRC, V_PICK_PAIR = 13, 14
A_PICK_SRC, A_PICK_PAIR = 0.34, 0.26


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
                 a is not None, int(a["sample_rate"]) if a and a.get("sample_rate") else 44100)


# --------------------------------------------------------------------------- #
@dataclass
class Profile:
    """A colour 'look' only — distinctness levers (framing, audio) are chosen by the engine."""
    id: int
    name: str
    look: str = ""
    hue_deg: float = 0.0
    saturation: float = 1.0
    brightness: float = 0.0
    contrast: float = 1.0
    gamma: float = 1.0
    warmth: float = 0.0
    sharpen: float = 0.0       # enhancement only; softening (negative) is ignored
    vignette: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        known = {fld.name for fld in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


NEUTRAL = Profile(0, "neutral")


@dataclass
class Framing:
    zoom_pct: float
    mirror: bool
    reframe: str


@dataclass
class AudioSet:
    pitch_st: float
    tempo_pct: float


def region_for(zoom_pct: float, W: int, H: int) -> tuple[int, int]:
    if zoom_pct and zoom_pct > 0:
        z = 1.0 + zoom_pct / 100.0
        return _even(W / z), _even(H / z)
    return W, H


def detail_pct(zoom_pct: float, W: int, out_w: int) -> float:
    rw, _ = region_for(zoom_pct, W, 100)
    return round(min(100.0, rw / out_w * 100.0), 1)


def build_visual_chain(p: Profile, fr: Framing, W: int, H: int, out_w: int, out_h: int) -> str:
    parts: list[str] = []
    rw, rh = region_for(fr.zoom_pct, W, H)
    if (rw, rh) != (W, H):
        mx, my = W - rw, H - rh
        dx, dy = DIRS.get(fr.reframe, (0, 0))
        x = min(max(int(round(mx / 2 * (1 + dx * 0.8))), 0), mx)
        y = min(max(int(round(my / 2 * (1 + dy * 0.8))), 0), my)
        parts.append(f"crop={rw}:{rh}:{x}:{y}")
    if fr.mirror:
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
    if p.sharpen and p.sharpen > 0:
        parts.append(f"unsharp=5:5:{_f(p.sharpen)}:5:5:0")
    if p.vignette:
        parts.append("vignette=PI/5")
    return ",".join(parts) or "null"


def build_audio_chain(a: AudioSet, sr: int) -> str:
    """Pitch (duration-preserving) + tempo. Tempo also speeds the video (setpts) so A/V stay
    in sync; here we produce the audio side: net atempo = tempo/pitch ratio."""
    r = 2.0 ** (a.pitch_st / 12.0) if a.pitch_st else 1.0
    s = 1.0 + a.tempo_pct / 100.0
    parts: list[str] = []
    if r != 1.0:
        parts.append(f"asetrate={int(round(sr * r))}")
        parts.append(f"aresample={sr}")
    factor = s / r
    if abs(factor - 1.0) > 1e-6:
        t = factor
        while t > 2.0:
            parts.append("atempo=2.0"); t /= 2.0
        while t < 0.5:
            parts.append("atempo=0.5"); t /= 0.5
        parts.append(f"atempo={_f(t)}")
    return ",".join(parts) or "anull"


# --------------------------------------------------------------------------- #
def _maximin_fill(chosen: list, pool: list, dist, n: int) -> None:
    """Best-effort: fill `chosen` up to n from pool, each pick maximising min-distance."""
    while len(chosen) < n and pool:
        best, bi = None, None
        for i, c in enumerate(pool):
            m = min((dist(c, k) for k in chosen), default=999)
            if best is None or m > best:
                best, bi = m, i
        chosen.append(pool.pop(bi))


def select_framings(source: Path, src_h: list[int], n: int, mirror_allowed: bool,
                    W: int, H: int, out_w: int, out_h: int, zooms: list[int]) -> list[Framing]:
    cands: list[Framing] = [Framing(z, False, d) for d in DIR_LIST for z in zooms]
    if mirror_allowed:
        cands.append(Framing(0, True, "center"))                       # pure mirror = lossless
        cands += [Framing(z, True, d) for d in DIR_LIST for z in zooms]
    H_ = {id(c): verify.frame_hashes(str(source), fps=2.0,
          pre_vf=build_visual_chain(NEUTRAL, c, W, H, out_w, out_h)) for c in cands}
    vs_src = {id(c): verify.distance_from(src_h, H_[id(c)]).mean_min for c in cands}
    pair = lambda a, b: verify.distance_from(H_[id(a)], H_[id(b)]).mean_min
    # detail-first: prefer the shallowest zoom (and the lossless mirror, zoom 0) that still
    # separates, so quality stays as high as possible.
    pool = sorted([c for c in cands if vs_src[id(c)] >= V_PICK_SRC],
                  key=lambda c: (c.zoom_pct, -vs_src[id(c)]))
    chosen: list[Framing] = []
    for c in pool:
        if all(pair(c, k) >= V_PICK_PAIR for k in chosen):
            chosen.append(c)
        if len(chosen) >= n:
            break
    if len(chosen) < n:  # best-effort fill (gate will flag any shortfall honestly)
        rest = [c for c in sorted(cands, key=lambda c: -vs_src[id(c)]) if c not in chosen]
        _maximin_fill(chosen, rest, pair, n)
    return chosen[:n]


def select_audio(source: Path, n: int, sr: int, pitches: list[float],
                 tempos: list[float]) -> list[AudioSet]:
    cands = [AudioSet(p, t) for p in pitches for t in tempos]
    tmp = HERE / ".audtmp"
    tmp.mkdir(exist_ok=True)
    ffmpeg = _require("ffmpeg")

    def render(a: AudioSet) -> list[int] | None:
        out = tmp / f"a_{a.pitch_st}_{a.tempo_pct}.m4a"
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
                        "-vn", "-af", build_audio_chain(a, sr), "-c:a", "aac", "-b:a", "256k",
                        str(out)], check=True)
        return verify.chroma_fp(str(out))

    src_fp = verify.chroma_fp(str(source))
    fps = {id(a): render(a) for a in cands}
    vs_src = {id(a): (verify.audio_dist_fp(src_fp, fps[id(a)]) or 0.0) for a in cands}
    pair = lambda a, b: (verify.audio_dist_fp(fps[id(a)], fps[id(b)]) or 0.0)
    pool = sorted([a for a in cands if vs_src[id(a)] >= A_PICK_SRC], key=lambda a: -vs_src[id(a)])
    chosen: list[AudioSet] = []
    for a in pool:
        if all(pair(a, k) >= A_PICK_PAIR for k in chosen):
            chosen.append(a)
        if len(chosen) >= n:
            break
    if len(chosen) < n:
        rest = [a for a in sorted(cands, key=lambda a: -vs_src[id(a)]) if a not in chosen]
        _maximin_fill(chosen, rest, pair, n)
    shutil.rmtree(tmp, ignore_errors=True)
    return chosen[:n]


# --------------------------------------------------------------------------- #
@dataclass
class Result:
    profile: Profile
    framing: Framing
    audio: AudioSet | None
    out_path: Path
    cmd: list[str] = field(default=None)  # type: ignore[assignment]
    ok: bool = False
    error: str = ""
    sha256: str = ""
    size_bytes: int = 0
    duration: float = 0.0
    thumb: str = ""
    detail: float = 100.0
    # green-gate
    v_src: float = 0.0
    v_pair: float = 0.0
    a_src: float | None = None
    a_pair: float | None = None
    green: bool = False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render_one(ffmpeg: str, input_path: Path, out_dir: Path, stem: str, p: Profile,
               fr: Framing, au: AudioSet | None, pr: Probe, out_w: int, out_h: int,
               crf: int, lossless: bool, audio_kbps: int, thumbs: bool) -> Result:
    safe = "".join(c if c.isalnum() else "-" for c in p.name).strip("-").lower()
    out_path = out_dir / f"{stem}__v{p.id:02d}_{safe}.mp4"
    res = Result(profile=p, framing=fr, audio=au, out_path=out_path)
    res.detail = detail_pct(fr.zoom_pct, pr.width, out_w)
    try:
        vchain = build_visual_chain(p, fr, pr.width, pr.height, out_w, out_h)
        if au and au.tempo_pct:
            s = 1.0 + au.tempo_pct / 100.0
            vchain = f"{vchain},setpts={_f(1.0 / s)}*PTS"

        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path)]
        if pr.has_audio and au is not None:
            cmd += ["-filter_complex",
                    f"[0:v]{vchain}[v];[0:a]{build_audio_chain(au, pr.sample_rate)}[a]",
                    "-map", "[v]", "-map", "[a]"]
        else:
            cmd += ["-filter_complex", f"[0:v]{vchain}[v]", "-map", "[v]", "-an"]
        cmd += ["-map_metadata", "-1", "-map_chapters", "-1",
                "-fflags", "+bitexact", "-flags:v", "+bitexact", "-c:v", "libx264"]
        cmd += (["-qp", "0"] if lossless else ["-crf", str(crf)])
        cmd += ["-preset", "medium", "-pix_fmt", "yuv420p"]
        if pr.has_audio and au is not None:
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
        if thumbs:
            tdir = out_dir / "thumbs"; tdir.mkdir(exist_ok=True)
            tpath = tdir / f"{out_path.stem}.jpg"
            tp = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                                 "-ss", _f(op.duration / 2.0), "-i", str(out_path),
                                 "-frames:v", "1", "-q:v", "2", str(tpath)],
                                capture_output=True, text=True)
            if tp.returncode == 0:
                res.thumb = str(tpath.relative_to(out_dir))
    except Exception as exc:  # noqa: BLE001
        res.error = f"{type(exc).__name__}: {exc}"
    return res


def green_gate(source: Path, src_h: list[int], results: list[Result], has_audio: bool) -> None:
    ok = [r for r in results if r.ok]
    vh = {r.profile.id: verify.frame_hashes(str(r.out_path), fps=2.0) for r in ok}
    afp = {r.profile.id: verify.chroma_fp(str(r.out_path)) for r in ok} if has_audio else {}
    src_fp = verify.chroma_fp(str(source)) if has_audio else None
    for r in ok:
        i = r.profile.id
        r.v_src = verify.distance_from(src_h, vh[i]).mean_min
        r.v_pair = min((verify.distance_from(vh[i], vh[j.profile.id]).mean_min
                        for j in ok if j is not r), default=99)
        v_ok = r.v_src >= V_GATE and r.v_pair >= V_GATE
        if has_audio and src_fp:
            r.a_src = verify.audio_dist_fp(src_fp, afp[i])
            r.a_pair = min((verify.audio_dist_fp(afp[i], afp[j.profile.id]) or 0.0
                            for j in ok if j is not r), default=1.0)
            a_ok = (r.a_src or 0) >= A_GATE and (r.a_pair or 0) >= A_GATE
        else:
            a_ok = True
        r.green = bool(v_ok and a_ok)


# --------------------------------------------------------------------------- #
def write_reports(out_dir: Path, stem: str, src: Path, pr: Probe, out_w: int, out_h: int,
                  results: list[Result], mirror_allowed: bool, lossless: bool, crf: int,
                  has_audio: bool) -> bool:
    quality = "mathematically lossless (qp 0)" if lossless else f"visually lossless (CRF {crf})"
    ok = [r for r in results if r.ok]
    all_green = bool(ok) and all(r.green for r in ok) and len({r.sha256 for r in ok}) == len(ok)
    manifest = {
        "source": str(src), "source_geometry": f"{pr.width}x{pr.height}",
        "output_geometry": f"{out_w}x{out_h}", "encode": quality,
        "mirror_allowed": mirror_allowed, "audio_layer": has_audio,
        "gates": {"video_phash": V_GATE, "audio_chromaprint": A_GATE},
        "GREEN": all_green, "variant_count": len(results),
        "variants": [{
            "id": r.profile.id, "name": r.profile.name, "look": r.profile.look,
            "file": r.out_path.name if r.ok else None, "ok": r.ok, "error": r.error or None,
            "sha256": r.sha256 or None, "detail_retained_pct": r.detail,
            "framing": {"zoom_pct": r.framing.zoom_pct, "mirror": r.framing.mirror,
                        "reframe": r.framing.reframe},
            "audio": ({"pitch_st": r.audio.pitch_st, "tempo_pct": r.audio.tempo_pct}
                      if r.audio else None),
            "video_dist_vs_source": r.v_src, "video_dist_vs_nearest_variant": r.v_pair,
            "audio_dist_vs_source": r.a_src, "audio_dist_vs_nearest_variant": r.a_pair,
            "GREEN": r.green, "ffmpeg_cmd": " ".join(r.cmd) if r.cmd else None,
        } for r in results],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    banner = "🟢 GREEN — all variants postable as distinct content" if all_green \
        else "🔴 NOT GREEN — see ⚠️ rows below"
    lines = [
        f"# Variant review — {stem}", "",
        f"## {banner}", "",
        f"Source `{src}` {pr.width}x{pr.height} → output {out_w}x{out_h} · encode {quality} · "
        f"audio layer {'ON' if has_audio else 'absent'} · mirror {'allowed' if mirror_allowed else 'off'}",
        "",
        f"Every variant is checked against the source **and every other variant** on both layers a "
        f"platform dedupes on. **GREEN** needs video pHash ≥ {V_GATE}"
        f"{' and audio Chromaprint ≥ ' + str(A_GATE) if has_audio else ''} on both vs-source and "
        f"vs-nearest-variant. `Vsrc`/`Vmin` = video distance to source / nearest variant; "
        f"`Asrc`/`Amin` = audio distance to source / nearest variant.",
        "",
        "| # | Profile | Lever | Detail | Vsrc | Vmin | Asrc | Amin | Status | File |",
        "|---|---------|-------|--------|------|------|------|------|--------|------|",
    ]
    for r in results:
        if not r.ok:
            lines.append(f"| {r.profile.id:02d} | {r.profile.name} | — | — | — | — | — | — | "
                         f"_FAIL: {r.error}_ | — |"); continue
        lev = (f"{'mirror' if r.framing.mirror else ''}"
               f"{'+' if r.framing.mirror and r.framing.zoom_pct else ''}"
               f"{(f'{r.framing.zoom_pct:.0f}%({r.framing.reframe})' if r.framing.zoom_pct else '')}"
               or "mirror") + (f" · {r.audio.pitch_st:+.1f}st/{r.audio.tempo_pct:+.0f}%"
                               if r.audio else "")
        asrc = f"{r.a_src:.2f}" if r.a_src is not None else "—"
        amin = f"{r.a_pair:.2f}" if r.a_pair is not None else "—"
        lines.append(f"| {r.profile.id:02d} | **{r.profile.name}** | {lev} | {r.detail:.0f}% | "
                     f"{r.v_src:.1f} | {r.v_pair:.1f} | {asrc} | {amin} | "
                     f"{'✅' if r.green else '⚠️'} | `{r.out_path.name}` |")
    green_n = sum(1 for r in ok if r.green)
    distinct = len({r.sha256 for r in ok})
    min_detail = min((r.detail for r in ok), default=100)
    lines += [
        "",
        f"**{len(ok)}/{len(results)} rendered · {distinct} byte-distinct · {green_n}/{len(ok)} GREEN · "
        f"lowest detail {min_detail:.0f}%.**", "",
        "_Metadata stripped (`-map_metadata -1`, bitexact). File metadata is moot once uploaded "
        "(platforms re-encode); the distances above are the perceptual layers that actually matter._",
    ]
    if not has_audio:
        lines.append("\n> No audio track / `fpcalc` not installed — audio layer skipped. "
                     "Install Chromaprint (`brew install chromaprint`) to gate audio too.")
    (out_dir / "REVIEW.md").write_text("\n".join(lines) + "\n")
    return all_green


# --------------------------------------------------------------------------- #
def load_profiles(path: Path) -> list[Profile]:
    if not path.exists():
        sys.exit(f"error: profiles file not found: {path}")
    data = json.loads(path.read_text())
    raw = data["profiles"] if isinstance(data, dict) else data
    return [Profile.from_dict(d) for d in raw]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Remake one video into N variants that are mutually + vs-source distinct on "
                    "video AND audio fingerprints — quality preserved.")
    ap.add_argument("input", type=Path)
    ap.add_argument("--out", type=Path, default=Path("./out"))
    ap.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    ap.add_argument("--count", type=int, default=0, help="how many variants (default: all profiles)")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--allow-mirror", action="store_true",
                    help="permit horizontal mirror (ONLY for clips with no on-screen text/logo)")
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--lossless", action="store_true")
    ap.add_argument("--audio-kbps", type=int, default=256)
    ap.add_argument("--target-height", type=int, default=0,
                    help="output height; if < source, reframes consume master headroom losslessly")
    ap.add_argument("--no-thumbs", action="store_true")
    args = ap.parse_args(argv)

    ffmpeg = _require("ffmpeg"); _require("ffprobe")
    if not args.input.exists():
        sys.exit(f"error: input not found: {args.input}")

    profiles = load_profiles(args.profiles)
    if args.count and args.count > 0:
        profiles = profiles[: args.count]
    n = len(profiles)
    if not n:
        sys.exit("error: no profiles to render.")

    pr = probe(args.input)
    out_h = args.target_height if (args.target_height and args.target_height < pr.height) else pr.height
    out_w = _even(pr.width * out_h / pr.height)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.input.stem
    jobs = args.jobs or min(os.cpu_count() or 4, n)
    has_audio = pr.has_audio and verify.have_chromaprint()

    enc = "lossless(qp0)" if args.lossless else f"CRF{args.crf}"
    print(f"remaking {n} variants of {args.input.name} ({pr.width}x{pr.height} → {out_w}x{out_h}, "
          f"{pr.duration:.1f}s) · mirror {'ON' if args.allow_mirror else 'off'} · {enc} · "
          f"audio-layer {'ON' if has_audio else 'off'}")

    print("  selecting mutually-distinct framings…")
    src_h = verify.frame_hashes(str(args.input), fps=3.0)
    framings = select_framings(args.input, src_h, n, args.allow_mirror,
                               pr.width, pr.height, out_w, out_h, zooms=[12, 18, 24, 30])
    if pr.has_audio and not verify.have_chromaprint():
        print("  (no fpcalc — audio layer skipped; install Chromaprint to gate audio)")
    if has_audio:
        print("  selecting mutually-distinct audio settings…")
        audios: list[AudioSet | None] = list(select_audio(
            args.input, n, pr.sample_rate,
            pitches=[-0.7, -0.5, -0.3, -0.15, 0.15, 0.3, 0.5, 0.7], tempos=[-7, -5, -3, 3, 5, 7]))
    else:
        audios = [None] * n

    print(f"  rendering {n} variants ({jobs} jobs)…")
    results: list[Result] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(render_one, ffmpeg, args.input, args.out, stem, p, framings[i],
                          audios[i], pr, out_w, out_h, args.crf, args.lossless,
                          args.audio_kbps, not args.no_thumbs)
                for i, p in enumerate(profiles)]
        for fut in concurrent.futures.as_completed(futs):
            results.append(fut.result())
    results.sort(key=lambda r: r.profile.id)

    print("  green-gate: measuring full pairwise + vs-source on both layers…")
    green_gate(args.input, src_h, results, has_audio)
    for r in results:
        if r.ok:
            a = f" A:{r.a_src:.2f}/{r.a_pair:.2f}" if r.a_src is not None else ""
            print(f"  {'🟢' if r.green else '🔴'} v{r.profile.id:02d} {r.profile.name:<18} "
                  f"V:{r.v_src:.1f}/{r.v_pair:.1f}{a} detail={r.detail:.0f}%")
        else:
            print(f"  ✗ v{r.profile.id:02d} {r.profile.name:<18} {r.error}")

    all_green = write_reports(args.out, stem, args.input, pr, out_w, out_h, results,
                              args.allow_mirror, args.lossless, args.crf, has_audio)
    ok = [r for r in results if r.ok]
    print(f"\n{'🟢 GREEN' if all_green else '🔴 NOT GREEN'}: "
          f"{sum(1 for r in ok if r.green)}/{len(ok)} variants postable as distinct content.")
    print(f"  review:   {args.out / 'REVIEW.md'}")
    print(f"  manifest: {args.out / 'manifest.json'}")
    return 0 if all_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
