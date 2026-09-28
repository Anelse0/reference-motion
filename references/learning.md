# Evidence-scoped feedback and local memory

Learning means maintaining inspectable records, not changing model weights. Keep three separate records: original feedback in its project, conditional user preferences in the authorized workspace, and tested methods in that workspace's capability file. Initially they contain no invented preferences or capabilities. Tests use explicitly labelled fixtures in isolated workspaces.

## Feedback and revisions

Append actual user words to `history/feedback.jsonl`. Record `id`, `projectId`, `reviewedRevision`, `quote`, `source`, and the known `target`. Without a known version or message ID, say unknown/manual source instead of guessing. Keep `interpretation` and `certainty` separate from the quotation; also record `scope`, actual `action`, `resultingRevision`, `technicalOutcome`, `userOutcome`, and project-relative `evidence`.

Append corrections or outcome records referencing the original ID instead of silently editing the quotation. A revision source and its media are frozen once produced for review. Diagnose whether “too slow” concerns delayed information, entry motion, a hold, total length or sound energy before choosing an edit. Do not change a reference lock under the guise of taste. Write the new version's actual difference and checks. An unanswered revision has `userOutcome: unknown`, not “accepted.”

## Preferences

`memory/preferences.json` is an array of `{id,category,rule,scope,appliesWhen,status,sources,updatedAt,supersedes}`. Distinguish aesthetic choices from explicit collaboration/resource conditions. Scope should identify the relevant project, brand, task class, or an explicitly requested broader scope. `candidate`, `confirmed`, `superseded` and `retracted` are distinct states. Confirmed needs explicit user evidence about that scope; repetition or an isolated “I like this” is insufficient for a general rule.

Select only relevant active records. Current instructions and project locks override memory; scope mismatches and revoked sources are excluded. Read candidate preferences as uncertain hints, never requirements. Record adopted/ignored record IDs, versions and reasons in the revision's `used-memory.json`; a historical use record does not authorize future use. Never transfer another customer's private copy or assets. Explicitly stated user capabilities can be recorded as collaboration conditions; do not infer skill level from taste.

Use atomic writes with a previous-content hash for shared JSON edits and fail on a concurrent change rather than overwriting it. `project.py` provides record validation; it does not infer, endorse or silently activate a preference.

## Capabilities

`memory/capabilities.json` holds `{id,task,method,conditions,status,evidence,limits,environment,codeRef,lastTested}`. Status is `proposed`, `verified_in_scope`, `failed`, or `stale`. Verification needs a real task or explicitly scoped controlled test, actual code/parameters, an environment, artifacts and the method/results of checks. Evidence must distinguish pixel determinism, measured performance, numeric audio, actual listening, motion review and user response.

One successful scene proves only its tested conditions. A user preference is not engineering evidence. Repeating one scene is not cross-scene validation; generating both reference and answer from one function is not reverse-engineering evidence. Expand scope only with new independent evidence. Code, dependency or environment changes can invalidate results; mark affected entries stale and retest. Do not attribute a script result to an invented model identity. Unknown host model identity stays unknown.

Methods may reuse computation and checking; old shot sequences, scores, palettes and copy do not become an implicit creative library. Promoting a project algorithm into the common tools is explicit maintenance with tests, not an automatic learning step.

## Retraction, deletion, and restoration

Honor user edits, disabling and deletion. Retract the source record and invalidate dependent summaries/candidates. For deletion, remove the requested local content and its derived text; a content-free tombstone may prevent accidental reactivation. Explain boundaries only if the request includes backups or locations outside available control. Do not claim to erase inaccessible copies.

Restoring an old source creates a new revision and preserves later history. Re-select memory from its current active state; never reactivate a retracted/deleted preference from the restored snapshot's `used-memory.json`. Existing delivered media need not be rewritten, but subsequent creative choices must honor the revocation. With learning disabled, normal production continues and only necessary project work facts are retained.
