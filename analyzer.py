"""Perception: turn a frame caption into structured objects.

RuleBasedAnalyzer - offline, deterministic (used for tests and as LLM fallback).
GroqAnalyzer      - LLM (Groq) JSON extraction, batched to respect rate limits.
Both return: {"objects": [{category,label,color,activity,trusted}], "summary": str, "source": str}
"""
from __future__ import annotations

import json
import re

from .llm import chat, extract_json

CATEGORIES = {"vehicle", "person", "animal", "package", "other"}
ACTIVITIES = {"entering", "exiting", "parked", "moving", "stationary", "none"}


def normalize_analysis(raw: dict, source: str) -> dict:
    objs, seen = [], set()
    for o in (raw or {}).get("objects", []) or []:
        if not isinstance(o, dict):
            continue
        label = str(o.get("label", "")).strip().lower()
        if not label or label in seen:
            continue
        seen.add(label)
        cat = str(o.get("category", "other")).lower()
        act = str(o.get("activity", "none")).lower()
        t = o.get("trusted")
        color = o.get("color")
        cat = cat if cat in CATEGORIES else "other"
        objs.append(
            {
                "category": cat,
                "label": label,
                "color": str(color).lower() if color else None,
                "activity": act if act in ACTIVITIES else "none",
                "trusted": cat == "person" and (t is True or str(t).lower() == "true"),
            }
        )
    return {"objects": objs, "summary": str((raw or {}).get("summary", "")).strip(), "source": source}


# ----------------------------------------------------------------------------- rule-based
_COLORS = r"blue|white|red|black|gray|grey|green|silver|yellow|orange|brown"
_MAKES = r"ford|toyota|honda|tesla|tata|mahindra|maruti|hyundai|chevrolet|nissan"
_KINDS = r"f-?150|pickup truck|delivery van|truck|van|sedan|suv|car|motorcycle|bus"
VEHICLE_RE = re.compile(
    rf"\b(?:(?P<color>{_COLORS})\s+)?(?:(?P<make>{_MAKES})\s+)?(?P<kind>{_KINDS})(?:\s+(?P<tail>truck|van|car|pickup))?\b",
    re.I,
)
PERSON_RE = re.compile(r"\b(?:person|people|man|men|woman|women|guard|courier|intruder|individual|figure|driver|worker|visitor)\b", re.I)
ANIMAL_RE = re.compile(r"\b(?:fox|dog|cat|deer|bird|monkey|cow|snake)\b", re.I)
PACKAGE_RE = re.compile(r"\b(?:package|parcel)\b", re.I)
TRUSTED_RE = re.compile(r"\b(?:guard|uniform\w*|staff|employee)\b", re.I)
NEGATION_RE = re.compile(r"\b(?:no|without|nobody|empty)\b", re.I)
_ACTIVITY_PATTERNS = [
    ("entering", r"\b(?:enter\w*|arriv\w*|drives? in)\b"),
    ("exiting", r"\b(?:exit\w*|leav\w*|depart\w*|drives? out)\b"),
    ("parked", r"\bpark\w*\b"),
    ("stationary", r"\b(?:standing|still|lingering|pacing|waiting|crouching|loiter\w*|stationary|idle|sitting)\b"),
    ("moving", r"\b(?:walk\w*|crossing|moving|carrying|unloading|running|driving|conversation)\b"),
]


def _activity(clause: str) -> str:
    for name, pat in _ACTIVITY_PATTERNS:
        if re.search(pat, clause, re.I):
            return name
    return "none"


class RuleBasedAnalyzer:
    def analyze_batch(self, frames, known_labels=()):
        return [self._one(f) for f in frames]

    def _one(self, frame) -> dict:
        objects = []
        for clause in re.split(r"[,;.:]|\band\b|\bwhile\b", frame.description):
            if not clause.strip() or NEGATION_RE.search(clause):
                continue
            act = _activity(clause)
            for m in VEHICLE_RE.finditer(clause):
                label = " ".join(p for p in (m["color"], m["make"], m["kind"], m["tail"]) if p).lower()
                objects.append({"category": "vehicle", "label": label, "color": (m["color"] or "").lower() or None, "activity": act})
            if PERSON_RE.search(clause):
                objects.append({"category": "person", "label": "person", "color": None, "activity": act, "trusted": bool(TRUSTED_RE.search(clause))})
            for m in ANIMAL_RE.finditer(clause):
                objects.append({"category": "animal", "label": m.group(0).lower(), "color": None, "activity": act})
            if PACKAGE_RE.search(clause):
                objects.append({"category": "package", "label": "package", "color": None, "activity": act})
        return normalize_analysis({"objects": objects, "summary": frame.description}, "rules")


# ----------------------------------------------------------------------------- LLM
SYSTEM_PROMPT = """You are the perception module of a drone security system.
You receive captions of drone video frames. For EACH frame list the security-relevant objects.
Return ONLY a JSON object of this shape:
{"frames":[{"frame_id":<int>,"summary":"<max 15 words>","objects":[{"category":"vehicle|person|animal|package|other","label":"<lowercase, e.g. blue ford f150 truck>","color":"<color or null>","activity":"entering|exiting|parked|moving|stationary|none","trusted":<true|false>}]}]}
Rules:
- Exactly one entry per input frame, with the same frame_id. Frames with nothing relevant get "objects": [].
- If the same object as in known_entities reappears, reuse that exact label. Do not invent label variants.
- Never report absent things ("no vehicle present" means NO vehicle object).
- activity = what that object is doing in THIS frame. Never output 'loitering'; a separate rule engine decides that from persistence over time.
- People: label is exactly "person". trusted=true ONLY for uniformed guards/staff, otherwise false.
"""


class GroqAnalyzer:
    def __init__(self, client, model: str, batch_size: int = 8, fallback=None):
        self.client, self.model, self.batch_size = client, model, batch_size
        self.fallback = fallback or RuleBasedAnalyzer()

    def analyze_batch(self, frames, known_labels=()):
        out = []
        for i in range(0, len(frames), self.batch_size):
            out.extend(self._chunk(frames[i : i + self.batch_size], list(known_labels)))
        return out

    def _chunk(self, chunk, known):
        payload = {
            "known_entities": known,
            "frames": [{"frame_id": f.frame_id, "time": f.tod, "location": f.location, "caption": f.description} for f in chunk],
        }
        by_id = {}
        try:
            resp = chat(
                self.client, self.model,
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(payload)}],
                json_mode=True, temperature=0, max_tokens=4096,
            )
            data = extract_json(resp.choices[0].message.content or "")
            items = data.get("frames", []) if isinstance(data, dict) else data
            for it in items:
                try:
                    by_id[int(it["frame_id"])] = it
                except (KeyError, TypeError, ValueError):
                    continue
        except Exception as e:  # network, parsing, rate limit exhausted...
            print(f"[GroqAnalyzer] batch failed ({type(e).__name__}: {e}); using rule-based fallback")
        return [
            normalize_analysis(by_id[f.frame_id], "llm") if f.frame_id in by_id else self.fallback.analyze_batch([f])[0]
            for f in chunk
        ]
