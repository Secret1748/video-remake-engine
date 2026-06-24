#!/usr/bin/env python3
"""
verify.py — does a COMPUTER still see a variant as the same video?

Instagram-class duplicate detection works on the decoded *perceptual fingerprint*,
not the file: a frame perceptual-hash (pHash, the DCT family) plus audio matching.
This module reimplements pHash in pure Python (zero dependencies beyond ffmpeg) so
the spinner can MEASURE, per variant, how far it has moved in perceptual space.

Metric: for each sampled variant frame we find the CLOSEST source frame (min Hamming
distance over a densely-sampled set of source frames — i.e. "could a matcher find
this frame anywhere in the original?"), then aggregate.

Calibration anchors (64-bit hash, so distance is 0..64):
    exact re-encode      ~0-4    -> SAME (a matcher flags it)
    subtle color grade   ~3-9    -> still SAME-ish (likely flagged)
    strong reframe        >=14    -> DIFFERENT (matcher won't link them)

PASS_THRESHOLD (default 12) is the line: mean-min pHash distance >= threshold means
"a frame matcher no longer links the variant to the source."

Audio: if `fpcalc` (Chromaprint, `brew install chromaprint`) is on PATH we also report
an audio-fingerprint similarity; otherwise audio is reported as "not measured".
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
from dataclasses import dataclass

PASS_THRESHOLD = 12          # mean-min pHash Hamming distance to count as "computer-distinct"
_N = 32                      # DCT works on 32x32 grayscale
_LOW = 8                     # keep the top-left 8x8 low-frequency block -> 64 bits

# Precomputed DCT-II cosine table (rows = frequency k, cols = sample n).
_COS = [[math.cos(math.pi / _N * (n + 0.5) * k) for n in range(_N)] for k in range(_N)]


def _dct1d(vec: list[float]) -> list[float]:
    return [sum(vec[n] * _COS[k][n] for n in range(_N)) for k in range(_N)]


def _phash(gray: bytes) -> int:
    """64-bit perceptual hash of one 32x32 grayscale frame (1024 bytes)."""
    mat = [[gray[r * _N + c] for c in range(_N)] for r in range(_N)]
    rows = [_dct1d(row) for row in mat]
    block: list[float] = []
    cols_cache: dict[int, list[float]] = {}
    for c in range(_LOW):
        col = [rows[r][c] for r in range(_N)]
        cols_cache[c] = _dct1d(col)
    for r in range(_LOW):
        for c in range(_LOW):
            block.append(cols_cache[c][r])
    # median of the low-freq coefficients, excluding the DC term (index 0)
    ac = sorted(block[1:])
    median = ac[len(ac) // 2]
    bits = 0
    for i, v in enumerate(block):
        bits = (bits << 1) | (1 if v > median else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def frame_hashes(path: str, fps: float, pre_vf: str = "") -> list[int]:
    """Sample frames at `fps`, optionally through `pre_vf` (a candidate transform,
    applied at full res BEFORE the 32x32 downscale), and return their pHashes.

    pre_vf lets the engine measure a candidate transform WITHOUT a full encode."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("error: ffmpeg not found.")
    parts = [p for p in [pre_vf] if p]
    parts += [f"fps={fps}", f"scale={_N}:{_N}:flags=area", "format=gray"]
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path, "-vf", ",".join(parts), "-f", "rawvideo", "-"],
        capture_output=True,
    )
    raw = proc.stdout
    fsize = _N * _N
    return [_phash(raw[i:i + fsize]) for i in range(0, len(raw) - fsize + 1, fsize)]


def distance_from(src_hashes: list[int], var_hashes: list[int],
                  threshold: int = PASS_THRESHOLD) -> VerdictV:
    """Aggregate min-Hamming distance of each variant frame to the closest source frame."""
    if not src_hashes or not var_hashes:
        return VerdictV(0, 0, 0, False, len(src_hashes), len(var_hashes))
    mins = sorted(min(_hamming(v, s) for s in src_hashes) for v in var_hashes)
    mean_min = sum(mins) / len(mins)
    return VerdictV(
        mean_min=round(mean_min, 2),
        median_min=mins[len(mins) // 2],
        min_min=mins[0],
        distinct=mean_min >= threshold,
        n_source=len(src_hashes),
        n_variant=len(var_hashes),
    )


@dataclass
class VerdictV:
    mean_min: float
    median_min: float
    min_min: int
    distinct: bool
    n_source: int
    n_variant: int


def video_distance(source: str, variant: str,
                   source_fps: float = 3.0, variant_fps: float = 2.0,
                   threshold: int = PASS_THRESHOLD) -> VerdictV:
    """How far has `variant` moved from `source` in frame-perceptual space?"""
    src = frame_hashes(source, source_fps)
    var = frame_hashes(variant, variant_fps)
    return distance_from(src, var, threshold)


def have_chromaprint() -> bool:
    return shutil.which("fpcalc") is not None


def chroma_fp(path: str, length: int = 60) -> list[int] | None:
    """Raw Chromaprint fingerprint (list of 32-bit ints), or None if fpcalc/audio absent."""
    fpcalc = shutil.which("fpcalc")
    if not fpcalc:
        return None
    try:
        out = subprocess.run([fpcalc, "-raw", "-length", str(length), path],
                             capture_output=True, text=True, timeout=120)
        for line in out.stdout.splitlines():
            if line.startswith("FINGERPRINT="):
                vals = [int(x) for x in line.split("=", 1)[1].split(",")
                        if x.strip().lstrip("-").isdigit()]
                return vals or None
    except Exception:  # noqa: BLE001
        return None
    return None


def audio_dist_fp(a: list[int] | None, b: list[int] | None) -> float | None:
    """Fraction of differing fingerprint bits (0=identical, 1=totally different)."""
    if not a or not b:
        return None
    n = min(len(a), len(b))
    if n == 0:
        return None
    diff = sum(bin((a[i] ^ b[i]) & 0xFFFFFFFF).count("1") for i in range(n))
    return round(diff / (n * 32.0), 4)


def audio_distance(source: str, variant: str) -> float | None:
    """Chromaprint distance source<->variant. None if fpcalc/Chromaprint or audio is absent."""
    return audio_dist_fp(chroma_fp(source), chroma_fp(variant))


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Measure perceptual distance source<->variant.")
    ap.add_argument("source")
    ap.add_argument("variant")
    ap.add_argument("--threshold", type=int, default=PASS_THRESHOLD)
    args = ap.parse_args(argv)

    v = video_distance(args.source, args.variant, threshold=args.threshold)
    a = audio_distance(args.source, args.variant)
    print(f"video pHash mean-min={v.mean_min}  median-min={v.median_min}  min={v.min_min}  "
          f"(src {v.n_source} / var {v.n_variant} frames)")
    print(f"  -> {'DISTINCT to a frame matcher ✅' if v.distinct else 'still MATCHES the source ❌'} "
          f"(threshold {args.threshold})")
    if a is None:
        print("audio: not measured (install Chromaprint `fpcalc` for an audio-fingerprint distance)")
    else:
        print(f"audio chromaprint differing-bits fraction={a}  "
              f"({'sufficiently different' if a >= 0.30 else 'still similar'})")
    return 0 if v.distinct else 1


if __name__ == "__main__":
    raise SystemExit(main())
