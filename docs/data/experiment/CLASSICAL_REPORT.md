# Classical arpeggio comparison report

Interpretation: the three consecutive rising figures end on peaks **p1 < p3 < p2**, giving low → high → middle. This applies across the three figures, rather than a three-note ornament within each figure.

The public-score scan covered **1,025 instrumental MIDI editions** found via Mutopia’s piano and guitar catalogue pages. It found **27 score editions** with broken-chord-like consecutive rises and the requested peak contour; 15 distinct works were selected for the page. The scan does not establish perceptual similarity to the original recording.

## Definition and ranking

- Highest newly attacked note per 55 ms attack group, within MIDI 48–108. Sustain durations do not count as new attacks.
- Three adjacent rising runs: at least three notes, at least five semitones of rise, steps no larger than an octave, at most 0.42 seconds between attacks and 0.60 seconds between runs. All intervening attack groups are retained; no unrelated fragment is skipped.
- A broken-chord-like screen requires 2–5 pitch classes in each run and at least two intervals of a minor third or larger. This is a loose symbolic filter, not a harmonic proof.
- Peak notes must satisfy p1 < p3 < p2, using MIDI register, not pitch class.
- Equal note counts rank first. Within that tier, closer to an approximate 0.15-second pulse ranks first, then timing evenness and shorter gaps. Exact pitches and harmony do not contribute.

## Ranked excerpts

All times below refer to the source MIDI score rendering. Playback includes the final note and then a three-second tail. The page includes a three-second visual lead-in and tail.

| Rank | Work / source | MIDI start–last attack | Notes per rise | Peak MIDI notes |
| --- | --- | --- | --- | --- |
| 1 | [Burgmüller — La Chasse, Op. 100 No. 9](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=220) | 27.300–28.500 s | 3–3–3 | 68 → 71 → 69 |
| 2 | [Giuliani — Op. 50 No. 3](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1331) | 15.000–16.212 s | 3–3–3 | 72 → 77 → 74 |
| 3 | [Schubert — Impromptu in G-flat major, D. 899 No. 3](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1193) | 168.167–169.500 s | 3–3–3 | 63 → 66 → 65 |
| 4 | [Chopin — Prelude in E-flat major, Op. 28 No. 19](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=2192) | 20.119–21.071 s | 3–3–3 | 77 → 80 → 78 |
| 5 | [Carcassi — Étude in C major, Op. 60 No. 1](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=13) | 45.000–47.062 s | 4–4–4 | 64 → 69 → 67 |
| 6 | [Burgmüller — L’Adieu, Op. 100 No. 12](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=223) | 16.250–17.120 s | 3–3–3 | 64 → 72 → 71 |
| 7 | [Rachmaninoff — Prelude in C-sharp minor, Op. 3 No. 2](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=2033) | 56.604–57.438 s | 3–3–3 | 66 → 69 → 68 |
| 8 | [Haydn — Sonata in G major, Hob. XVI:27, II: Menuetto](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=186) | 33.478–35.217 s | 3–3–3 | 71 → 79 → 75 |
| 9 | [Sor — Étude, Op. 6 No. 11](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=2134) | 256.486–258.648 s | 3–3–3 | 59 → 66 → 64 |
| 10 | [Mendelssohn — Song Without Words, Op. 85 No. 1](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1744) | 74.167–75.833 s | 3–3–5 | 64 → 69 → 67 |
| 11 | [Beethoven — Appassionata Sonata, Op. 57, III](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=937) | 380.261–381.380 s | 3–5–3 | 68 → 77 → 72 |
| 12 | [Handel — Allemande](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=166) | 39.167–41.667 s | 5–3–5 | 67 → 74 → 69 |
| 13 | [Debussy — Clair de lune](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1778) | 144.500–147.000 s | 5–3–3 | 66 → 77 → 74 |
| 14 | [Scarlatti — Sonata in C major](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=139) | 23.250–25.500 s | 4–4–3 | 67 → 72 → 70 |
| 15 | [Beethoven — Minuet, WoO 82](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=904) | 138.214–143.036 s | 7–6–6 | 75 → 85 → 84 |

## Playback and limits

The page synthesizes the actual source-MIDI excerpt, with an optional isolated detector line. That makes timing reproducible and avoids applying a MIDI timestamp to a different concert recording. The detector line is a reduction of the score; lower-register and simultaneous accompaniment can still sound in the full score. The no-intervening-note test applies to the screened attack stream, not every voice in the composition. No concert recording has been independently aligned or certified.

The original audio window is 7.55–13.10 seconds, followed by three seconds of context. Its Basic Pitch MIDI remains a noisy transcription hypothesis. The target peak contour is supplied by the user, not confirmed from that transcription. This ranking prioritizes the requested structure and is not an audio-embedding or listener-validated similarity score.

## Attribution and licenses

Excerpt note data is extracted, time-shifted and reduced for visualization from each linked Mutopia MIDI. Each work retains its source edition’s license; the preview timbre is synthesized. For CC BY-SA 4.0 editions, see [the license](https://creativecommons.org/licenses/by-sa/4.0/). Source pages identify edition history and contributor details.

- **Burgmüller — La Chasse, Op. 100 No. 9** — J. F. F. Burgmüller (1806–1874); Mutopia edition maintained by **Bas Wassink**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=220) · [MIDI](https://www.mutopiaproject.org/ftp/BurgmullerJFF/O100/25EF-09/25EF-09.mid).
- **Giuliani — Op. 50 No. 3** — M. Giuliani (1781–1829); Mutopia edition maintained by **Stephen Rhen**. Creative Commons Attribution 3.0. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1331) · [MIDI](https://www.mutopiaproject.org/ftp/GiulianiM/O50/giuliani-op50n03/giuliani-op50n03.mid).
- **Schubert — Impromptu in G-flat major, D. 899 No. 3** — F. Schubert (1797–1828); Mutopia edition maintained by **Ph. Raynaud**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1193) · [MIDI](https://www.mutopiaproject.org/ftp/SchubertF/D899/SchubertF-D899-3-Impromptu/SchubertF-D899-3-Impromptu.mid).
- **Chopin — Prelude in E-flat major, Op. 28 No. 19** — F. F. Chopin (1810–1849); Mutopia edition maintained by **Knute Snortum**. Creative Commons Attribution-ShareAlike 4.0. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=2192) · [MIDI](https://www.mutopiaproject.org/ftp/ChopinFF/O28/Chop-28-19/Chop-28-19.mid).
- **Carcassi — Étude in C major, Op. 60 No. 1** — M. Carcassi (1792–1853); Mutopia edition maintained by **Jeff Covey**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=13) · [MIDI](https://www.mutopiaproject.org/ftp/CarcassiM/O60/carcassi-op60-01/carcassi-op60-01.mid).
- **Burgmüller — L’Adieu, Op. 100 No. 12** — J. F. F. Burgmüller (1806–1874); Mutopia edition maintained by **Bas Wassink**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=223) · [MIDI](https://www.mutopiaproject.org/ftp/BurgmullerJFF/O100/25EF-12/25EF-12.mid).
- **Rachmaninoff — Prelude in C-sharp minor, Op. 3 No. 2** — S. Rachmaninoff (1873–1943); Mutopia edition maintained by **Petro Kostandy**. Creative Commons Attribution-ShareAlike 4.0. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=2033) · [MIDI](https://www.mutopiaproject.org/ftp/RachmaninoffS/O3/rach-prelude-op3-no2/rach-prelude-op3-no2.mid).
- **Haydn — Sonata in G major, Hob. XVI:27, II: Menuetto** — F. J. Haydn (1732–1809); Mutopia edition maintained by **Bas Wassink**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=186) · [MIDI](https://www.mutopiaproject.org/ftp/HaydnFJ/HOB-XVI-27/hob-xvi-27-ii-menuet-trio/hob-xvi-27-ii-menuet-trio.mid).
- **Sor — Étude, Op. 6 No. 11** — F. Sor (1778–1839); Mutopia edition maintained by **Glen Larsen**. Creative Commons Attribution-ShareAlike 4.0. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=2134) · [MIDI](https://www.mutopiaproject.org/ftp/SorF/O6/sorf-op6n11/sorf-op6n11.mid).
- **Mendelssohn — Song Without Words, Op. 85 No. 1** — F. Mendelssohn-Bartholdy (1809–1847); Mutopia edition maintained by **Ryan Prince**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1744) · [MIDI](https://www.mutopiaproject.org/ftp/Mendelssohn-BartholdyF/O85/LiederOhneWorte_-_Op85_No1/LiederOhneWorte_-_Op85_No1.mid).
- **Beethoven — Appassionata Sonata, Op. 57, III** — L. V. Beethoven (1770–1827); Mutopia edition maintained by **Stelios Samelis**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=937) · [MIDI](https://www.mutopiaproject.org/ftp/BeethovenLv/O57/LVB_Sonate_57_3/LVB_Sonate_57_3.mid).
- **Handel — Allemande** — G. F. Handel (1685–1759); Mutopia edition maintained by **Bas Wassink**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=166) · [MIDI](https://www.mutopiaproject.org/ftp/HandelGF/Aylesford/17-allemande/17-allemande.mid).
- **Debussy — Clair de lune** — C. Debussy (1862–1918); Mutopia edition maintained by **Keith OHara**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1778) · [MIDI](https://www.mutopiaproject.org/ftp/DebussyC/L75/debussy_Ste_Bergamesq_Clair/debussy_Ste_Bergamesq_Clair.mid).
- **Scarlatti — Sonata in C major** — D. Scarlatti (1685–1757); Mutopia edition maintained by **Bas Wassink**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=139) · [MIDI](https://www.mutopiaproject.org/ftp/ScarlattiD/ds_sonate_c/ds_sonate_c.mid).
- **Beethoven — Minuet, WoO 82** — L. V. Beethoven (1770–1827); Mutopia edition maintained by **Stelios Samelis**. Public Domain. [Edition](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=904) · [MIDI](https://www.mutopiaproject.org/ftp/BeethovenLv/WoO82/menuett_WoO82/menuett_WoO82.mid).
