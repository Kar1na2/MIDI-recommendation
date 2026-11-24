# Pre-registration: 300-song MVP v2 analysis

Registered 2026-11-10 (America/Los_Angeles), after taking the immutable v1
snapshot and before executing any recomputation, regression test or hypothesis
test for this task. This plan is committed separately from all implementation.

## Protection and scope

Read all v1 caches only. Write analysis artifacts exclusively under analysis_v2.
The snapshot covers mvp, pilot, protected song output folders (both aliases and
track IDs), BATCH_REPORT and the two reference result documents. Verify every
hash and size at the end, including detecting added or removed protected files.
No site, onion, recommendation, mood, pattern matcher or threshold changes.
Recompute on CPU; the only permitted GPU use is the conditional timeout
diagnostic explicitly requested by the user. Never launch the full batch.

## Decision rule (fixed)

> The lyric/rhythm alignment is attributed to the **accompaniment** only if the alignment holds with voice excluded, on the auto grid, under the circular-shift null, with pooled p < 0.01 and a 95% bootstrap CI for median lift that excludes 0.

The lift must be positive. The primary tolerance is plus/minus one bar. All
other tests are sensitivity analyses or diagnostics, not alternate routes to
passing this rule. No threshold is adjusted after inspecting results.

## Test matrix and statistical definitions

The factorial matrix has 24 cells: grid {raw v1 cached, auto v2}, voice
{included, excluded}, null {uniform downbeats, circular shift, phrase-aware},
tolerance {plus/minus one bar, exact downbeat}. Add 12 voice-only diagnostic
cells with both grids, all three nulls and both tolerances. Recompute rhythmic
sections after filtering instruments; do not merely remove voice labels from
existing sections. The instrument label voice is excluded or selected exactly
as represented in the cached data. Keep the default sectioning settings.

Use 10,000 null draws per song/cell. Derive an independent reproducible song
seed from SHA-256 of track ID and global seed 20261110; record it and child
seeds. Record seeds for all selections as well. A lyric boundary is a validated
timed section start other than the first section start. Vocal-activity boundaries
are not text-based lyric evidence and are excluded from hypothesis tests.
Keep songs with no rhythmic boundaries as zero-agreement, zero-lift observations;
exclude songs with no evaluable lyric boundaries and report why.

Represent boundaries on the grid's internal downbeats, excluding song start
and end. Observed agreement is the share of lyric boundaries within the chosen
tolerance of any rhythmic boundary. Exact means the same downbeat index;
one bar means index distance at most one. Preserve original raw lyric times
in v2, resnap them to the selected grid and keep validation/rejection metadata.

Uniform null: draw the observed count of distinct rhythmic boundaries without
replacement from internal downbeats. Circular null: translate the song's own
boundary indices by a uniformly drawn integer offset modulo the number of
eligible internal downbeats; this preserves circular spacing and boundary count.
Phrase-aware null: draw the same count without replacement from downbeats at
4-bar phrase lines counted from the first downbeat. If that count exceeds
available phrase lines, declare that song/cell unavailable; do not change its
boundary count or silently switch nulls. Zero-boundary cases remain defined.

Pooled statistic: equal-weight mean per-song agreement. Form its null by
averaging independent song null draws at each shuffle index. One-sided
empirical p = (1 + null draws at least observed)/(10,001). Per-song p uses the
same rule. Lift is observed agreement minus that song's null mean. Bootstrap
the median lift over songs with 10,000 resamples, seed 20261111, and report the
2.5th and 97.5th percentiles. No multiple-testing claim is made for exploratory
cells; only the pre-registered primary cell determines the verdict. Split that
primary cell by v2 detected language and LRCLIB versus WhisperX source.

## Recompute, provenance and compatibility

Reuse cached beats, transcription and routing decisions; no new inference in
the CPU recompute. Auto chooses DBN only with fewer irregular bars and at
least 90% regular-raw downbeat agreement within 70 ms. Use the current
coarsest-qualifying grid rule unchanged. Save quantize, layers, patterns,
rhythm, resnapped lyrics, join and report under analysis_v2/songs/<id>.
Raw/included control is cached v1; other raw voice conditions re-section cached
patterns. Thus raw versus auto also reflects any documented stale downstream
v1 code, a limitation to report rather than erase.

Default profile v1 retains current behavior. Test v1 recomputation in temporary
v2 folders for the first three lexicographically sorted completed MVP IDs with
all downstream caches. Compare stage JSON recursively, ignoring only timestamps
and run IDs. A mismatch is reported as a failure, not normalized away.
Audit each stage's explicit stamps, run logs, filesystem times and Git history.
Where exact code/config provenance cannot be recovered, say unknown; file-time
comparisons are labeled inferred and cannot prove an unstamped code version.
New v2 stages record commit, code digest and config digest without restamping v1.

## Fragmentation, language, routing and timeout

Compare cluster/run/compression metrics per instrument/layer. Rank MVP songs
by runs per encoded nonempty unit (ties by track ID) to select five fragmented
examples. For each and both references, sample up to 20 distinct bar pairs
whose existing similarity is at least 0.70 and below 0.85, using seed 20261112.
Use the most fragmented instrument/layer. Report shortages explicitly. Diagnose
pitch, onset displacement beyond one step, missing/extra events, layer movement
and grid/bar offsets from event evidence; distinguish heuristic attribution
from hand labels, and allow an unresolved category rather than invent causes.

Detect language from cached LRCLIB/Genius text using Unicode scripts plus a
pinned language-ID package. With no lyrics text, detect from 30 seconds of the
vocal stem with Whisper on CPU. Mark explicit instrumentals instrumental.
Missing inputs or unsupported detection remain visibly unresolved. Report
method, confidence, language mix and existing section validation by language.

Inspect timeout logs before considering one replay. If logs are insufficient,
confirm no batch PID is alive and nvidia-smi is idle; otherwise skip replay.
Permit one scratch-only replay, original settings, progress/token logging,
45-minute hard cap. Propose a fix only; do not apply it or alter successful-song
outputs. State the limits of what the logs can establish.

Select five routing-triggered MVP songs without replacement using seed
20261113. Evaluate full-mix and routed piano line density per half-bar,
simultaneous-onset share within 30 ms, ascending single-note runs of at least
four notes, and best period match at the unchanged 0.85/75% thresholds. Keep
human better_version and notes fields empty. Metrics inform a recommendation;
the user decides whether routing is worthwhile.

## Deviations

None at registration. Any necessary change will be appended here with a date
and reason, preserving the original registered text and reporting it openly.
