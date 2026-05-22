from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FeatureBundle:
    """Immutable snapshot of all features needed to evaluate a token candidate.

    Produced once per token per scan cycle by DipScanner. Passed by reference
    to every BotEvaluator (N bots see the same bundle, decide independently).
    """

    # Identity
    token: str
    address: str
    pair_address: str
    chain: str
    snapshot_ts: float

    # Price / market data
    price_usd: float
    mcap_usd: float
    age_hours: float
    pc_h24: Optional[float]
    pc_h6: Optional[float]
    pc_h1: Optional[float]
    pc_m5: Optional[float]
    vol_h1_usd: Optional[float]
    bs_h1: Optional[float]

    # Macro
    sol_pc_h1: Optional[float]
    sol_pc_h4: Optional[float]
    sol_pc_h6: Optional[float]
    sol_pc_h24: Optional[float]
    btc_pc_h1: Optional[float]
    btc_pc_h6: Optional[float]
    btc_bs_h1: Optional[float]

    # On-chain
    net_flow_15s_usd: Optional[float]
    net_flow_60s_usd: Optional[float]
    net_flow_5m_usd: Optional[float]
    top_buy_makers_n: Optional[int]
    p90_buy_size_usd: Optional[float]

    # Chart / model
    chart_mtf_score: Optional[float]
    chart_score: Optional[float]
    cnn_cluster_id: Optional[int]
    fusion_outcome_prob: Optional[float]

    # Triggers + filters already evaluated by the scanner pipeline
    triggers_fired: tuple[str, ...]
    triggers_shadow: tuple[str, ...]
    filters_block: tuple[str, ...]
    filters_pass: tuple[str, ...]
    filters_shadow: tuple[str, ...]

    # Legacy passthrough for fields not yet promoted to typed slots
    raw_meta: dict = field(default_factory=dict)
