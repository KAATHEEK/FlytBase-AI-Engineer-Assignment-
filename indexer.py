"""Frame-by-frame index in SQLite: structured filters + FTS5 full-text + TF-IDF ranked search."""
from __future__ import annotations

import re
import sqlite3

SYNONYMS = {"people": "person", "persons": "person", "man": "person", "woman": "person", "human": "person", "intruder": "person"}


class FrameIndex:
    def __init__(self, path: str = ":memory:", reset: bool = True):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if reset:
            for t in ("frames", "objects", "alerts", "frames_fts"):
                self.conn.execute(f"DROP TABLE IF EXISTS {t}")
        self._create()

    def _create(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS frames(
                frame_id INTEGER PRIMARY KEY, ts TEXT, tod TEXT, location TEXT,
                x REAL, y REAL, altitude REAL, battery INTEGER,
                description TEXT, summary TEXT, source TEXT);
            CREATE TABLE IF NOT EXISTS objects(
                id INTEGER PRIMARY KEY AUTOINCREMENT, frame_id INTEGER,
                category TEXT, label TEXT, color TEXT, activity TEXT, trusted INTEGER);
            CREATE TABLE IF NOT EXISTS alerts(
                id INTEGER PRIMARY KEY AUTOINCREMENT, frame_id INTEGER, ts TEXT, tod TEXT,
                rule TEXT, severity TEXT, message TEXT);
            CREATE INDEX IF NOT EXISTS idx_obj_label ON objects(label);
            CREATE INDEX IF NOT EXISTS idx_obj_frame ON objects(frame_id);
            CREATE INDEX IF NOT EXISTS idx_frames_tod ON frames(tod);
            """
        )
        try:
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS frames_fts USING fts5(description, summary, labels, frame_id UNINDEXED)")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False

    # ------------------------------------------------------------------ write
    def add_frame(self, frame, analysis: dict) -> None:
        c = self.conn
        c.execute(
            "INSERT OR REPLACE INTO frames(frame_id,ts,tod,location,x,y,altitude,battery,description,summary,source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (frame.frame_id, frame.ts, frame.tod, frame.location, frame.x, frame.y, frame.altitude, frame.battery,
             frame.description, analysis["summary"], analysis["source"]),
        )
        c.execute("DELETE FROM objects WHERE frame_id=?", (frame.frame_id,))
        for o in analysis["objects"]:
            c.execute(
                "INSERT INTO objects(frame_id,category,label,color,activity,trusted) VALUES (?,?,?,?,?,?)",
                (frame.frame_id, o["category"], o["label"], o["color"], o["activity"], int(o["trusted"])),
            )
        if self.fts:
            c.execute("DELETE FROM frames_fts WHERE frame_id=?", (frame.frame_id,))
            c.execute(
                "INSERT INTO frames_fts(description,summary,labels,frame_id) VALUES (?,?,?,?)",
                (frame.description, analysis["summary"], " ".join(o["label"] for o in analysis["objects"]), frame.frame_id),
            )
        c.commit()

    def add_alert(self, a) -> None:
        self.conn.execute(
            "INSERT INTO alerts(frame_id,ts,tod,rule,severity,message) VALUES (?,?,?,?,?,?)",
            (a.frame_id, a.ts, a.tod, a.rule, a.severity, a.message),
        )
        self.conn.commit()

    # ------------------------------------------------------------------ read
    def _rows(self, rows) -> list[dict]:
        out = []
        for r in rows:
            objs = [dict(o) for o in self.conn.execute(
                "SELECT category,label,color,activity,trusted FROM objects WHERE frame_id=?", (r["frame_id"],))]
            out.append({
                "frame_id": r["frame_id"], "ts": r["ts"], "time": r["tod"], "location": r["location"],
                "description": r["description"], "summary": r["summary"], "objects": objs,
                "battery": r["battery"], "altitude": r["altitude"],
            })
        return out

    def search(self, obj=None, location=None, start=None, end=None, activity=None, text=None, limit=50) -> list[dict]:
        """Filter frames. start/end are 'HH:MM' (a range like 22:00-02:00 wraps past midnight)."""
        sql, where, p = "SELECT DISTINCT f.* FROM frames f", [], []
        if obj or activity:
            sql += " JOIN objects o ON o.frame_id=f.frame_id"
            if obj:
                o_ = SYNONYMS.get(obj.lower().strip(), obj.lower().strip())
                where.append("(o.label LIKE ? OR o.category LIKE ?)")
                p += [f"%{o_}%", f"%{o_}%"]
            if activity:
                where.append("o.activity = ?")
                p.append(activity.lower())
        if location:
            where.append("LOWER(f.location) LIKE ?")
            p.append(f"%{location.lower()}%")
        if start or end:
            s, e = start or "00:00", end or "23:59"
            if s <= e:
                where.append("f.tod BETWEEN ? AND ?")
            else:
                where.append("(f.tod >= ? OR f.tod <= ?)")
            p += [s, e]
        if text:
            tokens = re.findall(r"\w+", text.lower())
            if tokens and self.fts:
                where.append("f.frame_id IN (SELECT frame_id FROM frames_fts WHERE frames_fts MATCH ?)")
                p.append(" OR ".join(f'"{t}"' for t in tokens))
            else:
                for t in tokens:
                    where.append("LOWER(f.description) LIKE ?")
                    p.append(f"%{t}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY f.ts LIMIT ?"
        p.append(int(limit))
        return self._rows(self.conn.execute(sql, p).fetchall())

    def event_times(self, obj: str, activity: str) -> list[str]:
        return [r["time"] for r in self.search(obj=obj, activity=activity, limit=10000)]

    def ranked_search(self, query: str, k: int = 5) -> list[dict]:
        """TF-IDF cosine ranking over captions+labels (swap for embeddings/CLIP to go truly semantic)."""
        rows = self.search(limit=100000)
        if not rows:
            return []
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            return self.search(text=query, limit=k)
        docs = [
            f'{r["description"]} {r["summary"]} {r["location"]} ' + " ".join(f'{o["label"]} {o["activity"]}' for o in r["objects"])
            for r in rows
        ]
        vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english").fit(docs + [query])
        sims = cosine_similarity(vec.transform([query]), vec.transform(docs))[0]
        ranked = sorted(zip(sims, rows), key=lambda t: -t[0])
        return [dict(r, score=round(float(s), 3)) for s, r in ranked[:k] if s > 0]

    def get_alerts(self, severity=None, rule=None) -> list[dict]:
        sql, where, p = "SELECT * FROM alerts", [], []
        if severity:
            where.append("severity = ?"); p.append(severity.upper())
        if rule:
            where.append("rule = ?"); p.append(rule.upper())
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY ts, id", p).fetchall()]

    def stats(self) -> dict:
        n = self.conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
        labels = [r[0] for r in self.conn.execute("SELECT DISTINCT label FROM objects ORDER BY label")]
        return {"frames": n, "distinct_objects": labels, "fts5": self.fts}
