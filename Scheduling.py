import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Protocol


def _hours(td: timedelta) -> float:
    return td.total_seconds() / 3600


# =====================================================================
# Core enums and crew requirements
# =====================================================================
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


CREW_REQUIREMENTS: Dict[FlightType, Dict[Role, int]] = {
    FlightType.DOMESTIC: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 1, Role.CABIN_CREW: 3},
    FlightType.INTERNATIONAL: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 2,
                               Role.PURSER: 1, Role.CABIN_CREW: 6},
    FlightType.CARGO: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 1, Role.LOADMASTER: 1},
    FlightType.CHARTER: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 1, Role.CABIN_CREW: 2},
    FlightType.TRAINING: {Role.CAPTAIN: 1, Role.FIRST_OFFICER: 1, Role.FLIGHT_ENGINEER: 1},
}


@dataclass(frozen=True)
class FlightSpec:
    """Defaults shared by every flight of one type."""
    range_km: float          # max distance on one tank (longest allowed leg)
    capacity: float          # how much the flight carries
    capacity_unit: str       # "passengers", "tonnes", ...


FLIGHT_SPECS: Dict[FlightType, FlightSpec] = {
    FlightType.DOMESTIC: FlightSpec(3000, 180, "passengers"),
    FlightType.INTERNATIONAL: FlightSpec(12000, 300, "passengers"),
    FlightType.CARGO: FlightSpec(7000, 100, "tonnes"),
    FlightType.CHARTER: FlightSpec(5000, 50, "passengers"),
    FlightType.TRAINING: FlightSpec(1500, 4, "seats"),
}


# =====================================================================
# Routes (ready for refuel stops and future path optimization)
# =====================================================================
@dataclass
class Leg:
    origin: str
    destination: str
    distance_km: float = 0.0
    refuel_stop: bool = False   # True if `destination` is a refuel-only stop


@dataclass
class Route:
    legs: List[Leg]

    def __post_init__(self):
        if not self.legs:
            raise ValueError("A route needs at least one leg")
        for a, b in zip(self.legs, self.legs[1:]):
            if a.destination != b.origin:
                raise ValueError(f"Broken route: {a.destination} -> {b.origin}")

    @property
    def origin(self) -> str:
        return self.legs[0].origin

    @property
    def destination(self) -> str:
        return self.legs[-1].destination

    @property
    def total_distance_km(self) -> float:
        return sum(l.distance_km for l in self.legs)

    @property
    def refuel_stops(self) -> List[str]:
        return [l.destination for l in self.legs if l.refuel_stop]

    def legs_over_range(self, range_km: float) -> List[Leg]:
        """Legs longer than the range. Legs with unknown distance (0) never match."""
        return [l for l in self.legs if l.distance_km > range_km]


class RoutePlanner(Protocol):
    """Anything with a plan() method can be plugged into the Scheduler."""
    def plan(self, origin: str, destination: str, **constraints) -> Route: ...


class DirectRoutePlanner:
    """Default: one non-stop leg, no refueling."""
    def plan(self, origin: str, destination: str, **constraints) -> Route:
        return Route([Leg(origin, destination)])


class RefuelRoutePlanner:
    """PLACEHOLDER for future work.

    Intended design:
      * Nodes are airports (with coordinates and a `can_refuel` flag).
      * An edge exists between two airports if the distance <= range_km. The
        Scheduler passes range_km in from the flight type (FLIGHT_SPECS), or
        from the flight's own override.
      * Search with Dijkstra / A* (haversine distance as heuristic), with the
        cost being distance, fuel, time, or a weighted mix.
      * Any intermediate airport on the path becomes a Leg with refuel_stop=True.
      * Ground time at each stop extends the flight's end time, so the crew rest
        and duty-hour rules below pick it up automatically.
    """
    def __init__(self, airports=None):
        self.airports = airports or {}

    def plan(self, origin: str, destination: str, range_km: Optional[float] = None,
             **constraints) -> Route:
        raise NotImplementedError("Refuel route optimization is not implemented yet")


# =====================================================================
# Rest rules
# =====================================================================
@dataclass
class RestPolicy:
    # Minimum rest after finishing a flight of this type
    min_rest: Dict[FlightType, timedelta] = field(default_factory=lambda: {
        FlightType.DOMESTIC: timedelta(hours=10),
        FlightType.INTERNATIONAL: timedelta(hours=14),
        FlightType.CARGO: timedelta(hours=12),
        FlightType.CHARTER: timedelta(hours=10),
        FlightType.TRAINING: timedelta(hours=8),
    })
    # Rest is at least this multiple of the flight's duration (long flights need more)
    rest_per_flight_hour: float = 1.0
    # Cap on flying hours in any rolling 7-day window
    max_hours_per_7_days: float = 40.0

    def rest_after(self, flight: "Flight") -> timedelta:
        base = self.min_rest[flight.flight_type]
        scaled = flight.duration * self.rest_per_flight_hour
        return max(base, scaled)


# =====================================================================
# Staff and flights
# =====================================================================
@dataclass(eq=False)
class Staff:
    staff_id: str
    name: str
    role: Role
    certified_for: set = field(default_factory=lambda: set(FlightType))
    max_hours_per_day: float = 8.0
    flights: List["Flight"] = field(default_factory=list, repr=False)

    def can_work(self, flight: "Flight") -> bool:
        return (self.role in CREW_REQUIREMENTS[flight.flight_type]
                and flight.flight_type in self.certified_for)

    def hours_on(self, day) -> float:
        return sum(_hours(f.duration) for f in self.flights if f.start.date() == day)

    def _hours_starting_between(self, a: datetime, b: datetime) -> float:
        return sum(_hours(f.duration) for f in self.flights if a <= f.start < b)

    def violations(self, flight: "Flight", policy: RestPolicy) -> List[str]:
        """Every reason this person can't take `flight`. Empty list = OK."""
        out: List[str] = []
        for f in self.flights:
            if f.overlaps(flight.start, flight.end):
                out.append(f"overlaps {f.flight_id}")
            elif f.end <= flight.start:                      # f came before
                gap, need = flight.start - f.end, policy.rest_after(f)
                if gap < need:
                    out.append(f"needs {_hours(need):.1f}h rest after {f.flight_id} "
                               f"(only {_hours(gap):.1f}h)")
            else:                                            # f comes after
                gap, need = f.start - flight.end, policy.rest_after(flight)
                if gap < need:
                    out.append(f"needs {_hours(need):.1f}h rest before {f.flight_id} "
                               f"(only {_hours(gap):.1f}h)")

        hrs = _hours(flight.duration)
        if self.hours_on(flight.start.date()) + hrs > self.max_hours_per_day:
            out.append(f"exceeds {self.max_hours_per_day:g}h daily limit")

        week = timedelta(days=7)
        for a, b in ((flight.end - week, flight.end), (flight.start, flight.start + week)):
            if self._hours_starting_between(a, b) + hrs > policy.max_hours_per_7_days:
                out.append(f"exceeds {policy.max_hours_per_7_days:g}h per 7 days")
                break
        return out


@dataclass(eq=False)
class Flight:
    flight_id: str
    flight_type: FlightType
    origin: str
    destination: str
    start: datetime
    end: datetime
    crew: List[Staff] = field(default_factory=list)
    route: Optional[Route] = None
    range_km_override: Optional[float] = None    # None = use the type's default
    capacity_override: Optional[float] = None    # None = use the type's default

    def __post_init__(self):
        if self.route is None:
            self.route = Route([Leg(self.origin, self.destination)])

    @property
    def spec(self) -> FlightSpec:
        return FLIGHT_SPECS[self.flight_type]

    @property
    def range_km(self) -> float:
        return self.spec.range_km if self.range_km_override is None else self.range_km_override

    @property
    def capacity(self) -> float:
        return self.spec.capacity if self.capacity_override is None else self.capacity_override

    @property
    def capacity_unit(self) -> str:
        return self.spec.capacity_unit

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

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


# =====================================================================
# Scheduler
# =====================================================================
class Scheduler:
    def __init__(self, rest_policy: Optional[RestPolicy] = None,
                 route_planner: Optional[RoutePlanner] = None):
        self.rest_policy = rest_policy or RestPolicy()
        self.route_planner = route_planner or DirectRoutePlanner()
        self.staff: Dict[str, Staff] = {}
        self.flights: Dict[str, Flight] = {}

    def add_staff(self, s: Staff) -> Staff:
        self.staff[s.staff_id] = s
        return s

    def create_flight(self, flight_id, flight_type, origin, destination,
                      start: datetime, end: datetime,
                      route: Optional[Route] = None,
                      range_km: Optional[float] = None,
                      capacity: Optional[float] = None) -> Flight:
        if end <= start:
            raise ValueError("End must be after start")
        if flight_id in self.flights:
            raise ValueError(f"Flight {flight_id} already exists")
        flight_type = FlightType(flight_type)
        effective_range = FLIGHT_SPECS[flight_type].range_km if range_km is None else range_km
        route = route or self.route_planner.plan(origin, destination, range_km=effective_range)
        too_long = route.legs_over_range(effective_range)
        if too_long:
            raise ValueError("Range exceeded: " + "; ".join(
                f"{l.origin}->{l.destination} {l.distance_km:g} km > {effective_range:g} km"
                for l in too_long))
        f = Flight(flight_id, flight_type, origin, destination, start, end, route=route,
                   range_km_override=range_km, capacity_override=capacity)
        self.flights[flight_id] = f
        return f

    def assign(self, flight: Flight, staff: Staff) -> None:
        if staff in flight.crew:
            raise ValueError(f"{staff.name} is already on {flight.flight_id}")
        if not staff.can_work(flight):
            raise ValueError(f"{staff.name} ({staff.role.value}) is not certified "
                             f"or not needed for {flight.flight_type.value} flights")
        if staff.role not in flight.missing_roles():
            raise ValueError(f"No open {staff.role.value} slots on {flight.flight_id}")
        problems = staff.violations(flight, self.rest_policy)
        if problems:
            raise ValueError(f"{staff.name} unavailable: " + "; ".join(problems))
        flight.crew.append(staff)
        staff.flights.append(flight)

    def auto_crew(self, flight: Flight) -> Dict[Role, int]:
        """All-or-nothing. Returns {} on success, else the shortfall per role."""
        picks: List[Staff] = []
        shortfall: Dict[Role, int] = {}
        for role, n in flight.missing_roles().items():
            candidates = [s for s in self.staff.values()
                          if s.role == role and s.can_work(flight)
                          and not s.violations(flight, self.rest_policy)]
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
                "range_km": f.range_km_override,       # None = type default
                "capacity": f.capacity_override,       # None = type default
                "route": [{"origin": l.origin, "destination": l.destination,
                           "distance_km": l.distance_km, "refuel_stop": l.refuel_stop}
                          for l in f.route.legs],
                "crew": [s.staff_id for s in f.crew],
            } for f in self.flights.values()],
        }
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)

    @classmethod
    def load(cls, path: str, rest_policy: Optional[RestPolicy] = None,
             route_planner: Optional[RoutePlanner] = None) -> "Scheduler":
        with open(path) as fh:
            data = json.load(fh)
        sch = cls(rest_policy, route_planner)
        for d in data["staff"]:
            sch.add_staff(Staff(d["staff_id"], d["name"], Role(d["role"]),
                                {FlightType(t) for t in d["certified_for"]},
                                d["max_hours_per_day"]))
        for d in data["flights"]:
            legs = d.get("route")        # older files have no route
            route = Route([Leg(**l) for l in legs]) if legs else None
            f = Flight(d["flight_id"], FlightType(d["flight_type"]), d["origin"],
                       d["destination"], datetime.fromisoformat(d["start"]),
                       datetime.fromisoformat(d["end"]), route=route,
                       range_km_override=d.get("range_km"),
                       capacity_override=d.get("capacity"))
            for sid in d["crew"]:
                s = sch.staff[sid]
                f.crew.append(s)
                s.flights.append(f)
            sch.flights[f.flight_id] = f
        return sch


if __name__ == "__main__":
    sch = Scheduler()
    asha = sch.add_staff(Staff("P1", "Asha", Role.CAPTAIN))

    a = sch.create_flight("AI101", "domestic", "MAA", "DEL",
                          datetime(2026, 10, 16, 6), datetime(2026, 10, 16, 9))
    sch.assign(a, asha)

    # Same afternoon: only 3h after landing, domestic rest is 10h -> rejected
    b = sch.create_flight("AI102", "domestic", "DEL", "MAA",
                          datetime(2026, 10, 16, 12), datetime(2026, 10, 16, 15))
    try:
        sch.assign(b, asha)
    except ValueError as e:
        print("Rejected:", e)

    # Next morning: 21h rest -> accepted
    c = sch.create_flight("AI103", "domestic", "DEL", "MAA",
                          datetime(2026, 10, 17, 6), datetime(2026, 10, 17, 9))
    sch.assign(c, asha)
    print("Accepted AI103")

    # A flight with a refuel stop supplied by hand (planner can do this later)
    d = sch.create_flight("CG201", "cargo", "MAA", "LHR",
                          datetime(2026, 10, 20, 2), datetime(2026, 10, 20, 16),
                          route=Route([Leg("MAA", "DXB", 3000, refuel_stop=True),
                                       Leg("DXB", "LHR", 5500)]))
    print("Refuel stops:", d.route.refuel_stops)
    print(f"Cargo defaults: {d.range_km:g} km range, {d.capacity:g} {d.capacity_unit}")

    # Range check: an 8000 km non-stop leg is too long for a domestic flight
    try:
        sch.create_flight("AI900", "domestic", "MAA", "LHR",
                          datetime(2026, 10, 25, 6), datetime(2026, 10, 25, 16),
                          route=Route([Leg("MAA", "LHR", 8000)]))
    except ValueError as err:
        print("Rejected:", err)

    # Per-flight override: this charter has a longer-range aircraft
    ch = sch.create_flight("CH300", "charter", "MAA", "SYD",
                           datetime(2026, 10, 26, 1), datetime(2026, 10, 26, 11),
                           route=Route([Leg("MAA", "SYD", 8000)]),
                           range_km=9000, capacity=30)
    print(f"CH300: {ch.range_km:g} km range, {ch.capacity:g} {ch.capacity_unit}")

    sch.save("schedule.json")
    r = Scheduler.load("schedule.json")
    print("Reloaded:", [x.name for x in r.flights["AI103"].crew],
          r.flights["CG201"].route.refuel_stops)
    print("Reloaded CH300:", r.flights["CH300"].range_km, r.flights["CH300"].capacity,
          "| AI103 default range:", r.flights["AI103"].range_km)