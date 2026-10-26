"""
4-QISM: Risk va Sessiya Filtri (risk_manager.py)
London (12:00-15:00) va Nyu-York (17:00-20:00) Killzone tekshiruvi,
High-Impact yangiliklardan himoya va ATR/SL asosida Lot hajmini hisoblash.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import (
    ACCOUNT_BALANCE_DEFAULT,
    RISK_PER_TRADE_PCT,
    MIN_CONFIDENCE_SCORE,
    KILLZONES_TASHKENT,
    TIMEZONE_OFFSET_HOURS,
)
from schemas import FundamentalSignal, TechnicalSetup, FinalTradeSignal


class RiskManager:
    """Sessiya vaqti, yangilik xavfi va pozitsiya hajmini (Lot Size) nazorat qiluvchi agent."""

    def __init__(
        self,
        account_balance: float = ACCOUNT_BALANCE_DEFAULT,
        risk_pct: float = RISK_PER_TRADE_PCT,
        enforce_killzone: bool = True,
    ):
        self.account_balance = account_balance
        self.risk_pct = risk_pct
        self.enforce_killzone = enforce_killzone

    @staticmethod
    def get_tashkent_time() -> datetime:
        tashkent_tz = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
        return datetime.now(tashkent_tz)

    def check_active_killzone(self, current_dt: Optional[datetime] = None) -> tuple[bool, str]:
        """Toshkent vaqti bilan London (12:00-15:00) yoki Nyu-York (17:00-20:00) sessiyasini tekshiradi."""
        dt = current_dt or self.get_tashkent_time()
        hour = dt.hour
        for name, (start_h, end_h) in KILLZONES_TASHKENT.items():
            if start_h <= hour < end_h:
                return True, f"{name} KILLZONE ({start_h:02d}:00-{end_h:02d}:00 UZT)"
        return False, f"OFF-SESSION ({dt.strftime('%H:%M')} UZT)"

    def calculate_lot_size(self, symbol: str, entry: float, sl: float) -> tuple[float, float]:
        """
        Balans va Stop-Loss masofasiga qarab optimal Lot hajmini hisoblaydi.
        XAUUSD uchun 1.00 lotda $1.00 harakat = $100.
        """
        risk_usd = round(self.account_balance * (self.risk_pct / 100.0), 2)
        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            return 0.01, risk_usd

        if "XAU" in symbol.upper():
            raw_lot = risk_usd / (sl_distance * 100.0)
        elif "BTC" in symbol.upper():
            raw_lot = risk_usd / sl_distance
        else:
            raw_lot = risk_usd / (sl_distance * 100.0)

        lot = max(0.01, min(round(raw_lot, 2), 50.0))
        return lot, risk_usd

    def evaluate_trade(
        self,
        fundamental: FundamentalSignal,
        technical: TechnicalSetup,
        current_dt: Optional[datetime] = None,
    ) -> FinalTradeSignal:
        """Barcha xavf filtrlaridan o'tkazib, FinalTradeSignal qaytaradi."""
        now_tashkent = current_dt or self.get_tashkent_time()
        in_killzone, session_label = self.check_active_killzone(now_tashkent)
        lot_size, risk_usd = self.calculate_lot_size(
            technical.symbol, technical.entry_price, technical.sl_price
        )

        reasons: list[str] = []
        if fundamental.high_impact_news_imminent:
            reasons.append("Yaqin 30 daqiqa ichida kuchli (High-Impact) USD yangiligi bor.")
        if abs(fundamental.confidence_score) < MIN_CONFIDENCE_SCORE:
            reasons.append(
                f"Fundamental ishonchlilik darajasi past ({fundamental.confidence_score} < {MIN_CONFIDENCE_SCORE})."
            )
        if not technical.is_valid or technical.direction == "NONE":
            reasons.append("M15/H1 grafikda tasdiqlangan SMC kirish nuqtasi topilmadi.")
        if self.enforce_killzone and not in_killzone:
            reasons.append(f"Tashqi sessiya vaqti ({session_label}). Faqat London va NY Killzoneda ruxsat etiladi.")

        is_approved = len(reasons) == 0
        return FinalTradeSignal(
            fundamental=fundamental,
            technical=technical,
            lot_size=lot_size,
            timestamp=now_tashkent.strftime("%Y-%m-%d %H:%M:%S UZT"),
            session_name=session_label,
            risk_amount_usd=risk_usd,
            is_approved=is_approved,
            rejection_reason="; ".join(reasons) if reasons else None,
        )