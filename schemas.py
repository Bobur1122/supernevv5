"""
1-QISM: Barcha modullarni bog'lovchi yagona ma'lumot qoliplari (Data Contracts - schemas.py)
PRO v4.5 YANGILANGAN MAYDONLARI:
- Har bir signal uchun unikal tartib raqami (`signal_id: #1, #2, #3...`)
- US10Y (10 yillik obligatsiya) jonli trendi
- Kunlik va Sessiya Likvidlik Xaritasi (`pdh`, `pdl`, `asian_high`, `asian_low`)
- Liquidity Sweep + Wick Rejection (`liquidity_sweep_detected`, `sweep_note`)
- 3-shamchali FVG (Fair Value Gap) + Order Block kesishmasi
- Intraday VWAP va Dealing Range (Premium / Discount %) holati
- Relative Volume (RVOL - Institutsional hajm) va M15 RSI ko'rsatkichlari
- Order-Book Liquidity Walls (Kitlarning eng yirik Ask/Bid limit devorlari va Imbalance Ratio)
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
import pandas as pd


BiasType = Literal["BULLISH", "BEARISH", "NEUTRAL"]
DirectionType = Literal["BUY", "SELL", "NONE"]
OrderExecutionType = Literal["BUY_LIMIT", "SELL_LIMIT", "MARKET_BUY", "MARKET_SELL", "WAIT_FOR_ZONE"]


@dataclass
class MarketSnapshot:
    """Bozorning joriy texnik, korrelyatsion, Sessiya Likvidlik va Order-Book holati."""
    symbol: str
    htf_df: pd.DataFrame
    ltf_df: pd.DataFrame
    dxy_trend: str  # "BULLISH" | "BEARISH" | "NEUTRAL"
    atr: float
    current_price: float
    price_source: str = "TradingView (FOREXCOM:XAUUSD)"
    us10y_trend: str = "NEUTRAL"  # "BULLISH" | "BEARISH" | "NEUTRAL"
    whale_ask_wall: float = 0.0
    whale_ask_vol: float = 0.0
    whale_bid_wall: float = 0.0
    whale_bid_vol: float = 0.0
    ob_imbalance_ratio: float = 1.0
    pdh: float = 0.0          # Previous Day / 24h High
    pdl: float = 0.0          # Previous Day / 24h Low
    asian_high: float = 0.0   # Osiyo sessiyasi (03:00-11:00 UZT) High
    asian_low: float = 0.0    # Osiyo sessiyasi (03:00-11:00 UZT) Low
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def validate(self) -> bool:
        required_cols = {"time", "open", "high", "low", "close", "volume"}
        htf_ok = required_cols.issubset(set(self.htf_df.columns)) and not self.htf_df.empty
        ltf_ok = required_cols.issubset(set(self.ltf_df.columns)) and not self.ltf_df.empty
        return htf_ok and ltf_ok and self.current_price > 0 and self.atr > 0


@dataclass
class FundamentalSignal:
    """
    Fundamental Agent (DXY + US10Y + Jonli Order-Flow + FXStreet Voqealar Zanjiri tahlilchisi) natijasi.
    """
    symbol: str
    bias: str  # "BULLISH" | "BEARISH" | "NEUTRAL"
    confidence_score: float  # -1.0 (Kuchli Bearish) dan +1.0 (Kuchli Bullish) gacha
    macro_reason: str
    high_impact_news_imminent: bool
    key_drivers: list[str] = field(default_factory=list)
    causal_chain: str = ""
    key_actors_analysis: list[str] = field(default_factory=list)
    geopolitical_hotspots: list[str] = field(default_factory=list)
    predicted_next_moves: list[str] = field(default_factory=list)


@dataclass
class TechnicalSetup:
    """
    Texnik Agent (PRO v4.5 SMC: Liquidity Sweep + PDH/PDL + Asian Range + OB/FVG + VWAP + Whale Walls) natijasi.
    """
    symbol: str
    is_valid: bool
    direction: str  # "BUY" | "SELL" | "NONE"
    order_type: str  # "BUY_LIMIT" | "SELL_LIMIT" | "MARKET_BUY" | "MARKET_SELL" | "WAIT_FOR_ZONE"
    current_price: float
    entry_price: float
    entry_zone_low: float
    entry_zone_high: float
    sl_price: float
    tp1_price: float
    tp_price: float  # TP2
    tp3_price: float = 0.0  # TP3
    tp1_prob: int = 85
    tp2_prob: int = 65
    tp3_prob: int = 35
    recommended_tp: float = 0.0
    recommended_tp_label: str = ""
    recommended_tp_prob: int = 85
    recommended_tp_reason: str = ""
    rr_ratio: float = 3.0
    market_structure: str = "RANGING"
    setup_type: str = ""
    structure_note: str = ""
    vwap_price: float = 0.0
    dealing_range_pct: float = 50.0
    fvg_zone: str = "Yo'q"
    rvol_ratio: float = 1.0
    rsi_m15: float = 50.0
    whale_ask_wall: float = 0.0
    whale_bid_wall: float = 0.0
    ob_imbalance_ratio: float = 1.0
    liquidity_sweep_detected: bool = False
    sweep_note: str = ""
    confluence_score: int = 0
    confluence_Checklist: list[str] = field(default_factory=list)
    is_ideal_setup: bool = False


@dataclass
class FinalTradeSignal:
    """Risk Manager tekshiruvidan o'tgan yakuniy savdo signali."""
    fundamental: FundamentalSignal
    technical: TechnicalSetup
    lot_size: float
    timestamp: str
    session_name: str = "LONDON/NY"
    risk_amount_usd: float = 0.0
    is_approved: bool = True
    rejection_reason: Optional[str] = None
    signal_id: int = 1

    @property
    def signal_key(self) -> str:
        """Takroriy spam yubormaslik uchun unikal signal kaliti."""
        return f"{self.technical.symbol}_{self.technical.order_type}_{round(self.technical.entry_price, 0)}"