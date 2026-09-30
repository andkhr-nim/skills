---
name: nim-ad-remix-seedance
description: Use when the user wants to remake an existing ad or video with Nim Seedance (2 or 2.5) — new people, new product, new background or setting, new outfits, new on-screen text, or any other change — while keeping the original shots, timing and camera. Triggers include "replace the person in this ad", "swap the actors", "replace these people with these photos", "put my product into this ad", "same ad, new cast", "move this ad to another location", "change the background", "dress them in X", "remake this ad". Handles one or many people, products, settings and text. Inputs are a source video, reference images (people, product, optional style), and optional extra directions. Runs on Nim: segmentation, depth and a colour-coded guide video, then Seedance. The user gets a link to the result. NOT for making ads from scratch without a source video.
---

# Ad Remix with Seedance (Nim)

Remake an existing video: same shots, cuts, camera and timing, but with new people, a new
product, a new setting, new outfits, new text, or whatever else the user asks for.

Seedance binds new identities by **prominence and similarity**, not by prompt wording: with
several people it replaces the most prominent one and mixes up the rest. The fix is a second
input video, a **guide**: a depth map with each person or object tinted in its own colour,
so the model sees what is what.

## Tools

- `media_upload`, `models_explore`, `generate_image`, `generate_video`,
  `get_generation_status`
- `segment_video`: tracks named people/objects through a video → one mask per object, plus
  a text health report per object
- `estimate_depth`: depth video of the source
- `build_guide_video`: depth + masks + colours → the guide video and its colour legend
- `prepare_chunks`: splits source (and guide) into runs that fit the chosen Seedance mode
- `assemble_chunks`: joins the generated runs and adds the original audio

Everything the pipeline produces (masks, depth, guide, the result) stays on Nim and moves
between tools as URLs. Deliver the result as a link; don't download it or write pipeline
files into the user's project. Looking at the original video locally (ffprobe, frames) is
fine.

## 1. Understand and ask once

Upload the original video and the references (`media_upload`). Look at the source (size,
duration, cuts, who appears when, products, text) and at every reference image. Then ask
everything in **one message**, with a recommended default each:

1. **Model:** Seedance **2** (recommended: accepts the guide video) or **2.5**.
2. **Resolution:** 480p / 720p / 1080p. Offer a cheaper test first: the full video at 480p,
   or a ~5s cut of the hardest part at 480p.
3. **Who and what replaces whom** (judge identities from full-size frames; the same person
   can look like two people at different distances).
4. **Outfits:** keep the source clothes or take them from the reference photos.
5. **Setting / background** and any **extras** (costumes, props, style, mood).
6. **On-screen text:** keep, remove, or replace.

Warn about visible risks (cross-gender swaps, tiny background people, alcohol/tobacco/vape,
famous people or film footage), quote the cost, and wait for a go-ahead.

## 2. Build the guide

`segment_video` with one object per person/product that changes, described by stable,
distinctive traits (hair works best; shared clothing merges people); give an object a few
`prompts` if one description may not catch it everywhere. Read the health report: an object
present where it shouldn't be, sudden jumps, or overlaps with another object usually mean
SAM switched to someone else; drop-outs mean it lost the object. Retry with more specific
or additional prompts before building the guide.

`estimate_depth` on the source, then `build_guide_video` with one colour per object (later
masks are drawn on top where they overlap). Keep the legend for the prompt. Skip the guide
for a single simple swap with Seedance 2.5.

## 3. Generate

Always `models_explore` → `get` the model first and use only params in its contract.

| | Seedance 2 Advanced (default) | Seedance 2.5 Advanced |
|---|---|---|
| How | `referenceVideos: [source, guide]` + `fileInputs` | `sourceVideo` + `fileInputs` (edit mode, no guide) |
| Use for | remixes with 2+ people, products, settings, outfits | a single simple swap |
| Limits | 15s of video combined (source + guide) → ~7s of source per run; `mediaLength` and `requestedAspectRatio` required; up to 9 images | 4–30s, ≥854x480; up to 30 images |

When the source is longer than one run allows, use `prepare_chunks` with the mode
(`seedance-2-guide`, `seedance-2`, `seedance-2.5-guide`, `seedance-2.5-edit`): it splits
source and guide at the same times, on shot cuts where possible, and gives each chunk's
`media_length_ms`. Generate every chunk with the same references and prompt, in parallel.
Poll `get_generation_status` in the background and keep each returned `mediaUrl`. A
`failed` status with no cause: retry once, then report the `promptId`. Then
`assemble_chunks` with the chunk list (each with its result `video`) and the original as
`audio_from`; use it for single runs too, to restore the original audio.

References: pass the person and product photos as they are. Seedance tends to copy the
outfit a photo shows, so when clothes should be kept, say so explicitly in the prompt.
Optional keyframes (`generate_image` on a source frame) help for hard identities and big
style changes; text-only references also work. Don't make keyframes of branded end cards
or logos.

## Prompt

Refer to inputs as `@Video1` (source), `@Video2` (guide), `@Image1`… in `fileInputs` order.
Put the text instruction near the top.

```text
Edit @Video1. @Video2 is a depth map of @Video1 with exactly the same timing: each person
and object is tinted in its own colour so you know what is what. Do not copy the colours,
the tint or the depth look from @Video2 into the output.
TEXT: [keep all on-screen text unchanged | remove all text | replace with: "..."]. No other
text or watermarks.

Keep every shot, cut, camera move, gesture and action of @Video1. Change:
- RED in @Video2 ([original: hair, clothes, role]) -> the [man/woman] in @Image1: [concrete
  traits]; [kept clothes | new outfit].
- GREEN ...
- BLUE in @Video2 (the [old product]) -> the product in @Image3: [shape, colours, label by
  look]. Hands handle it the same way.
- SETTING: [new location, ground, sky, light].
- EXTRAS: [costumes, style, mood as concrete visual instructions].
Each colour always maps to the same person or object in every frame.

The photos provide identity [, wardrobe] and the product only. Adults only.
Throughout the video, characters with completely identical appearance, clothing, and
accessories are prohibited. Do not generate duplicate avatars or a twin effect.
```

Describe people by concrete traits (hair, skin, facial hair, marks with side), products by
look (don't quote small print), and extra directions as visual instructions.

## 4. Review and deliver

Look at result frames next to the source at key moments (group shots, product shots,
cuts). Report a short table: mapping, identity, outfits, product, setting/extras, timing,
text, audio, and "motion not verified" if you only checked stills. Deliver the result link
and the real cost. For a real defect, say what would fix it and what it costs, then ask
once; never rerun without approval.

Known limits: burned-in captions and logos are usually kept even when told to change;
small or background people are often left unchanged. Seedance doesn't keep the original
audio; `assemble_chunks` adds it back.

## Content filter

Jobs can be rejected after minutes of processing. Known triggers: famous people, film or
TV footage (ask for another source; don't work around it); alcohol, tobacco or vape and real
brands, especially in keyframes or text; people who could read as minors; revealing outfits.
Warn before running; on a rejection explain the likely trigger instead of retrying.

## Cost

Nim credits from the contract and `estimatedCreditCost` (reference videos add a duration
charge). Seen at 480p for a 7s chunk with source + guide: Seedance 2 ≈ 185, Seedance 2.5 ≈
280. Keyframes ≈ 25 each. Quote a range first; report the real charge.
