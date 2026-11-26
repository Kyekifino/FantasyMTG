from __future__ import annotations

from typing import Sequence


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


def truncate_keeping_suffix(text: str, suffix: str, width: int) -> str:
    """Fit "text suffix" in width, shortening text first so the suffix stays readable.

    "Avacyn, Angel of Horror" + "(FRC #7)" at 28 -> "Avacyn, Angel of Ho… (FRC #7)".
    Falls back to plain truncation when there isn't room for the suffix and a few letters.
    """
    full = f"{text} {suffix}"
    if len(full) <= width:
        return full
    room = width - len(suffix) - 2  # the ellipsis and the space before the suffix
    if room < 3:
        return truncate(full, width)
    return f"{text[:room].rstrip()}… {suffix}"


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    align_right: Sequence[int] = (),
) -> str:
    """Render a monospace table wrapped in a Markdown code block."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells: Sequence[str]) -> str:
        out = []
        for i, cell in enumerate(cells):
            out.append(cell.rjust(widths[i]) if i in align_right else cell.ljust(widths[i]))
        return "  ".join(out).rstrip()

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines += [fmt(r) for r in rows]
    return "```\n" + "\n".join(lines) + "\n```"
