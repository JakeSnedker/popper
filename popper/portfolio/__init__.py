"""Portfolio-level analytics: correlation between edges' return streams (Phase D1)."""

from popper.portfolio.correlation import (
    correlation_matrix,
    diversification_score,
    return_stream,
)

__all__ = ["return_stream", "correlation_matrix", "diversification_score"]
