"""Canonical timeframe parsing and higher-timeframe signal aliases."""

import re

_TIMEFRAME = re.compile(r"^(?:M?(\d+)|(\d+)m|(\d+)h)$", re.IGNORECASE)


def timeframe_minutes(value: str) -> int:
    text = value.strip()
    match = _TIMEFRAME.fullmatch(text)
    if not match:
        raise ValueError(f"unsupported timeframe {value!r}; expected M1, 5m or 1h-style")
    minutes = int(match.group(1) or match.group(2) or match.group(3))
    if match.group(3):
        minutes *= 60
    if minutes < 1:
        raise ValueError(f"timeframe must be positive: {value!r}")
    return minutes


def normalize_timeframe(value: str) -> str:
    return f"M{timeframe_minutes(value)}"


def higher_feature_alias(timeframe: str, feature: str) -> str:
    return f"htf_{normalize_timeframe(timeframe).lower()}_{feature}"
