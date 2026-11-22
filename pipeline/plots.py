"""Timeline and piano-roll artifacts; never synthesize missing predictions."""
import hashlib


def colour(pid):
    from matplotlib import colormaps
    value = int(hashlib.sha256(pid.encode()).hexdigest()[:6], 16) % 20
    return colormaps["tab20"](value)


def timeline(joined, patterns, grid, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    instruments = sorted({r["instrument"] for r in patterns["runs"]})
    has_lyrics = joined is not None
    offset = 2 if has_lyrics else 1
    fig, ax = plt.subplots(figsize=(18, max(4, 2 + .7 * (len(instruments) + offset - 1))))
    if has_lyrics:
        for row in joined["lyric_sections"]:
            a, b = row.get("raw_start_s"), row.get("raw_end_s")
            if a is None or b is None:
                continue
            ax.broken_barh([(a, b - a)], (0, .7), facecolors=colour(row["label"]))
            ax.text((a + b) / 2, .35, row.get("display_label", row["label"]), ha="center", va="center", fontsize=7, rotation=25)
    for r in patterns["rhythmic_sections"]:
        y = 1 if has_lyrics else 0
        ax.broken_barh([(r["start_s"], r["end_s"] - r["start_s"])], (y, .7), facecolors=colour(r["rhythmic_section_id"]))
        odd = ", ".join(item["token"] for item in r.get("irregular_bars", []))
        label = f"{r['rhythmic_section_id']} {odd}".strip()
        ax.text((r["start_s"] + r["end_s"]) / 2, y + .35, label, ha="center", va="center", fontsize=7)
    for i, instrument in enumerate(instruments, offset):
        for row in patterns["bars"]:
            if row["instrument"] != instrument or not row["active"]:
                continue
            bar = grid["bars"][row["bar"]]
            ax.broken_barh([(bar["start_s"], min(grid["duration_s"], bar["end_s"]) - bar["start_s"])],
                          (i, .7), facecolors=colour(row["pattern_id"]))
    for t in grid["downbeat_times"]:
        ax.axvline(t, color="#333333", lw=.35, alpha=.35)
    labels = (["Lyric sections", "Rhythmic sections"] if has_lyrics else ["Rhythmic sections"]) + instruments
    ax.set_yticks([i + .35 for i in range(len(labels))], labels)
    ax.set_xlim(0, grid["duration_s"])
    ax.set_ylim(-.2, len(instruments) + offset)
    ax.set_xlabel("Original recording time (seconds); thin ticks are measured downbeats")
    ax.set_title("Lyric form and rhythm patterns — candidate model evidence" if has_lyrics
                 else "Rhythm patterns — candidate model evidence")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def piano_rolls(sources, start, end, grid, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    fig, axes = plt.subplots(1, len(sources), figsize=(max(12, len(sources) * 4), 6), sharex=True, sharey=True, squeeze=False)
    for ax, (name, notes) in zip(axes[0], sources.items()):
        for note in notes:
            a, b = note["onset_s"], note["offset_s"]
            ax.add_patch(Rectangle((a, note["pitch"] - .4), b - a, .8,
                                   color=colour(note["instrument"]), alpha=.75))
        for t in grid["downbeat_times"]:
            if start <= t <= end:
                ax.axvline(t, color="#555555", lw=.6, alpha=.5)
        ax.set_title(name)
        ax.set_xlim(start, end)
        ax.set_ylim(20, 110)
        ax.set_xlabel("Original seconds")
    axes[0][0].set_ylabel("MIDI pitch (drum keys are classes)")
    fig.suptitle("Same backend, same window and conditioning; full mix versus htdemucs stems")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
