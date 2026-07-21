from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True, slots=True)
class Asset:
    symbol: str
    name: str
    asset_class: str

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("asset symbol cannot be empty")


@dataclass(frozen=True, slots=True)
class PositionWeight:
    asset_symbol: str
    weight: Decimal

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.weight <= Decimal("1"):
            raise ValueError("position weight must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class PortfolioProduct:
    product_id: str
    name: str
    benchmark_symbol: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PortfolioVersion:
    version_id: str
    product_id: str
    version_number: int
    effective_date: date
    weights: tuple[PositionWeight, ...]

    def __post_init__(self) -> None:
        if self.version_number < 1:
            raise ValueError("version number must be positive")
        symbols = [position.asset_symbol for position in self.weights]
        if len(symbols) != len(set(symbols)):
            raise ValueError("an asset cannot appear twice in one version")
        total = sum((position.weight for position in self.weights), Decimal("0"))
        if abs(total - Decimal("1")) > Decimal("0.0001"):
            raise ValueError("portfolio weights must sum to 1")


@dataclass(frozen=True, slots=True)
class InvestmentMandate:
    product_id: str
    objective: str
    risk_level: str
    max_single_asset_weight: Decimal
    min_cash_weight: Decimal
    max_turnover: Decimal
    maximum_data_age_days: int = 3
    maximum_stress_loss: Decimal = Decimal("0.20")

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("investment objective cannot be empty")
        for name, value in (
            ("max single asset weight", self.max_single_asset_weight),
            ("minimum cash weight", self.min_cash_weight),
            ("maximum turnover", self.max_turnover),
            ("maximum stress loss", self.maximum_stress_loss),
        ):
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError(f"{name} must be between 0 and 1")
        if self.maximum_data_age_days < 0:
            raise ValueError("maximum data age days cannot be negative")


@dataclass(frozen=True, slots=True)
class ResearchEvidence:
    evidence_id: str
    title: str
    source: str
    url: str
    published_at: datetime

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.source.strip() or not self.url.strip():
            raise ValueError("evidence title, source and URL are required")


@dataclass(frozen=True, slots=True)
class AssetView:
    asset_symbol: str
    direction: Literal["negative", "neutral", "positive"]
    confidence: Decimal
    thesis: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("asset view confidence must be between 0 and 1")
        if not self.thesis.strip():
            raise ValueError("asset view thesis cannot be empty")
        if not self.evidence_ids:
            raise ValueError("asset view must cite at least one evidence item")


@dataclass(frozen=True, slots=True)
class ResearchReport:
    report_id: str
    product_id: str
    as_of_date: date
    market_regime: str
    summary: str
    confidence: Decimal
    evidence: tuple[ResearchEvidence, ...]
    asset_views: tuple[AssetView, ...]

    def __post_init__(self) -> None:
        if not self.market_regime.strip() or not self.summary.strip():
            raise ValueError("market regime and research summary are required")
        if not Decimal("0") <= self.confidence <= Decimal("1"):
            raise ValueError("research confidence must be between 0 and 1")
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("research evidence IDs must be unique")
        if not self.asset_views:
            raise ValueError("research report must contain asset views")
        for view in self.asset_views:
            unknown = set(view.evidence_ids) - evidence_ids
            if unknown:
                raise ValueError(f"asset view cites unknown evidence: {', '.join(sorted(unknown))}")
