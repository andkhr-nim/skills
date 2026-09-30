"""Tool: estimate_depth — per-frame depth video with Video Depth Anything (fal)."""

from __future__ import annotations

from pathlib import Path

import fal_common as fal

ENDPOINT = "fal-ai/depth-anything-video"

TOOL = {
    "name": "estimate_depth",
    "description": (
        "Estimate per-frame depth for a video with Video Depth Anything and return a depth "
        "video (grayscale by default: near = bright), temporally consistent, same size and "
        "frame count as the input. Used as the base of the colour-coded guide video. "
        "Cost: a few cents per clip."
    ),
    "input_schema": {
        "type": "object",
        "required": ["video"],
        "properties": {
            "video": {"type": "string", "description": "Local path or URL of the source video."},
            "model": {"type": "string", "enum": ["VDA-Small", "VDA-Base", "VDA-Large"],
                      "default": "VDA-Base"},
            "colormap": {"type": "string",
                         "enum": ["grayscale", "turbo", "inferno", "magma", "viridis"],
                         "default": "grayscale"},
            "out_path": {"type": "string", "description": "If set, the depth video is also downloaded here."},
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "status": {"const": "finished"},
            "video_url": {"type": "string"},
            "depth_url": {"type": "string"},
            "local_path": {"type": ["string", "null"]},
            "model": {"type": "string"}, "colormap": {"type": "string"},
            "request_id": {"type": "string"},
            "media": {"type": ["object", "null"]},
        },
    },
}


def run(params: dict) -> dict:
    key = fal.get_secret()
    model = params.get("model", "VDA-Base")
    colormap = params.get("colormap", "grayscale")
    video_url = fal.ensure_url(params["video"], key=key)
    result, request_id = fal.run(ENDPOINT, {"video_url": video_url, "model": model,
                                            "colormap": colormap}, key=key)
    depth_url = result["video"]["url"]
    local = fal.download(depth_url, params["out_path"]) if params.get("out_path") else None
    return {"status": "finished", "video_url": video_url, "depth_url": depth_url,
            "local_path": str(local) if local else None, "model": model, "colormap": colormap,
            "request_id": request_id, "media": fal.probe(depth_url)}
