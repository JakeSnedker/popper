import pytest

from popper.data import bars


def test_missing_data_points_to_format_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(bars, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bars, "CANONICAL_DATASET_DIR", tmp_path / "none")
    bars._read.cache_clear()
    with pytest.raises(FileNotFoundError, match=r"No ABC M5 market data found.*data/README.md"):
        bars._read("ABC", "M5")
