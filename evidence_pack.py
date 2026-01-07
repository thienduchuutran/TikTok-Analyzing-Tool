# evidence_pack.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from rapidfuzz import fuzz
from unidecode import unidecode

from vi_insights_reader import TimedText
from whisper_stt import WhisperSegment


def _fmt_ts(sec: Optional[float]) -> str:
    if sec is None:
        return "??:??"
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:04.1f}"


def normalize_key(s: str) -> str:
    """
    For dedupe/keys: lowercase, remove diacritics, collapse spaces, keep letters/numbers.
    """
    s = unidecode(s).lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def dedupe_lines(lines: List[str], threshold: int = 92) -> List[str]:
    """
    Remove near-duplicates using fuzzy ratio.
    threshold 0-100, higher = stricter.
    """
    kept: List[str] = []
    kept_norm: List[str] = []
    for line in lines:
        n = normalize_key(line)
        if not n:
            continue
        is_dup = False
        for kn in kept_norm:
            if fuzz.ratio(n, kn) >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(line)
            kept_norm.append(n)
    return kept


def build_evidence_pack(
    ocr_items: List[TimedText],
    stt_segments: List[WhisperSegment],
    *,
    min_ocr_conf: float = 0.50,
    max_chars: int = 12000,
    dedupe_threshold: int = 92,
) -> Dict[str, Any]:
    """
    Produces:
      - evidence_lines: list[str]
      - evidence_text: str (joined, truncated)
      - ocr_compact: list[str]
      - stt_compact: list[str]
    """
    lines: List[str] = []

    # OCR first (your primary signal)
    for it in ocr_items:
        if it.confidence is not None and it.confidence < min_ocr_conf:
            continue
        text = it.text.strip()
        if not text:
            continue
        prefix = f"[OCR {_fmt_ts(it.start_sec)}-{_fmt_ts(it.end_sec)} conf={it.confidence if it.confidence is not None else 'NA'}]"
        lines.append(f"{prefix} {text}")

    # Whisper STT second
    for s in stt_segments:
        text = s.text.strip()
        if not text:
            continue
        prefix = f"[STT {_fmt_ts(s.start_sec)}-{_fmt_ts(s.end_sec)}]"
        lines.append(f"{prefix} {text}")

    # Dedupe & truncate
    lines = dedupe_lines(lines, threshold=dedupe_threshold)

    # Keep in chronological-ish order by timestamp in bracket (rough sort)
    def sort_key(line: str) -> float:
        m = re.search(r"\[(?:OCR|STT)\s+(\d{2}):(\d{2}\.\d)\-", line)
        if not m:
            return 1e9
        mm = int(m.group(1))
        ss = float(m.group(2))
        return mm * 60 + ss

    lines.sort(key=sort_key)

    out_lines: List[str] = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > max_chars:
            break
        out_lines.append(line)
        total += len(line) + 1

    # Also keep compact raw text chunks (useful for Notion raw fields)
    ocr_compact = [l for l in out_lines if l.startswith("[OCR ")]
    stt_compact = [l for l in out_lines if l.startswith("[STT ")]

    return {
        "evidence_lines": out_lines,
        "evidence_text": "\n".join(out_lines),
        "ocr_compact": "\n".join(ocr_compact)[:max_chars],
        "stt_compact": "\n".join(stt_compact)[:max_chars],
    }
