"""ffmpeg helpers shared by the tools: cut detection, small grayscale frame reads, encoding."""

from __future__ import annotations

import re
import subprocess

import fal_common as fal


def scene_cuts(video: str, threshold: float = 0.25) -> list[float]:
    """Times (s) where a new shot starts, by ffmpeg scene-change score."""
    ffmpeg = fal.require_binary("ffmpeg")
    out = subprocess.run(
        [ffmpeg, "-v", "info", "-i", video, "-vf", f"select='gt(scene,{threshold})',showinfo",
         "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    return [round(float(t), 3) for t in re.findall(r"pts_time:([0-9.]+)", out.stderr)]


def gray_frames(video: str, width: int, height: int) -> list[bytes]:
    """All frames of `video`, scaled to width x height, as 8-bit grayscale byte strings."""
    ffmpeg = fal.require_binary("ffmpeg")
    out = subprocess.run(
        [ffmpeg, "-v", "error", "-i", video, "-vf", f"scale={width}:{height},format=gray",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True)
    if out.returncode != 0:
        raise fal.ToolError("ffmpeg_error", out.stderr.decode("utf-8", "replace")[-500:])
    size = width * height
    return [out.stdout[i:i + size] for i in range(0, len(out.stdout) - size + 1, size)]


def run_ffmpeg(args: list[str]) -> None:
    ffmpeg = fal.require_binary("ffmpeg")
    done = subprocess.run([ffmpeg, "-v", "error", "-y", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise fal.ToolError("ffmpeg_error", done.stderr.strip()[-500:] or "ffmpeg failed")
