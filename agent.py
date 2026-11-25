"""Follow-up Q&A agent: Groq tool-calling over the frame index + live context memory."""
from __future__ import annotations

import json

from .llm import chat, strip_think, text_of

TOOLS = [
    {"type": "function", "function": {
        "name": "search_frames",
        "description": "Search indexed video frames. Combine any filters. Returns matching frames in time order.",
        "parameters": {"type": "object", "properties": {
            "object": {"type": "string", "description": "e.g. 'truck', 'van', 'vehicle', 'person', 'f150'"},
            "location": {"type": "string", "description": "e.g. 'main gate', 'garage', 'warehouse door'"},
            "start_time": {"type": "string", "description": "HH:MM 24h; range may wrap midnight"},
            "end_time": {"type": "string", "description": "HH:MM 24h"},
            "activity": {"type": "string", "enum": ["entering", "exiting", "parked", "moving", "stationary"]},
            "text": {"type": "string", "description": "free-text keywords from captions, e.g. 'hoodie'"},
            "limit": {"type": "integer", "description": "max frames (default 15)"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "list_alerts",
        "description": "List security alerts raised so far.",
        "parameters": {"type": "object", "properties": {
            "severity": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
            "rule": {"type": "string", "description": "NIGHT_PERSON, LOITERING, RESTRICTED_ZONE, REPEAT_VEHICLE, LOW_BATTERY"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "count_events",
        "description": "Times an object performed an activity (e.g. how many times a truck entered).",
        "parameters": {"type": "object", "properties": {
            "object": {"type": "string"},
            "activity": {"type": "string", "enum": ["entering", "exiting", "parked", "moving", "stationary"]},
        }, "required": ["object", "activity"]}}},
    {"type": "function", "function": {
        "name": "get_known_entities",
        "description": "All distinct objects seen, with first/last seen, counts, places, entry/exit times.",
        "parameters": {"type": "object", "properties": {}}}},
]


def _compact(r: dict) -> dict:
    return {
        "frame_id": r["frame_id"], "time": r["time"], "location": r["location"],
        "objects": [f'{o["label"]}' + ("" if o["activity"] == "none" else f' ({o["activity"]})') for o in r["objects"]],
        "caption": r["description"],
    }


class SecurityAgent:
    def __init__(self, client, model: str, index, context, max_steps: int = 5):
        self.client, self.model, self.index, self.context, self.max_steps = client, model, index, context, max_steps
        self.history: list[dict] = []

    # ------------------------------------------------------------ tools
    def run_tool(self, name: str, args: dict) -> dict:
        if name == "search_frames":
            rows = self.index.search(
                obj=args.get("object"), location=args.get("location"), start=args.get("start_time"),
                end=args.get("end_time"), activity=args.get("activity"), text=args.get("text"),
                limit=min(int(args.get("limit") or 15), 30),
            )
            return {"count": len(rows), "frames": [_compact(r) for r in rows]}
        if name == "list_alerts":
            al = self.index.get_alerts(args.get("severity"), args.get("rule"))
            return {"count": len(al), "alerts": [{"time": a["tod"], "rule": a["rule"], "severity": a["severity"], "message": a["message"]} for a in al]}
        if name == "count_events":
            times = self.index.event_times(args.get("object", ""), args.get("activity", ""))
            return {"count": len(times), "times": times}
        if name == "get_known_entities":
            return {"entities": self.context.snapshot()}
        return {"error": f"unknown tool {name}"}

    # ------------------------------------------------------------ chat loop
    def _system_prompt(self) -> str:
        return (
            "You are the Drone Security Analyst Agent for a fixed property. A docked drone patrols it; every video frame "
            "is indexed in a database and you can query it with tools.\n"
            "RULES: ALWAYS call tools to look facts up before answering; never invent events, times or locations. "
            "Times are 24h HH:MM. If tools return nothing, say nothing was found. Be concise and cite times + locations. "
            "For people use object='person'; for vehicles use words like 'truck', 'van' or 'vehicle'.\n\n"
            f"LIVE CONTEXT (entities tracked so far):\n{self.context.snapshot()}"
        )

    def _loop(self, messages: list[dict]) -> str:
        for _ in range(self.max_steps):
            resp = chat(self.client, self.model, messages, tools=TOOLS, tool_choice="auto", temperature=0)
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None)
            if not calls:
                return strip_think(msg.content or "")
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in calls],
            })
            for tc in calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self.run_tool(tc.function.name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)[:6000]})
        return "I could not finish the lookup within the step limit."

    def _fallback(self, question: str) -> str:
        """No tool-calling? Retrieve top frames (RAG) and answer from them; worst case print them."""
        rows = self.index.ranked_search(question, k=8)
        ctx = "\n".join(f'[{r["time"]}] {r["location"]}: {r["description"]}' for r in rows) or "(no matching frames)"
        try:
            resp = chat(self.client, self.model, [
                {"role": "system", "content": "Answer ONLY from these indexed frames. Cite times. If not covered, say so.\n" + ctx},
                {"role": "user", "content": question}], temperature=0, max_tokens=500)
            return text_of(resp)
        except Exception:
            return "Relevant frames:\n" + ctx

    def ask(self, question: str) -> str:
        messages = [{"role": "system", "content": self._system_prompt()}, *self.history, {"role": "user", "content": question}]
        try:
            answer = self._loop(messages)
        except Exception as e:  # e.g. malformed tool call from the model
            print(f"[agent] tool loop failed ({type(e).__name__}); using retrieval fallback")
            answer = self._fallback(question)
        self.history = (self.history + [{"role": "user", "content": question}, {"role": "assistant", "content": answer}])[-8:]
        return answer
