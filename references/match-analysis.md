# Match: measurements before production

Read this for every match task. This contract describes evidence, not a creative template. The Agent authors the actual shot breakdown, content, geometry and sounds from this reference. No fixed number of shots, colors, tempo, narrative slots, agent roles or model IDs is prescribed. Create does not need this contract.

## Reference and coverage

Run `python3 scripts/analyze.py --project P`. It decodes **every presentation frame** to lossless PNG at native dimensions, preserves frame hashes and PTS in `ref/index.json`, and computes adjacent-frame native RGB mean absolute difference. Contact sheets are navigation aids, not the measurement source. Use PNG arrays for pixel measurement, never resized/JPEG overview pixels.

Use the actual rational fps and presentation-frame count. The analyzer refuses unsupported VFR/HDR/rotation/non-square pixels. If the user authorizes normalization, retain the original, explicit PTS/frame mapping and lossless normalized reference; label the result normalized-reference alignment. Do not call it original-PTS identity. Extracted frames and reference crops are analysis inputs only. They must not become production backgrounds, textures or composited screenshots unless the user explicitly authorizes that use.

There are three distinct coverages: decoded frames, measured properties, and actual visual review. Extracting all frames does not mean all objects have been measured or the film watched. Report each separately. Inspect the entire reference at normal speed when available, inspect frame ranges for transitions/events, and retain an honest list of viewed frames/ranges. A contact sheet does not establish normal-speed review or listening.

## Mechanical tools

All commands below require `--project P` and a new project-relative `--out analysis/...json`. Evidence files are immutable: changing parameters needs a new path. Coordinates and sizes are native pixels; ranges are zero-based `[A,B)`.

| Command | Actual output and interpretation |
| --- | --- |
| `match.py cuts --minimum M --prominence P --radius R --distance D` | Local MAD peaks, including flat peaks, neighborhood prominence and nonmaximum suppression. Thresholds are tunable detector parameters, not filmmaking rules. Review every candidate with its neighboring frames; inspect gradual transitions and low-contrast changes separately. Peaks can be motion/flash; no peak is not proof of no cut. |
| `match.py color --frame F --roi X,Y,W,H` | Actual RGB8 median, mean, 5th/95th percentiles and sample count. Name project color roles only after inspecting the ROI. Gradients, antialiasing and decoded color conversion remain relevant. One patch is not the whole palette. |
| `match.py ink --frame F --roi X,Y,W,H --background R,G,B --tolerance T --glyphs TEXT` | Actual foreground bounding box and pixel count using RGB distance from a selected background. Verify the isolated glyph, threshold and effects. Foreground height is cap height only under appropriate glyph/transform conditions. |
| `match.py ink ... --cap-ratio R --font-basis DESCRIPTION` | Optional derived font-size estimate using a measured candidate font's cap-height/em ratio. Source font identity/size cannot be uniquely recovered from cap height. Missing font stays unknown; fallback loading is not verification. |
| `measure.py track --object ID --range A:B --roi X,Y,W,H --color R,G,B --tolerance T --min-pixels K --component largest` | Native pixel color segmentation, four-connected flood fill, measured ink bounds and pixel counts. Inspect foreground selection and object identity. Supports measuring position and size. |
| `match.py track --object ID --range A:B --template-frame F --template X,Y,W,H --search X,Y,W,H --anchor X,Y --minimum S --margin M` | Per-frame grayscale zero-mean normalized cross-correlation using FFT and integral sums. Anchor is relative to template and can identify the cursor tip. Reports score, competing peak margin, actual image hash and null for uncertainty. **Template window size is not measured object size.** |
| `match.py camera --tracks analysis/a.json,analysis/b.json --anchor-frame F --residual PX` | Uniform scale/translation fit to at least two actual point tracks. Per-frame maximum/RMS residuals and null for disagreement/missing data. Camera interpretation remains inferred: object motion can produce the same image-space movement. Declare source tracks as evidence too. |
| `match.py audio-candidates` | Reference spectral-flux onset candidates, RMS and autocorrelation period candidates. No fixed BPM or score. Period candidates can be harmonics; beat phase/downbeat, voice transcript/pitch, all SFX, drops and perceived synchronization remain unverified. No external STT/TTS/music services. |

Fixed-template tracking does not search scale/rotation/deformation. Split verified templates into new measured ranges when appearance changes; report unmeasured gaps as null. Do not fill gaps, deduce absence from failed tracking, or rename estimates measured. For cursor tracking, inspect its tip anchor and distinguish pointer shape/state changes; record offscreen/occlusion explicitly. For camera, two landmarks provide weak ambiguity checks; use more independent landmarks when possible. A successful fit alone does not prove real camera movement.

## Authored analysis contract

Create `analysis/match.json` and copy the completed `ref/index.json` to a new path inside `analysis/`. Use schemaVersion `1`, `referenceSha256`, an exact copy of project `output`, and `referenceIndex:{path,sha256}`. Paths and evidence hashes are project-relative; do not put actual frame images in the analysis snapshot. Copy reference measurements into `analysis/` and retain their source frame hashes. Production rendering cannot request analysis files as picture assets.

Every section below is an object with `state`, specific `reason`, `items` array and `evidence:[{path,sha256}]`. States: `measured`, `reviewed`, `inferred`, `unknown`, `not_applicable`. Evidence documents are JSON under analysis, with `kind`, `referenceSha256`, actual method and observations. Do not declare a state merely to satisfy the parser.

| Section | Required authored contents |
| --- | --- |
| `cuts` | Every candidate classified with `frame`, `decision:hard_cut|transition|flash|motion|no_cut`, and observed reason. Cite candidate report plus actual review. Confirmed hard cuts must start shots. Unresolved candidate reviews prevent readiness. |
| `shots` | Ordered `id,f0,f1,content,entrance,exit,details`; cover `[0,N)` exactly and match `timeline.json.shots`. Details describe actual scene layers, trajectories, masks, typography, transitions and relationships, with evidence or explicit uncertainty. Continuous video may have one shot and multiple internal state changes. |
| `components` | Unique `id`, `kind`, `critical:boolean`, `visibleRanges:[[A,B]]`. Critical components also declare `properties:[x,y,w,h]` as applicable and `tracks:[path]`. Every required property in every declared visible frame needs a non-null measured track observation before production readiness. Inventory all material visual components; declaring them noncritical is not permission to ignore them. |
| `text` | Appearance-ordered `id,text,component,f0,f1`. Transcribe all visible text verbatim, including UI, filenames, punctuation and deliberate clipping. `characterEvents:[{frame,count}]` records actually observed typing changes. Verify wrapping, baseline, spacing and crop. Incomplete transcription/timing stays unknown. |
| `colors` | Project roles and `measurement` paths to actual `kind:color` ROI reports. Colors are reference/project facts, not installed style tokens. |
| `typography` | Relevant text/glyph identity, `measurement` to `kind:ink`, candidate font/weight, measured cap/ink bounds, wrapping/baseline and limits. Use unknown/inferred if font size/identity or required coverage remains unresolved. |
| `cursor` | Actual tip trajectories via `measurement` track paths, visible/occluded intervals, anchor calibration and cursor states. Full visible interval, not selected convenient keyframes. |
| `camera` | Landmark fit paths as `measurement`, source track evidence, explicitly reviewed interpretation/residuals, and keyframes recording frame/scale/translation with their measurement basis. `measured` is disallowed for the camera interpretation itself. |
| `motion` | Measured component trajectories, acceleration/easing interpretations and observed transition intervals. Keep measured pixels separate from fitted curves and inferred effects. |
| `audio` | Stream presence, measured candidates, observed speech/SFX/music/silence, event alignment and remaining uncertainty. Numerical onset reports cannot establish all sounds or listening. Production sounds are newly authored procedural code. |
| `replacements` | Authorized substitutions, provenance, locked/unlocked properties, text fitting and effect on the fidelity claim. If none, explicitly record no replacements with evidence. |

An active measured/reviewed section needs nonempty evidence and relevant items (cuts can have zero candidates). Colors and typography need the actual measurement report types. `not_applicable` needs a scoped reason and observational evidence, with empty items; cuts/shots/components/colors cannot be waived. Neither reviewer assertions nor JSON schemas can prove a complete, truthful inventory. The Agent remains responsible for checking the film instead of gaming readiness.

Top-level `review` records `reviewer`, `method`, `ranges:[[A,B]]` and actual `observations`. Top-level `events:[{id,frame,basis}]` records any additional discrete visual locks, such as first appearance, click state or transition completion. Do not derive measured locks from renderer code. Prefixes `cut:` and `text:` are reserved for generated lock IDs. Freeze input findings and relevant timeline decisions together.

## Gate, probes, production, comparison

`python3 scripts/match.py gate --project P --stage production` returns:

- `blocked`: missing or contradictory structure, wrong reference/frame evidence, stale hashes, incompatible timeline. No match snapshot.
- `probe_ready`: structure is present but uncertain sections, unmeasured critical frames or incomplete review remain. Production is blocked.
- `ready`: declared analysis/evidence coverage is complete. This is structural readiness, not a claim of perceptual fidelity or user acceptance.

To diagnose uncertain portions, `project.py snapshot --project P --revision R --probe-frames 0,12` freezes a bounded analysis probe. Choose 1–15 actual frames. The renderer permits only those frames through stills/compare; `full`, preview encoding and final export are blocked. This escape hatch enables measurement experiments without quietly bypassing analysis. Production snapshots omit `--probe-frames` and require ready.

`match.py spec --project P --out analysis/SPEC-v2.md` writes a readable document of all sections, evidence links and unknowns without overwriting an existing spec. Review it and author the project's canonical SPEC.md. It is a document generator, not an automatic shot/creative specification.

After a ready production snapshot, full render, procedural synthesis and preview encode, run `match.py verify --project P --revision R`. It independently measures rendered pixels with the frozen critical reference detectors/templates, checks full declared visible ranges and maximum per-axis errors against 1%, and writes artifact-bound `out/R/review/match-verification.json`. Stricter user/project tolerances require an additional explicit check; this default gate does not waive them. It does not automatically compare all colors, typography, materials or audio similarity.

Discrete hard cuts, text character changes and declared events additionally require actual output observations in `out/R/review/events.json`: `artifactSha256`, `reviewer`, `method`, `observations`, and `events:[{id,actualFrame}]`. Reference hard-cut IDs are `cut:F`; character IDs are `text:TEXT_ID:INDEX` (zero-based character-event index). Exact frame arithmetic uses zero tolerance. Perceptual observations must actually occur; the parser checks identity/arithmetic, not whether someone watched.

`encode.py --final` requires a passing match verification bound to the current preview, snapshot and tool, unchanged event evidence, and all existing technical/content/motion/listening/reference QA. Numerical match success alone cannot promote to final. Existing `sync.py --kind reference` provides native-size, unstretched comparison. Inspect decoded MP4 as well as source PNGs. Report MATCHES only when all relevant measured and perceptual conditions pass; otherwise state CLOSE/ROUGH/UNVERIFIED and actual remaining error.

## Version migration

v0.2 adds a production gate; it does not rewrite v0.1 project history. Keep old snapshots and outputs intact. Current tools can read v0.1 source metadata, but cannot render or encode old tool hashes. Use the pinned v0.1 code to reproduce an old snapshot, or enrich the working project with real analysis and make a new v0.2 revision. Restoring an incompatible old match snapshot is rejected before changing working files. Restoring a probe preserves its restrictions. Analysis documents are frozen with each new snapshot, and feedback stays bound to the version actually reviewed.
