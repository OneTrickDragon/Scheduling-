"""Bulk-load airports, staff and flights from CSV files.

Import in this order: airports, then staff, then flights. Flights check the
airport table and can name their crew by staff ID.

Bad rows are skipped and reported with their line number; good rows are kept.
A flight whose crew list breaks a rule (rest time, certification, ...) is
skipped as a whole, so you never get a flight with half its crew.

Command line:
    python csv_import.py --airports a.csv --staff s.csv --flights f.csv [--data schedule.json]
"""
import argparse
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List

from Scheduling import Airport, FlightType, Role, Route, Scheduler, Staff


@dataclass
class ImportResult:
    added: int = 0
    errors: List[str] = field(default_factory=list)

    def report(self, label: str) -> str:
        lines = [f"  {label}: {self.added} imported, {len(self.errors)} skipped"]
        lines += [f"    {e}" for e in self.errors]
        return "\n".join(lines)


# ---------------------------------------------------------------- cell parsers
def _text(row: Dict[str, str], col: str) -> str:
    value = row.get(col, "")
    if not value:
        raise ValueError(f"'{col}' is empty")
    return value


def _float(row: Dict[str, str], col: str) -> float:
    raw = _text(row, col)
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"'{col}' must be a number, got '{raw}'") from None


def _opt_float(row: Dict[str, str], col: str, default=None):
    return _float(row, col) if row.get(col) else default


def _bool(row: Dict[str, str], col: str, default: bool) -> bool:
    raw = row.get(col, "").lower()
    if not raw:
        return default
    if raw in ("y", "yes", "true", "1"):
        return True
    if raw in ("n", "no", "false", "0"):
        return False
    raise ValueError(f"'{col}' must be yes or no, got '{raw}'")


def _enum(cls, raw: str, label: str):
    try:
        return cls(raw.lower())
    except ValueError:
        options = ", ".join(e.value for e in cls)
        raise ValueError(f"unknown {label} '{raw}' (expected one of: {options})") from None


def _list(raw: str) -> List[str]:
    """Split on | ; or , so cells like 'domestic|cargo' work."""
    return [p.strip() for p in raw.replace(";", "|").replace(",", "|").split("|") if p.strip()]


def _datetime(row: Dict[str, str], col: str) -> datetime:
    raw = _text(row, col)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"'{col}' must look like 2026-10-16 06:30, got '{raw}'") from None
    if dt.tzinfo is not None:
        raise ValueError(f"'{col}': time zones are not supported")
    return dt


# ---------------------------------------------------------------- file runner
def _run(path: str, required: List[str], handler: Callable[[Dict[str, str]], None]) -> ImportResult:
    result = ImportResult()
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                raise ValueError("the file is empty")
            reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
            missing = [c for c in required if c not in reader.fieldnames]
            if missing:
                raise ValueError(f"missing column(s): {', '.join(missing)}. "
                                 f"Found: {', '.join(reader.fieldnames)}")
            for row in reader:
                line = reader.line_num
                row = {k: (v or "").strip() for k, v in row.items() if k is not None}
                if not any(row.values()):
                    continue                         # blank line
                try:
                    handler(row)
                    result.added += 1
                except ValueError as err:
                    result.errors.append(f"line {line}: {err}")
    except csv.Error as err:
        raise ValueError(f"not a valid CSV file: {err}") from None
    return result


# ---------------------------------------------------------------- importers
def import_airports(sch: Scheduler, path: str) -> ImportResult:
    """Columns: code, name, lat, lon, can_refuel (optional, default yes)"""
    def handle(r):
        code = _text(r, "code").upper()
        if code in sch.airports:
            raise ValueError(f"airport {code} already exists")
        lat, lon = _float(r, "lat"), _float(r, "lon")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("lat/lon out of range")
        sch.add_airport(Airport(code, _text(r, "name"), lat, lon, _bool(r, "can_refuel", True)))
    return _run(path, ["code", "name", "lat", "lon"], handle)


def import_staff(sch: Scheduler, path: str) -> ImportResult:
    """Columns: staff_id, name, role, certified_for (optional, blank = all types,
    e.g. 'domestic|cargo'), max_hours_per_day (optional, default 8)"""
    def handle(r):
        staff_id = _text(r, "staff_id")
        if staff_id in sch.staff:
            raise ValueError(f"staff ID {staff_id} already exists")
        role = _enum(Role, _text(r, "role"), "role")
        types = {_enum(FlightType, t, "flight type") for t in _list(r.get("certified_for", ""))}
        hours = _opt_float(r, "max_hours_per_day", 8.0)
        if hours <= 0:
            raise ValueError("'max_hours_per_day' must be positive")
        sch.add_staff(Staff(staff_id, _text(r, "name"), role, types or set(FlightType), hours))
    return _run(path, ["staff_id", "name", "role"], handle)


def import_flights(sch: Scheduler, path: str) -> ImportResult:
    """Columns: flight_id, flight_type, origin, destination, start, end,
    refuel_stops (optional, e.g. 'DXB|BOM'), range_km (optional override),
    capacity (optional override), crew (optional staff IDs, e.g. 'P1|P2|C1')"""
    def handle(r):
        origin, dest = _text(r, "origin").upper(), _text(r, "destination").upper()
        stops = [s.upper() for s in _list(r.get("refuel_stops", ""))]
        crew_ids = _list(r.get("crew", ""))
        unknown = [c for c in crew_ids if c not in sch.staff]
        if unknown:
            raise ValueError(f"unknown staff ID(s): {', '.join(unknown)}")

        flight_id = _text(r, "flight_id")
        flight = sch.create_flight(
            flight_id, _enum(FlightType, _text(r, "flight_type"), "flight type"),
            origin, dest, _datetime(r, "start"), _datetime(r, "end"),
            route=Route.via(origin, dest, stops) if stops else None,
            range_km=_opt_float(r, "range_km"), capacity=_opt_float(r, "capacity"))
        try:
            for cid in crew_ids:
                sch.assign(flight, sch.staff[cid])
        except ValueError:
            sch.cancel_flight(flight_id)             # all or nothing per flight
            raise
    return _run(path, ["flight_id", "flight_type", "origin", "destination", "start", "end"],
                handle)


def main():
    ap = argparse.ArgumentParser(description="Import airports, staff and flights from CSV.")
    ap.add_argument("--airports")
    ap.add_argument("--staff")
    ap.add_argument("--flights")
    ap.add_argument("--data", default="schedule.json", help="JSON file to update (default: %(default)s)")
    args = ap.parse_args()
    if not (args.airports or args.staff or args.flights):
        ap.print_help()
        return
    sch = Scheduler.load(args.data) if os.path.exists(args.data) else Scheduler()
    for label, fn, path in (("airports", import_airports, args.airports),
                            ("staff", import_staff, args.staff),
                            ("flights", import_flights, args.flights)):
        if path:
            try:
                print(fn(sch, path).report(label))
            except (ValueError, OSError) as err:
                print(f"  {label}: could not import: {err}")
                sys.exit(1)
    sch.save(args.data)
    print(f"Saved to {args.data}.")


if __name__ == "__main__":
    main()