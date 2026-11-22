"""Join cached Task D scores by section and aggregate individual frame series.

Pure functions only: this module neither reads lyric text nor writes results.
Normalization and trust decisions require the entire random cohort and belong
in a later step, after all scoring jobs have reached a terminal state.
"""
import math
import statistics


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize_frames(frames, key):
    values = [frame[key] for frame in frames if is_number(frame.get(key))]
    if not values:
        return {'mean': None, 'median': None, 'std': None, 'available_frames': 0,
                'possible_frames': len(frames), 'frame_share': None if not frames else 0.0}
    return {'mean': statistics.fmean(values), 'median': statistics.median(values),
            'std': statistics.pstdev(values), 'available_frames': len(values),
            'possible_frames': len(frames), 'frame_share': len(values)/len(frames)}


def index_sections(payload, track_id, cohort, plan_commit):
    if payload is None:
        return {}
    if (payload.get('track_id') != track_id or payload.get('cohort') != cohort
            or payload.get('plan_commit') != plan_commit):
        raise ValueError('Score cache identity or registered plan does not match')
    result = {}
    for section in payload.get('sections', []):
        section_id = section['section_id']
        if section_id in result:
            raise ValueError(f'Duplicate section ID: {section_id}')
        result[section_id] = section
    return result


def _frame_key(frame):
    a, b = frame.get('start_s'), frame.get('end_s')
    if not is_number(a) or not is_number(b):
        raise ValueError('Frame has invalid timing')
    if b <= a:
        coverage = frame.get('coverage', frame.get('instrumental_coverage'))
        score_fields = [value for key, value in frame.items()
                        if key.endswith(('_valence', '_arousal')) or key == 'prosody']
        if b == a and coverage == 0 and all(value is None for value in score_fields):
            return None
        raise ValueError('Nonempty frame has invalid timing')
    # Different sample rates round end times independently. Starts all share
    # the registered one-second schedule; use them to join across channels.
    return round(a, 6)


def aggregate_section(public, text, audio):
    """Build one sanitized section and its frame rows from indexed caches.

    audio maps m1/m2/voice to their cached section records or None. No scores
    are imputed when a model or its source is absent.
    """
    if text is not None and text.get('text_sha256') != public.get('text_sha256'):
        raise ValueError('Section text hash does not match cached text score')
    result = dict(public)
    result['text_scores'] = None if text is None else {
        key: text.get(key) for key in ('T1', 'T2_valence', 'T2_arousal', 'T1_truncated', 'T2_truncated', 'status')}
    result['line_scores'] = [] if text is None else [
        {key: line.get(key) for key in ('line_no', 'text_sha256', 'T1', 'T2_valence',
                                        'T2_arousal', 'T1_truncated', 'T2_truncated')}
        for line in text.get('lines', [])]
    aggregate, curves = {}, {}
    fields = {'m1': ('M1_valence', 'M1_arousal', 'M1_full_mix_valence', 'M1_full_mix_arousal'),
              'm2': ('M2_valence', 'M2_arousal'), 'voice': ('V1_valence', 'V1_arousal')}
    features = ('f0_median_hz', 'f0_range_semitones', 'voiced_ratio', 'vibrato_rate_hz',
                'vibrato_extent_semitones', 'rms_mean', 'rms_variance', 'brightness_hz',
                'voice_note_onset_rate_hz')
    for source, names in fields.items():
        section = audio.get(source)
        raw_frames = section.get('frames', []) if section is not None else []
        frames = [frame for frame in raw_frames if _frame_key(frame) is not None]
        aggregate[source] = {'status': section.get('status') if section is not None else 'cache_unavailable',
                             'scores': {name: summarize_frames(frames, name) for name in names},
                             'excluded_zero_length_frames': len(raw_frames)-len(frames)}
        if source == 'voice':
            aggregate[source]['prosody'] = {
                name: summarize_frames([{name: (frame.get('prosody') or {}).get(name)} for frame in frames], name)
                for name in features}
        seen = set()
        for frame in frames:
            key = _frame_key(frame)
            if key in seen:
                raise ValueError(f'Duplicate {source} frame start: {key}')
            seen.add(key)
            row = curves.setdefault(key, {'track_id': public['track_id'], 'cohort': public['cohort'],
                                          'language': public.get('language'),
                                          'section_id': public['section_id'], 'start_s': key})
            row[source+'_end_s'] = frame['end_s']
            for name in names:
                row[name] = frame.get(name)
            if source == 'voice':
                row['voice_coverage'] = frame.get('coverage')
                row['voice_active'] = frame.get('active')
                for name in features:
                    row['voice_'+name] = (frame.get('prosody') or {}).get(name)
            else:
                row[source+'_coverage'] = frame.get('instrumental_coverage')
                if source == 'm1':
                    row['mix_coverage'] = frame.get('mix_coverage')
    result['audio_models'] = aggregate
    return result, [curves[key] for key in sorted(curves)]
