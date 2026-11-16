from __future__ import annotations

from typing import Sequence


def truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


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
