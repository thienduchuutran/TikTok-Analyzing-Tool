# notion_dbs.py
from __future__ import annotations

import os
import time
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests


NOTION_API = "https://api.notion.com/v1"


def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def truncate(s: str, limit: int) -> str:
    s = s or ""
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 20)] + "\n\n[TRUNCATED]"


def stable_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


@dataclass
class NotionClient:
    token: str
    version: str = "2022-06-28"
    timeout_sec: int = 30
    max_retries: int = 6

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = NOTION_API + path
        backoff = 0.8

        for attempt in range(1, self.max_retries + 1):
            resp = requests.request(
                method=method,
                url=url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout_sec,
            )

            # success
            if 200 <= resp.status_code < 300:
                return resp.json()

            # rate limited (429): respect Retry-After. :contentReference[oaicite:3]{index=3}
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                sleep_s = float(retry_after) if retry_after else backoff
                time.sleep(sleep_s)
                backoff = min(backoff * 2, 8.0)
                continue

            # transient server errors
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
                continue

            # permanent error
            raise RuntimeError(f"Notion API error {resp.status_code}: {resp.text}")

        raise RuntimeError(f"Notion request failed after {self.max_retries} attempts: {method} {path}")

    # --- Query helpers ---

    def query_database(self, database_id: str, filter_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
        res = self.request("POST", f"/databases/{database_id}/query", {"filter": filter_obj})
        return res.get("results", [])

    def retrieve_page(self, page_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/pages/{page_id}")

    def create_page(self, database_id: str, properties: Dict[str, Any]) -> str:
        res = self.request("POST", "/pages", {"parent": {"database_id": database_id}, "properties": properties})
        return res["id"]

    def update_page(self, page_id: str, properties: Dict[str, Any]) -> None:
        self.request("PATCH", f"/pages/{page_id}", {"properties": properties})

    # --- Property builders ---

    @staticmethod
    def prop_title(text: str) -> Dict[str, Any]:
        return {"title": [{"text": {"content": text}}]}

    @staticmethod
    def prop_text(text: str) -> Dict[str, Any]:
        return {"rich_text": [{"text": {"content": text}}]}

    @staticmethod
    def prop_number(n: float) -> Dict[str, Any]:
        return {"number": n}

    @staticmethod
    def prop_date_iso(dt_iso: str) -> Dict[str, Any]:
        return {"date": {"start": dt_iso}}

    @staticmethod
    def prop_select(name: str) -> Dict[str, Any]:
        return {"select": {"name": name}}

    @staticmethod
    def prop_multi_select(names: List[str]) -> Dict[str, Any]:
        return {"multi_select": [{"name": n} for n in names if n]}

    @staticmethod
    def prop_relation(page_ids: List[str]) -> Dict[str, Any]:
        return {"relation": [{"id": pid} for pid in page_ids if pid]}


# --- UPSERTS tailored to YOUR Notion schema --- #

@dataclass
class NotionDBIds:
    dishes: str
    places: str
    videos: str
    mentions: str


@dataclass
class NotionProps:
    # Dishes
    dish_title: str = "Name"
    dish_aliases: str = "Aliases"
    dish_category: str = "Category"

    # Places
    place_title: str = "Name"
    place_dish_rel: str = "Dish"
    place_address: str = "Address"
    place_district: str = "District"
    place_hours: str = "Hours"
    place_price: str = "Price Range"
    place_desc: str = "Description"
    place_handle: str = "TikTok Handle"

    # Videos
    video_title: str = "Name"
    video_id_prop: str = "Video ID"
    video_source_file: str = "Source File"
    video_created: str = "Created"
    video_duration: str = "Duration"
    video_ocr_raw: str = "OCR Raw"
    video_stt_raw: str = "STT Raw"

    # Mentions
    mention_title: str = "Name"
    mention_dish: str = "Dish"
    mention_place: str = "Place"
    mention_video: str = "Video"
    mention_ev_ocr: str = "Evidence (OCR)"
    mention_ev_stt: str = "Evidence (STT)"
    mention_conf: str = "Confidence"
    mention_score: str = "Mention Score"
    mention_time: str = "Mention Time"


def load_notion_ids_from_env() -> NotionDBIds:
    return NotionDBIds(
        dishes=require_env("NOTION_DISHES_DB_ID"),
        places=require_env("NOTION_PLACES_DB_ID"),
        videos=require_env("NOTION_VIDEOS_DB_ID"),
        mentions=require_env("NOTION_MENTIONS_DB_ID"),
    )


def upsert_video(
    notion: NotionClient,
    db: NotionDBIds,
    props: NotionProps,
    *,
    video_id: str,
    title: str,
    source_file: str,
    created_iso: Optional[str],
    duration: Optional[str],
    ocr_raw: str,
    stt_raw: str,
) -> str:
    # Query by Video ID (rich_text equals)
    results = notion.query_database(
        db.videos,
        {"property": props.video_id_prop, "rich_text": {"equals": video_id}},
    )

    text_limit = int(os.getenv("NOTION_TEXT_LIMIT", "1900"))

    p = {
        props.video_title: notion.prop_title(title),
        props.video_id_prop: notion.prop_text(video_id),
        props.video_source_file: notion.prop_text(source_file),
        props.video_duration: notion.prop_text(duration or ""),
        props.video_ocr_raw: notion.prop_text(truncate(ocr_raw, text_limit)),
        props.video_stt_raw: notion.prop_text(truncate(stt_raw, text_limit)),
    }
    if created_iso:
        p[props.video_created] = notion.prop_date_iso(created_iso)

    if results:
        page_id = results[0]["id"]
        notion.update_page(page_id, p)
        return page_id

    return notion.create_page(db.videos, p)


def upsert_dish(
    notion: NotionClient,
    db: NotionDBIds,
    props: NotionProps,
    *,
    canonical: str,
    aliases: List[str],
    category: Optional[str],
) -> str:
    # Query by title equals canonical
    results = notion.query_database(
        db.dishes,
        {"property": props.dish_title, "title": {"equals": canonical}},
    )

    # Merge aliases if existing
    merged_aliases = list(dict.fromkeys([a.strip() for a in aliases if a and a.strip()]))

    p: Dict[str, Any] = {
        props.dish_title: notion.prop_title(canonical),
        props.dish_aliases: notion.prop_multi_select(merged_aliases),
    }
    if category:
        p[props.dish_category] = notion.prop_select(category)

    if results:
        page_id = results[0]["id"]
        # Best-effort merge with existing aliases
        try:
            existing = notion.retrieve_page(page_id)
            existing_aliases = []
            ms = existing.get("properties", {}).get(props.dish_aliases, {}).get("multi_select", [])
            for x in ms:
                name = x.get("name")
                if name:
                    existing_aliases.append(name)
            merged = list(dict.fromkeys(existing_aliases + merged_aliases))
            p[props.dish_aliases] = notion.prop_multi_select(merged)
        except Exception:
            pass

        notion.update_page(page_id, p)
        return page_id

    return notion.create_page(db.dishes, p)


def upsert_place(
    notion: NotionClient,
    db: NotionDBIds,
    props: NotionProps,
    *,
    name: str,
    dish_ids: List[str],
    address: Optional[str],
    district: Optional[str],
    hours: Optional[str],
    price_range: Optional[str],
    description: Optional[str],
    tiktok_handle: Optional[str],
) -> str:
    # Prefer (Name AND Address) if address exists, else Name only
    if address:
        filter_obj = {
            "and": [
                {"property": props.place_title, "title": {"equals": name}},
                {"property": props.place_address, "rich_text": {"equals": address}},
            ]
        }
    else:
        filter_obj = {"property": props.place_title, "title": {"equals": name}}

    results = notion.query_database(db.places, filter_obj)

    p: Dict[str, Any] = {
        props.place_title: notion.prop_title(name),
        props.place_dish_rel: notion.prop_relation(dish_ids),
    }
    if address:
        p[props.place_address] = notion.prop_text(address)
    if district:
        p[props.place_district] = notion.prop_select(district)
    if hours:
        p[props.place_hours] = notion.prop_text(hours)
    if price_range:
        p[props.place_price] = notion.prop_select(price_range)
    if description:
        p[props.place_desc] = notion.prop_text(description)
    if tiktok_handle:
        p[props.place_handle] = notion.prop_text(tiktok_handle)

    if results:
        page_id = results[0]["id"]

        # Merge relation Dish (union)
        try:
            existing = notion.retrieve_page(page_id)
            existing_rel = existing.get("properties", {}).get(props.place_dish_rel, {}).get("relation", [])
            existing_ids = [x.get("id") for x in existing_rel if x.get("id")]
            union_ids = list(dict.fromkeys(existing_ids + dish_ids))
            p[props.place_dish_rel] = notion.prop_relation(union_ids)
        except Exception:
            pass

        notion.update_page(page_id, p)
        return page_id

    return notion.create_page(db.places, p)


def create_or_get_mention(
    notion: NotionClient,
    db: NotionDBIds,
    props: NotionProps,
    *,
    mention_name: str,
    dish_id: Optional[str],
    place_id: Optional[str],
    video_id: str,
    evidence_ocr: str,
    evidence_stt: str,
    confidence: float,
    mention_score: float,
    mention_time_iso: Optional[str],
) -> str:
    # Use title equals mention_name as idempotency key
    results = notion.query_database(
        db.mentions,
        {"property": props.mention_title, "title": {"equals": mention_name}},
    )

    text_limit = int(os.getenv("NOTION_TEXT_LIMIT", "1900"))

    p: Dict[str, Any] = {
        props.mention_title: notion.prop_title(mention_name),
        props.mention_conf: notion.prop_number(float(confidence)),
        props.mention_score: notion.prop_number(float(mention_score)),
        props.mention_ev_ocr: notion.prop_text(truncate(evidence_ocr, text_limit)),
        props.mention_ev_stt: notion.prop_text(truncate(evidence_stt, text_limit)),
        props.mention_video: notion.prop_relation([video_id]),
    }
    if dish_id:
        p[props.mention_dish] = notion.prop_relation([dish_id])
    if place_id:
        p[props.mention_place] = notion.prop_relation([place_id])
    if mention_time_iso:
        p[props.mention_time] = notion.prop_date_iso(mention_time_iso)

    if results:
        page_id = results[0]["id"]
        notion.update_page(page_id, p)
        return page_id

    return notion.create_page(db.mentions, p)


def compute_mention_score(confidence: float, has_place: bool, has_address: bool, has_hours: bool) -> float:
    """
    Simple starter scoring. You can later swap to your exact formula.
    """
    score = confidence
    if has_place:
        score += 0.10
    if has_address:
        score += 0.10
    if has_hours:
        score += 0.05
    return min(score, 1.0)


def iso_from_created_plus_offset(created_iso: Optional[str], start_sec: Optional[float]) -> Optional[str]:
    if not created_iso or start_sec is None:
        return None
    try:
        # created_iso often includes timezone offset; parse robustly
        dt = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt2 = dt + timedelta(seconds=float(start_sec))
        return dt2.isoformat()
    except Exception:
        return None
