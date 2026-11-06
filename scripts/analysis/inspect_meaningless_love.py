"""Read-only diagnostic for the opening arpeggio experiment (scipy + MIDI)."""
from pathlib import Path
import csv
import json
import numpy as np
import pretty_midi
from scipy import signal
from scipy.io import wavfile
from scipy.ndimage import median_filter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

SONG = '1nonly - Meaningless Love (Official Music Video)'
OUT = Path('output/analysis/meaningless_love')

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    mid = pretty_midi.PrettyMIDI(str(Path('midi') / SONG / 'other.mid'))
    notes = sorted((n for i in mid.instruments for n in i.notes), key=lambda n: (n.start, n.pitch))
    print('MIDI', [(i.name, len(i.notes)) for i in mid.instruments], 'tempo metadata', mid.get_tempo_changes())
    sr, stereo = wavfile.read(Path('stems') / SONG / 'other.wav')
    audio = stereo.mean(axis=1).astype(float) / 32768
    start, end = 5.4, 22.0
    y = signal.resample_poly(audio[int(start*sr):int(end*sr)], 1, 2)
    sr2 = sr // 2
    f, t, z = signal.stft(y, sr2, nperseg=8192, noverlap=8192-128)
    spec = abs(z)
    whitened = spec / np.maximum(median_filter(spec, size=(101,1)), 0.00001)**0.7
    pitches = np.arange(36, 85)
    salience = []
    for pitch in pitches:
        freq = pretty_midi.note_number_to_hz(pitch)
        bins = [np.argmin(abs(f-freq*h)) for h in range(1,5)]
        salience.append(sum(whitened[b] / (h+1) for h,b in enumerate(bins)))
    salience = np.array(salience)
    flux = np.maximum(np.diff(spec, axis=1),0).sum(axis=0)
    peaks,_ = signal.find_peaks(flux, distance=int(.09*sr2/128), prominence=np.max(flux)*.025)
    attacks = (t[1:][peaks]+start).tolist()
    print('spectral attacks', [round(x,3) for x in attacks if 7.4<x<14.2])
    fig, axes = plt.subplots(3,1,figsize=(17,10),sharex=True,layout='constrained')
    axes[0].plot(np.arange(len(y))/sr2+start,y,lw=.4)
    axes[0].set_ylabel('Other stem')
    axes[1].pcolormesh(t+start,pitches,20*np.log10(salience+1e-6),cmap='magma',shading='auto',vmin=-25,vmax=10)
    axes[1].set_ylabel('Audio harmonic salience\nMIDI pitch')
    for n in notes:
        if start<n.start<end:
            axes[2].plot([n.start,n.end],[n.pitch,n.pitch],lw=2)
    axes[2].set_ylabel('Raw MIDI pitch')
    axes[2].set_ylim(36,84)
    axes[2].set_xlabel('Full-song time (seconds)')
    for ax in axes:
        ax.set_xlim(start,end)
        ax.grid(alpha=.15)
    fig.savefig(OUT/'opening_diagnostic.png',dpi=150)
    plt.close(fig)
    with (OUT/'opening_raw_notes.csv').open('w',newline='') as fp:
        w=csv.writer(fp);w.writerow(['onset','offset','pitch','name','velocity'])
        w.writerows((n.start,n.end,n.pitch,pretty_midi.note_number_to_name(n.pitch),n.velocity) for n in notes if start<n.start<end)
    np.savez_compressed(OUT/'audio_salience.npz',times=t+start,pitches=pitches,salience=salience,attacks=attacks)
    print('outputs',OUT)

if __name__ == '__main__':
    main()
