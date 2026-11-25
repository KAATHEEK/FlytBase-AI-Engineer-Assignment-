"""Context management: a running memory of entities seen across the whole video."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class Entity:
    label: str
    category: str
    first_seen: str
    last_seen: str
    sightings: int = 0
    locations: set = field(default_factory=set)
    entry_times: list = field(default_factory=list)
    exit_times: list = field(default_factory=list)


class ContextManager:
    def __init__(self, max_recent: int = 12):
        self.entities: dict[str, Entity] = {}
        self.recent = deque(maxlen=max_recent)

    def update(self, frame, analysis: dict) -> None:
        for o in analysis["objects"]:
            e = self.entities.get(o["label"])
            if e is None:
                e = self.entities[o["label"]] = Entity(o["label"], o["category"], frame.tod, frame.tod)
            e.last_seen = frame.tod
            e.sightings += 1
            e.locations.add(frame.location)
            if o["activity"] == "entering":
                e.entry_times.append(frame.tod)
            elif o["activity"] == "exiting":
                e.exit_times.append(frame.tod)
            act = "" if o["activity"] == "none" else f" ({o['activity']})"
            self.recent.append(f"{frame.tod} {o['label']}{act} at {frame.location}")

    def known_labels(self) -> list[str]:
        """Labels passed to the perception model so it reuses the same names (consistent identity)."""
        return [l for l, e in self.entities.items() if e.category in ("vehicle", "animal", "package", "other")]

    def snapshot(self, max_entities: int = 12) -> str:
        if not self.entities:
            return "No entities seen yet."
        lines = []
        for e in list(self.entities.values())[:max_entities]:
            extra = f", entries at {', '.join(e.entry_times)}" if e.entry_times else ""
            extra += f", exits at {', '.join(e.exit_times)}" if e.exit_times else ""
            lines.append(
                f"- {e.label} [{e.category}]: seen {e.sightings}x, first {e.first_seen}, last {e.last_seen}, "
                f"places: {', '.join(sorted(e.locations))}{extra}"
            )
        lines.append("Recent events: " + " | ".join(self.recent))
        return "\n".join(lines)
