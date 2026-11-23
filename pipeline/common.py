"""Shared paths, config and a polite HTTP client for the pipeline."""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = ROOT.parent / "cache"
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text())


def slugify(sci_name: str) -> str:
    """'Puma concolor' or 'Puma_concolor' -> 'puma-concolor'."""
    s = unicodedata.normalize("NFKD", sci_name or "unknown").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s


class Http:
    """requests.Session with a User-Agent, retries and a per-host delay."""

    def __init__(self, min_interval: float = 1.0):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = CONFIG["user_agent"]
        self.min_interval = min_interval
        self._last = 0.0

    def get(self, url: str, **kw) -> requests.Response:
        timeout = kw.pop("timeout", 60)
        for attempt in range(10):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.get(url, timeout=timeout, **kw)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                # Back off hard: Wikimedia and iNat both throttle shared IPs.
                pause = max(int(r.headers.get("Retry-After", 0) or 0), 30 * (attempt + 1))
                print(f"    429 from {url.split('/')[2]}, pausing {pause}s", flush=True)
                time.sleep(pause)
                continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"gave up on {url}")

    def json(self, url: str, **kw):
        return self.get(url, **kw).json()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
