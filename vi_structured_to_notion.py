# vi_structured_to_notion.py
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from vi_insights_reader import (
    read_insights_json,
    get_vi_metadata,
    extract_ocr_items,
    extract_vi_transcript_items,
)
from whisper_stt import transcribe_video, join_whisper_text
from evidence_pack import build_evidence_pack
from llm_extract import extract_structured
from notion_dbs import (
    NotionClient,
    NotionProps,
    load_notion_ids_from_env,
    upsert_video,
    upsert_dish,
    upsert_place,
    create_or_get_mention,
    compute_mention_score,
    iso_from_created_plus_offset,
    require_env,
)


def main():
    load_dotenv()

    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="Path to insights_*.json")
    ap.add_argument("--video", required=False, help="Path to .mp4 (optional, for Whisper STT)")
    ap.add_argument("--min-ocr-conf", type=float, default=0.50)
    ap.add_argument("--no-whisper", action="store_true", help="Skip Whisper even if --video is provided")
    ap.add_argument("--save-extracted", action="store_true", help="Write extracted_{video_id}.json")
    args = ap.parse_args()

    insights_path = Path(args.file)
    insights = read_insights_json(insights_path)

    meta = get_vi_metadata(insights)
    video_id = meta.get("video_id")
    if not video_id:
        raise RuntimeError("insights JSON missing top-level 'id'")

    # 1) OCR from VI JSON
    ocr_items = extract_ocr_items(insights, min_conf=args.min_ocr_conf)

    # (Optional) VI transcript (not reliable for you, but can still be included if you want)
    # vi_tx = extract_vi_transcript_items(insights, min_conf=0.0)

    # 2) Whisper STT (forced vi)
    stt_segments = []
    if args.video and not args.no_whisper:
        cache_dir = Path(os.getenv("CACHE_DIR", ".cache"))
        cache_dir.mkdir(exist_ok=True)
        cache_path = cache_dir / f"whisper_{video_id}.json"

        model_size = os.getenv("WHISPER_MODEL_SIZE", "small")
        language = os.getenv("WHISPER_LANGUAGE", "vi")

        stt_segments = transcribe_video(
            args.video,
            cache_path=cache_path,
            model_size=model_size,
            language=language,
        )

    # 3) Build evidence pack (this is the “OCR → LLM” bridge)
    evidence = build_evidence_pack(
        ocr_items=ocr_items,
        stt_segments=stt_segments,
        min_ocr_conf=args.min_ocr_conf,
        max_chars=int(os.getenv("EVIDENCE_MAX_CHARS", "12000")),
        dedupe_threshold=int(os.getenv("EVIDENCE_DEDUPE_THRESHOLD", "92")),
    )

    # 4) LLM extraction to strict JSON
    extraction = extract_structured(
        video={
            "video_id": video_id,
            "filename": meta.get("filename") or "",
            "created": meta.get("created"),
        },
        evidence_text=evidence["evidence_text"],
    )

    if args.save_extracted:
        outp = Path(f"extracted_{video_id}.json")
        outp.write_text(json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) Notion UPSERTS
    notion_token = require_env("NOTION_TOKEN")
    notion_version = os.getenv("NOTION_VERSION", "2022-06-28")  # safe default
    notion = NotionClient(token=notion_token, version=notion_version)

    db_ids = load_notion_ids_from_env()
    props = NotionProps()

    # 5a) Upsert Video row
    video_page_id = upsert_video(
        notion,
        db_ids,
        props,
        video_id=video_id,
        title=f"{meta.get('filename') or video_id}",
        source_file=str(insights_path.name),
        created_iso=meta.get("created"),
        duration=meta.get("duration"),
        ocr_raw=evidence["ocr_compact"],
        stt_raw=evidence["stt_compact"],
    )

    # 5b) For each mention: upsert dish, upsert place, create mention
    mentions = extraction.get("mentions", [])
    created_mentions = 0
    for i, m in enumerate(mentions, start=1):
        dish = m["dish"]
        place = m.get("place")  # may be null
        confidence = float(m["confidence"])
        start_sec = m.get("start_sec")
        end_sec = m.get("end_sec")

        dish_id = upsert_dish(
            notion,
            db_ids,
            props,
            canonical=dish["canonical"],
            aliases=dish.get("aliases", []),
            category=dish.get("category"),
        )

        place_id = None
        if place and (place.get("name") or place.get("address")):
            place_id = upsert_place(
                notion,
                db_ids,
                props,
                name=(place.get("name") or "Unknown place").strip(),
                dish_ids=[dish_id],
                address=place.get("address"),
                district=place.get("district"),
                hours=place.get("hours"),
                price_range=place.get("price_range"),
                description=place.get("description"),
                tiktok_handle=place.get("tiktok_handle"),
            )

        has_place = place_id is not None
        has_address = bool(place and place.get("address"))
        has_hours = bool(place and place.get("hours"))
        mention_score = compute_mention_score(confidence, has_place, has_address, has_hours)

        # Mention idempotency via title string
        place_part = (place.get("name") if place else None) or "Unknown"
        start_part = f"{float(start_sec):.1f}" if start_sec is not None else "NA"
        mention_name = f"{video_id} | {dish['canonical']} | {place_part} | {start_part}"

        mention_time_iso = iso_from_created_plus_offset(meta.get("created"), start_sec)

        _mention_id = create_or_get_mention(
            notion,
            db_ids,
            props,
            mention_name=mention_name,
            dish_id=dish_id,
            place_id=place_id,
            video_id=video_page_id,  # relation expects Notion page id
            evidence_ocr="\n".join(m.get("evidence_ocr", [])),
            evidence_stt="\n".join(m.get("evidence_stt", [])),
            confidence=confidence,
            mention_score=mention_score,
            mention_time_iso=mention_time_iso,
        )
        created_mentions += 1

    print(f"✅ Done. Video={video_id} VideoRow={video_page_id} MentionsUpserted={created_mentions}")


if __name__ == "__main__":
    main()
