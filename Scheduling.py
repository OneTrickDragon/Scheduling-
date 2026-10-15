import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List


class FlightType(str, Enum):
    DOMESTIC = "domestic"
    INTERNATIONAL = "international"
    CARGO = "cargo"
    CHARTER = "charter"
    TRAINING = "training"


class Role(str, Enum):
    CAPTAIN = "captain"
    FIRST_OFFICER = "first_officer"
    PURSER = "purser"
    CABIN_CREW = "cabin_crew"
    LOADMASTER = "loadmaster"
    FLIGHT_ENGINEER = "flight_engineer"


# Crew needed for each type of flight: {role: headcount}
CREW_REQUIREMENTS: Dict[FlightType, Dict[Role, int]] = {
    FlightType.DOMESTIC: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 1, Role.CABIN_CREW: 3},
    FlightType.INTERNATIONAL: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 2,
                               Role.PURSER: 1, Role.CABIN_CREW: 6},
    FlightType.CARGO: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 1, Role.LOADMASTER: 1},
    FlightType.CHARTER: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 1, Role.CABIN_CREW: 2},
    FlightType.TRAINING: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 1, Role.FLIGHT_ENGINEER: 1},
}


@dataclass(eq=False)
class Staff:
    staff_id: str
    name: str
    role: Role
    certified_for: set = field(default_factory=lambda: set(FlightType))
    max_hours_per_day: float = 8.0
    flights: List["Flight"] = field(default_factory=list, repr=False)

    def can_work(self, flight: "Flight") -> bool:
        needed = CREW_REQUIREMENTS[flight.flight_type]
        return self.role in needed and flight.flight_type in self.certified_for

    def hours_on(self, day) -> float:
        return sum((f.end - f.start).total_seconds() / 3600
                   for f in self.flights if f.start.date() == day)

    def is_available(self, start: datetime, end: datetime) -> bool:
        if any(f.overlaps(start, end) for f in self.flights):
            return False
        new_hours = (end - start).total_seconds() / 3600
        return self.hours_on(start.date()) + new_hours <= self.max_hours_per_day


@dataclass(eq=False)
class Flight:
    flight_id: str
    flight_type: FlightType
    origin: str
    destination: str
    start: datetime
    end: datetime
    crew: List[Staff] = field(default_factory=list)

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end

    def missing_roles(self) -> Dict[Role, int]:
        missing = {}
        for role, needed in CREW_REQUIREMENTS[self.flight_type].items():
            have = sum(1 for s in self.crew if s.role == role)
            if have < needed:
                missing[role] = needed - have
        return missing

    def is_fully_crewed(self) -> bool:
        return not self.missing_roles()


class Scheduler:
    def __init__(self):
        self.staff: Dict[str, Staff] = {}
        self.flights: Dict[str, Flight] = {}

    # ---- setup ----
    def add_staff(self, s: Staff) -> Staff:
        self.staff[s.staff_id] = s
        return s

    def create_flight(self, flight_id, flight_type, origin, destination,
                      start: datetime, end: datetime) -> Flight:
        if end <= start:
            raise ValueError("End must be after start")
        if flight_id in self.flights:
            raise ValueError(f"Flight {flight_id} already exists")
        f = Flight(flight_id, FlightType(flight_type), origin, destination, start, end)
        self.flights[flight_id] = f
        return f

    # ---- assignment ----
    def assign(self, flight: Flight, staff: Staff) -> None:
        if staff in flight.crew:
            raise ValueError(f"{staff.name} is already on {flight.flight_id}")
        if not staff.can_work(flight):
            raise ValueError(f"{staff.name} ({staff.role.value}) is not certified "
                             f"or not needed for {flight.flight_type.value} flights")
        if staff.role not in flight.missing_roles():
            raise ValueError(f"No open {staff.role.value} slots on {flight.flight_id}")
        if not staff.is_available(flight.start, flight.end):
            raise ValueError(f"{staff.name} is unavailable")
        flight.crew.append(staff)
        staff.flights.append(flight)

    def auto_crew(self, flight: Flight) -> Dict[Role, int]:
        """Fill every open slot. All-or-nothing: returns {} on success, or the
        shortfall per role (and assigns nobody) if the flight can't be fully crewed."""
        picks: List[Staff] = []
        shortfall: Dict[Role, int] = {}
        for role, n in flight.missing_roles().items():
            candidates = [s for s in self.staff.values()
                          if s.role == role and s.can_work(flight)
                          and s.is_available(flight.start, flight.end)
                          and s not in picks]
            picks.extend(candidates[:n])
            if len(candidates) < n:
                shortfall[role] = n - len(candidates)
        if shortfall:
            return shortfall
        for s in picks:
            self.assign(flight, s)
        return {}

    def cancel_flight(self, flight_id: str) -> None:
        f = self.flights.pop(flight_id)
        for s in f.crew:
            s.flights.remove(f)

    # ---- persistence ----
    def save(self, path: str) -> None:
        data = {
            "staff": [{
                "staff_id": s.staff_id, "name": s.name, "role": s.role.value,
                "certified_for": sorted(t.value for t in s.certified_for),
                "max_hours_per_day": s.max_hours_per_day,
            } for s in self.staff.values()],
            "flights": [{
                "flight_id": f.flight_id, "flight_type": f.flight_type.value,
                "origin": f.origin, "destination": f.destination,
                "start": f.start.isoformat(), "end": f.end.isoformat(),
                "crew": [s.staff_id for s in f.crew],   # links stored as IDs
            } for f in self.flights.values()],
        }
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "Scheduler":
        with open(path) as fh:
            data = json.load(fh)
        sch = cls()
        for d in data["staff"]:                         # 1) staff first
            sch.add_staff(Staff(d["staff_id"], d["name"], Role(d["role"]),
                                {FlightType(t) for t in d["certified_for"]},
                                d["max_hours_per_day"]))
        for d in data["flights"]:                       # 2) flights, then relink crew
            f = Flight(d["flight_id"], FlightType(d["flight_type"]), d["origin"],
                       d["destination"], datetime.fromisoformat(d["start"]),
                       datetime.fromisoformat(d["end"]))
            for sid in d["crew"]:
                s = sch.staff[sid]
                f.crew.append(s)
                s.flights.append(f)
            sch.flights[f.flight_id] = f
        return sch


if __name__ == "__main__":
    sch = Scheduler()
    sch.add_staff(Staff("P1", "Asha", Role.CAPTAIN))
    sch.add_staff(Staff("P2", "Ravi", Role.FIRST_OFFICER))
    for i in range(3):
        sch.add_staff(Staff(f"C{i}", f"Crew{i}", Role.CABIN_CREW))

    f = sch.create_flight("AI101", "domestic", "MAA", "DEL",
                          datetime(2026, 10, 16, 6), datetime(2026, 10, 16, 9))
    print("Shortfall:", sch.auto_crew(f) or "none")
    print("Fully crewed:", f.is_fully_crewed())

    sch.save("schedule.json")
    reloaded = Scheduler.load("schedule.json")
    print("Reloaded crew:", [s.name for s in reloaded.flights["AI101"].crew])