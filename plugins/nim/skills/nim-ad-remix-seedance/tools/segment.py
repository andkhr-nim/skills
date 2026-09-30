"""Tool: segment_video — track people/objects through a video with SAM 3 (fal)."""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import fal_common as fal
import mask_health
import media

ENDPOINT = "fal-ai/sam-3/video"

TOOL = {
    "name": "segment_video",
    "description": (
        "Track one or more people or objects through a video with SAM 3 and return one "
        "black/white mask video per object (white = object), same size and frame count as "
        "the input. Each object can have several prompts; their masks are merged. Objects "
        "and prompts run in parallel. Describe objects by stable, distinctive traits (hair "
        "works best; clothing shared by several people merges them). Returns a text health "
        "report per object (presence per shot, drop-outs, sudden jumps, overlaps between "
        "objects): jumps and overlaps usually mean SAM switched to another person, drop-outs "
        "that it lost the object; retry with more specific or additional prompts. "
        "Cost: about $0.005 per 16 frames per prompt."
    ),
    "input_schema": {
        "type": "object",
        "required": ["video", "objects"],
        "properties": {
            "video": {"type": "string", "description": "Local path or URL of the source video."},
            "objects": {
                "type": "array", "minItems": 1, "maxItems": 8,
                "items": {
                    "type": "object", "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "description": "Short id used in the result."},
                        "prompt": {"type": "string", "description": "Text description of the object."},
                        "prompts": {"type": "array", "minItems": 1, "maxItems": 4,
                                    "items": {"type": "string"},
                                    "description": "Several descriptions of the same object; masks are merged."},
                    },
                },
                "description": "Each object needs `prompt` or `prompts`.",
            },
            "detection_threshold": {"type": "number", "minimum": 0, "maximum": 1,
                                    "description": "Lower finds more but less precisely. Default 0.5."},
            "health": {"type": "boolean", "default": True,
                       "description": "Return the mask health report (needs ffmpeg)."},
            "out_dir": {"type": "string",
                        "description": "If set, masks are also downloaded here as mask_<name>.mp4."},
        },
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "status": {"const": "finished"},
            "video_url": {"type": "string"},
            "media": {"type": ["object", "null"]},
            "objects": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "prompts": {"type": "array"},
                "mask_url": {"type": "string"}, "local_path": {"type": ["string", "null"]},
                "request_ids": {"type": "array"}}}},
            "health": {"type": ["object", "null"], "properties": {
                "shots": {"type": "array"}, "objects": {"type": "object"}, "overlaps": {"type": "array"}}},
        },
    },
}


def _merge_masks(urls: list[str], key: str) -> str:
    """OR several mask videos into one and upload it; returns the merged mask URL."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "merged_mask.mp4"
        chain, last = [], "[0:v]format=gray[m0]"
        chain.append(last)
        for i in range(1, len(urls)):
            chain.append(f"[m{i - 1}][{i}:v]blend=all_mode=lighten,format=gray[m{i}]")
        args = sum([["-i", u] for u in urls], [])
        args += ["-filter_complex", ";".join(chain), "-map", f"[m{len(urls) - 1}]",
                 "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", str(out)]
        media.run_ffmpeg(args)
        return fal.upload(out, key=key)


def run(params: dict) -> dict:
    objects = []
    for obj in params["objects"]:
        prompts = obj.get("prompts") or ([obj["prompt"]] if obj.get("prompt") else [])
        if not prompts:
            raise fal.ToolError("invalid_input", f"object {obj['name']} needs `prompt` or `prompts`")
        objects.append({"name": obj["name"], "prompts": prompts})
    names = [o["name"] for o in objects]
    if len(set(names)) != len(names):
        raise fal.ToolError("invalid_input", "object names must be unique")

    key = fal.get_secret()
    video_url = fal.ensure_url(params["video"], key=key)  # upload once, reuse everywhere
    threshold = params.get("detection_threshold")

    def sam(prompt: str) -> tuple[str, str]:
        payload = {"video_url": video_url, "prompt": prompt, "apply_mask": False}
        if threshold is not None:
            payload["detection_threshold"] = threshold
        result, request_id = fal.run(ENDPOINT, payload, key=key)
        return result["video"]["url"], request_id

    jobs = [(o["name"], p) for o in objects for p in o["prompts"]]
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        done = list(pool.map(lambda j: sam(j[1]), jobs))

    out_dir = Path(params["out_dir"]) if params.get("out_dir") else None
    results = []
    for o in objects:
        parts = [done[i] for i, (n, _) in enumerate(jobs) if n == o["name"]]
        mask_url = parts[0][0] if len(parts) == 1 else _merge_masks([u for u, _ in parts], key)
        local = fal.download(mask_url, out_dir / f"mask_{o['name']}.mp4") if out_dir else None
        results.append({"name": o["name"], "prompts": o["prompts"], "mask_url": mask_url,
                        "local_path": str(local) if local else None,
                        "request_ids": [r for _, r in parts]})

    info = fal.probe(video_url)
    health = None
    if params.get("health", True) and info:
        health = mask_health.report(video_url, {r["name"]: r["mask_url"] for r in results}, info)
    return {"status": "finished", "video_url": video_url, "media": info,
            "objects": results, "health": health}
