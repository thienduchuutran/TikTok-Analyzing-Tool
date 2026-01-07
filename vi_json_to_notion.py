import os
import re
import json
import time
import argparse
import requests
from typing import Any, Dict, List, Optional, Union
from dotenv import load_dotenv

NOTION_API_BASE = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2025-09-03"
DEFAULT_STATE_FILE = ".notion_state.json"


# -----------------------------
# General helpers
# -----------------------------
def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise SystemExit(f"Missing env var: {name}")
    return val


def notion_headers(token: str, notion_version: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": notion_version,
        "Content-Type": "application/json",
    }


def safe_get(obj: Any, path: List[Any], default=None):
    """
    Safe getter that supports dict keys + list indices.
    Your old version only worked for dicts, so videos[0] always failed.
    """
    cur = obj
    for p in path:
        try:
            if isinstance(cur, dict):
                cur = cur[p]
            elif isinstance(cur, list) and isinstance(p, int):
                cur = cur[p]
            else:
                return default
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def chunk_text(s: str, chunk_size: int = 1800) -> List[str]:
    s = s or ""
    return [s[i : i + chunk_size] for i in range(0, len(s), chunk_size)] or [""]


def rt(text: str) -> List[Dict[str, Any]]:
    # Notion rich_text helper
    return [{"type": "text", "text": {"content": text}}]


def parse_time_to_seconds(t: str) -> Optional[float]:
    # Handles "0:00:03.0333333"
    if not t:
        return None
    parts = t.split(":")
    try:
        if len(parts) == 3:
            h = int(parts[0])
            m = int(parts[1])
            s = float(parts[2])
            return h * 3600 + m * 60 + s
        if len(parts) == 2:
            m = int(parts[0])
            s = float(parts[1])
            return m * 60 + s
    except ValueError:
        return None
    return None


# -----------------------------
# Notion API wrapper w/ retry
# -----------------------------
def notion_request(method: str, url: str, headers: Dict[str, str], payload: Optional[dict] = None) -> dict:
    max_attempts = 6
    backoff = 0.8

    for attempt in range(1, max_attempts + 1):
        r = requests.request(method, url, headers=headers, json=payload, timeout=30)

        if r.ok:
            return r.json() if r.text else {}

        # Rate limit
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            sleep_s = float(retry_after) if retry_after else backoff
            time.sleep(sleep_s)
            backoff = min(backoff * 1.7, 8.0)
            continue

        # Transient server/network-ish errors
        if r.status_code in (500, 502, 503, 504):
            time.sleep(backoff)
            backoff = min(backoff * 1.7, 8.0)
            continue

        raise RuntimeError(f"Notion API error ({r.status_code}): {r.text}")

    raise RuntimeError(f"Notion API error: exceeded retries for {method} {url}")


# -----------------------------
# Notion blocks
# -----------------------------
def heading(level: int, text: str) -> Dict[str, Any]:
    t = f"heading_{level}"
    return {"object": "block", "type": t, t: {"rich_text": rt(text)}}


def paragraph(text: str) -> Dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(text)}}


def bullet(text: str) -> Dict[str, Any]:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt(text)}}


def divider() -> Dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


# -----------------------------
# Video Indexer extraction
# -----------------------------
def get_insights_dict(vi: dict) -> dict:
    # Most common shape: vi["videos"][0]["insights"]
    d = safe_get(vi, ["videos", 0, "insights"], None)
    if isinstance(d, dict):
        return d
    # Fallback: if someone passed inner object
    d = vi.get("insights")
    return d if isinstance(d, dict) else {}


def pick_video_name(vi: dict, fallback: str) -> str:
    return (
        vi.get("name")
        or safe_get(vi, ["videos", 0, "name"])
        or safe_get(vi, ["videos", 0, "videoName"])
        or safe_get(vi, ["summarizedInsights", "name"])
        or fallback
    )


def pick_video_id(vi: dict) -> Optional[str]:
    return vi.get("id") or safe_get(vi, ["summarizedInsights", "id"]) or safe_get(vi, ["videos", 0, "id"])


def pick_duration(vi: dict) -> Optional[str]:
    ins = get_insights_dict(vi)
    return (
        vi.get("duration")
        or ins.get("duration")
        or safe_get(vi, ["summarizedInsights", "duration", "time"])
        or safe_get(vi, ["summarizedInsights", "duration"])
    )


def pick_created(vi: dict) -> Optional[str]:
    return vi.get("created") or safe_get(vi, ["summarizedInsights", "created"])


def extract_keywords(vi: dict, limit: int = 30) -> List[str]:
    ins = get_insights_dict(vi)
    # Could be summarizedInsights.keywords or insights.keywords
    items = safe_get(vi, ["summarizedInsights", "keywords"], None)
    if not isinstance(items, list):
        items = ins.get("keywords") or []

    out = []
    for it in items[:limit]:
        # summarizedInsights often has "name"; insights has "text"
        val = it.get("name") or it.get("text")
        if val:
            out.append(str(val).strip())
    return [x for x in out if x]


def extract_labels(vi: dict, limit: int = 30) -> List[str]:
    ins = get_insights_dict(vi)
    items = safe_get(vi, ["summarizedInsights", "labels"], None)
    if not isinstance(items, list):
        items = ins.get("labels") or []

    out = []
    for it in items[:limit]:
        val = it.get("name") or it.get("text")
        if val:
            out.append(str(val).strip())
    return [x for x in out if x]


def extract_topics(vi: dict, limit: int = 15) -> List[str]:
    ins = get_insights_dict(vi)
    items = safe_get(vi, ["summarizedInsights", "topics"], None)
    if not isinstance(items, list):
        items = ins.get("topics") or []

    out = []
    for t in items[:limit]:
        nm = t.get("name")
        conf = t.get("confidence")
        if nm is None:
            continue
        if isinstance(conf, (int, float)):
            out.append(f"{nm} (conf {conf:.2f})")
        else:
            out.append(str(nm))
    return out


def extract_sentiments(vi: dict, limit: int = 10) -> List[str]:
    ins = get_insights_dict(vi)
    items = safe_get(vi, ["summarizedInsights", "sentiments"], None)
    if not isinstance(items, list):
        items = ins.get("sentiments") or []

    out = []
    for s in items[:limit]:
        sk = s.get("sentimentKey") or s.get("sentimentType")
        ratio = s.get("seenDurationRatio")
        avg = s.get("averageScore")
        if not sk:
            continue

        extras = []
        if isinstance(ratio, (int, float)):
            extras.append(f"ratio {ratio:.2f}")
        if isinstance(avg, (int, float)):
            extras.append(f"avg {avg:.2f}")

        out.append(f"{sk}" + (f" ({', '.join(extras)})" if extras else ""))
    return out


def extract_transcript_text(vi: dict) -> str:
    ins = get_insights_dict(vi)
    items = ins.get("transcript") or []
    texts = []
    for t in items:
        txt = (t.get("text") or "").strip()
        conf = t.get("confidence", None)

        # filter junk like "I."
        if len(txt) < 4:
            continue
        if re.fullmatch(r"[\W_]+", txt):
            continue
        if isinstance(conf, (int, float)) and conf < 0.30:
            continue

        texts.append(txt)

    return " ".join(texts).strip()


def extract_ocr_lines(vi: dict, max_lines: int = 60) -> List[str]:
    ins = get_insights_dict(vi)
    ocr_items = ins.get("ocr") or []
    best_start: Dict[str, float] = {}

    for o in ocr_items:
        txt = (o.get("text") or "").strip()
        if not txt:
            continue

        instances = o.get("instances") or []
        start = None
        if instances:
            s = instances[0].get("start") or instances[0].get("adjustedStart")
            start = parse_time_to_seconds(s) if s else None

        key = txt
        cur = best_start.get(key)
        val = start if start is not None else 999999.0
        if cur is None or val < cur:
            best_start[key] = val

    # sort by timestamp, then keep only max_lines
    ordered = sorted(best_start.items(), key=lambda x: x[1])[:max_lines]
    out = []
    for txt, ts in ordered:
        if ts != 999999.0:
            out.append(f"[{ts:.1f}s] {txt}")
        else:
            out.append(txt)
    return out


def extract_takeaways_from_ocr(ocr_lines: List[str]) -> Dict[str, Any]:
    # ocr_lines are like "[12.3s] TEXT" — strip timestamps
    texts = []
    for line in ocr_lines:
        cleaned = re.sub(r"^\[\d+(\.\d+)?s\]\s*", "", line).strip()
        if cleaned:
            texts.append(cleaned)

    handle = next((t for t in texts if "@" in t), None)

    # Very rough "address-ish" detector: has a number and a comma
    address = next((t for t in texts if re.search(r"\b\d{2,}\b", t) and "," in t), None)

    dish = None
    for t in texts:
        up = t.upper()
        if "BANH" in up and ("TIEU" in up or "TIÊU" in up):
            dish = t
            break

    fillings = []
    for t in texts:
        up = t.upper()
        if any(k in up for k in ["NHAN", "TRUNG", "PHO MAI", "PHÔ MAI"]):
            fillings.append(t)

    claims = []
    for t in texts:
        up = t.upper()
        if any(k in up for k in ["NONG", "NÓNG", "GION", "GIÒN", "THOM", "THƠM", "NGON", "NGON", "DAM BAO", "ĐẢM BẢO"]):
            claims.append(t)

    def dedupe(lst):
        seen = set()
        out = []
        for x in lst:
            k = x.strip().lower()
            if k and k not in seen:
                seen.add(k)
                out.append(x)
        return out

    return {
        "handle": handle,
        "address": address,
        "dish": dish,
        "fillings": dedupe(fillings)[:10],
        "claims": dedupe(claims)[:10],
    }


# -----------------------------
# Notion page strategy: one log page
# -----------------------------
def get_data_source_id(database_id: str, headers: Dict[str, str]) -> Optional[str]:
    # Some Notion versions return data_sources[]
    url = f"{NOTION_API_BASE}/databases/{database_id}"
    try:
        data = notion_request("GET", url, headers=headers)
    except Exception:
        return None
    ds = data.get("data_sources") or []
    return ds[0].get("id") if ds else None


def find_page_in_database(database_id: str, title_prop: str, title: str, headers: Dict[str, str]) -> Optional[str]:
    url = f"{NOTION_API_BASE}/databases/{database_id}/query"
    payload = {
        "filter": {
            "property": title_prop,
            "title": {"equals": title},
        }
    }
    data = notion_request("POST", url, headers=headers, payload=payload)
    results = data.get("results") or []
    if not results:
        return None
    return results[0].get("id")


def create_log_page(database_id: str, data_source_id: Optional[str], title_prop: str, title: str, headers: Dict[str, str]) -> Dict[str, Any]:
    url = f"{NOTION_API_BASE}/pages"
    parent = {"type": "database_id", "database_id": database_id}
    if data_source_id:
        parent = {"type": "data_source_id", "data_source_id": data_source_id}

    payload = {
        "parent": parent,
        "properties": {
            title_prop: {"title": [{"type": "text", "text": {"content": title}}]}
        },
    }
    return notion_request("POST", url, headers=headers, payload=payload)


def append_blocks(block_id: str, blocks: List[Dict[str, Any]], headers: Dict[str, str]) -> None:
    url = f"{NOTION_API_BASE}/blocks/{block_id}/children"
    batch_size = 90
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i : i + batch_size]
        notion_request("PATCH", url, headers=headers, payload={"children": batch})


# -----------------------------
# State (avoid duplicates)
# -----------------------------
def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"ingested_video_ids": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"ingested_video_ids": []}


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# -----------------------------
# Build blocks for ONE video entry (append section)
# -----------------------------
def build_video_section_blocks(vi: dict, source_file: str) -> List[Dict[str, Any]]:
    name = pick_video_name(vi, fallback=os.path.basename(source_file))
    video_id = pick_video_id(vi) or "unknown"
    duration = pick_duration(vi)
    created = pick_created(vi)

    keywords = extract_keywords(vi)
    labels = extract_labels(vi)
    topics = extract_topics(vi)
    sentiments = extract_sentiments(vi)
    transcript = extract_transcript_text(vi)
    ocr_lines = extract_ocr_lines(vi)
    take = extract_takeaways_from_ocr(ocr_lines)

    blocks: List[Dict[str, Any]] = []
    blocks.append(heading(2, f"🎬 {name}  —  {video_id}"))
    blocks.append(bullet(f"Source file: {os.path.basename(source_file)}"))
    if duration:
        blocks.append(bullet(f"Duration: {duration}"))
    if created:
        blocks.append(bullet(f"Created: {created}"))

    # Key takeaways (OCR-first)
    blocks.append(heading(3, "Key takeaways (OCR-first)"))
    any_take = False
    if take["dish"]:
        blocks.append(bullet(f"Dish: {take['dish']}")); any_take = True
    if take["fillings"]:
        blocks.append(bullet("Fillings/ingredients: " + " | ".join(take["fillings"]))); any_take = True
    if take["handle"]:
        blocks.append(bullet(f"Handle: {take['handle']}")); any_take = True
    if take["address"]:
        blocks.append(bullet(f"Address-ish text: {take['address']}")); any_take = True
    if take["claims"]:
        blocks.append(bullet("Claims: " + " | ".join(take["claims"]))); any_take = True
    if not any_take:
        blocks.append(paragraph("(No strong takeaways detected.)"))

    # Keywords/labels/topics/sentiment
    blocks.append(heading(3, "Keywords"))
    blocks += [bullet(k) for k in keywords[:25]] or [paragraph("(none)")]

    blocks.append(heading(3, "Labels"))
    blocks += [bullet(l) for l in labels[:25]] or [paragraph("(none)")]

    blocks.append(heading(3, "Topics"))
    blocks += [bullet(t) for t in topics[:15]] or [paragraph("(none)")]

    blocks.append(heading(3, "Sentiment"))
    blocks += [bullet(s) for s in sentiments[:10]] or [paragraph("(none)")]

    # Transcript
    blocks.append(heading(3, "Transcript"))
    if transcript:
        for chunk in chunk_text(transcript):
            blocks.append(paragraph(chunk))
    else:
        blocks.append(paragraph("(Transcript is empty or too low-quality; OCR is likely more useful for TikToks.)"))

    # OCR
    blocks.append(heading(3, "On-screen text (OCR)"))
    if ocr_lines:
        for line in ocr_lines:
            blocks.append(bullet(line))
    else:
        blocks.append(paragraph("(none)"))

    blocks.append(divider())
    return blocks


# -----------------------------
# Main
# -----------------------------
def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="Append Azure Video Indexer JSON into a single Notion log page")
    parser.add_argument("--file", required=True, help="Path to insights_*.json")
    parser.add_argument("--page-id", default=os.getenv("NOTION_PAGE_ID"), help="Existing Notion page ID to append into")
    parser.add_argument("--log-title", default=os.getenv("NOTION_LOG_PAGE_TITLE", "Video Indexer Log"), help="Title of the single log page (created if missing)")
    parser.add_argument("--state-file", default=os.getenv("NOTION_STATE_FILE", DEFAULT_STATE_FILE), help="Local state file to avoid duplicates")
    parser.add_argument("--force", action="store_true", help="Append even if this video_id was already ingested")
    args = parser.parse_args()

    notion_token = require_env("NOTION_TOKEN")
    notion_db_id = require_env("NOTION_DATABASE_ID")
    notion_version = os.getenv("NOTION_VERSION", DEFAULT_NOTION_VERSION)
    title_prop = os.getenv("NOTION_TITLE_PROPERTY", "Name")

    headers = notion_headers(notion_token, notion_version)

    with open(args.file, "r", encoding="utf-8") as f:
        vi = json.load(f)

    video_id = pick_video_id(vi) or os.path.basename(args.file)

    # Load state for dedupe
    state = load_state(args.state_file)
    ingested = set(state.get("ingested_video_ids") or [])

    if (not args.force) and (video_id in ingested):
        print(f"↩️  Skipping (already ingested): {video_id}")
        return

    # Decide target page (append to one log page)
    page_id = args.page_id

    # ✅ add this
    if not page_id:
        page_id = state.get("page_id")

    if not page_id:
        existing = find_page_in_database(notion_db_id, title_prop, args.log_title, headers)
        if existing:
            page_id = existing
        else:
            ds_id = get_data_source_id(notion_db_id, headers)
            page = create_log_page(notion_db_id, ds_id, title_prop, args.log_title, headers)
            page_id = page["id"]

    # Build and append
    blocks = build_video_section_blocks(vi, source_file=args.file)
    append_blocks(page_id, blocks, headers=headers)

    # Save state
    ingested.add(video_id)
    state["ingested_video_ids"] = sorted(ingested)
    state["page_id"] = page_id
    save_state(args.state_file, state)

    print(f"✅ Appended video section to log page. Video ID: {video_id}")
    print(f"   (state saved in {args.state_file})")


if __name__ == "__main__":
    main()
