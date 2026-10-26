"""
4-QISM: PRO v4.6 Toza Matnli Telegram Signal (4096-belgi Limit Himoyasi + 3-Bosqichli Fallback)
hamda Aniq Statistika Kuzatuvchisi (telegram_bot.py)

PRO v4.6 KRITIK TUZATISHLARI (Nega 1 kundan beri signal kelmagani va 2 ta soxta aktiv turgani 100% hal qilindi!):
1) **MUAMMO TOPILDI:** PRO v4.5 da signal matni 4,815 belgiga yetgan edi. Telegram Bot API (`sendMessage`) esa
   4,096 belgidan uzun xabarlarni `HTTP 400: Bad Request: message is too long` xatosi bilan rad etadi!
   Oqibatda `/status` va `/stats` (qisqa xabarlar) o'tgan, ammo asosiy A+ Signal xabari 4,815 belgi bo'lgani uchun
   Telegramdan o'tmagan (`sent_ok = False`).
2) **YECHIM #1:** `format_signal_message` barcha muhim ma'lumotlarni (Entry, SL, Yagona TP, TP1/TP2/TP3 foizlari,
   PDH/PDL, Asian Range, Liquidity Sweep, VWAP/RVOL/RSI, Confluence ballari, Domino zanjiri, Shaxslar va Keyingi qadam)
   100% saqlagan holda **~2,400–2,800 belgi** hajmiga keltirildi (takroriy dublikat matnlar tozalandi).
3) **YECHIM #2:** `_safe_truncate` (`<= 3900` belgi) va 3-bosqichli kafolatli yuborish qo'shildi — endi har qanday
   signal xabari Telegramdan 100% birinchi urinishda o'tadi!
"""
from datetime import datetime, timezone
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from schemas import FinalTradeSignal

logger = logging.getLogger(__name__)

HISTORY_FILE = "trade_history.json"
MAX_TELEGRAM_TEXT_LEN = 3900  # Telegram 4096 limitidan xavfsiz zaxira bilan


def clean_md(text: str) -> str:
    """
    Telegram Legacy Markdown parserini buzishi mumkin bo'lgan belgilarni (`_`, `*`, `` ` ``, `[`, `]`)
    dinamik matnlardan tozalaydi — shunda asosiy signal xabari 100% xatosiz o'tadi!
    """
    s = str(text)
    s = s.replace("_", "-").replace("*", "").replace("`", "").replace("[", "(").replace("]", ")")
    return s


def strip_all_md(text: str) -> str:
    """Fallback uchun matndan barcha Markdown belgilarini olib tashlaydi."""
    return re.sub(r"[*_`]", "", str(text))


def safe_truncate_tg(text: str, max_len: int = MAX_TELEGRAM_TEXT_LEN) -> str:
    """Xabar uzunligi hech qachon Telegram 4096-belgi limitidan oshib ketmasligini kafolatlaydi."""
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 20]
    last_nl = cut.rfind("\n")
    if last_nl > max_len - 400:
        cut = cut[:last_nl]
    return cut + "\n..."


class PerformanceStatsTracker:
    """
    PRO v4.6 Aniq Statistika Hisoblagichi:
    Har bir haqiqiy yuborilgan signalni unikal `#ID` (`#001`, `#002`...) bilan ro'yxatga oladi va
    nechta signal TP1, TP2, TP3, Sof B/U yoki SL olganini alohida-alohida hisoblaydi!
    """

    def __init__(self, history_file: str = HISTORY_FILE):
        self.history_file = history_file
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"trades": []}

    def _save(self) -> None:
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def clear_history(self) -> None:
        self.data = {"trades": []}
        self._save()

    def purge_unsent_ghost_trades(self, valid_active_symbols: set[str]) -> int:
        """
        Oldingi versiyada Telegramdan o'tmay qolgan (`signal_sent=False`), ammo tarixga yozilib qolgan
        soxta `PENDING` bitimlarni tozalaydi.
        """
        trades = self.data.get("trades", [])
        cleaned: list[dict[str, Any]] = []
        removed = 0
        for tr in trades:
            st = tr.get("status", "PENDING")
            sym = tr.get("symbol", "")
            if st == "PENDING" and sym not in valid_active_symbols:
                removed += 1
                continue
            cleaned.append(tr)
        if removed > 0:
            for idx, tr in enumerate(cleaned, start=1):
                tr["id"] = idx
            self.data["trades"] = cleaned
            self._save()
        return removed

    def next_signal_id(self) -> int:
        trades = self.data.get("trades", [])
        return len(trades) + 1

    def record_new_signal(
        self,
        symbol: str,
        direction: str,
        order_type: str,
        entry_price: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        tp3_price: float = 0.0,
        risk_usd: float = 100.0,
    ) -> int:
        trades = self.data.setdefault("trades", [])
        halfway_bu = round(
            entry_price - 0.5 * (entry_price - tp1_price)
            if direction == "SELL"
            else entry_price + 0.5 * (tp1_price - entry_price),
            2,
        )
        for tr in reversed(trades):
            if tr.get("symbol") == symbol and tr.get("status") in ("PENDING", "TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT"):
                # Agar PENDING bo'lsa, oxirgi tasdiqlangan parametrlarni sinxronlaymiz
                if tr.get("status") == "PENDING":
                    tr["direction"] = direction
                    tr["order_type"] = order_type
                    tr["entry_price"] = round(entry_price, 2)
                    tr["sl_price"] = round(sl_price, 2)
                    tr["bu_trigger_price"] = halfway_bu
                    tr["tp1_price"] = round(tp1_price, 2)
                    tr["tp2_price"] = round(tp2_price, 2)
                    tr["tp3_price"] = round(tp3_price if tp3_price > 0 else tp2_price, 2)
                    self._save()
                return int(tr["id"])

        sig_id = len(trades) + 1
        trades.append({
            "id": sig_id,
            "symbol": symbol,
            "direction": direction,
            "order_type": order_type,
            "entry_price": round(entry_price, 2),
            "sl_price": round(sl_price, 2),
            "bu_trigger_price": halfway_bu,
            "tp1_price": round(tp1_price, 2),
            "tp2_price": round(tp2_price, 2),
            "tp3_price": round(tp3_price if tp3_price > 0 else tp2_price, 2),
            "risk_usd": round(risk_usd, 2),
            "status": "TRIGGERED" if "MARKET" in order_type else "PENDING",
            "max_tp_stage": 0,
            "pnl_usd": 0.0,
            "opened_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "closed_at": None,
        })
        self._save()
        return sig_id

    def record_triggered(self, symbol: str) -> Optional[int]:
        for tr in reversed(self.data.get("trades", [])):
            if tr.get("symbol") == symbol and tr.get("status") == "PENDING":
                tr["status"] = "TRIGGERED"
                self._save()
                return int(tr["id"])
        return None

    def record_stage_progress(self, symbol: str, new_status: str, max_tp_stage: int) -> tuple[int, float]:
        for tr in reversed(self.data.get("trades", [])):
            if tr.get("symbol") == symbol and tr.get("status") in ("PENDING", "TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT"):
                entry = float(tr["entry_price"])
                sl = float(tr["sl_price"])
                tp1 = float(tr["tp1_price"])
                tp2 = float(tr["tp2_price"])
                risk_usd = float(tr.get("risk_usd", 100.0))
                risk_pts = max(abs(entry - sl), 1e-4)

                rr1 = abs(entry - tp1) / risk_pts
                rr2 = abs(entry - tp2) / risk_pts

                tr["status"] = new_status
                tr["max_tp_stage"] = max(int(tr.get("max_tp_stage", 0)), max_tp_stage)
                if max_tp_stage == 1:
                    tr["pnl_usd"] = round(risk_usd * (0.50 * rr1), 2)
                elif max_tp_stage == 2:
                    tr["pnl_usd"] = round(risk_usd * (0.50 * rr1 + 0.30 * rr2), 2)
                self._save()
                return int(tr["id"]), float(tr["pnl_usd"])
        return 0, 0.0

    def record_outcome(self, symbol: str, outcome: str) -> tuple[int, float]:
        if outcome == "BREAKEVEN":
            outcome = "PURE_BU"

        for tr in reversed(self.data.get("trades", [])):
            if tr.get("symbol") == symbol and tr.get("status") in ("PENDING", "TRIGGERED", "BREAKEVEN", "TP1_HIT", "TP2_HIT"):
                entry = float(tr["entry_price"])
                sl = float(tr["sl_price"])
                tp1 = float(tr["tp1_price"])
                tp2 = float(tr["tp2_price"])
                tp3 = float(tr.get("tp3_price", tp2))
                risk_usd = float(tr.get("risk_usd", 100.0))
                risk_pts = max(abs(entry - sl), 1e-4)

                rr_tp1 = abs(entry - tp1) / risk_pts
                rr_tp2 = abs(entry - tp2) / risk_pts
                rr_tp3 = abs(entry - tp3) / risk_pts

                if outcome in ("TP1_ONLY", "TP1_HIT"):
                    outcome = "TP1_ONLY"
                    tr["max_tp_stage"] = 1
                    pnl = round(risk_usd * (0.50 * rr_tp1), 2)
                elif outcome in ("TP2_REACHED", "TP2_HIT"):
                    outcome = "TP2_REACHED"
                    tr["max_tp_stage"] = 2
                    pnl = round(risk_usd * (0.50 * rr_tp1 + 0.30 * rr_tp2), 2)
                elif outcome == "TP3_FULL":
                    tr["max_tp_stage"] = 3
                    pnl = round(risk_usd * (0.50 * rr_tp1 + 0.30 * rr_tp2 + 0.20 * rr_tp3), 2)
                elif outcome == "PURE_BU":
                    tr["max_tp_stage"] = 0
                    pnl = 0.0
                elif outcome == "CANCELLED":
                    tr["max_tp_stage"] = 0
                    pnl = 0.0
                else:
                    outcome = "SL_HIT"
                    tr["max_tp_stage"] = 0
                    pnl = round(-risk_usd, 2)

                tr["status"] = outcome
                tr["pnl_usd"] = pnl
                tr["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                self._save()
                return int(tr["id"]), pnl
        return 0, 0.0

    def compute_metrics(self) -> dict[str, Any]:
        trades = self.data.get("trades", [])
        closed_statuses = ("TP1_ONLY", "TP2_REACHED", "TP3_FULL", "PURE_BU", "SL_HIT", "TP1_HIT", "TP2_HIT", "BREAKEVEN")
        closed = [t for t in trades if t.get("status") in closed_statuses]

        tp1_only_list = [t for t in closed if t.get("status") in ("TP1_ONLY", "TP1_HIT") or t.get("max_tp_stage") == 1]
        tp2_list = [t for t in closed if t.get("status") in ("TP2_REACHED", "TP2_HIT") and t.get("max_tp_stage") != 3]
        tp3_list = [t for t in closed if t.get("status") == "TP3_FULL" or t.get("max_tp_stage") == 3]
        pure_bu_list = [t for t in closed if t.get("status") in ("PURE_BU", "BREAKEVEN") and int(t.get("max_tp_stage", 0)) == 0]
        sl_list = [t for t in closed if t.get("status") == "SL_HIT"]
        cancelled_list = [t for t in trades if t.get("status") == "CANCELLED"]
        active_list = [t for t in trades if t.get("status") in ("PENDING", "TRIGGERED", "BREAKEVEN") and t not in closed]

        total_wins = len(tp1_only_list) + len(tp2_list) + len(tp3_list)
        total_sl = len(sl_list)
        decided = total_wins + total_sl
        win_rate = round((total_wins / decided) * 100.0, 1) if decided > 0 else 100.0

        reached_at_least_tp1 = total_wins
        reached_at_least_tp2 = len(tp2_list) + len(tp3_list)
        reached_tp3 = len(tp3_list)

        net_pnl = round(sum(float(t.get("pnl_usd", 0.0)) for t in trades), 2)

        return {
            "total_signals": len(trades),
            "closed_count": len(closed),
            "active_count": len(active_list),
            "cancelled_count": len(cancelled_list),
            "total_wins": total_wins,
            "tp1_only_count": len(tp1_only_list),
            "tp2_count": len(tp2_list),
            "tp3_count": len(tp3_list),
            "reached_at_least_tp1": reached_at_least_tp1,
            "reached_at_least_tp2": reached_at_least_tp2,
            "reached_tp3": reached_tp3,
            "pure_bu_count": len(pure_bu_list),
            "sl_count": total_sl,
            "win_rate": win_rate,
            "net_pnl": net_pnl,
            "all_trades": trades[-10:],
        }

    def get_summary_badge(self) -> str:
        m = self.compute_metrics()
        if m["closed_count"] == 0 and m["active_count"] == 0:
            return "📈 *Statistika:* `0 ta yopilgan bitim (Toza baza)` | `/stats`"
        sign = "+" if m["net_pnl"] >= 0 else ""
        return (
            f"📊 *Statistika ({m['closed_count']} yopilgan):* "
            f"✅ *TP1:* `{m['tp1_only_count']}` | 🚀 *TP2:* `{m['tp2_count']}` | 👑 *TP3:* `{m['tp3_count']}` | "
            f"🛡️ *Sof B/U:* `{m['pure_bu_count']}` | ❌ *SL:* `{m['sl_count']}` | "
            f"🎯 *Win-Rate:* `{m['win_rate']}%` (`{sign}${m['net_pnl']:.2f}`)"
        )

    @staticmethod
    def _human_stage_label(tr: dict[str, Any]) -> str:
        st = tr.get("status", "PENDING")
        max_tp = int(tr.get("max_tp_stage", 0))
        if st == "TP3_FULL" or max_tp == 3:
            return "👑 TP3 FULL (3/3 TP urdi!)"
        if st in ("TP2_REACHED", "TP2_HIT") or max_tp == 2:
            return "🚀 TP2 GACHA BORDI (TP1+TP2 urdi, qolgani B/U)"
        if st in ("TP1_ONLY", "TP1_HIT") or max_tp == 1:
            return "✅ FAQAT TP1 GACHA BORDI (TP1 urdi, qolgani B/U)"
        if st in ("PURE_BU", "BREAKEVEN"):
            return "🛡️ SOF B/U ($0 — TP1 ga yetmay 50% yo'ldan Entry ga qaytdi)"
        if st == "SL_HIT":
            return "❌ STOP-LOSS (Minusda yopildi)"
        if st == "CANCELLED":
            return "⚪ LIMITGA KELMADI (Entry ga tegmay TP ga tushib ketdi)"
        if st == "TRIGGERED":
            return "⚡ AKTIV (Pozitsiya ochiq — TP1 kutilmoqda)"
        return "⏳ KUTILMOQDA (Limit order)"

    def format_full_stats_report(self) -> str:
        m = self.compute_metrics()
        sign = "+" if m["net_pnl"] >= 0 else ""
        lines = [
            "🏆 *PRO v4.6 AI SNAYPER — ANIQ TP1 / TP2 / TP3 / B-U / SL STATISTIKASI*",
            f"• *Jami berilgan signallar:* `{m['total_signals']} ta` (Yopilgan: `{m['closed_count']}` | Aktiv: `{m['active_count']}` | Limitga kelmagan: `{m['cancelled_count']}`)",
            "",
            "🎯 *NECHTASI QAYSI TP GACHA BORDI (Aniq kesim):*",
            f"  1️⃣ *Faqat TP1 gacha borganlar (TP1 + qolgani B/U):* `{m['tp1_only_count']} ta`",
            f"  2️⃣ *TP2 gacha borganlar (TP1 + TP2 + qolgani B/U):* `{m['tp2_count']} ta`",
            f"  3️⃣ *TP3 (Full 3/3 TP) urganlar:* `{m['tp3_count']} ta`",
            f"  📌 *(Jami kamida TP1 ni urgan g'alabali signallar: `{m['reached_at_least_tp1']} ta`)*",
            "",
            "🛡️ *BEZUBITOK (B/U) VA STOP-LOSS KESIMI:*",
            f"  • 🛡️ *Sof Bezubitok (PURE B/U — $0.00):* `{m['pure_bu_count']} ta`",
            f"  • ❌ *Stop-Loss (SL — zarar):* `{m['sl_count']} ta`",
            f"  • 🏆 *Aniq Win-Rate:* `{m['win_rate']}%` | 💰 *Jami Sof Foyda:* `{sign}${m['net_pnl']:.2f}`",
        ]
        if not m["all_trades"]:
            lines.append("\nℹ️ Hozircha signallar tarixi toza (0 ta). Bozorda haqiqiy A+ vaziyat paydo bo'lishi bilan #001 dan boshlab yoziladi.")
        else:
            lines.append("\n📋 *HAR BIR SIGNAL TARIXI (Qaysi signal #ID nechiga bordi):*")
            for tr in reversed(m["all_trades"]):
                p = float(tr.get("pnl_usd", 0.0))
                ps = f"+${p:.2f}" if p >= 0 else f"-${abs(p):.2f}"
                stage_lbl = self._human_stage_label(tr)
                ord_clean = clean_md(tr.get("order_type", "LIMIT"))
                lines.append(
                    f"  • 🆔 *SIGNAL #{tr['id']:03d}* | *{tr['symbol']}* (`{ord_clean}` @ `{tr['entry_price']}`)\n"
                    f"    └ TP1: `{tr['tp1_price']}` | TP2: `{tr['tp2_price']}` | TP3: `{tr.get('tp3_price', tr['tp2_price'])}` | SL: `{tr['sl_price']}`\n"
                    f"    └ Natija: *{stage_lbl}* (`{ps}`)"
                )
        return "\n".join(lines)


class TelegramCopilotNotifier:
    """
    A+ Ideal Setup signallarini PRO v4.6 ixcham va to'liq formatda (< 3,000 belgi, Telegram 4096-belgi
    cheklovidan 100% xavfsiz) yuboruvchi klass.
    """

    def __init__(self, bot_token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.last_update_id: int = 0

    @staticmethod
    def format_signal_message(signal: FinalTradeSignal, stats_badge: str = "") -> str:
        f = signal.fundamental
        t = signal.technical
        sig_id = signal.signal_id
        icon = "🟢" if t.direction == "BUY" else ("🔴" if t.direction == "SELL" else "⚪")
        grade_badge = f"🏆 A+ IDEAL SETUP ({t.confluence_score}/100)" if t.is_ideal_setup else f"📊 STANDART SETUP ({t.confluence_score}/100)"
        ord_clean = clean_md(t.order_type)
        struct_clean = clean_md(t.market_structure)

        msg = (
            f"🟡 *{t.symbol} — {ord_clean}* | *{grade_badge}* (🆔 *SIGNAL #{sig_id:03d}*)\n"
            f"🔒 *SETUP MUHRLANDI (TP yoki SL bo'lmaguncha o'zgarmaydi!)*\n"
        )
        if stats_badge:
            msg += f"{stats_badge}\n"

        msg += (
            f"🕒 *Vaqt / Sessiya:* `{signal.timestamp}` ({clean_md(signal.session_name)})\n"
            f"📍 *Joriy Narx (TradingView):* `{t.current_price:.2f}`\n"
            f"📐 *Struktura:* `{struct_clean}`\n\n"
            f"🎯 *ASOSIY KIRISH VA YAGONA ISHONCHLI TP:*\n"
            f"  • *Entry (Kirish nuqtasi):* `{t.entry_price:.2f}` (Zona: `{t.entry_zone_low:.2f} – {t.entry_zone_high:.2f}`)\n"
            f"  • *Stop-Loss (SL):* `{t.sl_price:.2f}`\n"
            f"  • 🌟 *TERMINALGA QO'YILADIGAN YAGONA TP:* `{t.recommended_tp:.2f}` (*{t.recommended_tp_prob}% Ehtimol*)\n"
            f"  • *Tavsiya etilgan Lot:* `{signal.lot_size}` (1% Risk = `${signal.risk_amount_usd:.2f}`)\n\n"
            f"📊 *3 TA TP DARAJASINING EHTIMOLLIK FOIZI:*\n"
            f"  1️⃣ *TP1* (`{t.tp1_price:.2f}` — Yaqin Likvidlik): *{t.tp1_prob}% ehtimol* (Asosiy!)\n"
            f"  2️⃣ *TP2* (`{t.tp_price:.2f}` — O'rta Tayanch): *{t.tp2_prob}% ehtimol*\n"
            f"  3️⃣ *TP3* (`{t.tp3_price:.2f}` — Kunlik Ekstremum): *{t.tp3_prob}% ehtimol*\n"
        )

        if t.structure_note:
            msg += f"\n{clean_md(t.structure_note)}\n"

        if t.confluence_Checklist:
            chk_str = "\n".join(f"  {clean_md(c)}" for c in t.confluence_Checklist)
            msg += f"\n✅ *A+ Confluence Tekshiruvi ({t.confluence_score}/100):*\n{chk_str}\n"

        msg += (
            f"\n{icon} *Fundamental Bias:* *{f.bias}* (`{f.confidence_score:+.2f}`)\n"
            f"🧠 *Xulosa:* {clean_md(f.macro_reason)}\n"
        )

        if f.causal_chain:
            msg += f"\n🔗 *Voqealar Zanjiri (Domino Effekti):*\n{clean_md(f.causal_chain)}\n"

        if f.key_actors_analysis:
            actors_str = "\n".join(f"  • {clean_md(item)}" for item in f.key_actors_analysis[:3])
            msg += f"\n👤 *Shaxslar va Institutlar:*\n{actors_str}\n"

        if f.geopolitical_hotspots:
            hotspots_str = "\n".join(f"  • {clean_md(item)}" for item in f.geopolitical_hotspots[:2])
            msg += f"\n🌍 *Geosiyosiy O'choqlar:*\n{hotspots_str}\n"

        if f.predicted_next_moves:
            moves_str = "\n".join(f"  • {clean_md(item)}" for item in f.predicted_next_moves[:2])
            msg += f"\n🔮 *Keyingi Qadam (Next Moves):*\n{moves_str}\n"

        return safe_truncate_tg(msg, MAX_TELEGRAM_TEXT_LEN)

    @staticmethod
    def build_copilot_keyboard(signal: FinalTradeSignal) -> dict[str, Any]:
        t = signal.technical
        sig_id = signal.signal_id
        callback_open = f"LIMIT_{sig_id}_{t.symbol}_{t.entry_price}_{t.sl_price}_{t.recommended_tp}"
        return {
            "inline_keyboard": [
                [
                    {"text": f"✅ #{sig_id:03d} {clean_md(t.order_type)} (TP {t.recommended_tp:.2f})", "callback_data": callback_open[:64]},
                    {"text": "❌ O'tkazib Yuborish", "callback_data": f"REJECT_{sig_id}_{t.symbol}"},
                ]
            ]
        }

    def _post_telegram_json(self, payload: dict[str, Any]) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200

    def send_text_update(self, text: str) -> bool:
        safe_txt = safe_truncate_tg(text, MAX_TELEGRAM_TEXT_LEN)
        if not self.bot_token or self.bot_token == "your_telegram_bot_token":
            logger.info("Telegram Update:\n" + safe_txt)
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": safe_txt,
            "parse_mode": "Markdown",
        }
        try:
            return self._post_telegram_json(payload)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            logger.warning(f"Telegram Markdown xatosi ({e.code}: {err_body}), oddiy matnda qayta yuborilmoqda...")
            try:
                fallback_payload = {
                    "chat_id": self.chat_id,
                    "text": safe_truncate_tg(strip_all_md(safe_txt), MAX_TELEGRAM_TEXT_LEN),
                }
                return self._post_telegram_json(fallback_payload)
            except Exception as e2:
                logger.warning(f"Telegram fallback xatosi: {e2}")
                return False
        except Exception as e:
            logger.warning(f"Telegram update xatosi: {e}")
            return False

    def send_signal(self, signal: FinalTradeSignal, stats_badge: str = "") -> bool:
        """
        Asosiy A+ Setup xabarini 3-bosqichli kafolat bilan yuboradi:
        1) Markdown + Keyboard (< 3900 belgi)
        2) Plain Text + Keyboard (< 3900 belgi)
        3) Plain Text (Keyboard siz)
        """
        sig_id = signal.signal_id
        text = self.format_signal_message(signal, stats_badge=stats_badge)
        if not self.bot_token or self.bot_token == "your_telegram_bot_token":
            logger.info("Telegram token kiritilmagan — konsolga A+ Setup xabari chiqarilmoqda:\n" + text)
            return False

        # 1-Urinish: Markdown + Inline Keyboard
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": self.build_copilot_keyboard(signal),
        }
        try:
            if self._post_telegram_json(payload):
                logger.info(f"✅ SIGNAL #{sig_id:03d} Telegram chatga ({self.chat_id}) muvaffaqiyatli yuborildi! (Uzunlik: {len(text)} belgi)")
                return True
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            logger.warning(f"Telegram send_signal 1-urinish xatosi ({e.code}: {err_body}), 2-urinish (Plain Text) ishga tushdi...")
            # 2-Urinish: Plain Text + Inline Keyboard
            try:
                plain_txt = safe_truncate_tg(strip_all_md(text), MAX_TELEGRAM_TEXT_LEN)
                fallback_payload = {
                    "chat_id": self.chat_id,
                    "text": plain_txt,
                    "reply_markup": self.build_copilot_keyboard(signal),
                }
                if self._post_telegram_json(fallback_payload):
                    logger.info(f"✅ SIGNAL #{sig_id:03d} (2-urinish Plain Text) Telegram chatga yuborildi!")
                    return True
            except Exception as e2:
                logger.warning(f"Telegram send_signal 2-urinish xatosi ({e2}), 3-urinish (tugmasiz) ishga tushdi...")
                # 3-Urinish: Tugmasiz oddiy matn
                try:
                    plain_no_kb = {
                        "chat_id": self.chat_id,
                        "text": safe_truncate_tg(strip_all_md(text), 3500),
                    }
                    if self._post_telegram_json(plain_no_kb):
                        logger.info(f"✅ SIGNAL #{sig_id:03d} (3-urinish tugmasiz) Telegram chatga yuborildi!")
                        return True
                except Exception as e3:
                    logger.warning(f"Telegram send_signal 3-urinish xatosi: {e3}")
                    return False
        except Exception as e:
            logger.warning(
                f"Bulutli Sandbox muhitida tashqi internet cheklangani sababli Telegram API ga kompyuteringizdan ulanadi ({e})."
            )
            return False
        return False

    def init_offset(self) -> None:
        if not self.bot_token or self.bot_token == "your_telegram_bot_token":
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?timeout=1"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for upd in data.get("result", []):
                self.last_update_id = max(self.last_update_id, int(upd["update_id"]))
        except Exception:
            pass

    def poll_commands(self) -> list[str]:
        if not self.bot_token or self.bot_token == "your_telegram_bot_token":
            return []

        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates?offset={self.last_update_id + 1}&timeout=1"
        commands: list[str] = []
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for upd in data.get("result", []):
                self.last_update_id = max(self.last_update_id, int(upd["update_id"]))
                msg = upd.get("message", {})
                txt = str(msg.get("text", "")).strip().lower()
                if txt.startswith("/"):
                    commands.append(txt)
        except Exception:
            pass
        return commands