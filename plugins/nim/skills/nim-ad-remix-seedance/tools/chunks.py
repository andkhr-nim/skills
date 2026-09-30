"""Tools: prepare_chunks and assemble_chunks — fit long videos into Seedance's input limits.

prepare_chunks splits the source (and, if given, the guide at exactly the same times) into
chunks that fit the chosen Seedance mode, preferring shot cuts as boundaries. Each chunk is
padded with its last frame to whole seconds (Seedance 2 needs `mediaLength` in whole
seconds; Seedance 2.5 keeps the input length). assemble_chunks puts the generated chunks
back together: retimes each to its padded length, trims the padding, joins them and adds the
original audio.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import fal_common as fal
import media

# Max seconds of *source* per run for each mode (source + guide share the input limit).
MODES = {
    "seedance-2-guide": 7,       # referenceVideos [source, guide], 15s combined
    "seedance-2": 15,            # referenceVideos [source], 15s
    "seedance-2.5-guide": 15,    # referenceVideos [source, guide], 30s combined
    "seedance-2.5-edit": 30,     # sourceVideo edit mode, 4–30s, no guide
}
MIN_SECONDS = 4

PREPARE_TOOL = {
    "name": "prepare_chunks",
    "description": (
        "Split a source video (and its guide video, if given) into chunks that fit a Seedance "
        "run: at most 7s of source for Seedance 2 with a guide, 15s for Seedance 2 without a "
        "guide or Seedance 2.5 with a guide, 30s for Seedance 2.5 edit mode; at least 4s. "
        "Boundaries go on shot cuts where possible. Each chunk is padded with its last frame to "
        "whole seconds; use `media_length_ms` as Seedance `mediaLength`. Pass the chunk list "
        "unchanged to assemble_chunks afterwards."
    ),
    "input_schema": {
        "type": "object",
        "required": ["source", "mode", "out_dir"],
        "properties": {
            "source": {"type": "string", "description": "Source video (path or URL)."},
            "guide": {"type": "string", "description": "Guide video (path or URL), same timing as the source."},
            "mode": {"type": "string", "enum": list(MODES)},
            "max_seconds": {"type": "number", "minimum": MIN_SECONDS,
                            "description": "Override the mode's maximum chunk length."},
            "out_dir": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "status": {"const": "finished"},
            "mode": {"type": "string"},
            "cuts": {"type": "array"},
            "chunks": {"type": "array", "items": {"type": "object", "properties": {
                "index": {"type": "integer"}, "start": {"type": "number"}, "end": {"type": "number"},
                "duration": {"type": "number"}, "padded_duration": {"type": "integer"},
                "media_length_ms": {"type": "integer"},
                "source_path": {"type": "string"}, "guide_path": {"type": ["string", "null"]}}}},
        },
    },
}

ASSEMBLE_TOOL = {
    "name": "assemble_chunks",
    "description": (
        "Join generated chunks back into one video: each generated chunk is retimed to its "
        "padded length, the padding is trimmed, chunks are joined in order at the source fps, "
        "and the original video's audio is added. Pass the chunks from prepare_chunks with a "
        "`video` (URL or path of the generated result) added to each."
    ),
    "input_schema": {
        "type": "object",
        "required": ["chunks", "out_path"],
        "properties": {
            "chunks": {
                "type": "array", "minItems": 1,
                "items": {"type": "object", "required": ["video", "duration", "padded_duration"],
                          "properties": {"video": {"type": "string"}, "duration": {"type": "number"},
                                         "padded_duration": {"type": "number"},
                                         "index": {}, "start": {}, "end": {}, "media_length_ms": {},
                                         "source_path": {}, "guide_path": {}}},
            },
            "audio_from": {"type": "string", "description": "Original video whose audio is added (path or URL)."},
            "fps": {"type": "number", "description": "Output fps; default: audio_from's fps, else 30."},
            "out_path": {"type": "string"},
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {"status": {"const": "finished"}, "local_path": {"type": "string"},
                       "media": {"type": ["object", "null"]}},
    },
}


def plan(duration: float, cuts: list[float], max_s: float, min_s: float = MIN_SECONDS) -> list[tuple[float, float]]:
    """Chunk boundaries: each chunk <= max_s, on a cut where possible, no tiny remainders."""
    bounds, start = [0.0], 0.0
    while duration - start > max_s + 1e-6:
        window = [c for c in cuts if start + min_s - 1 < c <= start + max_s]
        end = max(window) if window else start + max_s
        if duration - end < 2:  # avoid a tiny last chunk: split the rest in half
            end = start + (duration - start) / 2
        bounds.append(end)
        start = end
    bounds.append(duration)
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def prepare(params: dict) -> dict:
    mode = params["mode"]
    max_s = params.get("max_seconds") or MODES[mode]
    if mode.endswith("guide") and not params.get("guide"):
        raise fal.ToolError("invalid_input", f"mode {mode} needs a guide video")
    info = fal.probe(params["source"])
    if not info or not info.get("duration"):
        raise fal.ToolError("invalid_input", "could not read the source video")
    fps, total = info["fps"], info["duration"]
    cuts = media.scene_cuts(params["source"])
    out_dir = Path(params["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks = []
    for i, (a, b) in enumerate(plan(total, cuts, max_s)):
        dur = b - a
        padded = max(MIN_SECONDS, math.ceil(dur - 1e-3))
        vf = f"fps={fps},tpad=stop_mode=clone:stop_duration={padded - dur:.3f}"
        paths = {}
        for kind in ("source", "guide"):
            if not params.get(kind):
                paths[kind] = None
                continue
            dest = out_dir / f"chunk{i:02d}_{kind}.mp4"
            media.run_ffmpeg(["-ss", f"{a:.3f}", "-i", params[kind], "-t", f"{dur:.3f}", "-vf", vf,
                              "-t", str(padded), "-an", "-c:v", "libx264", "-crf", "14",
                              "-pix_fmt", "yuv420p", str(dest)])
            paths[kind] = str(dest)
        chunks.append({"index": i, "start": round(a, 3), "end": round(b, 3), "duration": round(dur, 3),
                       "padded_duration": padded, "media_length_ms": padded * 1000,
                       "source_path": paths["source"], "guide_path": paths["guide"]})
    return {"status": "finished", "mode": mode, "cuts": cuts, "media": info, "chunks": chunks}


def assemble(params: dict) -> dict:
    chunks = params["chunks"]
    audio_from = params.get("audio_from")
    ref = fal.probe(audio_from) if audio_from else None
    fps = params.get("fps") or (ref or {}).get("fps") or 30
    with tempfile.TemporaryDirectory() as tmp:
        first = fal.probe(chunks[0]["video"]) or {}
        w, h = first.get("width"), first.get("height")
        if not (w and h):
            raise fal.ToolError("invalid_input", "could not read the first chunk video")
        args, parts = [], []
        for i, c in enumerate(chunks):
            local = fal.local_copy(c["video"], Path(tmp) / f"gen{i}.mp4")
            actual = (fal.probe(str(local)) or {}).get("duration") or c["padded_duration"]
            factor = c["padded_duration"] / actual
            args += ["-i", str(local)]
            parts.append(f"[{i}:v]setpts={factor:.6f}*PTS,fps={fps},trim=duration={c['duration']:.3f},"
                         f"setpts=PTS-STARTPTS,scale={w}:{h},setsar=1[v{i}]")
        parts.append("".join(f"[v{i}]" for i in range(len(chunks))) + f"concat=n={len(chunks)}:v=1:a=0[out]")
        maps = ["-map", "[out]"]
        if audio_from and ref and _has_audio(audio_from):
            args += ["-i", audio_from]
            maps += ["-map", f"{len(chunks)}:a", "-c:a", "aac", "-shortest"]
        out_path = Path(params["out_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        media.run_ffmpeg(args + ["-filter_complex", ";".join(parts), *maps,
                                 "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(out_path)])
    return {"status": "finished", "local_path": str(out_path), "media": fal.probe(str(out_path))}


def _has_audio(video: str) -> bool:
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index", "-of", "csv=p=0", video], capture_output=True, text=True)
    return bool(out.stdout.strip())
