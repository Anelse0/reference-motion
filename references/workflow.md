# Workflow and project boundary

The scripts use explicit project paths and have `--help`. Resolve the installed Skill directory once and invoke its scripts by that path. Use one production process sequentially; this package is not a model router or an orchestration service.

## Start or continue

`project.py init --workspace W --id ID --mode create` creates identity and metadata only. Match adds `--mode match --ref ACTUAL_FILE`. It refuses to overwrite an existing project. Early undecided parameters are allowed at initialization; rendering requires a complete output contract, source entries, and timeline. The Agent writes BRIEF.md, SPEC.md, `src/main.js`, `audio/score.py`, and the timeline from the task, rather than copying a project template.

The workspace holds `projects/ID/` and optional `memory/preferences.json` and `memory/capabilities.json`. Keep customer source media and private copy within their authorized project. The installation is normally read-only. Do not package the entire workspace memory with a client delivery.

For an existing project, read `project.json`, BRIEF.md, SPEC.md, REPORT.md, current timeline, and unresolved `history/feedback.jsonl`. Check version and source hashes before using a cache. Current working sources may differ from the version a user reviewed.

## Minimal authored data contract

Complete `project.json.output` with `width`, `height`, `fps:{num,den}`, `frames` (positive integers) and `colorPolicy:"srgb-to-bt709-limited"`. `renderEntry` and `scoreEntry` point to project-relative authored files. Keep `audio.source:"procedural"`, positive `sampleRate`, `channels` (1 or 2), `loudnessTarget`, `truePeakMax`, and actual narration needs. `audio.allowSilence:true` is only for a deliberately silent project. Assets declare `id/path/type/source/use`; `use` is analysis or production. The renderer supports decoded raster images and declared fonts; production audio/video files and unsanitized SVG are not renderer inputs. File fonts must be declared production assets. `fonts` can use a verified `source:"local"` with actual font name or a declared file path.

`timeline.json.shots` is an authored array of `{id,f0,f1}` covering `[0,N)` exactly once without gaps/overlap. `events` is an array with unique `id`, exactly one numeric `frame`, `seconds` or `beat` anchor, plus `basis:user|measured|designed|inferred` and a source/reason string. `transitions`, when needed, declare valid `{f0,f1}` ranges. Beat anchors additionally need `beats:{bpm,offsetSeconds}`. These are timing relationships, not prescribed shots or creative choices. `project.py check --stage render` validates them before freezing.

## Design and representative output

For match, `analyze.py --project P` indexes the actual reference and extracts frames. View the overview and relevant continuous frame ranges; use `measure.py track --help` for explicit pixel-based measurements. Mechanical cut or beat candidates are observations requiring interpretation. Unknown or occluded values stay unknown. Locked cut points and events have zero-frame tolerance; measurable object bounds default to 1% of the corresponding image axis, subject to any stricter project requirement.

For create, skip reference analysis. Write the real content goal, audience, source facts, chosen creative mechanism and reason, necessary storyboard, relevant design rules, and concrete checks. Safe design assumptions can be recorded and executed. Never invent product claims, missing legal copy, testimonials, or unavailable brand assets.

Use the same project code for proof frames and the eventual film. A representative section should expose the most significant risk, not meet a default number of seconds or shots. A still cannot establish continuous motion or sound quality.

## Freeze and produce

1. Set output size, rational fps, total frames, declared assets and audio contract. Set time facts in `timeline.json` and validate with `project.py check --project P --stage render`.
2. Freeze with `project.py snapshot --project P --revision r001`. All subsequent commands for r001 read `revisions/r001/source`, not the mutable working sources.
3. Generate `render.mjs --project P --revision r001 stills 0,12,24` for a few meaningful frames or `full 0 N` for `[0,N)`. Write sound with `audio.py --project P --revision r001 synth`, then `check`.
4. Run `measure.py check --project P --revision r001` and `encode.py --project P --revision r001 --preview`. In match, use `sync.py --project P --revision r001 --kind reference` for actual reference comparison. Comparison videos are silent visual inspection aids; review sound separately. Revision comparison uses explicit `--before MP4 --after MP4`, preserves source time, and labels the ended side if lengths differ.
5. Inspect output, record evidence, and revise if necessary. A changed working source requires a new snapshot and output directory. Use `project.py restore --help` to recover a historical source into a new revision while keeping later history.

Commands are a production sequence, not an animation or music template. Choose relevant frame requests from the project; the three frame numbers above only illustrate CLI syntax.

## Report and delivery

`out/R/review/qa.json` is an array of `{check,status,evidence,note}`. Status is `pass`, `fail`, `unverified`, or `not_applicable`. Evidence paths are relative to the project. Separate technical, content, motion, audio numeric, listening, reference (match), and user acceptance checks. Explain not-applicable results; do not use that status to waive a mandatory check. Configured `requiredChecks` adds to the default checks rather than removing them.

Each required check must cite a real JSON evidence report with `artifactSha256` matching the exact preview bytes. Content, motion, listening and reference evidence also needs `status:"pass"`, `reviewer`, `method`, and specific `observations`. Record these only after the corresponding review actually occurs. A source-frame inspection report cannot pass encoded-video playback review. Additional failed QA or unresolved critical QA also blocks final promotion. The parser verifies the evidence structure and artifact binding; it cannot verify that an author truthfully performed a perceptual activity.

For match, `MATCHES` requires its measurements and perceptual checks to meet the declared conditions. Use `CLOSE` or `ROUGH` with residuals where they do not. Create uses `PASS`, `REVISE`, or `UNVERIFIED` against its own brief. These are review summaries, not automatic acceptance or a universal quality score.

`encode.py --final` requires actual required QA and only promotes a technically checked output. If listening or continuous playback is unavailable, record that and keep a preview. A QA JSON assertion alone is not proof of viewing or listening: describe the actual activity, person/tool, revision, and evidence. A genuine user response is the only source of user acceptance.

Deliver the exact revision's editable sources, brief/specification, timeline, stems, asset provenance, report, and preview/final. Match adds comparison material. Real-reference reconstruction remains unverified when only synthetic test media were available. Installed host loading and cross-host continuation are separate checks from packaging or static validation.
