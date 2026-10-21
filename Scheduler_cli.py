"""Interactive console for entering and managing schedule data by hand.

Run:  python scheduler_cli.py [data_file.json]
"""
import os
import sys
from datetime import datetime

from Scheduling import (Airport, Flight, FlightType, Leg, Role, Route,
                              Scheduler, Staff, FLIGHT_SPECS)

DT_FMT = "%Y-%m-%d %H:%M"


# ---------------------------------------------------------------- input helpers
def ask(prompt, default=None):
    """Text input. With a default (even ""), Enter accepts it; otherwise a value is required."""
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        print("  A value is required.")


def ask_float(prompt, default=None, optional=False):
    while True:
        raw = ask(prompt, "" if optional else (None if default is None else str(default)))
        if optional and raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            print("  Please enter a number.")


def ask_yes_no(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} ({hint}): ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please answer y or n.")


def ask_datetime(prompt):
    while True:
        raw = ask(f"{prompt} (YYYY-MM-DD HH:MM)")
        try:
            return datetime.strptime(raw, DT_FMT)
        except ValueError:
            print("  Use the format 2026-10-16 06:30")


def ask_enum(enum_cls, prompt):
    items = list(enum_cls)
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it.value}")
    while True:
        raw = input(f"{prompt}: ").strip().lower()
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        try:
            return enum_cls(raw)
        except ValueError:
            print("  Pick a number or a name from the list.")


def ask_flight_types(prompt):
    """Comma-separated numbers/names; Enter = all flight types."""
    items = list(FlightType)
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it.value}")
    while True:
        raw = input(f"{prompt} (Enter = all): ").strip().lower()
        if not raw:
            return set(items)
        chosen, ok = set(), True
        for part in (p.strip() for p in raw.split(",")):
            if part.isdigit() and 1 <= int(part) <= len(items):
                chosen.add(items[int(part) - 1])
            else:
                try:
                    chosen.add(FlightType(part))
                except ValueError:
                    ok = False
        if ok and chosen:
            return chosen
        print("  Use numbers or names separated by commas, e.g. 1,3")


# ---------------------------------------------------------------- lookups
def get_flight(sch: Scheduler, flight_id: str) -> Flight:
    if flight_id not in sch.flights:
        raise ValueError(f"No flight '{flight_id}'. Known: {', '.join(sch.flights) or 'none'}")
    return sch.flights[flight_id]


def get_staff(sch: Scheduler, staff_id: str) -> Staff:
    if staff_id not in sch.staff:
        raise ValueError(f"No staff member '{staff_id}'. Known: {', '.join(sch.staff) or 'none'}")
    return sch.staff[staff_id]


# ---------------------------------------------------------------- actions
def add_airport(sch):
    code = ask("Airport code (e.g. MAA)").upper()
    name = ask("Name")
    lat = ask_float("Latitude (-90 to 90)")
    lon = ask_float("Longitude (-180 to 180)")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude/longitude out of range")
    can_refuel = ask_yes_no("Can aircraft refuel here?", True)
    sch.add_airport(Airport(code, name, lat, lon, can_refuel))
    print(f"  Added airport {code}.")


def add_staff(sch):
    staff_id = ask("Staff ID")
    if staff_id in sch.staff:
        raise ValueError(f"Staff ID {staff_id} already exists")
    name = ask("Name")
    role = ask_enum(Role, "Role")
    certified = ask_flight_types("Certified flight types")
    hours = ask_float("Max flying hours per day", 8)
    sch.add_staff(Staff(staff_id, name, role, certified, hours))
    print(f"  Added {name} ({role.value}).")


def create_flight(sch):
    flight_id = ask("Flight ID")
    ftype = ask_enum(FlightType, "Flight type")
    spec = FLIGHT_SPECS[ftype]
    origin = ask("Origin airport code").upper()
    dest = ask("Destination airport code").upper()
    start = ask_datetime("Departure")
    end = ask_datetime("Arrival")
    stops_raw = ask("Refuel stops in order, comma-separated (Enter for none)", "")
    stops = [s.strip().upper() for s in stops_raw.split(",") if s.strip()]
    range_km = ask_float(f"Range override in km (Enter = {spec.range_km:g})", optional=True)
    capacity = ask_float(f"Capacity override in {spec.capacity_unit} "
                         f"(Enter = {spec.capacity:g})", optional=True)

    route = None
    if stops:
        points = [origin] + stops + [dest]
        route = Route([Leg(a, b, refuel_stop=(b != dest))
                       for a, b in zip(points, points[1:])])
    f = sch.create_flight(flight_id, ftype, origin, dest, start, end,
                          route=route, range_km=range_km, capacity=capacity)
    print(f"  Created {flight_id}. Needs: {describe_missing(f)}")


def describe_missing(f: Flight) -> str:
    return ", ".join(f"{n}x {r.value}" for r, n in f.missing_roles().items()) or "nobody (fully crewed)"


def assign_staff(sch):
    f = get_flight(sch, ask("Flight ID"))
    print(f"  Open slots: {describe_missing(f)}")
    s = get_staff(sch, ask("Staff ID to assign"))
    sch.assign(f, s)
    print(f"  Assigned {s.name} to {f.flight_id}.")


def auto_crew(sch):
    f = get_flight(sch, ask("Flight ID"))
    shortfall = sch.auto_crew(f)
    if shortfall:
        print("  Could not fully crew the flight; nobody was assigned. Short by: "
              + ", ".join(f"{n}x {r.value}" for r, n in shortfall.items()))
    else:
        print(f"  {f.flight_id} is fully crewed.")


def remove_staff(sch):
    f = get_flight(sch, ask("Flight ID"))
    s = get_staff(sch, ask("Staff ID to remove"))
    sch.unassign(f, s)
    print(f"  Removed {s.name} from {f.flight_id}.")


def cancel_flight(sch):
    f = get_flight(sch, ask("Flight ID to cancel"))
    if ask_yes_no(f"Cancel {f.flight_id} and free its crew?", False):
        sch.cancel_flight(f.flight_id)
        print("  Cancelled.")


def view_all(sch):
    print("\nAirports:")
    for a in sch.airports.values():
        print(f"  {a.code}  {a.name} ({a.lat:g}, {a.lon:g})"
              + ("" if a.can_refuel else "  [no refuelling]"))
    if not sch.airports:
        print("  (none)")
    print("\nStaff:")
    for s in sch.staff.values():
        types = ", ".join(sorted(t.value for t in s.certified_for))
        print(f"  {s.staff_id}  {s.name}  {s.role.value}  max {s.max_hours_per_day:g}h/day"
              f"  flights: {len(s.flights)}  certified: {types}")
    if not sch.staff:
        print("  (none)")
    print("\nFlights:")
    for f in sch.flights.values():
        stops = f.route.refuel_stops
        print(f"  {f.flight_id} [{f.flight_type.value}] {f.origin}->{f.destination}  "
              f"{f.start:{DT_FMT}} to {f.end:{DT_FMT}}")
        print(f"      range {f.range_km:g} km, capacity {f.capacity:g} {f.capacity_unit}"
              + (f", refuel at {', '.join(stops)}" if stops else ""))
        crew = ", ".join(f"{s.name} ({s.role.value})" for s in f.crew) or "none"
        print(f"      crew: {crew}\n      missing: {describe_missing(f)}")
    if not sch.flights:
        print("  (none)")
    print()


# ---------------------------------------------------------------- main loop
MENU = """
==== Flight crew scheduler ====
 1  Add airport              6  Remove staff from flight
 2  Add staff member         7  Cancel flight
 3  Create flight            8  View everything
 4  Assign staff to flight   9  Save
 5  Auto-crew a flight      10  Load
 0  Quit
"""


def main(path="schedule.json"):
    if os.path.exists(path):
        sch = Scheduler.load(path)
        print(f"Loaded {path}: {len(sch.airports)} airports, {len(sch.staff)} staff, "
              f"{len(sch.flights)} flights.")
    else:
        sch = Scheduler()
        print(f"Starting empty. Data will be saved to {path}.")

    actions = {"1": add_airport, "2": add_staff, "3": create_flight, "4": assign_staff,
               "5": auto_crew, "6": remove_staff, "7": cancel_flight, "8": view_all}
    try:
        while True:
            print(MENU)
            choice = input("Choose: ").strip()
            if choice == "0":
                if ask_yes_no(f"Save to {path} before quitting?", True):
                    sch.save(path)
                    print(f"Saved to {path}.")
                break
            elif choice == "9":
                sch.save(ask("File", path))
                print("  Saved.")
            elif choice == "10":
                src = ask("File", path)
                if os.path.exists(src):
                    sch = Scheduler.load(src)
                    print("  Loaded.")
                else:
                    print(f"  No such file: {src}")
            elif choice in actions:
                try:
                    actions[choice](sch)
                except ValueError as err:
                    print(f"  Error: {err}")
            else:
                print("  Please choose a number from the menu.")
    except (EOFError, KeyboardInterrupt):
        print("\nExiting without saving.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "schedule.json")