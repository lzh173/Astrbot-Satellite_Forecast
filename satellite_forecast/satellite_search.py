"""Fuzzy search over normalized CelesTrak and SatNOGS satellite records."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from .models import SatelliteRecord


def normalize_query(value: str) -> str:
    """Normalize names such as ``METOP-C`` and ``metop c`` consistently."""

    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class SatelliteSearchResult:
    satellite: SatelliteRecord
    score: float
    matched_on: str


class SatelliteSearchIndex:
    """In-memory catalog index with deterministic fuzzy ranking."""

    def __init__(self, satellites: Iterable[SatelliteRecord] = ()) -> None:
        self._satellites: dict[str, SatelliteRecord] = {}
        self.replace(satellites)

    def replace(self, satellites: Iterable[SatelliteRecord]) -> None:
        self._satellites = {item.norad_id: item for item in satellites}

    def upsert(self, satellite: SatelliteRecord) -> None:
        self._satellites[satellite.norad_id] = satellite

    def remove(self, norad_id: str) -> bool:
        return self._satellites.pop(norad_id, None) is not None

    def all(self) -> list[SatelliteRecord]:
        return list(self._satellites.values())

    def search(self, query: str, limit: int = 10) -> list[SatelliteSearchResult]:
        """Search NORAD IDs, names, aliases, and transmitter descriptions."""

        normalized = normalize_query(query)
        if not normalized:
            return []
        if limit < 1:
            return []

        results: list[SatelliteSearchResult] = []
        for satellite in self._satellites.values():
            candidates = [(satellite.norad_id, "norad_id"), (satellite.name, "name")]
            candidates.extend((alias, "alias") for alias in satellite.aliases)
            candidates.extend(
                (transmitter.description, "transmitter")
                for transmitter in satellite.transmitters
                if transmitter.description
            )
            best_score = 0.0
            best_label = ""
            for candidate, label in candidates:
                score = self._score(normalized, normalize_query(candidate))
                if score > best_score:
                    best_score, best_label = score, label
            if best_score > 0:
                results.append(SatelliteSearchResult(satellite, best_score, best_label))

        results.sort(key=lambda item: (-item.score, item.satellite.name.casefold(), item.satellite.norad_id))
        return results[:limit]

    @staticmethod
    def _score(query: str, candidate: str) -> float:
        if not candidate:
            return 0.0
        if query == candidate:
            return 100.0
        if candidate.startswith(query):
            return 90.0 + len(query) / max(len(candidate), 1)
        if query in candidate:
            return 75.0 + len(query) / max(len(candidate), 1)
        ratio = SequenceMatcher(None, query, candidate).ratio()
        if ratio < 0.45:
            return 0.0
        return ratio * 70.0
