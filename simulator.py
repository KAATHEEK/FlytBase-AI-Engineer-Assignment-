"""Simulated drone telemetry + 'video frames' (text captions)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

LOC_XY = {  # metres from the dock
    "Main Gate": (0.0, 0.0),
    "Garage": (30.0, 10.0),
    "Warehouse Door": (60.0, 25.0),
    "Backyard": (20.0, 40.0),
    "Rear Fence": (45.0, 60.0),
}


@dataclass
class Frame:
    frame_id: int
    ts: str  # ISO timestamp, e.g. 2026-11-25T12:00:00
    location: str
    x: float
    y: float
    altitude: float
    battery: int
    description: str  # caption from a VLM, or hand-written in simulation

    @property
    def tod(self) -> str:
        return self.ts[11:16]  # "HH:MM"

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.ts)


# (time, location, caption)
SCENARIO = [
    ("00:01", "Main Gate", "Dark scene: a person in a dark hoodie standing near the main gate, looking around."),
    ("00:04", "Main Gate", "Person in dark hoodie still standing at the main gate, hands in pockets, no vehicle present."),
    ("00:07", "Main Gate", "Person in dark hoodie pacing near the main gate and glancing at the camera."),
    ("00:25", "Garage", "Garage door closed, no people or vehicles visible."),
    ("02:15", "Rear Fence", "Empty perimeter fence, a fox crossing the lawn."),
    ("06:00", "Main Gate", "Main gate closed, empty driveway at sunrise."),
    ("07:55", "Main Gate", "Security guard in uniform standing at the main gate."),
    ("08:00", "Main Gate", "Security guard in uniform standing at the main gate, checking a clipboard."),
    ("08:06", "Main Gate", "Security guard in uniform still standing at the main gate."),
    ("08:30", "Main Gate", "Blue Ford F150 truck entering through the main gate."),
    ("08:32", "Garage", "Blue Ford F150 truck parked at the garage."),
    ("09:15", "Garage", "Blue Ford F150 truck parked at the garage, man unloading a toolbox."),
    ("10:00", "Warehouse Door", "Delivery courier carrying a package toward the warehouse door."),
    ("10:05", "Warehouse Door", "Delivery courier leaving a package at the warehouse door and walking away."),
    ("12:00", "Garage", "Blue Ford F150 truck parked at the garage."),
    ("13:10", "Main Gate", "White delivery van waiting at the main gate."),
    ("13:12", "Main Gate", "White delivery van entering through the main gate."),
    ("15:40", "Main Gate", "Blue Ford F150 truck exiting through the main gate."),
    ("17:20", "Main Gate", "Blue Ford F150 truck entering through the main gate."),
    ("17:22", "Garage", "Blue Ford F150 truck parked at the garage."),
    ("19:00", "Backyard", "Two people having a conversation on the patio."),
    ("21:30", "Main Gate", "Main gate closed, no activity."),
    ("23:10", "Warehouse Door", "Person in gray jacket trying the handle of the warehouse door."),
    ("23:13", "Warehouse Door", "Person in gray jacket still at the warehouse door, crouching near the lock."),
]


def make_frame(idx: int, tod: str, location: str, description: str, date: str = "2026-11-25") -> Frame:
    x, y = LOC_XY.get(location, (0.0, 0.0))
    return Frame(
        frame_id=idx + 1,
        ts=f"{date}T{tod}:00",
        location=location,
        x=x + (idx % 3),
        y=y + (idx % 2),
        altitude=25.0 + (idx % 3) * 2,
        battery=100 - (idx % 13) * 7,  # drops, then 'recharges' at the dock
        description=description,
    )


def generate_day_scenario(date: str = "2026-11-25") -> list[Frame]:
    return [make_frame(i, t, loc, d, date) for i, (t, loc, d) in enumerate(SCENARIO)]


def stream(frames: list[Frame]):
    """Yield frames one by one, like a live feed."""
    yield from frames
