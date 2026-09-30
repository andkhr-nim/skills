# tools

Tools for the ad-remix pipeline that Nim doesn't provide yet, shaped like Nim tools:
each one describes itself (name, description, JSON input/output schema), takes JSON input
and returns JSON. Python 3.10+, standard library only; `build_guide_video` needs ffmpeg,
`media` info needs ffprobe.

| Tool | Runs on | Returns |
|---|---|---|
| `segment_video` | fal `fal-ai/sam-3/video` (+ ffmpeg) | one mask video per object (`mask_url`; several `prompts` per object are merged) and a text `health` report: presence per shot, drop-outs, jumps, overlaps |
| `estimate_depth` | fal `fal-ai/depth-anything-video` | depth video (`depth_url`, optional `local_path`) |
| `build_guide_video` | local ffmpeg | tinted-depth guide video (`local_path`, colour `legend`) |
| `prepare_chunks` | local ffmpeg | source (+ guide) chunks for a Seedance mode, on shot cuts where possible, padded to whole seconds, with `media_length_ms` |
| `assemble_chunks` | local ffmpeg | generated chunks retimed, trimmed, joined, with the original audio |

## Calling

```bash
python tools/cli.py list                       # names + descriptions
python tools/cli.py describe segment_video     # full spec with input/output schema
python tools/cli.py run segment_video '{"video": "clip.mp4", "objects": [{"name": "a", "prompt": "woman with red hair"}], "out_dir": "masks"}'
python tools/cli.py run estimate_depth @params.json
```

From Python: `cli.call("segment_video", {...})` returns the same dict.

Results always have `"status": "finished"` or `"status": "failed"`:

```json
{"tool": "estimate_depth", "status": "failed",
 "error": {"code": "content_policy_violation", "message": "...", "reason": "partner_validation_failed", "request_id": "..."}}
```

Error codes: `invalid_input`, `file_not_found`, `unknown_tool`, `missing_secret`,
`missing_dependency`, `unauthorized`, `content_policy_violation`, `file_download_error`,
`provider_error`, `network_error`, `download_error`, `timeout`, `ffmpeg_error`,
`internal_error`.

Chunk modes (max seconds of source per run): `seedance-2-guide` 7, `seedance-2` 15,
`seedance-2.5-guide` 15, `seedance-2.5-edit` 30; minimum 4.

Inputs can be local paths (uploaded to fal first) or URLs; returned URLs can be passed
straight to the next tool.

## Secrets

`FAL_KEY` comes from the environment or the nearest `.env` (`FAL_KEY=...`), via
`fal_common.get_secret()`, which is the single place to switch to the platform's secret
system. The key is never printed.

## Adding a tool

Write a spec dict (`name`, `description`, `input_schema`, `output_schema`) and a
`run(params) -> dict`; raise `fal_common.ToolError(code, message)` on failure; register it
in `REGISTRY` in `cli.py`. Shared helpers: `fal_common` (secrets, fal, downloads, probe),
`media` (ffmpeg: cut detection, frame reads), `mask_health`.

## Addendum (concept): one composite guide tool

Instead of Claude calling `segment_video`, `estimate_depth` and `build_guide_video` one by
one, the skill could call a single `build_guide_video` that takes the source video and the
objects to track (`[{name, prompt, color}]`) and runs segmentation, depth and colouring
internally, returning the guide URL, the colour legend and mask QA stats (coverage, gaps,
jumps per object and shot). Fewer calls and round trips, no intermediate URLs for Claude to
juggle, and nothing to accidentally show. The trade-off: mask problems (SAM switching or
dropping people at cuts and exits) can only be fixed if the tool also accepts corrections,
e.g. per-object mask edits (clear a range, hand over at a frame) or externally supplied mask
URLs, and returns the mask URLs alongside the guide so Claude can run `mask_crops` /
`edit_mask` and rebuild. The separate tools would stay available for that fallback.
