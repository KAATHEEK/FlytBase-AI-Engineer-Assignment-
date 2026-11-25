"""Video summarization: map-reduce over frame chunks (scales to long videos)."""
from __future__ import annotations

from .llm import chat, extract_json, text_of


def _lines(rows):
    return [f'{r["time"]} {r["location"]}: {r["description"]}' for r in rows]


def template_summary(index) -> dict:
    rows, alerts = index.search(limit=100000), index.get_alerts()
    high = [a for a in alerts if a["severity"] == "HIGH"]
    one = f"{len(rows)} frames analysed, {len(alerts)} alerts ({len(high)} high)."
    if high:
        one += " Most serious: " + "; ".join(a["message"] for a in high[:2]) + "."
    return {"one_line": one, "highlights": [f'{a["tod"]} [{a["severity"]}] {a["message"]}' for a in alerts[:6]], "method": "template"}


def summarize_video(client, model: str, index, chunk_size: int = 15) -> dict:
    rows, alerts = index.search(limit=100000), index.get_alerts()
    if not rows:
        return {"one_line": "No frames indexed.", "highlights": [], "method": "none"}
    try:
        parts = []
        for i in range(0, len(rows), chunk_size):  # MAP
            r = chat(client, model, [
                {"role": "system", "content": "Summarize these drone security frames in 2-3 factual sentences. Keep times and places. No speculation."},
                {"role": "user", "content": "\n".join(_lines(rows[i : i + chunk_size]))}], temperature=0, max_tokens=500)
            parts.append(text_of(r))
        alert_txt = "\n".join(f'{a["tod"]} [{a["severity"]}] {a["message"]}' for a in alerts) or "none"
        r = chat(client, model, [  # REDUCE
            {"role": "system", "content": 'You write the final report for a property owner. Return ONLY JSON: {"one_line": "<one sentence covering the whole video>", "highlights": ["<up to 5 short strings>"]}'},
            {"role": "user", "content": "CHUNK SUMMARIES:\n" + "\n".join(parts) + "\n\nALERTS:\n" + alert_txt}],
            json_mode=True, temperature=0, max_tokens=1000)
        data = extract_json(r.choices[0].message.content or "")
        return {"one_line": str(data["one_line"]), "highlights": [str(h) for h in data.get("highlights", [])], "method": "llm map-reduce"}
    except Exception as e:
        out = template_summary(index)
        out["method"] = f"template (LLM failed: {type(e).__name__})"
        return out
