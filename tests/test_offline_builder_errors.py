import importlib.util
from pathlib import Path

import httpx
import pytest


def _load_builder():
    path = Path(__file__).parents[1] / "scripts" / "download_ephemeris.py"
    spec = importlib.util.spec_from_file_location("offline_builder_errors", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_builder_reports_source_and_exception_type(monkeypatch, tmp_path):
    builder = _load_builder()

    async def failed_fetch(self):
        raise httpx.ReadTimeout("source timeout")

    monkeypatch.setattr(builder.CelesTrakSource, "fetch", failed_fetch)
    with pytest.raises(RuntimeError, match=r"CelesTrak download failed: ReadTimeout"):
        await builder.build_database(tmp_path / "satellite.db", skip_transmitters=True)
