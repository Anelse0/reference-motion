# Entirely procedural production audio

Reference audio is analysis-only. `audio.py analyze` may measure real reference sound; `synth` imports only the frozen project score and supplies structured timing and design values. Do not pass reference PCM, sample-library audio, a music-service output or an external TTS result into production. The score checker restricts imports to NumPy/math and rejects file/dynamic operations in its AST checks. This boundary is not a security sandbox: inspect score dependencies and source as ordinary executable project code.

## Score interface

Write `compose(synth, timeline, spec)` at the project's `scoreEntry`. Return a dictionary of named stems, or `(stems, synth.events)`. Each stem is a finite floating array of shape `(spec["duration_samples"], spec["channels"])`. `spec` has `sample_rate`, `channels`, `duration_samples`, `fps`, `seed`, `design`, and `narration_required`; it has no reference input.

The synthesis primitives are deliberately without arrangement:

| Method | Meaning |
| --- | --- |
| `track()` | Empty full-length multichannel float track |
| `oscillator(freq, samples, wave="sine", phase=0)` | Mono signal; frequency may be a scalar or integrated array |
| `noise(samples, event_id)` | Deterministic noise tied to the event identity |
| `envelope(samples, attack=.005, release=.05)` | Attack/release envelope; durations in seconds |
| `lowpass(signal, cutoff)` | Deterministic low-pass processing |
| `pan(signal, pan)` | Stereo placement, pan from −1 to +1 |
| `delay(signal, delay_samples, feedback=.3, repeats=3)` | Delay including its output tail |
| `place(track, signal, start_sample, event_id, anchor="onset")` | Mix into track and record measured PCM cue; anchor can be `peak` |

Each placed cue's event ID must exist in the timeline. Derive start samples from that event's absolute frame/seconds/beat position. Include delay tails in the duration; out-of-bounds placement fails rather than silently truncating sound. For a continuous bed without an intended discrete anchor, assigning the generated signal into a stem directly is valid. Do not hand-fill a cue with a desired time and call it measured alignment.

Composition, pitches, texture, density, rhythm, silence and sound-event choices belong to this project's score. There is no required BPM, instrument configuration, beat sequence or advertisement ending. Keep independent noise event seeds so adding an event does not mutate unrelated sounds. Control oscillator aliasing, initialize state deterministically, and taper boundaries to avoid unintended clicks.

## Synthesize, measure, and listen

Use `audio.py --project P --revision R synth`, then `check`. The output includes stems, mix and cue/measurement reports under `out/R/audio/`. Default working rate is 48 kHz; length is `round(N*q*sampleRate/p)`. Follow project loudness/peak values (normally −14 LUFS ±1 and ≤−1 dBTP), and remeasure after encoding. Silence can make integrated loudness inapplicable; it does not justify fabricating a passing measurement.

Check finite samples, clipping, DC, peaks, duration, boundary discontinuities, unexpected silence, tails and timeline alignment. `audio.requiredEvents` can name required cues and `audio.cueToleranceFrames` can tighten their tolerance. Cues measure inserted-waveform onset/peak and test presence in the mix. The encoder compares master and decoded local PCM near cues and first activity, recording lag, correlation and tolerance; neither this nor cue presence proves perceptual audibility under masking. Numeric checks cannot establish timbre quality, composition quality or intelligibility. Record an actual listening review separately with the revision and method. If listening is unavailable, state “numeric checks completed; listening unverified” and leave the listening QA unresolved.

No narration requirement means no invented voice task. Required narration cannot be silently removed. Natural-human speech quality may conflict with pure algorithmic synthesis; if that requirement cannot be met, report the conflict and ask only for the necessary scope decision. Invoking an external neural voice through code does not make it procedural audio. Do not install or contact voice/music services automatically.

For match, `match.py audio-candidates` adds reference spectral-flux onsets and autocorrelation period candidates. They are hypotheses for actual review, not an automatically chosen BPM, beat phase, complete SFX inventory or listening pass. See [match-analysis.md](match-analysis.md).
