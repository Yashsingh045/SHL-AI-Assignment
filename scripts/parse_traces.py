"""Parse the 10 official sample conversations into data/ground_truth.json.

Each trace (data/traces/C*.md) is a markdown transcript with **User** / **Agent**
turns and markdown shortlist tables. The LAST table in a file is the labeled
expected shortlist that Recall@10 is scored against.

Run:  python scripts/parse_traces.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACES_DIR = ROOT / "data" / "traces"
OUT_PATH = ROOT / "data" / "ground_truth.json"


def _trace_sort_key(p: Path) -> int:
    m = re.search(r"C(\d+)", p.stem)
    return int(m.group(1)) if m else 0


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row into stripped cells (dropping edge pipes)."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells) and any(
        "-" in c for c in cells
    )


def _clean_url(raw: str) -> str:
    return raw.strip().lstrip("<").rstrip(">").strip()


def parse_trace(text: str) -> dict:
    lines = text.splitlines()

    user_turns: list[str] = []
    tables: list[list[dict]] = []  # each parsed table, in file order

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # ---- User turn: capture the following blockquote block ----
        if re.fullmatch(r"\*\*User\*\*", stripped):
            i += 1
            quote_lines: list[str] = []
            # Skip blank lines between the marker and the blockquote.
            while i < n and lines[i].strip() == "":
                i += 1
            # Collect consecutive blockquote lines (">" prefixed, blanks allowed
            # inside as paragraph breaks).
            while i < n and (lines[i].lstrip().startswith(">") or (
                quote_lines and lines[i].strip() == ""
            )):
                if lines[i].lstrip().startswith(">"):
                    content = lines[i].lstrip()[1:]
                    if content.startswith(" "):
                        content = content[1:]
                    quote_lines.append(content)
                else:
                    quote_lines.append("")  # paragraph break inside the quote
                i += 1
            # Join, collapsing runs of blank lines, trimming edges.
            msg = "\n".join(quote_lines).strip()
            msg = re.sub(r"\n{3,}", "\n\n", msg)
            if msg:
                user_turns.append(msg)
            continue

        # ---- Table block: a run of lines starting with "|" ----
        if stripped.startswith("|"):
            block: list[str] = []
            while i < n and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            rows = [_split_table_row(r) for r in block]
            items: list[dict] = []
            for cells in rows:
                if len(cells) < 3:
                    continue
                if _is_separator_row(cells):
                    continue
                # Header row detection (case-insensitive).
                joined = " ".join(cells).lower()
                if "name" in joined and "test type" in joined and "url" in joined:
                    continue
                name = cells[1]
                test_type = cells[2]
                url = _clean_url(cells[-1])
                if not url or not name:
                    continue
                items.append({"name": name, "test_type": test_type, "url": url})
            if items:
                tables.append(items)
            continue

        i += 1

    final_shortlist = tables[-1] if tables else []
    return {
        "user_turns": user_turns,
        "final_shortlist": final_shortlist,
        "num_turns": len(user_turns),
    }


def main() -> None:
    files = sorted(TRACES_DIR.glob("C*.md"), key=_trace_sort_key)
    ground_truth = []
    for f in files:
        parsed = parse_trace(f.read_text(encoding="utf-8"))
        ground_truth.append({"trace_id": f.stem, **parsed})

    OUT_PATH.write_text(
        json.dumps(ground_truth, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {OUT_PATH} ({len(ground_truth)} traces)")
    for t in ground_truth:
        print(
            f"  {t['trace_id']}: {t['num_turns']} user turns, "
            f"{len(t['final_shortlist'])} items in final shortlist"
        )


if __name__ == "__main__":
    main()
