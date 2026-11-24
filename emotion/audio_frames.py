"""Section window handling shared by the audio models; no model inference."""
import math
from fractions import Fraction

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def read_mono(path, target_rate):
    signal, source_rate = sf.read(str(path), dtype='float32', always_2d=True)
    mono = signal.mean(axis=1, dtype=np.float32)
    if source_rate != target_rate:
        ratio = Fraction(target_rate, source_rate)
        mono = resample_poly(mono, ratio.numerator, ratio.denominator).astype(np.float32)
    return mono


def windows(start, end, signal, sample_rate):
    """Return section-relative 4-second zero-padded windows at a 1-second hop."""
    if not (isinstance(start, (float, int)) and isinstance(end, (float, int))
            and math.isfinite(start) and math.isfinite(end) and start >= 0 and end > start):
        return
    count = math.ceil(end-start)
    for i in range(count):
        t = start + i
        left = round(t*sample_rate)
        right = min(round(min(end, t+4.)*sample_rate), len(signal))
        actual = max(0, right-left)
        frame = np.zeros(4*sample_rate, dtype=np.float32)
        if actual:
            frame[:actual] = signal[left:right]
        yield {'start_s': t, 'end_s': min(end, t+4.), 'actual_seconds': actual/sample_rate,
               'coverage': min(1., actual/(4*sample_rate)), 'samples': frame}


def vocal_active(frame, sample_rate):
    """At least 20% of the 100-ms frames exceed -40 dBFS, including padded audio."""
    width = sample_rate//10
    blocks = frame[:len(frame)//width*width].reshape(-1, width)
    rms = np.sqrt(np.mean(blocks.astype(np.float64)**2, axis=1))
    return float(np.mean(rms > 10**(-40/20))) >= 0.2
