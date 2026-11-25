"""Deterministic, explainable alert rules (the LLM extracts facts; rules decide what is an alert)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


def in_window(tod: str, start: str, end: str) -> bool:
    """True if 'HH:MM' falls in [start, end]; windows may wrap past midnight (22:00-05:00)."""
    return start <= tod <= end if start <= end else (tod >= start or tod <= end)


@dataclass
class Alert:
    frame_id: int
    ts: str
    tod: str
    rule: str
    severity: str
    message: str

    def to_dict(self):
        return asdict(self)


@dataclass
class RuleConfig:
    night_start: str = "22:00"
    night_end: str = "05:00"
    loiter_min_sightings: int = 3
    loiter_min_minutes: float = 5
    loiter_gap_minutes: float = 10
    restricted_zones: dict = field(default_factory=lambda: {"Warehouse Door": ("08:00", "18:00")})  # allowed hours
    low_battery_pct: int = 20
    repeat_entry_threshold: int = 2
    cooldown_minutes: float = 30


class RuleEngine:
    def __init__(self, cfg: RuleConfig | None = None):
        self.cfg = cfg or RuleConfig()
        self._zones = {k.lower(): v for k, v in self.cfg.restricted_zones.items()}
        self._tracks: dict = {}
        self._last_fired: dict = {}

    def _cool(self, key, now) -> bool:
        last = self._last_fired.get(key)
        if last and (now - last).total_seconds() / 60 < self.cfg.cooldown_minutes:
            return False
        self._last_fired[key] = now
        return True

    def evaluate(self, frame, analysis: dict, context) -> list[Alert]:
        cfg, now, loc = self.cfg, frame.dt, frame.location
        alerts: list[Alert] = []

        def mk(rule, sev, msg):
            alerts.append(Alert(frame.frame_id, frame.ts, frame.tod, rule, sev, msg))

        objs = analysis["objects"]
        people = [o for o in objs if o["category"] == "person" and not o["trusted"]]
        night = in_window(frame.tod, cfg.night_start, cfg.night_end)

        # R1: any untrusted person at night
        if people and night and self._cool(("NIGHT_PERSON", loc), now):
            mk("NIGHT_PERSON", "HIGH", f"Person detected at {loc} at night, {frame.tod}")

        # R2: loitering = untrusted person stationary at one place for N sightings over M minutes
        if any(p["activity"] in ("stationary", "loitering") for p in people):
            tr = self._tracks.get(loc)
            if tr and (now - tr["last"]).total_seconds() / 60 > cfg.loiter_gap_minutes:
                tr = None
            if tr is None:
                tr = {"first": now, "first_tod": frame.tod, "last": now, "n": 0, "fired": False}
            tr["n"] += 1
            tr["last"] = now
            self._tracks[loc] = tr
            mins = (now - tr["first"]).total_seconds() / 60
            if not tr["fired"] and tr["n"] >= cfg.loiter_min_sightings and mins >= cfg.loiter_min_minutes:
                tr["fired"] = True
                mk("LOITERING", "HIGH" if night else "MEDIUM",
                   f"Person loitering at {loc}, {tr['first_tod']} ({tr['n']} sightings over {mins:.0f} min)")
        else:
            self._tracks.pop(loc, None)

        # R3: person in a restricted zone outside its authorised hours
        zone = self._zones.get(loc.lower())
        if people and zone and not in_window(frame.tod, *zone) and self._cool(("RESTRICTED_ZONE", loc), now):
            mk("RESTRICTED_ZONE", "HIGH", f"Unauthorized person at {loc} outside allowed hours {zone[0]}-{zone[1]}, {frame.tod}")

        # R4: same vehicle entering the property repeatedly
        for o in objs:
            if o["category"] == "vehicle" and o["activity"] == "entering":
                ent = context.entities.get(o["label"])
                if ent and len(ent.entry_times) >= cfg.repeat_entry_threshold:
                    mk("REPEAT_VEHICLE", "MEDIUM",
                       f"{o['label'].title()} entered the property {len(ent.entry_times)} times today ({', '.join(ent.entry_times)})")

        # R5: telemetry rule - low battery
        if frame.battery <= cfg.low_battery_pct and self._cool(("LOW_BATTERY", "drone"), now):
            mk("LOW_BATTERY", "MEDIUM", f"Drone battery at {frame.battery}% at {frame.tod}; return to dock")
        return alerts
