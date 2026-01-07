# whisper_stt.py
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any

from faster_whisper import WhisperModel


@dataclass
class WhisperSegment:
    start_sec: float
    end_sec: float
    text: str
    avg_logprob: Optional[float] = None


def _run_ffmpeg_extract_wav(video_path: Path, wav_path: Path, ffmpeg_path: str = "ffmpeg") -> None:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path,
        "-y",
        "-i", str(video_path),
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        str(wav_path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr}")


def transcribe_video(
    video_path: str | Path,
    cache_path: str | Path,
    model_size: str = "small",
    language: str = "vi",
    device: str = "cpu",
    compute_type: str = "int8",
) -> List[WhisperSegment]:
    """
    Uses faster-whisper. Caches result to cache_path (json).
    """
    cache_path = Path(cache_path)
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return [WhisperSegment(**s) for s in raw["segments"]]

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    ffmpeg_path = os.getenv("FFMPEG_PATH", "ffmpeg")
    wav_path = cache_path.with_suffix(".wav")
    _run_ffmpeg_extract_wav(video_path, wav_path, ffmpeg_path=ffmpeg_path)

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(wav_path),
        language=language,
        vad_filter=True,
        beam_size=5,
    )

    out: List[WhisperSegment] = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        out.append(
            WhisperSegment(
                start_sec=float(seg.start),
                end_sec=float(seg.end),
                text=text,
                avg_logprob=getattr(seg, "avg_logprob", None),
            )
        )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump({"segments": [s.__dict__ for s in out]}, f, ensure_ascii=False, indent=2)

    # Optional: keep wav for debugging; delete if you want
    try:
        wav_path.unlink(missing_ok=True)
    except Exception:
        pass

    return out


def join_whisper_text(segments: List[WhisperSegment], max_chars: int = 12000) -> str:
    buf = []
    total = 0
    for s in segments:
        line = s.text.strip()
        if not line:
            continue
        if total + len(line) + 1 > max_chars:
            break
        buf.append(line)
        total += len(line) + 1
    return "\n".join(buf)
