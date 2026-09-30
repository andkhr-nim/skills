"""Compact, text-only health report for a set of object masks over one source video.

Lets an agent judge masks without looking at them: per shot presence and size, drop-outs,
sudden jumps (the typical sign of SAM switching to another person), and overlaps between
objects.
"""

from __future__ import annotations

import media

GRID_W = 64            # masks are analysed at this width (height keeps the aspect)
PRESENT = 0.002        # object counts as present if it covers >= 0.2% of the frame
JUMP_DIST = 0.20       # centroid move between frames, as a fraction of frame width/height
JUMP_AREA = 2.5        # area ratio between consecutive frames
OVERLAP = 0.30         # intersection / smaller mask
MIN_GAP_S = 0.3        # report drop-outs at least this long
MAX_EVENTS = 6


def _stats(frame: bytes, w: int, h: int) -> tuple[float, float, float, set[int]]:
    idx = {i for i, v in enumerate(frame) if v > 127}
    if not idx:
        return 0.0, 0.0, 0.0, idx
    xs = sum(i % w for i in idx) / len(idx) / w
    ys = sum(i // w for i in idx) / len(idx) / h
    return len(idx) / (w * h), xs, ys, idx


def _ranges(frames: list[int], fps: float) -> list[list[float]]:
    out, start, prev = [], None, None
    for f in frames:
        if start is None:
            start = prev = f
        elif f == prev + 1:
            prev = f
        else:
            out.append([start, prev])
            start = prev = f
    if start is not None:
        out.append([start, prev])
    return [[round(a / fps, 2), round((b + 1) / fps, 2)] for a, b in out]


def report(video: str, masks: dict[str, str], media_info: dict) -> dict:
    w0, h0, fps = media_info["width"], media_info["height"], media_info["fps"] or 30
    w, h = GRID_W, max(2, round(GRID_W * h0 / w0 / 2) * 2)
    cuts = media.scene_cuts(video)
    n_frames = media_info.get("frames") or 0
    bounds = [0.0, *cuts, (n_frames / fps) if n_frames else float("inf")]
    shots = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]

    per_obj = {name: [_stats(f, w, h) for f in media.gray_frames(url, w, h)]
               for name, url in masks.items()}
    result = {"shots": [[round(a, 2), round(b, 2)] for a, b in shots], "objects": {}, "overlaps": []}

    for name, frames in per_obj.items():
        present = [i for i, s in enumerate(frames) if s[0] >= PRESENT]
        shot_rows, gaps, events = [], [], []
        for a, b in shots:
            idx = [i for i in range(len(frames)) if a <= i / fps < b]
            here = [i for i in idx if frames[i][0] >= PRESENT]
            if not idx:
                continue
            shot_rows.append({"shot": [round(a, 2), round(b, 2)],
                              "present": round(len(here) / len(idx), 2),
                              "area": round(sum(frames[i][0] for i in here) / len(here), 3) if here else 0})
            if here:  # drop-outs inside a shot where the object does appear
                missing = [i for i in range(here[0], here[-1] + 1) if frames[i][0] < PRESENT]
                gaps += [g for g in _ranges(missing, fps) if g[1] - g[0] >= MIN_GAP_S]
            for i, j in zip(here, here[1:]):
                if j != i + 1 or j <= idx[0] + 1:  # consecutive frames, not across the cut
                    continue
                (a1, x1, y1, _), (a2, x2, y2, _) = frames[i], frames[j]
                moved = max(abs(x2 - x1), abs(y2 - y1))
                ratio = max(a1, a2) / max(min(a1, a2), 1e-6)
                if moved >= JUMP_DIST or ratio >= JUMP_AREA:
                    events.append({"t": round(j / fps, 2), "moved": round(moved, 2), "area_ratio": round(ratio, 1)})
        result["objects"][name] = {
            "present_ranges": _ranges(present, fps),
            "per_shot": shot_rows,
            "dropouts": gaps[:MAX_EVENTS],
            "jumps": events[:MAX_EVENTS],
        }

    names = list(per_obj)
    for x in range(len(names)):
        for y in range(x + 1, len(names)):
            fa, fb = per_obj[names[x]], per_obj[names[y]]
            hits = [i for i in range(min(len(fa), len(fb)))
                    if fa[i][3] and fb[i][3]
                    and len(fa[i][3] & fb[i][3]) / min(len(fa[i][3]), len(fb[i][3])) >= OVERLAP]
            if hits:
                result["overlaps"].append({"objects": [names[x], names[y]], "ranges": _ranges(hits, fps)[:MAX_EVENTS]})
    return result
