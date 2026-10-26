"""
5-QISM: Asosiy Asinxron Orkestrator — PRO v4.6 (100% Avtomatik Snayper Rejimi — `/signal` siz!) (main.py)

PRO v4.6 MUHIM TUZATISHLARI:
1) **100% Avtomatik Snayper Rejimi (`/signal` buyrug'isiz):**
   - Bot sizning buyrug'ingizni kutmaydi va majburiy signal chiqarmaydi — 24/7 har 1 daqiqada bozorni o'zi kuzatib,
     faqatgina bozorda haqiqiy **A+ qulay fursat (Setup >= 85/100 ball)** to'lganda o'zi avtomatik Telegramga yuboradi!
2) **Soxta "Aktiv: 2" (Ghost Pending) va 4096-belgi Muammosi 100% Hal Qilindi:**
   - Bot ishga tushishi bilan eski xabarsiz (`signal_sent=False`) soxta qulflar va tarix avtomatik tozalanadi.
   - Signal FAQAT Telegramga haqiqatan yetib borgandagina (`sent_ok == True`) bazaga yoziladi va qulflanadi.
3) **Shamcha Dumi (Wick Touch) va Tezkor Yangilanish (65% TP1 / 90 daqiqa):**
   - Limit orderga narx 1 daqiqa ichida dum (wick) bilan tegib qaytsa ham darhol `⚡ LIMIT ISHGA TUSHDI` xabari yuboriladi.
   - Agar narx Limit orderga tegmasdan TP1 yo'lining 65% qismini bosib o'tsa yoki 90 daqiqada tegmasa,
     eski limit avtomatik bekor qilinib yangi A+ fursat qidiriladi.
"""
import asyncio
import argparse
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import sys
import time
import urllib.request
from typing import Any, Optional

from config import SYMBOLS, NEWS_CHECK_INTERVAL_SECONDS, TIMEZONE_OFFSET_HOURS
from schemas import MarketSnapshot
from data_feed import MarketDataCollector
from news_scraper import MacroNewsScraper
from fundamental_agent import FundamentalAgent
from technical_agent import TechnicalAgent
from risk_manager import RiskManager
from telegram_bot import (
    TelegramCopilotNotifier,
    PerformanceStatsTracker,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("AI_Gold_Sniper")

STATE_FILE = "active_setups_state.json"
SL_COOLDOWN_SECONDS = 45 * 60       # SL dan keyin 45 daqiqa tanaffus
PENDING_EXPIRY_MINUTES = 90         # Limit order 90 daqiqada ishga tushmasa yangi struktura qidiriladi


class MultiAgentTradingOrchestrator:
    """
    PRO v4.6 24/7 fon rejimida 100% avtomatik ishlovchi, Ghost-Lock himoyasi, Smart Pending Cancel
    va Intra-Candle Trigger orkestratori.
    """

    def __init__(
        self,
        use_mt5: bool = False,
        enforce_killzone: bool = False,
        sniper_only: bool = True,
        gold_only: bool = False,
    ):
        self.collector = MarketDataCollector(use_mt5=use_mt5)
        self.scraper = MacroNewsScraper()
        self.fundamental_agent = FundamentalAgent()
        self.technical_agent = TechnicalAgent()
        self.risk_manager = RiskManager(enforce_killzone=enforce_killzone)
        self.notifier = TelegramCopilotNotifier()
        self.stats_tracker = PerformanceStatsTracker()
        self.sniper_only = sniper_only
        self.gold_only = gold_only
        self.active_setups: dict[str, dict[str, Any]] = self._load_and_clean_state()
        self.sl_cooldown_until: dict[str, float] = {}
        self.last_check_time: str = "Hali tekshirilmadi"
        self.last_prices: dict[str, float] = {}
        self.total_cycles: int = 0

    def _load_and_clean_state(self) -> dict[str, dict[str, Any]]:
        raw_state: dict[str, dict[str, Any]] = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    raw_state = json.load(f)
            except Exception:
                raw_state = {}

        # Faqat haqiqatda Telegramga yuborilgan (`signal_sent=True`) setuplarni qoldiramiz
        cleaned_state: dict[str, dict[str, Any]] = {}
        for sym, st in raw_state.items():
            if st.get("signal_sent", False):
                cleaned_state[sym] = st
            else:
                logger.info(f"[{sym}] Eski xabarsiz (ghost) setup tozalandi.")

        # `trade_history.json` dagi xabarsiz qolgan soxta PENDING bitimlarni ham tozalaymiz
        purged = self.stats_tracker.purge_unsent_ghost_trades(set(cleaned_state.keys()))
        if purged > 0:
            logger.info(f"Statistika bazasidan {purged} ta xabarsiz (ghost) PENDING qayd tozalandi.")

        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(cleaned_state, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        return cleaned_state

    def _save_state(self) -> None:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.active_setups, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Holatni saqlashda xato: {e}")

    @staticmethod
    def is_forex_market_open() -> bool:
        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        wd = tashkent_now.weekday()
        hr = tashkent_now.hour
        if wd == 5 and hr >= 2:
            return False
        if wd == 6:
            return False
        if wd == 0 and hr < 3:
            return False
        return True

    @staticmethod
    def _check_smart_pending_invalidation(
        setup: dict[str, Any],
        snapshot: Optional[MarketSnapshot],
        imminent_news: bool = False,
    ) -> tuple[bool, str]:
        """
        3-MODUL: Smart Pending Cancel (Xavfli Limit Orderni SL yemasdan oldin bekor qilish).
        """
        if imminent_news:
            return True, "Yaqin daqiqalarda kuchli (High-Impact) makroiqtisodiy yangilik chiqmoqda"

        if snapshot is None or snapshot.ltf_df.empty or len(snapshot.ltf_df) < 30:
            return False, ""

        direction = setup.get("direction", "SELL")
        entry = float(setup.get("entry_price", snapshot.current_price))
        atr = max(float(snapshot.atr), 8.5 if "XAU" in snapshot.symbol.upper() else 250.0)
        ltf = snapshot.ltf_df
        closed = ltf.iloc[:-1]

        sub36 = closed.tail(36)
        typ = (sub36["high"] + sub36["low"] + sub36["close"]) / 3.0
        vol = sub36["volume"].replace(0, 1.0)
        vwap = float((typ * vol).sum() / vol.sum())
        avg_vol = max(float(vol.mean()), 1.0)

        ema20 = float(closed["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(closed["close"].ewm(span=50, adjust=False).mean().iloc[-1])

        last_bar = closed.iloc[-1]
        live_bar = ltf.iloc[-1]
        last_body = float(last_bar["close"]) - float(last_bar["open"])
        live_body = float(live_bar["close"]) - float(live_bar["open"])
        last_rvol = round(float(last_bar["volume"]) / avg_vol, 2)

        if direction == "BUY":
            violent_bear_candle = (
                (last_body < -0.62 * atr and last_rvol >= 2.2)
                or (live_body < -0.72 * atr)
            )
            bearish_structure_flip = (
                float(last_bar["close"]) < vwap - 0.40 * atr
                and ema20 < ema50
                and snapshot.current_price <= entry - 0.15 * atr
            )
            if violent_bear_candle:
                return (
                    True,
                    f"Narx BUY_LIMIT (`{entry:.2f}`) ga kuchli Bearish impuls shamcha "
                    f"(`{abs(min(last_body, live_body)):.2f}` pt, RVOL `{last_rvol}x`) bilan qulab kelmoqda",
                )
            if bearish_structure_flip:
                return (
                    True,
                    f"Bozor strukturasi Bearish tomonga sindi (Narx `{snapshot.current_price:.2f}` < VWAP `{vwap:.2f}`, EMA20 < EMA50)",
                )
        else:
            violent_bull_candle = (
                (last_body > 0.62 * atr and last_rvol >= 2.2)
                or (live_body > 0.72 * atr)
            )
            bullish_structure_flip = (
                float(last_bar["close"]) > vwap + 0.40 * atr
                and ema20 > ema50
                and snapshot.current_price >= entry + 0.15 * atr
            )
            if violent_bull_candle:
                return (
                    True,
                    f"Narx SELL_LIMIT (`{entry:.2f}`) ga kuchli Bullish Short-Squeeze shamcha "
                    f"(`+{max(last_body, live_body):.2f}` pt, RVOL `{last_rvol}x`) bilan uchib kelmoqda",
                )
            if bullish_structure_flip:
                return (
                    True,
                    f"Bozor strukturasi Bullish tomonga sindi (Narx `{snapshot.current_price:.2f}` > VWAP `{vwap:.2f}`, EMA20 > EMA50)",
                )

        return False, ""

    async def handle_telegram_commands(self) -> None:
        cmds = await asyncio.to_thread(self.notifier.poll_commands)
        for cmd in cmds:
            if "/start" in cmd:
                welcome = (
                    "🚀 *PRO v4.6 AI Gold & BTC Snayper Boti (100% Avtomatik Rejim) Ishlamoqda!*\n\n"
                    "Bot hech qanday qo'lda buyruq kutmasdan 24/7 bozorni kuzatadi va faqatgina *bozorda haqiqiy A+ qulay fursat* "
                    "(Makro + Jonli Kapital Oqimi + Liquidity Sweep + Order Block >= 85/100 ball) to'lganda o'zi avtomatik signal yuboradi!\n\n"
                    "📌 *Tekshiruv Buyruqlari:*\n"
                    "• `/stats` — Nechta signal TP1, TP2, TP3, Sof B/U yoki SL bo'lganini ko'rish\n"
                    "• `/status` — Botning jonli holati, oxirgi narxlar va aktiv qulflangan bitimlar\n"
                    "• `/clear` — Eski tarixni va qulflarni `0` ga tozalash"
                )
                await asyncio.to_thread(self.notifier.send_text_update, welcome)
            elif "/clear" in cmd or "/reset" in cmd:
                self.stats_tracker.clear_history()
                self.active_setups.clear()
                self.sl_cooldown_until.clear()
                self._save_state()
                msg = (
                    "🧹 *Statistika, Cooldown va Aktiv Qulflar 100% tozalandi (`0 ta signal`)!*\n"
                    "Endi bozorda keyingi haqiqiy A+ fursat to'lishi bilan `🆔 SIGNAL #001` avtomatik yuboriladi."
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
            elif "/stats" in cmd:
                report = self.stats_tracker.format_full_stats_report()
                await asyncio.to_thread(self.notifier.send_text_update, report)
            elif "/status" in cmd:
                m = self.stats_tracker.compute_metrics()
                locked = (
                    ", ".join(
                        f"#{v.get('signal_id', 0):03d} {k} ({v.get('direction')} {v.get('status')} @ {v.get('entry_price')})"
                        for k, v in self.active_setups.items()
                    )
                    or "Yo'q (Bozorda haqiqiy A+ vaziyat kutilmoqda)"
                )
                status_msg = (
                    f"🤖 *PRO v4.6 AI Snayper Jonli Holati:*\n"
                    f"• *Bozor tekshirildi (1-daqiqalik skanerlash):* `{self.total_cycles} marta`\n"
                    f"• *Jami yuborilgan signallar:* `{m['total_signals']} ta` (Aktiv: `{m['active_count']}` | Yopilgan: `{m['closed_count']}`)\n"
                    f"• *Oxirgi tekshiruv:* `{self.last_check_time}`\n"
                    f"• *Jonli narxlar:* `{json.dumps(self.last_prices)}`\n"
                    f"• *Aktiv qulflangan bitimlar:* `{locked}`\n"
                    f"{self.stats_tracker.get_summary_badge()}"
                )
                await asyncio.to_thread(self.notifier.send_text_update, status_msg)

    async def _monitor_existing_setup(
        self,
        symbol: str,
        current_price: float,
        snapshot: Optional[MarketSnapshot] = None,
        imminent_news: bool = False,
    ) -> bool:
        """
        PRO v4.6 Ikki Tomonlama (`SELL` va `BUY`) Bitim, Intra-Candle Wick Touch va Smart Pending Cancel Kuzatuvchisi.
        """
        setup = self.active_setups.get(symbol)
        if not setup:
            return False

        if not setup.get("signal_sent", False):
            logger.info(f"[{symbol}] Oldingi versiyadan qolgan xabarsiz setup tozalandi — yangi to'liq signal yuboriladi.")
            del self.active_setups[symbol]
            self._save_state()
            self.stats_tracker.purge_unsent_ghost_trades(set(self.active_setups.keys()))
            return False

        now_ts = time.time()
        created_ts = float(setup.get("created_ts", now_ts))
        age_minutes = (now_ts - created_ts) / 60.0
        sig_id = int(setup.get("signal_id", 1))
        direction = setup["direction"]
        entry = float(setup["entry_price"])
        zone_low = float(setup["zone_low"])
        zone_high = float(setup["zone_high"])
        sl = float(setup["sl_price"])
        tp1 = float(setup["recommended_tp"])
        tp2 = float(setup.get("tp2_price", tp1))
        tp3 = float(setup.get("tp3_price", tp2))
        status = setup.get("status", "PENDING")

        # 90 daqiqada ishga tushmagan PENDING orderni bekor qilib, yangi imkoniyat qidiramiz
        if status == "PENDING" and age_minutes >= PENDING_EXPIRY_MINUTES:
            logger.info(f"[SIGNAL #{sig_id:03d} {symbol}] Pending setup {PENDING_EXPIRY_MINUTES} daqiqada ishga tushmadi — yangilanmoqda.")
            self.stats_tracker.record_outcome(symbol, "CANCELLED")
            msg = (
                f"🔄 *SIGNAL #{sig_id:03d} ({symbol}) — MUDDATI TUGADI ({PENDING_EXPIRY_MINUTES} daq):*\n"
                f"Narx `{entry:.2f}` limitga kelmadi. Eski limit order bekor qilinib, joriy bozor strukturasidan yangi A+ nuqta qidirilmoqda."
            )
            await asyncio.to_thread(self.notifier.send_text_update, msg)
            del self.active_setups[symbol]
            self._save_state()
            return False

        if status == "PENDING":
            should_cancel, cancel_reason = self._check_smart_pending_invalidation(setup, snapshot, imminent_news)
            if should_cancel:
                self.stats_tracker.record_outcome(symbol, "CANCELLED")
                msg = (
                    f"⚠️ *SIGNAL #{sig_id:03d} ({symbol}) — XAVFSIZLIK UCHUN BEKOR QILINDI (SMART CANCEL)!*\n"
                    f"• *Sabab:* {cancel_reason}.\n"
                    f"👉 Terminalda `{entry:.2f}` ga qo'yilgan `{direction} LIMIT` orderni o'chirib tashlang!"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                del self.active_setups[symbol]
                self._save_state()
                return False

        # Intra-candle High/Low tekshiruvi (1 daqiqa ichida shamcha dumi Entry ga tegib qaytgan bo'lsa ham ushlaydi!)
        live_high = current_price
        live_low = current_price
        if snapshot is not None and not snapshot.ltf_df.empty and age_minutes >= 1.0:
            last_row = snapshot.ltf_df.iloc[-1]
            live_high = max(current_price, float(last_row["high"]))
            live_low = min(current_price, float(last_row["low"]))

        is_sell = direction == "SELL"
        triggered_cond = (live_high >= entry) if is_sell else (live_low <= entry)

        # Agar narx Entry ga tegmasdan TP1 yo'lining 65% qismini bosib o'tib ketsa, eski limitni bekor qilib yangi setup qidiradi
        early_miss_level = round(entry - 0.65 * (entry - tp1), 2) if is_sell else round(entry + 0.65 * (tp1 - entry), 2)
        missed_cond = (current_price <= early_miss_level) if is_sell else (current_price >= early_miss_level)

        sl_hit_cond = (live_high >= sl) if is_sell else (live_low <= sl)
        tp3_hit_cond = (live_low <= tp3) if is_sell else (live_high >= tp3)
        tp2_hit_cond = (live_low <= tp2) if is_sell else (live_high >= tp2)
        tp1_hit_cond = (live_low <= tp1) if is_sell else (live_high >= tp1)
        halfway = (entry - 0.5 * (entry - tp1)) if is_sell else (entry + 0.5 * (tp1 - entry))
        bu_hit_cond = (live_low <= halfway) if is_sell else (live_high >= halfway)

        # 1) PENDING -> TRIGGERED
        if status == "PENDING" and triggered_cond:
            setup["status"] = "TRIGGERED"
            self._save_state()
            self.stats_tracker.record_triggered(symbol)
            msg = (
                f"⚡ *SIGNAL #{sig_id:03d} ({symbol}) — {direction} LIMIT ISHGA TUSHDI!*\n"
                f"📍 *Joriy Narx:* `{current_price:.2f}` (Entry: `{entry:.2f}` | Zona: `{zone_low:.2f}–{zone_high:.2f}`)\n"
                f"🛑 *Stop-Loss:* `{sl:.2f}`\n"
                f"🎯 *Maqsadlar:* TP1 `{tp1:.2f}` | TP2 `{tp2:.2f}` | TP3 `{tp3:.2f}`"
            )
            await asyncio.to_thread(self.notifier.send_text_update, msg)
            return True

        if status == "PENDING" and missed_cond:
            self.stats_tracker.record_outcome(symbol, "CANCELLED")
            msg = (
                f"⚪ *SIGNAL #{sig_id:03d} ({symbol}) — LIMITGA TEGMASDAN KETDI:*\n"
                f"Narx (`{current_price:.2f}`) kirish nuqtasiga (`{entry:.2f}`) qaytmasdan maqsad tomon (`{tp1:.2f}`) ketib qoldi. "
                f"Eski Limit order bekor qilindi — yangi struktura qidirilmoqda!"
            )
            await asyncio.to_thread(self.notifier.send_text_update, msg)
            del self.active_setups[symbol]
            self._save_state()
            return False

        # 2) AKTIV BITIMNI KUZATISH
        if status in ("TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT"):
            if sl_hit_cond:
                if status == "TP2_HIT":
                    _, pnl = self.stats_tracker.record_outcome(symbol, "TP2_REACHED")
                    msg = (
                        f"🚀 *SIGNAL #{sig_id:03d} ({symbol}) YAKUNLANDI — TP1 VA TP2 OLINDI (+${pnl:.2f}), QOLGAN 20% B/U DA YOPILDI!*\n"
                        f"• Ushbu signal statistikada *🚀 TP2 gacha bordi* deb qayd etildi.\n"
                        f"{self.stats_tracker.get_summary_badge()}"
                    )
                elif status == "TP1_HIT":
                    _, pnl = self.stats_tracker.record_outcome(symbol, "TP1_ONLY")
                    msg = (
                        f"✅ *SIGNAL #{sig_id:03d} ({symbol}) YAKUNLANDI — FAQAT TP1 OLINDI (+${pnl:.2f}), QOLGAN 50% B/U DA YOPILDI!*\n"
                        f"• Ushbu signal statistikada *✅ Faqat TP1 gacha bordi* deb qayd etildi.\n"
                        f"{self.stats_tracker.get_summary_badge()}"
                    )
                elif status == "BREAKEVEN":
                    _, pnl = self.stats_tracker.record_outcome(symbol, "PURE_BU")
                    msg = (
                        f"🛡️ *SIGNAL #{sig_id:03d} ({symbol}) — SOF BEZUBITOKDA (B/U = $0.00) YOPILDI!*\n"
                        f"• Narx TP1 ga yetmasdan 50% yo'ldan orqaga qaytib, kirish nuqtangizda (`{entry:.2f}`) 0% zarar bilan yopildi.\n"
                        f"{self.stats_tracker.get_summary_badge()}"
                    )
                else:
                    _, pnl = self.stats_tracker.record_outcome(symbol, "SL_HIT")
                    self.sl_cooldown_until[symbol] = time.time() + SL_COOLDOWN_SECONDS
                    msg = (
                        f"❌ *SIGNAL #{sig_id:03d} ({symbol}) — STOP-LOSS ({sl:.2f}) URILDI (${pnl:+.2f}).*\n"
                        f"⏸️ *Anti-Revenge Himoyasi:* `{symbol}` da 45 daqiqa tanaffus e'lon qilindi!\n"
                        f"{self.stats_tracker.get_summary_badge()}"
                    )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                del self.active_setups[symbol]
                self._save_state()
                return False

            if tp3_hit_cond:
                _, pnl = self.stats_tracker.record_outcome(symbol, "TP3_FULL")
                msg = (
                    f"👑 *SIGNAL #{sig_id:03d} ({symbol}) — BARCHA 3 TA TP (TP1 + TP2 + TP3 @ {tp3:.2f}) TO'LIQ URILDI! (+${pnl:.2f})* 🔥💰\n"
                    f"• Ushbu signal statistikada *👑 TP3 FULL (3/3)* deb muhrlandi!\n"
                    f"{self.stats_tracker.get_summary_badge()}"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                del self.active_setups[symbol]
                self._save_state()
                return False

            if status != "TP2_HIT" and tp2_hit_cond:
                setup["status"] = "TP2_HIT"
                setup["sl_price"] = entry
                self._save_state()
                _, locked_pnl = self.stats_tracker.record_stage_progress(symbol, "TP2_HIT", max_tp_stage=2)
                msg = (
                    f"🚀 *SIGNAL #{sig_id:03d} ({symbol}) — TP2 ({tp2:.2f}) URILDI! (+${locked_pnl:.2f} naqd qilindi)* 💰\n"
                    f"👉 Pozitsiyaning yana *30%* qismini yoping (jami 80% yopildi) va qolgan *20%* qismini B/U (`{entry:.2f}`) da TP3 (`{tp3:.2f}`) gacha ushlang!"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                return True

            if status not in ("TP1_HIT", "TP2_HIT") and tp1_hit_cond:
                setup["status"] = "TP1_HIT"
                setup["sl_price"] = entry
                self._save_state()
                _, locked_pnl = self.stats_tracker.record_stage_progress(symbol, "TP1_HIT", max_tp_stage=1)
                msg = (
                    f"✅ *SIGNAL #{sig_id:03d} ({symbol}) — GARANT TP1 ({tp1:.2f}) URILDI! (+${locked_pnl:.2f} naqd qilindi)* 💰\n"
                    f"👉 Pozitsiyaning *50%* qismini hozir foydada yoping, SL ni `{entry:.2f}` (B/U) ga qo'yib, qolganini TP2 (`{tp2:.2f}`) va TP3 (`{tp3:.2f}`) gacha ushlang!"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                return True

            if status == "TRIGGERED" and bu_hit_cond:
                setup["status"] = "BREAKEVEN"
                setup["sl_price"] = entry
                self._save_state()
                self.stats_tracker.record_stage_progress(symbol, "BREAKEVEN", max_tp_stage=0)
                msg = (
                    f"🛡️ *SIGNAL #{sig_id:03d} ({symbol}) — BEZUBITOK (B/U) VAQTI!*\n"
                    f"Narx TP1 yo'lining yarmini (`{current_price:.2f}`) bosib o'tdi. Stop-Loss'ni `{sl:.2f}` dan "
                    f"kirish nuqtangizga (`{entry:.2f}`) ko'chiring (100% risksiz rejim)!"
                )
                await asyncio.to_thread(self.notifier.send_text_update, msg)
                return True

        logger.info(
            f"[SIGNAL #{sig_id:03d} {symbol}] 🔒 Aktiv ({direction} {status}): Entry={entry:.2f} | SL={sl:.2f} | TP1={tp1:.2f} | TP2={tp2:.2f} | TP3={tp3:.2f} | Joriy={current_price:.2f}"
        )
        sys.stdout.flush()
        return True

    async def analyze_symbol(
        self,
        symbol: str,
        imminent_news: bool,
    ) -> None:
        if "XAU" in symbol.upper() and not self.is_forex_market_open():
            logger.info(
                f"[Sikl #{self.total_cycles} | XAUUSD] ⏸️ Dam olish kuni — Oltin (Forex) bozori yopiq. Dushanba 03:00 UZT da avtomatik davom etadi."
            )
            sys.stdout.flush()
            return

        snapshot = await asyncio.to_thread(self.collector.get_snapshot, symbol)
        cp = round(snapshot.current_price, 2)
        self.last_prices[symbol] = cp

        is_locked = await self._monitor_existing_setup(symbol, cp, snapshot=snapshot, imminent_news=imminent_news)
        if is_locked:
            return

        cooldown_exp = self.sl_cooldown_until.get(symbol, 0.0)
        if time.time() < cooldown_exp:
            rem_min = int((cooldown_exp - time.time()) // 60) + 1
            logger.info(
                f"[Sikl #{self.total_cycles} | {symbol}] ⏸️ SL Cooldown rejimida ({rem_min} daqiqa qoldi). Bozor strukturasi barqarorlashishi kutilmoqda..."
            )
            sys.stdout.flush()
            return

        headlines = await asyncio.to_thread(self.scraper.fetch_headlines, 8, symbol)
        fundamental_sig = await asyncio.to_thread(
            self.fundamental_agent.analyze, snapshot, headlines, imminent_news
        )
        tech_setup = await asyncio.to_thread(self.technical_agent.find_setup, snapshot, fundamental_sig)
        final_signal = self.risk_manager.evaluate_trade(fundamental_sig, tech_setup)

        if self.sniper_only and (not tech_setup.is_ideal_setup or not final_signal.is_approved):
            reason_str = final_signal.rejection_reason or "Ideal A+ Setup kutilmoqda"
            logger.info(
                f"[Sikl #{self.total_cycles} | {symbol}] Kuzatuvda: Narx={cp:.2f} | Bias={fundamental_sig.bias} ({fundamental_sig.confidence_score:+.2f}) | "
                f"Score={tech_setup.confluence_score}/100. ({reason_str})"
            )
            sys.stdout.flush()
            return

        sig_id = self.stats_tracker.next_signal_id()
        final_signal.signal_id = sig_id
        stats_badge = self.stats_tracker.get_summary_badge()

        print("\n" + self.notifier.format_signal_message(final_signal, stats_badge=stats_badge) + "\n", flush=True)
        if final_signal.is_approved:
            sent_ok = await asyncio.to_thread(
                self.notifier.send_signal, final_signal, stats_badge
            )
            # MUHIM PRO v4.6 QOIDASI: Faqat xabar Telegramga haqiqatan yuborilgandagina (`sent_ok == True`)
            # bazaga va `active_setups` ga yoziladi! Soxta "Aktiv: 2" hech qachon paydo bo'lmaydi!
            if sent_ok:
                rec_id = self.stats_tracker.record_new_signal(
                    symbol=symbol,
                    direction=tech_setup.direction,
                    order_type=tech_setup.order_type,
                    entry_price=tech_setup.entry_price,
                    sl_price=tech_setup.sl_price,
                    tp1_price=tech_setup.recommended_tp,
                    tp2_price=tech_setup.tp_price,
                    tp3_price=tech_setup.tp3_price,
                    risk_usd=final_signal.risk_amount_usd,
                )
                final_signal.signal_id = rec_id
                initial_status = "TRIGGERED" if "MARKET" in tech_setup.order_type else "PENDING"
                self.active_setups[symbol] = {
                    "signal_id": rec_id,
                    "symbol": symbol,
                    "direction": tech_setup.direction,
                    "order_type": tech_setup.order_type,
                    "entry_price": tech_setup.entry_price,
                    "zone_low": tech_setup.entry_zone_low,
                    "zone_high": tech_setup.entry_zone_high,
                    "sl_price": tech_setup.sl_price,
                    "recommended_tp": tech_setup.recommended_tp,
                    "tp2_price": tech_setup.tp_price,
                    "tp3_price": tech_setup.tp3_price,
                    "status": initial_status,
                    "signal_sent": True,
                    "created_ts": time.time(),
                }
                self._save_state()

    @staticmethod
    def _ping_self_keepalive() -> None:
        ext_url = os.getenv("RENDER_EXTERNAL_URL", "https://newversion-fope.onrender.com")
        port = os.getenv("PORT", "8080")
        for target in (f"{ext_url.rstrip('/')}/health", f"http://127.0.0.1:{port}/health"):
            try:
                req = urllib.request.Request(target, headers={"User-Agent": "PRO-v4.6-KeepAlive/1.0"})
                with urllib.request.urlopen(req, timeout=4) as _:
                    return
            except Exception:
                continue

    async def _handle_http_ping(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.read(1024), timeout=3.0)
            status_payload = {
                "status": "alive",
                "version": "PRO v4.6 Institutional Text Sniper (100% Autonomous + Ghost-Lock Clean)",
                "forex_market_open": self.is_forex_market_open(),
                "market_scan_cycles": self.total_cycles,
                "last_check_utc": self.last_check_time,
                "last_prices": self.last_prices,
                "stats_summary": self.stats_tracker.get_summary_badge(),
                "active_locked_setups": self.active_setups,
            }
            body = json.dumps(status_payload, ensure_ascii=False).encode("utf-8")
            headers = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("utf-8")
            writer.write(headers + body)
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def start_keepalive_server(self) -> asyncio.AbstractServer:
        port = int(os.getenv("PORT", "8080"))
        server = await asyncio.start_server(self._handle_http_ping, host="0.0.0.0", port=port)
        logger.info(
            f"🌐 UptimeRobot / Render Keep-Alive Web Server ishga tushdi: http://0.0.0.0:{port}/health"
        )
        sys.stdout.flush()
        return server

    def _get_target_symbols(self) -> list[str]:
        if self.gold_only:
            return [SYMBOLS["GOLD"]]
        return [SYMBOLS["GOLD"], SYMBOLS["BTC"]]

    async def run_once(self) -> None:
        imminent_news, _ = await asyncio.to_thread(self.scraper.check_imminent_high_impact_news, 30)
        self.total_cycles += 1
        self.last_check_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        await self.handle_telegram_commands()
        for sym in self._get_target_symbols():
            await self.analyze_symbol(sym, imminent_news)

    async def run_forever(self) -> None:
        server = await self.start_keepalive_server()
        await asyncio.to_thread(self.notifier.init_offset)
        logger.info(
            "🎯 PRO v4.6 Institutional Snayper Boti (100% Avtomatik Rejim + 4096-belgi Himoyasi + Ghost-Lock Tozalagich) ishga tushdi!"
        )
        sys.stdout.flush()
        async with server:
            while True:
                cycle_start = time.time()
                try:
                    imminent_news, _ = await asyncio.to_thread(self.scraper.check_imminent_high_impact_news, 30)
                    self.total_cycles += 1
                    self.last_check_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    for sym in self._get_target_symbols():
                        await self.analyze_symbol(sym, imminent_news)
                    await asyncio.to_thread(self._ping_self_keepalive)
                except Exception as e:
                    logger.error(f"Sikl ichida xatolik (bot to'xtamaydi): {e}")
                    sys.stdout.flush()

                while (time.time() - cycle_start) < NEWS_CHECK_INTERVAL_SECONDS:
                    try:
                        await self.handle_telegram_commands()
                    except Exception:
                        pass
                    await asyncio.sleep(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PRO v4.6 Institutional AI Trading Bot")
    parser.add_argument("--once", action="store_true", help="Tahlilni bir marta ishga tushirish")
    parser.add_argument("--mt5", action="store_true", help="MetaTrader 5 terminaliga ulanish (ixtiyoriy)")
    parser.add_argument("--killzone-only", action="store_true", help="Faqat London va NY Killzone vaqtida ruxsat berish")
    parser.add_argument("--all-setups", action="store_true", help="85 balldan past standart setuplarni ham ko'rsatish")
    parser.add_argument("--gold-only", action="store_true", help="Faqat XAUUSD (Oltin) uchun signal yuborish")
    parser.add_argument("--reset-lock", action="store_true", help="Saqlangan aktiv setup qulfini tozalash")
    parser.add_argument("--clear-stats", action="store_true", help="Barcha eski test signallari tarixini 0 ga tozalash")
    parser.add_argument("--stats", action="store_true", help="To'liq TP1/TP2/TP3/B-U/SL va Signal #ID statistikasini ko'rsatish")
    args = parser.parse_args()

    if args.reset_lock and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

    orchestrator = MultiAgentTradingOrchestrator(
        use_mt5=args.mt5,
        enforce_killzone=args.killzone_only,
        sniper_only=not args.all_setups,
        gold_only=args.gold_only,
    )
    if args.clear_stats:
        orchestrator.stats_tracker.clear_history()
        print("🧹 Statistika tarixi 0 ga tozalandi!", flush=True)
    if args.stats:
        print(orchestrator.stats_tracker.format_full_stats_report(), flush=True)
    elif args.once:
        asyncio.run(orchestrator.run_once())
    else:
        asyncio.run(orchestrator.run_forever())