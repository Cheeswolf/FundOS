from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class AssetInput(BaseModel):
    symbol: str = Field(min_length=1)
    name: str = Field(min_length=1)
    asset_class: str = Field(min_length=1)


class ProductCreate(BaseModel):
    product_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    benchmark_symbol: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    risk_level: str = Field(min_length=1)
    max_single_asset_weight: Decimal = Field(ge=0, le=1)
    min_cash_weight: Decimal = Field(ge=0, le=1)
    max_turnover: Decimal = Field(ge=0, le=1)
    maximum_data_age_days: int = Field(default=3, ge=0)
    maximum_stress_loss: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)


class PriceInput(BaseModel):
    symbol: str = Field(min_length=1)
    trade_date: date
    close: float = Field(gt=0)


class PriceBatchInput(BaseModel):
    provider: str = Field(min_length=1)
    prices: list[PriceInput] = Field(min_items=1)


class EvidenceInput(BaseModel):
    evidence_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    url: str = Field(min_length=1)
    published_at: datetime
    content: str = Field(default="", max_length=12000)


class AssetViewInput(BaseModel):
    asset_symbol: str = Field(min_length=1)
    direction: Literal["negative", "neutral", "positive"]
    confidence: Decimal = Field(ge=0, le=1)
    thesis: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_items=1)


class ResearchCreate(BaseModel):
    report_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    as_of_date: date
    market_regime: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    confidence: Decimal = Field(ge=0, le=1)
    evidence: list[EvidenceInput] = Field(min_items=1)
    asset_views: list[AssetViewInput] = Field(min_items=1)
    finalize: bool = True


class WeightInput(BaseModel):
    asset_symbol: str = Field(min_length=1)
    weight: Decimal = Field(ge=0, le=1)


class ProposalCreate(BaseModel):
    version_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    version_number: int = Field(gt=0)
    effective_date: date
    weights: list[WeightInput] = Field(min_items=1)
    research_report_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    run_id: str | None = None


class RiskReviewInput(BaseModel):
    provider: str = Field(min_length=1)
    as_of_date: date
    stress_scenarios: dict[str, dict[str, float]]


class CommitteeDecisionInput(BaseModel):
    approved: bool
    rationale: str = Field(min_length=1)
    decided_by: str = Field(min_length=1)


class CircuitResetInput(BaseModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reset_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AlertLifecycleInput(BaseModel):
    updated_by: str = Field(min_length=1)
    note: str = Field(min_length=1)


class EvidenceReviewInput(BaseModel):
    approved: bool
    reviewed_by: str = Field(min_length=1)
    note: str = Field(min_length=1)


class ApprovedResearchRequestInput(BaseModel):
    report_id: str = Field(min_length=1)
    as_of_date: date
