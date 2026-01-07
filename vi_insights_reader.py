# vi_insights_reader.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def timecode_to_seconds(tc: str) -> Optional[float]:
    """
    Video Indexer timestamps look like:
      "0:00:03.0333333"  or  "0:00:02.44"
    Returns seconds as float.
    """
    if not tc or not isinstance(tc, str):
        return None
    try:
        parts = tc.split(":")
        if len(parts) != 3:
            return None
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
    except Exception:
        return None


@dataclass
class TimedText:
    source: str  # "ocr" or "vi_transcript"
    text: str
    start_sec: Optional[float]
    end_sec: Optional[float]
    confidence: Optional[float]


def read_insights_json(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_vi_metadata(insights: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "video_id": insights.get("id"),
        "filename": insights.get("name"),
        "created": insights.get("created"),
        "duration": insights.get("duration"),
        "duration_seconds": insights.get("durationInSeconds") or insights.get("durationInSeconds", None),
    }


def _get_primary_video_insights(insights: Dict[str, Any]) -> Dict[str, Any]:
    videos = insights.get("videos") or []
    if videos and isinstance(videos, list):
        v0 = videos[0] or {}
        return (v0.get("insights") or {}) if isinstance(v0, dict) else {}
    return {}


def extract_ocr_items(insights: Dict[str, Any], min_conf: float = 0.0) -> List[TimedText]:
    """
    Prefers videos[0].insights.ocr; falls back to summarizedInsights.ocr
    Emits one TimedText per (ocr_item x instance) because timestamps are per instance.
    """
    vi = _get_primary_video_insights(insights)
    ocr = vi.get("ocr")
    if not ocr:
        ocr = (insights.get("summarizedInsights") or {}).get("ocr") or []

    out: List[TimedText] = []
    if not isinstance(ocr, list):
        return out

    for item in ocr:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        conf = item.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else None
        except Exception:
            conf_f = None

        if conf_f is not None and conf_f < min_conf:
            continue

        instances = item.get("instances") or []
        if isinstance(instances, list) and instances:
            for inst in instances:
                if not isinstance(inst, dict):
                    continue
                st = timecode_to_seconds(inst.get("start"))
                en = timecode_to_seconds(inst.get("end"))
                out.append(TimedText("ocr", text, st, en, conf_f))
        else:
            out.append(TimedText("ocr", text, None, None, conf_f))

    return out


def extract_vi_transcript_items(insights: Dict[str, Any], min_conf: float = 0.0) -> List[TimedText]:
    vi = _get_primary_video_insights(insights)
    tx = vi.get("transcript") or []
    out: List[TimedText] = []
    if not isinstance(tx, list):
        return out

    for item in tx:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if not text:
            continue
        conf = item.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else None
        except Exception:
            conf_f = None

        if conf_f is not None and conf_f < min_conf:
            continue

        instances = item.get("instances") or []
        if isinstance(instances, list) and instances:
            for inst in instances:
                if not isinstance(inst, dict):
                    continue
                st = timecode_to_seconds(inst.get("start"))
                en = timecode_to_seconds(inst.get("end"))
                out.append(TimedText("vi_transcript", text, st, en, conf_f))
        else:
            out.append(TimedText("vi_transcript", text, None, None, conf_f))
    return out


def join_text(items: List[TimedText], max_chars: int = 12000) -> str:
    """
    Joins texts with newline, truncates.
    """
    buf = []
    total = 0
    for it in items:
        line = it.text.strip()
        if not line:
            continue
        if total + len(line) + 1 > max_chars:
            break
        buf.append(line)
        total += len(line) + 1
    return "\n".join(buf)
