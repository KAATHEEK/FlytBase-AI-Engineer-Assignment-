"""Optional REAL-video mode: OpenCV samples frames, a Groq vision model captions them,
then the same pipeline (analyzer -> index -> rules -> agent) runs on those captions."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta

from .llm import chat, text_of
from .simulator import LOC_XY, Frame

DESCRIBE_PROMPT = (
    "You are a drone security camera analyst. In 1-2 plain sentences describe ONLY security-relevant content: "
    "vehicles (color, make/model if visible, type), people (clothing, what they are doing), their activity "
    "(entering, exiting, parked, standing still, moving), and lighting (day/night). Do not speculate or invent details."
)


def to_data_url(img_bgr, max_side: int = 768, quality: int = 80) -> str:
    import cv2

    h, w = img_bgr.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img_bgr = cv2.resize(img_bgr, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def extract_frames(video_path: str, every_sec: float = 2.0, max_frames: int = 12):
    import cv2

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step, out, i = max(int(fps * every_sec), 1), [], 0
    while len(out) < max_frames:
        ok, img = cap.read()
        if not ok:
            break
        if i % step == 0:
            out.append((i / fps, img))
        i += 1
    cap.release()
    return out


def describe_image(client, model: str, data_url: str, prior: str = "") -> str:
    prompt = DESCRIBE_PROMPT + (f"\nPrevious frame: {prior}" if prior else "")
    content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": data_url}}]
    return text_of(chat(client, model, [{"role": "user", "content": content}], temperature=0, max_tokens=800))


def video_to_frames(client, model, video_path, start="00:00", date="2026-11-25", location="Main Gate",
                    every_sec=2.0, max_frames=12, sim_minutes_per_frame=2) -> list[Frame]:
    """sim_minutes_per_frame compresses time so a 20s clip can exercise time-based rules (loitering)."""
    base = datetime.fromisoformat(f"{date}T{start}:00")
    x, y = LOC_XY.get(location, (0.0, 0.0))
    frames, prior = [], ""
    for n, (_, img) in enumerate(extract_frames(video_path, every_sec, max_frames)):
        desc = describe_image(client, model, to_data_url(img), prior)
        ts = (base + timedelta(minutes=n * sim_minutes_per_frame)).isoformat(timespec="seconds")
        frames.append(Frame(n + 1, ts, location, x, y, 25.0, max(100 - n * 2, 21), desc))
        prior = desc
    return frames
