# Liked Songs Harmonic and Melodic Analysis

Generated from the completed local MIDI pipeline on 2026-10-30.
The report describes structural patterns in the transcribed `other` Demucs stem: harmonic accompaniment, keys, guitars, and synths. It does not claim to measure vocals, bass, drums, genre, authorship, or influence. Categories are transparent heuristics intended for exploration.

## Corpus coverage

- Spotify Liked Songs records fetched: **1,128**
- Local ingestion status: **1,053** downloaded, **40** existing-corpus aliases, **17** exact duplicates, **18** failed
- Songs with chord sequences: **1,127**
- Songs with melodic interval sequences: **1,128**
- Chord segments: **117,112** across **9** canonical qualities
- Note intervals: **1,270,046**
- Chord alignment pairs retained: **20,303**
- Note alignment pairs retained: **21,116**

## Harmonic patterns

Chordino reduces detected sonorities to pitch-class sets, then the analysis canonicalizes them to transposition-invariant Forte classes. The most common qualities are:

- `major` — 39,498 occurrences (33.7%)
- `minor` — 25,853 occurrences (22.1%)
- `minor seventh` — 18,200 occurrences (15.5%)
- `major seventh` — 13,187 occurrences (11.3%)
- `dominant seventh` — 11,525 occurrences (9.8%)
- `half-diminished seventh` — 4,658 occurrences (4.0%)
- `augmented` — 1,878 occurrences (1.6%)
- `diminished` — 1,476 occurrences (1.3%)

Root motion is most often short-distance motion within a tonal center, with a smaller but meaningful set of fourth/fifth and chromatic transitions. The eight most frequent directed pitch-class moves are:

- `+5 semitones` — 26,038 occurrences (22.4%)
- `+7 semitones` — 15,908 occurrences (13.7%)
- `+2 semitones` — 12,945 occurrences (11.2%)
- `+10 semitones` — 10,113 occurrences (8.7%)
- `+11 semitones` — 9,050 occurrences (7.8%)
- `+8 semitones` — 7,516 occurrences (6.5%)
- `+1 semitones` — 7,141 occurrences (6.2%)
- `+9 semitones` — 6,793 occurrences (5.9%)

The most frequent recurring chord n-grams (quality sequences, independent of absolute key) are:

- `3-11B-3-11A` — 1005 songs, 9439 occurrences
- `3-11A-3-11B` — 989 songs, 9343 occurrences
- `3-11B-3-11B` — 943 songs, 15316 occurrences
- `4-26-3-11B` — 919 songs, 5120 occurrences
- `3-11B-4-26` — 908 songs, 4962 occurrences
- `3-11B-4-20` — 867 songs, 3996 occurrences
- `4-20-3-11B` — 833 songs, 3851 occurrences
- `3-11A-4-26` — 801 songs, 3463 occurrences

## Melodic patterns

Melodic similarity is computed on signed semitone intervals between successive transcribed notes, so the representation is invariant to whole-song transposition.

- `0` — 118,122 occurrences (9.3%)
- `7` — 74,451 occurrences (5.9%)
- `-7` — 73,260 occurrences (5.8%)
- `5` — 54,833 occurrences (4.3%)
- `3` — 54,398 occurrences (4.3%)
- `-3` — 52,607 occurrences (4.1%)
- `-5` — 52,333 occurrences (4.1%)
- `4` — 52,237 occurrences (4.1%)
- `-4` — 51,311 occurrences (4.0%)
- `-19` — 42,718 occurrences (3.4%)
- `19` — 40,743 occurrences (3.2%)
- `9` — 32,772 occurrences (2.6%)

The dominant interval sizes by absolute distance are:

- `7 semitones` — 147,711 occurrences (11.6%)
- `0 semitones` — 118,122 occurrences (9.3%)
- `5 semitones` — 107,166 occurrences (8.4%)
- `3 semitones` — 107,005 occurrences (8.4%)
- `4 semitones` — 103,548 occurrences (8.2%)
- `19 semitones` — 83,461 occurrences (6.6%)
- `9 semitones` — 64,015 occurrences (5.0%)
- `16 semitones` — 58,884 occurrences (4.6%)
- `8 semitones` — 57,771 occurrences (4.5%)
- `15 semitones` — 53,232 occurrences (4.2%)

The most common five-interval contours are:

- `0,0,0,0,0` — 4,158 occurrences
- `7,-7,7,-7,7` — 684 occurrences
- `-7,7,-7,7,-7` — 659 occurrences
- `7,-7,0,7,-7` — 319 occurrences
- `7,-7,7,-7,0` — 310 occurrences
- `5,-5,5,-5,5` — 308 occurrences
- `-5,5,-5,5,-5` — 293 occurrences
- `0,7,-7,7,-7` — 291 occurrences

## Structural groups

Each song is assigned to one exploratory group using chord repetition, chord-quality mix, interval entropy, stepwise share, and leap share. A group is useful for browsing and recommendation features; it is not a genre classifier.

### Leap driven melody (819 songs)
- `P.T. Adamczyk - The Rebel Path [3m8UfZwPMMbiWfGR8Lg2cR]` — triadic 48%, seventh/extended 52%, stepwise 56%, leaps 37%, interval entropy 3.19
- `SEEINGSTVRS - MINISKIRT [3jfTAVzd5namBpjZDbd73X]` — triadic 81%, seventh/extended 19%, stepwise 39%, leaps 57%, interval entropy 3.39
- `Beyoncé - Crazy In Love (feat. JAŸ-Z) [5IVuqXILoxVWvWEPm82Jxr]` — triadic 52%, seventh/extended 48%, stepwise 48%, leaps 39%, interval entropy 3.46
- `Enur - Calabria 2007 - Radio Mix [5NaXhloo19xlvd5X6kN2MN]` — triadic 98%, seventh/extended 2%, stepwise 32%, leaps 48%, interval entropy 3.56
- `Connor Price - Violet [3zb9k8UUuKz5tYmKJvNqQA]` — triadic 86%, seventh/extended 14%, stepwise 25%, leaps 54%, interval entropy 3.72

### Seventh / extended harmony (275 songs)
- `Dizzy Dizzo 蔡詩芸 - Wifey [6CzNGPQ6l6K4EppbiH61Bx]` — triadic 0%, seventh/extended 100%, stepwise 79%, leaps 21%, interval entropy 2.16
- `Sage The Gemini - Gas Pedal [1UMKquW1olreYO1LlLmTqi]` — triadic 44%, seventh/extended 56%, stepwise 68%, leaps 32%, interval entropy 2.77
- `Lvbel C5 - KASA ORIJINAL [6sVqMxZnOMyZrATLR6v5Ft]` — triadic 43%, seventh/extended 57%, stepwise 71%, leaps 28%, interval entropy 2.79
- `XG - WOKE UP [4keuUM29CtIWgsPRzRhXoW]` — triadic 32%, seventh/extended 68%, stepwise 72%, leaps 14%, interval entropy 2.92
- `MEOVV - BODY [0u18ibQ2zk470oLSP3D5WR]` — triadic 25%, seventh/extended 75%, stepwise 55%, leaps 24%, interval entropy 2.93

### Triadic, stepwise melody (18 songs)
- `Connor Price - False Alarm [1O0NZZ5YyhUe3ijGqquom4]` — triadic 69%, seventh/extended 31%, stepwise 89%, leaps 11%, interval entropy 2.29
- `Dj Bountyhunter - Woops - Slowed + Reverb [3ajR6pspj94O8XkqkCNCir]` — triadic 70%, seventh/extended 30%, stepwise 74%, leaps 25%, interval entropy 2.53
- `IVE - Heroine [171vXOYrexGMVNEMA5rs00]` — triadic 83%, seventh/extended 17%, stepwise 53%, leaps 39%, interval entropy 3.06
- `PXSEIDON - Satisfaction - Push Push Push [0Xg5RTrZabrcHUFtLInwvX]` — triadic 92%, seventh/extended 8%, stepwise 59%, leaps 38%, interval entropy 3.19
- `alt! - RAHHHH [5NLnV0krXXSIOqL1PyAd1Q]` — triadic 97%, seventh/extended 3%, stepwise 62%, leaps 38%, interval entropy 3.19

### Mixed structural profile (11 songs)
- `bbno$ - it boy [2UerDbZznc07mmGMD3WfHH]` — triadic 62%, seventh/extended 38%, stepwise 80%, leaps 18%, interval entropy 2.72
- `Connor Price - AMG [7fcQLY4j4QYQKzjh7UVFx3]` — triadic 50%, seventh/extended 50%, stepwise 46%, leaps 2%, interval entropy 2.73
- `Baby Jane - winter 4ever - sped up [6zojb7PWpmWzYiDnclTvQY]` — triadic 60%, seventh/extended 40%, stepwise 76%, leaps 14%, interval entropy 2.75
- `ACRAZE - Do It To It [20on25jryn53hWghthWWW3]` — triadic 75%, seventh/extended 25%, stepwise 44%, leaps 9%, interval entropy 2.82
- `Ndotz - Embrace It [0io16MKpbeDIdYzmGpQaES]` — triadic 52%, seventh/extended 48%, stepwise 72%, leaps 8%, interval entropy 2.97

### Loop / vamp driven (5 songs)
- `XmegaLxl - UMAMUSUME x JET SET! [0VyNMIEYCl1pld53eYmgBJ]` — triadic 0%, seventh/extended 100%, stepwise 0%, leaps 100%, interval entropy 1.58
- `OMINVS - WE BOUT TO GET DOWN [4IeV5yYwvdDbQ2fjniu97W]` — triadic 50%, seventh/extended 50%, stepwise 91%, leaps 9%, interval entropy 1.96
- `MEMBER - LURKIN' 65 [4DrGDMO8mj4riw0AQ4niWo]` — triadic 60%, seventh/extended 40%, stepwise 79%, leaps 8%, interval entropy 2.23
- `Memphis Cult - 9mm [2Dp5Rqruog8SR6x7UtlIpY]` — triadic 12%, seventh/extended 88%, stepwise 72%, leaps 28%, interval entropy 2.46
- `Ski Mask The Slump God - Tuk-Tuk [0dxGlilXDBOK8UGQfgQq1d]` — triadic 0%, seventh/extended 100%, stepwise 88%, leaps 6%, interval entropy 2.65

## Strongest cross-song leads

These are leads for human review. Pair scores are corpus-relative and should not be read as proof of shared composition or sampling.

### Harmonic alignment

- **Farizki - Shinunoga E-Wa [2xRG1Oq1EA8kotW3TjQoRu]** ↔ **Fujii Kaze - Shinunoga E-Wa [0o9zmvc5f3EFApU52PPIyW]** — 17.252 (retrieval; aligned span 88)
- **DEAN - 하루살이 [2AA1f7utyvXpmtKsEvp5lz]** ↔ **[lyrics가사] dean - 하루살이 (dayfly) ft. sulli, rad museum** — 11.398 (retrieval; aligned span 125)
- **Youra 유라 - RAL 9002 하양 Feat. Heize 가사 Color Coded Lyrics HanRomEng** ↔ **youra - RAL 9002 (feat. HEIZE) [3P9jINw5TmUelQlm0TJGTY]** — 10.869 (retrieval; aligned span 87)
- **Heize - Jenga (feat. Gaeko) [1dA1tlzwcJ3YDYsSul1m06]** ↔ **헤이즈 (Heize) - Jenga (Feat. Gaeko) MV** — 10.828 (retrieval; aligned span 218)
- **[MV] 유라 (youra) - 미미 (MIMI)** ↔ **youra - MIMI [4RfVtka58I93lEnPsLlBEZ]** — 10.223 (retrieval; aligned span 93)
- **MEOVV - BODY [0u18ibQ2zk470oLSP3D5WR]** ↔ **ひとひら** — 10.034 (retrieval; aligned span 4)
- **Creepy Nuts - よふかしのうた - Yofukashino Uta [1j6Agt1Sn2DjQ0NA33J3r2]** ↔ **Sheena Ringo - 薄ら氷心中 [1DxOabtnI3W4ouECc6AdyT]** — 9.675 (retrieval; aligned span 3)
- **Honeymoon Un Deux Trois - Piano Version** ↔ **Penguin Piano - Honeymoon Un Deux Trois - Piano Version [6kotBBNxJJ1sVnyBqF3J4a]** — 9.347 (retrieval; aligned span 79)
- **Heize - And July [0Yz3F0UGDibDe8uU69zmjn]** ↔ **헤이즈 (Heize) - And July (Feat. DEAN, DJ Friz) MV** — 9.080 (retrieval; aligned span 184)
- **Punch - 가끔 이러다 [2EqejG8TnQ09Wwu7HZh0aj]** ↔ **[MV] Punch(펀치)  Sometimes(가끔 이러다)** — 8.738 (retrieval; aligned span 159)

### Melodic alignment

- **Dancing In My Closet (Feat. 이고도)** ↔ **J.BASS - Dancing in my closet (feat. Lee go do) [59G16khcnG0PYHJL2hZzkE]** — 77.817 (retrieval; aligned span 1541)
- **Farizki - Shinunoga E-Wa [2xRG1Oq1EA8kotW3TjQoRu]** ↔ **Fujii Kaze - Shinunoga E-Wa [0o9zmvc5f3EFApU52PPIyW]** — 41.335 (retrieval; aligned span 1435)
- **Heize - Jenga (feat. Gaeko) [1dA1tlzwcJ3YDYsSul1m06]** ↔ **헤이즈 (Heize) - Jenga (Feat. Gaeko) MV** — 39.563 (retrieval; aligned span 1586)
- **BEAST- 비가 오는 날엔 (On Rainy Days) lyrics [Eng.  Rom.  Han.]** ↔ **Beast - On Rainy Days [2njgIBj0nJ1UUFYNuW06et]** — 38.302 (retrieval; aligned span 1598)
- **Honeymoon Un Deux Trois - Piano Version** ↔ **Penguin Piano - Honeymoon Un Deux Trois - Piano Version [6kotBBNxJJ1sVnyBqF3J4a]** — 37.302 (retrieval; aligned span 939)
- **Kim Hyunchang - Sunshine [0CAZxQvGYpHEatgbECwkuV]** ↔ **Sunshine(볕)** — 30.514 (retrieval; aligned span 1917)
- **Heize - Love Virus (Feat. I.M) [5jgXg4Sen0qi02jDf68RSA]** ↔ **헤이즈(Heize) - Love Virus (Feat. I.M) MV** — 29.345 (retrieval; aligned span 2149)
- **Ethan Bortnick - engravings [4u5CfuAGAHNYDIHSSW7BiY]** ↔ **Ethan Bortnick - engravings [7ByDxFf6Xy7xPFAYafsLxI]** — 28.089 (retrieval; aligned span 1137)
- **Bridge Collective  The Wonderful Blood** ↔ **Bridge Collective - The Wonderful Blood [3t4A9kNTO7VNT8RWOFzjgz]** — 26.307 (retrieval; aligned span 3884)
- **HALCALI  おつかれSUMMER (Official Music Video)** ↔ **HALCALI - おつかれSUMMER [3clPsjTbx4UOPMeQn7FZkV]** — 25.806 (retrieval; aligned span 1897)

## Method and limits

- Stem separation, Basic Pitch/ADTLib transcription, Chordino extraction, chord-tone tagging, correction, export, sequence construction, and the validated Smith–Waterman scoring were run in the repository’s documented order.
- For a library this size, candidate retrieval selects high-overlap n-gram neighbors and adds deterministic background pairs before the same alignment scorer runs. This keeps the analysis tractable while retaining a real-corpus comparison set; the `candidate_type` column records the distinction.
- Transcription errors, missing short notes, and the `other`-stem scope can change both counts and alignments. Treat the groups and pair rankings as review queues rather than definitive musical judgments.
