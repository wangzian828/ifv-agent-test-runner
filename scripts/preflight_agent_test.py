#!/usr/bin/env python3
"""Validate the evaluator-only Agent test environment without printing secrets."""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urljoin

import requests


REQUIRED = (
    "QWEN_TEACHER_BASE_URL",
    "QWEN_TEACHER_API_KEY",
    "QWEN_TEACHER_MODEL",
    "QWEN_TEACHER_VISION_MODEL",
    "SERPER_API_KEY",
    "JINA_API_KEY",
    "BAIDU_OCR_API_KEY",
    "BAIDU_OCR_SECRET_KEY",
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET",
    "OSS_ENDPOINT",
    "OSS_BUCKET_NAME",
)


def main() -> int:
    missing = [name for name in REQUIRED if not os.getenv(name, "").strip()]
    if missing:
        raise SystemExit("missing required environment variables: " + ", ".join(missing))
    if os.getenv("OMP_NUM_THREADS") != "1":
        raise SystemExit("OMP_NUM_THREADS=1 is required")
    if os.getenv("OCR_BACKEND", "baidu") != "baidu":
        raise SystemExit("OCR_BACKEND=baidu is required; local OCR is prohibited")
    if os.getenv("BROWSE_FETCH_PROVIDER", "jina") != "jina":
        raise SystemExit("BROWSE_FETCH_PROVIDER=jina is required")
    if os.getenv("VISUAL_SEARCH_PROVIDER", "serper_lens") != "serper_lens":
        raise SystemExit("VISUAL_SEARCH_PROVIDER=serper_lens is required")
    if os.getenv("IMAGE_UPLOAD_PROVIDER") != "oss":
        raise SystemExit("IMAGE_UPLOAD_PROVIDER=oss is required")

    model = os.environ["QWEN_TEACHER_MODEL"].strip()
    vision_model = os.environ["QWEN_TEACHER_VISION_MODEL"].strip()
    if model != vision_model:
        raise SystemExit("QWEN_TEACHER_VISION_MODEL must equal QWEN_TEACHER_MODEL")

    base_url = os.environ["QWEN_TEACHER_BASE_URL"].rstrip("/") + "/"
    response = requests.get(
        urljoin(base_url, "models"),
        headers={"Authorization": f"Bearer {os.environ['QWEN_TEACHER_API_KEY']}"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    model_ids = {
        str(item.get("id") or "")
        for item in payload.get("data", [])
        if isinstance(item, dict)
    }
    if model_ids and model not in model_ids:
        raise SystemExit(f"configured model is absent from /models: {model}")
    print(
        json.dumps(
            {
                "status": "ok",
                "model": model,
                "models_endpoint_returned": len(model_ids),
                "ocr_backend": "baidu",
                "browse_fetch_provider": "jina",
                "visual_search_provider": "serper_lens",
                "image_upload_provider": "oss",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
