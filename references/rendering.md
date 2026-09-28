# Canvas, timing, and measurement

## Project module contract

Author a native JavaScript module at `renderEntry` (normally `src/main.js`). Export `async prepare(env)` for fixed resource preparation and synchronous `drawFrame(ctx, env)` for a complete frame. The renderer creates an empty native-size Canvas; it supplies no background, layout, animation, font choice, or story.

Frame environment fields are `outputFrame` (legal integer), `sampleFrame` (normally the same; internal effects may evaluate fractional time), `timeSeconds`, `width`, `height`, `fps:{num,den}`, `seed`, `timeline`, and `assets` (read-only lookup exposing `get(id)`, `has(id)`, `keys()`). Prepare must resolve only when its fixed resources are ready, and throw on failure. Use declared project assets; decode images and verify fonts before measuring them. Local fonts can be declared in project config, for example `{family:"Arial",source:"local",name:"Arial",weight:"400"}` when that actual font is available. A successful generic fallback does not verify a requested font.

The browser exports `window.seekFrame(F)` after readiness. It only accepts integer output frames in `[0,N)`. Draw from absolute frame/time, fixed data, and fixed assets. Do not use wall-clock time, timers, unseeded randomness, CSS playback, a video's approximate seek, or previous frame state to determine pixels. A random-access frame must look the same as its sequential counterpart.

The tool resets Canvas state and pixels before each frame; still pair component `save()`/`restore()` calls and begin paths deliberately. Keep exactly one owner for a continuous transition. Shared objects require explicit continuity, rather than accidentally restarting each shot.

## Time contract

Use 0-based frames and half-open shot intervals `[f0,f1)`. For fps `p/q`, `t(F)=F*q/p`, duration `N*q/p`, and sample index `round(F*q*sampleRate/p)`. Compute every anchor from absolute time. Never repeatedly add a rounded beat or sample increment. `timeline.json` owns shots and events; an event has exactly one of `frame`, `seconds`, or `beat`, plus `basis` and `source`. A beat needs the project's actual BPM and offset.

Create chooses its CFR time base; state the realized duration if a requested number of seconds requires frame quantization. Match uses the actual presentation frames and PTS. VFR, HDR, non-square pixels, rotation and timestamp anomalies require explicit handling, not a silent conversion followed by a strict 1:1 claim. See the analyzer's report for unsupported conditions. Internal subframe sampling does not double output fps or frame count.

## CLI and actual-output checks

`node render.mjs --project P --revision R stills 0,1,2` captures at most 15 frames per review request; `full 0 N` renders `[0,N)`. `compare` requires actual match reference data. Frame paths are `out/R/frames/000000.png`. Use the frozen source snapshot and review the renderer's input fingerprint, hashes, environment and determinism result. Same-byte output reuse is allowed; different output needs a new revision.

Test repeated, shuffled, and reverse frame requests in the recorded browser/font environment. Pixel stability in one environment is not a portability promise across font libraries or operating systems. Missing assets, missing fonts, script exceptions and frame bounds must fail visibly. Recheck affected frames after changes; there is no validity in caching merely because a PNG exists.

For measurements, use `measure.py track --project P --object ID --range A:B --roi X,Y,W,H --color R,G,B`, adding the actual tolerance, minimum component size and `--component largest|all` if needed. RGB targets come from inspected pixels. `--full-visible-range` explicitly asserts the selected range covers that object's full visible interval; do not set it without evidence. Inspect the ROI and segmentation first. Report valid coverage, missing/occluded samples, maximum normalized errors and their frame indices. Do not repair evidence by interpolating unknown values and relabelling them measured. Reference cuts and locked events have zero-frame tolerance; object position/size defaults to 1% of its corresponding axis. Check rendered pixels, not just matching coordinate tables. Synthetic-fixture agreement tests engineering, not the ability to reconstruct an unseen real film.

`measure.py check` writes numericStatus, per-axis maxima, frame locations and coverage in `review/measurements.json`; it does not automatically prove semantic object identity, event meaning, content quality or perceptual reference fidelity. For create, optional project `measurementChecks` can bound an explicitly selected object's pixel geometry; no detector means no invented numeric content pass. The analyzer currently rejects unsupported VFR/HDR/rotation/non-square-pixel input instead of silently normalizing it. An authorized normalization must be done explicitly and retain the mapping as a changed contract.

For effects, preserve units. Pixels/frame is frame-rate and size dependent. Gaussian blur is not directional exposure. Temporal accumulation must normalize premultiplied alpha in an explicitly chosen linear-light space, avoid hard-cut contamination, and preserve discrete content events. Repeated source-over ghosts are not automatically a physically valid average. Only claim an effect's quality where that actual implementation has been checked.

Encode at the exact rational fps and declared color policy. Reject incompatible odd dimensions rather than cropping. Inspect the encoded video's decoded frame count, dimensions, effective time base and key pixels. Color conversion must actually transform RGB to limited-range BT.709, not merely add tags. PNG validation alone does not validate an MP4.

The renderer serves only hash-listed modules and declared production assets through an intercepted virtual browser origin. It opens no HTTP server or listening port and rejects external requests. Chromium sandboxing stays enabled. Preparation and per-frame calls have a 30-second timeout. Production scripts reject changed tool hashes for a frozen snapshot: restore the matching tool version or explicitly create a new revision. Render run evidence is retained separately; identical full-frame retries do not rewrite the original summary.
