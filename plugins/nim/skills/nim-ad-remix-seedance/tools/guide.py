"""Tool: build_guide_video — tint each mask in its own colour over a grayscale depth video."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import fal_common as fal

COLORS = {  # colorchannelmixer weights: (rr, gg, bb, cross) — tint a gray value
    "red": "rr=1:rg=0:rb=0:gr=0.12:gg=0:gb=0:br=0.12:bg=0:bb=0",
    "green": "rr=0.12:rg=0:rb=0:gr=0:gg=1:gb=0:br=0:bg=0:bb=0.12",
    "blue": "rr=0.15:rg=0:rb=0:gr=0:gg=0.15:gb=0:br=0:bg=0:bb=1",
    "yellow": "rr=1:rg=0:rb=0:gr=0:gg=1:gb=0:br=0:bg=0:bb=0.1",
    "magenta": "rr=1:rg=0:rb=0:gr=0:gg=0.12:gb=0:br=0:bg=0:bb=1",
    "cyan": "rr=0.12:rg=0:rb=0:gr=0:gg=1:gb=0:br=0:bg=0:bb=1",
    "orange": "rr=1:rg=0:rb=0:gr=0:gg=0.55:gb=0:br=0:bg=0:bb=0.1",
}

TOOL = {
    "name": "build_guide_video",
    "description": (
        "Build the colour-coded guide video for a Seedance remix: a grayscale depth video "
        "with each mask tinted in its own colour (depth shading kept inside the tint), same "
        "size, fps and frame count as the depth/source video, no text. Pass it to Seedance "
        "as the second reference video and name the colours in the prompt. Runs locally "
        "with ffmpeg; inputs may be local paths or URLs."
    ),
    "input_schema": {
        "type": "object",
        "required": ["depth", "masks", "out_path"],
        "properties": {
            "depth": {"type": "string", "description": "Depth video (local path or URL)."},
            "masks": {
                "type": "array", "minItems": 1, "maxItems": len(COLORS),
                "items": {
                    "type": "object", "required": ["name", "mask", "color"],
                    "properties": {
                        "name": {"type": "string"},
                        "mask": {"type": "string", "description": "Black/white mask video (path or URL)."},
                        "color": {"type": "string", "enum": list(COLORS)},
                    },
                },
                "description": "Later masks are drawn on top of earlier ones where they overlap.",
            },
            "out_path": {"type": "string"},
            "shading": {"type": "boolean", "default": True,
                        "description": "Keep depth shading inside the tint (recommended)."},
            "width": {"type": "integer", "description": "Output width; default: depth video's."},
            "height": {"type": "integer", "description": "Output height; default: depth video's."},
            "fps": {"type": "number", "description": "Output fps; default: depth video's."},
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "status": {"const": "finished"},
            "local_path": {"type": "string"},
            "legend": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "color": {"type": "string"}}}},
            "media": {"type": ["object", "null"]},
        },
    },
}


def run(params: dict) -> dict:
    ffmpeg = fal.require_binary("ffmpeg")
    masks = params["masks"]
    colors = [m["color"] for m in masks]
    if len(set(colors)) != len(colors):
        raise fal.ToolError("invalid_input", "each mask needs a different colour")

    with tempfile.TemporaryDirectory() as tmp:
        depth = fal.local_copy(params["depth"], Path(tmp) / "depth.mp4")
        mask_paths = [fal.local_copy(m["mask"], Path(tmp) / f"mask_{i}.mp4")
                      for i, m in enumerate(masks)]
        info = fal.probe(str(depth)) or {}
        w = params.get("width") or info.get("width")
        h = params.get("height") or info.get("height")
        fps = params.get("fps") or info.get("fps")
        if not (w and h and fps):
            raise fal.ToolError("invalid_input", "could not read size/fps of the depth video; pass width, height, fps")

        lut = "val*0.7+76" if params.get("shading", True) else "200"
        n = len(masks)
        parts = [f"[0]scale={w}:{h},fps={fps},format=gray,split={n + 1}[g0]" +
                 "".join(f"[g{i + 1}]" for i in range(n))]
        for i, m in enumerate(masks, start=1):
            parts.append(f"[g{i}]lut=y='{lut}',format=rgb24,colorchannelmixer={COLORS[m['color']]}[t{i}]")
            parts.append(f"[{i}]scale={w}:{h},fps={fps},format=gray,lut=y='if(gt(val,127),255,0)'[m{i}]")
            parts.append(f"[t{i}][m{i}]alphamerge[a{i}]")
        chain, last = [], "[g0f]"
        parts.append("[g0]format=rgb24[g0f]")
        for i in range(1, n + 1):
            out = f"[o{i}]"
            chain.append(f"{last}[a{i}]overlay=shortest=1{out}")
            last = out
        parts += chain
        parts.append(f"{last}format=yuv420p[out]")

        out_path = Path(params["out_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg, "-v", "error", "-y", "-i", str(depth)]
        for p in mask_paths:
            cmd += ["-i", str(p)]
        cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-r", str(fps),
                "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-an", str(out_path)]
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0:
            raise fal.ToolError("ffmpeg_error", done.stderr.strip()[-500:] or "ffmpeg failed")

    return {"status": "finished", "local_path": str(out_path),
            "legend": [{"name": m["name"], "color": m["color"]} for m in masks],
            "media": fal.probe(str(out_path))}
