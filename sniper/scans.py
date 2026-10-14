"""Load scan profiles from scans/scans.yaml."""
from __future__ import annotations

from dataclasses import dataclass

import yaml

from . import config


@dataclass(frozen=True)
class Scan:
    name: str
    module: str
    when: str  # intraday | daily
    filters: str = ""
    signal: str = ""
    order: str = ""


def load_scans(path=None) -> list[Scan]:
    path = path or (config.SCANS_DIR / "scans.yaml")
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    out = []
    for s in doc.get("scans", []):
        out.append(Scan(
            name=s["name"], module=s["module"], when=s.get("when", "daily"),
            filters=s.get("filters", "") or "", signal=s.get("signal", "") or "",
            order=s.get("order", "") or "",
        ))
    return out
