"""Order-preserving Needleman–Wunsch alignment of repeated lyric lines."""
import re
import unicodedata


def normalize(text):
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("ー", "")
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in text).split())


def romanize_hangul(text):
    from korean_romanizer.romanizer import Romanizer
    return re.sub(r"[가-힣]+", lambda m: Romanizer(m.group()).romanize(), text)


def japanese_readings(text):
    """Return Hepburn and kana readings; pykakasi handles kanji and kana."""
    from pykakasi import kakasi
    converter = kakasi()
    pieces = converter.convert(text)
    return ("".join(p.get("hepburn", "") for p in pieces),
            "".join(p.get("kana", "") for p in pieces))


def similarity(a, b):
    from rapidfuzz.fuzz import ratio
    return ratio(normalize(a).replace(" ", ""), normalize(b).replace(" ", "")) / 100


def align_lines(source, timed, min_similarity=.45, romanize=False, language=None):
    """Return matched indices/scores; every target index can be used only once.

    A global alignment keeps distinct repeated choruses in their source order.
    Gaps are preferable to a very poor text match; unmatched lines stay missing.
    """
    def candidates(a, b):
        direct = similarity(a, b)
        if language == "ja":
            ah, ak = japanese_readings(a)
            bh, bk = japanese_readings(b)
            roman = max(similarity(ah, bh), similarity(ak, bk))
            return max(direct, roman), "direct" if direct >= roman else "romanized"
        if language == "ko" or romanize:
            roman = similarity(romanize_hangul(a), romanize_hangul(b))
            return max(direct, roman), "direct" if direct >= roman else "romanized"
        return direct, "direct"

    n, m = len(source), len(timed)
    gap = -.55
    table = [[0.] * (m + 1) for _ in range(n + 1)]
    trace = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        table[i][0], trace[i][0] = i * gap, 1
    for j in range(1, m + 1):
        table[0][j], trace[0][j] = j * gap, 2
    pairs = [[candidates(a, b) for b in timed] for a in source]
    scores = [[pair[0] for pair in row] for row in pairs]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = (table[i - 1][j - 1] + 2 * scores[i - 1][j - 1] - 1,
                       table[i - 1][j] + gap, table[i][j - 1] + gap)
            direction = max(range(3), key=lambda d: options[d])
            table[i][j], trace[i][j] = options[direction], direction
    matches = []
    i, j = n, m
    while i or j:
        direction = trace[i][j]
        if i and j and direction == 0:
            score = scores[i - 1][j - 1]
            if score >= min_similarity:
                matches.append({"source_index": i - 1, "target_index": j - 1, "similarity": score,
                                "match_method": pairs[i - 1][j - 1][1]})
            i, j = i - 1, j - 1
        elif i and (not j or direction == 1):
            i -= 1
        else:
            j -= 1
    return list(reversed(matches))
