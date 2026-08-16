"""Throwaway: dump the structure of the sample Moodle gradebook so the Phase 1
parser is built against real columns. Safe to delete."""
import re
from collections import Counter
import pandas as pd

path = r"C:\Users\carlo\code\Brainstorm\MUSAI\samples\1-LED-A- INGLES I- 5500- 01- 533711 Calificaciones (5).ods"

sheets = pd.read_excel(path, sheet_name=None, engine="odf", header=None)
print(f"SHEETS: {list(sheets.keys())}\n")

for name, df in sheets.items():
    print("=" * 72)
    print(f"SHEET: {name!r}  shape={df.shape}  (rows x cols)")
    print("=" * 72)
    if df.shape[0] == 0:
        continue

    header = [str(c) for c in df.iloc[0].tolist()]
    data = df.iloc[1:]

    # Column-type prefix breakdown (text before the first ':')
    prefixes = Counter()
    for h in header:
        pre = h.split(":", 1)[0].strip() if ":" in h else "(no prefix / identity / total)"
        prefixes[pre] += 1
    print("COLUMN-TYPE PREFIX BREAKDOWN:")
    for pre, n in prefixes.most_common():
        print(f"  {n:3d}  {pre}")
    print()

    # Full header list
    print("ALL HEADERS:")
    for i, h in enumerate(header):
        print(f"  [{i}] {h}")
    print()

    # How many student rows (rows with a 6+ digit id somewhere)
    def has_id(row):
        return any(re.search(r"\b\d{5,10}\b", str(v) or "") for v in row.tolist())
    student_rows = sum(1 for _, r in data.iterrows() if has_id(r))
    print(f"DATA ROWS: {len(data)}  | rows-with-an-id: {student_rows}")

    # Show the first 3 data rows, identity + last 3 columns (totals/letters/timestamps)
    show_cols = list(range(min(6, len(header)))) + list(range(max(0, len(header) - 4), len(header)))
    show_cols = sorted(set(show_cols))
    print("\nFIRST 3 DATA ROWS (identity cols + last few cols):")
    for _, r in data.head(3).iterrows():
        vals = [f"[{c}]{header[c][:22]}={str(r.iloc[c])[:18]!r}" for c in show_cols]
        print("  " + " | ".join(vals))
    print()
