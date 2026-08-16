"""Moodle gradebook CSV/ODS importer.

Parses the Moodle gradebook export (any variant: 1-col or 3-col per activity)
into a flat DataFrame of (matricula, activity_base_name, pct) rows.

Real export shape (from HANDOFF.md):
  - Sheet "Calificaciones", shape ~21×199
  - Identity cols: Nombre, Apellido(s), Número de ID (matrícula), Institución, Departamento, Email
  - Each activity = 3 columns: <name> (Real) · <name> (Porcentaje) · <name> (Letra)
  - Values like "90.00 %" or "-" for not attempted
  - Activity columns prefixed: "Examen:<name>" or "Tarea:<name>"
  - Trailing columns: "Total del curso (Real/Porcentaje/Letra)" + download timestamp
"""

from __future__ import annotations
import math
import re
from pathlib import Path

import pandas as pd


_VARIANT_SUFFIXES = re.compile(r"\s*\((Real|Porcentaje|Letra)\)\s*$", re.I)
_PCT_CLEAN = re.compile(r"[%,\s]")
_MOODLE_PREFIX = re.compile(r"^(Examen|Tarea):\s*", re.I)
_IDENTITY_COLS = {"nombre", "apellido(s)", "apellidos", "número de id", "numero de id",
                  "institución", "institucion", "departamento", "dirección email",
                  "direccion email", "email address", "email"}
_TOTAL_COURSE = re.compile(r"total del curso", re.I)
_TIMESTAMP_HINT = re.compile(r"descargado|download|timestamp|fecha de descarga", re.I)


def _norm_col(c: str) -> str:
    return str(c).strip().lower()


def _find_header_row(raw: pd.DataFrame) -> int:
    for i in range(min(15, len(raw))):
        row = raw.iloc[i].astype(str).str.lower().str.strip()
        hits = sum([
            row.str.contains("nombre").any(),
            row.str.contains(r"apellido", regex=True).any(),
            row.str.contains(r"n[uú]mero|numero", regex=True).any(),
        ])
        if hits >= 2:
            return i
    return 0


def _coerce_pct(val: object) -> float | None:
    """Parse a percentage cell like '90.00 %', '85', '-', or NaN → float | None."""
    s = str(val).strip() if val is not None else ""
    if s in ("", "-", "nan", "NaN", "None"):
        return None
    s = _PCT_CLEAN.sub("", s)
    try:
        x = float(s)
    except ValueError:
        return None
    # Values like "0.90" are fractions, not %; normalize to 0–100
    if x <= 1.2 and "%" not in str(val):
        return x * 100.0
    return x


def _base_name(col: str) -> str:
    """Strip (Real)/(Porcentaje)/(Letra) suffix and Moodle type prefix."""
    name = _VARIANT_SUFFIXES.sub("", col).strip()
    name = _MOODLE_PREFIX.sub("", name).strip()
    return name


def _is_identity(col: str) -> bool:
    return _norm_col(col) in _IDENTITY_COLS


def _is_total_course(col: str) -> bool:
    return bool(_TOTAL_COURSE.search(col))


def _is_timestamp(col: str) -> bool:
    return bool(_TIMESTAMP_HINT.search(col))


def load_gradebook(file_path: str | Path, sheet_name: str = "Calificaciones") -> pd.DataFrame:
    """Load a Moodle gradebook export and return a tidy DataFrame.

    Returns columns: matricula, activity, pct
      - matricula: student ID string
      - activity: base activity name (Moodle type prefix stripped)
      - pct: percentage grade 0–100, or None if not attempted
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv"):
        sep = "\t" if ext == ".tsv" else ","
        raw = pd.read_csv(path, sep=sep, header=None, dtype=object)
    elif ext in (".ods",):
        raw = pd.read_excel(path, sheet_name=sheet_name, engine="odf", header=None, dtype=object)
    elif ext in (".xlsx", ".xls"):
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    hdr_idx = _find_header_row(raw)
    cols = raw.iloc[hdr_idx].astype(str).str.strip().tolist()
    df = raw.iloc[hdr_idx + 1:].copy()
    df.columns = cols
    df = df.reset_index(drop=True)

    # Find the matrícula column
    mat_col = None
    for c in cols:
        if re.search(r"n[uú]mero\s*de\s*id|numero\s*de\s*id", c, re.I):
            mat_col = c
            break
    if mat_col is None:
        raise ValueError("Could not find matrícula column (Número de ID).")

    # Extract matrícula digits only
    matriculas = (
        df[mat_col].astype(str)
        .str.extract(r"(\d+)")[0]
        .fillna("")
        .str.strip()
    )

    # Identify activity columns: not identity, not total del curso, not timestamp
    # Prefer (Porcentaje) column if present; fall back to the single column.
    activity_cols: dict[str, str] = {}  # base_name → column with pct values
    for c in cols:
        if _is_identity(c) or _is_total_course(c) or _is_timestamp(c):
            continue
        base = _base_name(c)
        if not base:
            continue
        norm = _norm_col(c)
        is_pct = "(porcentaje)" in norm
        is_real = "(real)" in norm
        is_letra = "(letra)" in norm
        if is_letra:
            continue  # discard letter-grade columns
        if is_pct or (base not in activity_cols and not is_real):
            activity_cols[base] = c

    # Build tidy output
    records = []
    for i, mat in enumerate(matriculas):
        if not mat:
            continue
        for base, col in activity_cols.items():
            raw_val = df[col].iloc[i]
            pct = _coerce_pct(raw_val)
            records.append({"matricula": mat, "activity": base, "pct": pct})

    return pd.DataFrame(records, columns=["matricula", "activity", "pct"])


_NOMBRE_COL = re.compile(r"^\s*nombre\s*$", re.I)
_APELLIDO_COL = re.compile(r"apellido", re.I)


def load_roster(file_path: str | Path, sheet_name: str = "Calificaciones") -> pd.DataFrame:
    """Load just the student roster (matrícula → full name) from a Moodle export.

    The Moodle gradebook export carries 'Nombre' and 'Apellido(s)' identity columns
    that ``load_gradebook`` deliberately drops. This pulls them so the cockpit can show
    real names instead of a placeholder.

    Returns columns: matricula, full_name. Students with no parseable matrícula are
    skipped. ``full_name`` falls back to the matrícula if both name cells are blank.
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv"):
        sep = "\t" if ext == ".tsv" else ","
        raw = pd.read_csv(path, sep=sep, header=None, dtype=object)
    elif ext == ".ods":
        raw = pd.read_excel(path, sheet_name=sheet_name, engine="odf", header=None, dtype=object)
    elif ext in (".xlsx", ".xls"):
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    hdr_idx = _find_header_row(raw)
    cols = raw.iloc[hdr_idx].astype(str).str.strip().tolist()
    df = raw.iloc[hdr_idx + 1:].copy()
    df.columns = cols
    df = df.reset_index(drop=True)

    mat_col = next((c for c in cols if re.search(r"n[uú]mero\s*de\s*id|numero\s*de\s*id", c, re.I)), None)
    if mat_col is None:
        raise ValueError("Could not find matrícula column (Número de ID).")
    nombre_col = next((c for c in cols if _NOMBRE_COL.search(c)), None)
    apellido_col = next((c for c in cols if _APELLIDO_COL.search(c)), None)

    def _clean(val: object) -> str:
        s = str(val).strip() if val is not None else ""
        return "" if s.lower() in ("", "nan", "none") else s

    records = []
    for i in range(len(df)):
        mat = re.search(r"(\d+)", _clean(df[mat_col].iloc[i]))
        if not mat:
            continue
        mat = mat.group(1)
        nombre = _clean(df[nombre_col].iloc[i]) if nombre_col else ""
        apellido = _clean(df[apellido_col].iloc[i]) if apellido_col else ""
        full_name = " ".join(p for p in (nombre, apellido) if p).strip() or mat
        records.append({"matricula": mat, "full_name": full_name})

    return pd.DataFrame(records, columns=["matricula", "full_name"]).drop_duplicates(
        subset=["matricula"], keep="last"
    ).reset_index(drop=True)
