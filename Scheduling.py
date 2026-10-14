from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

@dataclass
class Booking:
    start: datetime
    end: datetime
    vehicle: "Vehicle"
    operator: "Operator"

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end


@dataclass
class Vehicle:
    vehicle_id: str
    kind: str                     
    capacity: int = 0
    required_license: str = "B"    # license class needed to operate it
    in_service: bool = True
    bookings: List[Booking] = field(default_factory=list, repr=False)

    def is_available(self, start: datetime, end: datetime) -> bool:
        return self.in_service and not any(b.overlaps(start, end) for b in self.bookings)

@dataclass
class Operator:
    operator_id: str
    name: str
    licenses: set = field(default_factory=lambda: {"B"})
    max_hours_per_day: float = 8.0
    bookings: List[Booking] = field(default_factory=list, repr=False)

    def can_operate(self, vehicle: Vehicle) -> bool:
        return vehicle.required_license in self.licenses

    def hours_on(self, day) -> float:
        return sum((b.end - b.start).total_seconds() / 3600
                   for b in self.bookings if b.start.date() == day)

    def is_available(self, start: datetime, end: datetime) -> bool:
        if any(b.overlaps(start, end) for b in self.bookings):
            return False
        new_hours = (end - start).total_seconds() / 3600
        return self.hours_on(start.date()) + new_hours <= self.max_hours_per_day


class Scheduler:
    def __init__(self):
        self.vehicles: List[Vehicle] = []
        self.operators: List[Operator] = []
        self.bookings: List[Booking] = []

    def add_vehicle(self, v: Vehicle): self.vehicles.append(v)
    def add_operator(self, o: Operator): self.operators.append(o)

    def book(self, vehicle: Vehicle, operator: Operator,
             start: datetime, end: datetime) -> Booking:
        if end <= start:
            raise ValueError("End must be after start")
        if not operator.can_operate(vehicle):
            raise ValueError(f"{operator.name} lacks license {vehicle.required_license}")
        if not vehicle.is_available(start, end):
            raise ValueError(f"Vehicle {vehicle.vehicle_id} unavailable")
        if not operator.is_available(start, end):
            raise ValueError(f"Operator {operator.name} unavailable")
        b = Booking(start, end, vehicle, operator)
        vehicle.bookings.append(b)
        operator.bookings.append(b)
        self.bookings.append(b)
        return b

    def auto_assign(self, kind: str, start: datetime, end: datetime) -> Optional[Booking]:
        """Find the first free vehicle of a kind plus a qualified free operator."""
        for v in self.vehicles:
            if v.kind == kind and v.is_available(start, end):
                for o in self.operators:
                    if o.can_operate(v) and o.is_available(start, end):
                        return self.book(v, o, start, end)
        return None

    def cancel(self, booking: Booking):
        booking.vehicle.bookings.remove(booking)
        booking.operator.bookings.remove(booking)
        self.bookings.remove(booking)



if __name__ == "__main__":
    s = Scheduler()
    s.add_vehicle(Vehicle("T-01", "truck", 10, required_license="C"))
    s.add_operator(Operator("O-1", "Ravi", licenses={"B", "C"}))

    b = s.auto_assign("truck", datetime(2026, 10, 15, 9), datetime(2026, 10, 15, 13))
    print(b)