"""Shared pYIN window gate for V1 and V3; no RMS proxy."""
import os
from pathlib import Path
HERE=Path(__file__).resolve().parent
os.environ.setdefault('NUMBA_CACHE_DIR',str(HERE/'scratch/numba'))
os.environ.setdefault('XDG_CACHE_HOME',str(HERE/'scratch/xdg'))
import librosa
import numpy as np

SAMPLE_RATE=16000
FRAME_LENGTH=2048
HOP=320
PROBABILITY_MIN=.5
WINDOW_VOICED_SHARE=.2

def gate_from_probabilities(probabilities, relative_start_s, actual_seconds, window_seconds=4.):
    """Padded time is unvoiced, as in the registered four-second window convention."""
    n=max(1,round(window_seconds*SAMPLE_RATE/HOP))
    first=round(relative_start_s*SAMPLE_RATE/HOP)
    available=max(0,round(actual_seconds*SAMPLE_RATE/HOP))
    selected=np.asarray(probabilities[first:first+min(n,available)],dtype=float)
    if not np.isfinite(selected).all(): raise ValueError('Nonfinite pYIN voiced probability')
    voiced=int(np.count_nonzero(selected>=PROBABILITY_MIN))
    return {'active':voiced/n>=WINDOW_VOICED_SHARE,'voiced_frames':voiced,'total_frames':n,'voiced_share':voiced/n}

def section_probabilities(audio, start_s, end_s):
    left=round(start_s*SAMPLE_RATE); right=min(round(end_s*SAMPLE_RATE),len(audio))
    if left<0 or right<=left: raise ValueError('Section has no vocal audio')
    crop=audio[left:right]
    if len(crop)<2: raise ValueError('Section is too short for pYIN')
    _,_,probabilities=librosa.pyin(crop,fmin=librosa.note_to_hz('C2'),fmax=librosa.note_to_hz('C6'),
        sr=SAMPLE_RATE,frame_length=FRAME_LENGTH,hop_length=HOP,center=True)
    if probabilities is None or not np.isfinite(probabilities).all(): raise ValueError('pYIN failed to produce finite probabilities')
    return probabilities
