# llm_extract.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from openai import OpenAI


def get_extraction_schema() -> Dict[str, Any]:
    """
    Strict JSON schema for Vietnamese food extraction from OCR+STT evidence.
    Keep it minimal but scalable.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "video": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "video_id": {"type": "string"},
                    "filename": {"type": "string"},
                    "created": {"type": ["string", "null"]},
                },
                "required": ["video_id", "filename", "created"],
            },
            "mentions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "dish": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "canonical": {"type": "string"},
                                "aliases": {"type": "array", "items": {"type": "string"}},
                                "category": {"type": ["string", "null"]},
                            },
                            "required": ["canonical", "aliases", "category"],
                        },
                        "place": {
                            "type": ["object", "null"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": ["string", "null"]},
                                "address": {"type": ["string", "null"]},
                                "district": {"type": ["string", "null"]},
                                "hours": {"type": ["string", "null"]},
                                "price_range": {"type": ["string", "null"]},
                                "description": {"type": ["string", "null"]},
                                "tiktok_handle": {"type": ["string", "null"]},
                            },
                            "required": ["name", "address", "district", "hours", "price_range", "description", "tiktok_handle"],
                        },
                        "claims": {"type": "array", "items": {"type": "string"}},
                        "evidence_ocr": {"type": "array", "items": {"type": "string"}},
                        "evidence_stt": {"type": "array", "items": {"type": "string"}},
                        "start_sec": {"type": ["number", "null"]},
                        "end_sec": {"type": ["number", "null"]},
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                    "required": [
                        "dish",
                        "place",
                        "claims",
                        "evidence_ocr",
                        "evidence_stt",
                        "start_sec",
                        "end_sec",
                        "confidence",
                    ],
                },
            },
        },
        "required": ["video", "mentions"],
    }


def extract_structured(
    *,
    video: Dict[str, Any],
    evidence_text: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Returns parsed JSON dict (schema guaranteed by Structured Outputs).
    """
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider != "openai":
        raise RuntimeError("This MVP implements LLM_PROVIDER=openai only. (Add others later.)")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env")

    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    client = OpenAI(api_key=api_key)

    schema = get_extraction_schema()

    system = (
        "You extract Vietnamese Đà Nẵng food info from TikTok-style videos.\n"
        "PRIMARY signal: OCR lines (on-screen text).\n"
        "SECONDARY signal: Whisper STT lines (voiceover).\n"
        "Rules:\n"
        "- If place info is missing, set place fields to null (place object still present or null overall).\n"
        "- Only extract what is supported by evidence lines.\n"
        "- Prefer Vietnamese with correct diacritics.\n"
        "- Output must match the provided JSON schema strictly.\n"
        "- Confidence is 0..1 based on strength of evidence.\n"
    )

    user = (
        f"VIDEO METADATA:\n{json.dumps(video, ensure_ascii=False)}\n\n"
        f"EVIDENCE (timestamped OCR+STT lines):\n{evidence_text}\n\n"
        "Extract dish mentions and any linked place/address/hours if present. "
        "Return only JSON."
    )

    # Structured Outputs via Responses API: text.format json_schema strict. :contentReference[oaicite:1]{index=1}
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "danang_food_extraction",
                "strict": True,
                "schema": schema,
            }
        },
    )

    out_text = resp.output_text
    if not out_text:
        raise RuntimeError("OpenAI returned empty output_text")

    return json.loads(out_text)
