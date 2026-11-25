"""Glue: caption -> perception -> index -> context -> rules -> logs/alerts."""
from __future__ import annotations


class Pipeline:
    def __init__(self, analyzer, index, rules, context, verbose: bool = True):
        self.analyzer, self.index, self.rules, self.context, self.verbose = analyzer, index, rules, context, verbose
        self.logs: list[str] = []
        self.alerts: list = []

    def _emit(self, line: str):
        self.logs.append(line)
        if self.verbose:
            print(line)

    def process(self, frame, analysis: dict) -> list:
        """Handle ONE frame (this is the 'real-time' entry point)."""
        self.index.add_frame(frame, analysis)
        self.context.update(frame, analysis)
        if not analysis["objects"]:
            self._emit(f"[{frame.tod}] {frame.location}: nothing notable")
        for o in analysis["objects"]:
            act = "" if o["activity"] == "none" else f" ({o['activity']})"
            tag = " [trusted]" if o["trusted"] else ""
            self._emit(f"[{frame.tod}] {o['label'].title()} spotted at {frame.location}{act}{tag}")
        new = self.rules.evaluate(frame, analysis, self.context)
        for a in new:
            self.index.add_alert(a)
            self.alerts.append(a)
            self._emit(f"    ALERT [{a.severity}] {a.rule}: {a.message}")
        return new

    def run(self, frames, analyses=None, batch_size: int = 8):
        for i in range(0, len(frames), batch_size):
            batch = frames[i : i + batch_size]
            res = analyses[i : i + batch_size] if analyses else self.analyzer.analyze_batch(batch, self.context.known_labels())
            for f, a in zip(batch, res):
                self.process(f, a)
        return self
