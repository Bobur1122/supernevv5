"""
2-QISM: Fundamental Agent — PRO v4.6 Haqiqiy Makro + Jonli Kapital Oqimi (Order-Flow) Tahlilchisi (fundamental_agent.py)
PRO v4.6 YANGILANISHLARI:
1) Jonli Kapital Oqimi (Real-Time Money Flow — Intraday VWAP, M15/H1 EMA20/50, RVOL) ustuvorligi kuchaytirildi:
   agar bozor amalda VWAP ustida xarid oqimida bo'lsa, kechikkan DXY svodkasi sababli signal bloklanib qolmaydi.
2) Oltin (`XAUUSD`) va Bitcoin (`BTCUSD`) yangiliklari alohida filtrlanib, Voqealar Zanjiri (Domino effekti)
   Telegram 4096-belgi chekloviga mos ixcham va aniq tilda shakllantiriladi.
"""
from datetime import datetime, timezone, timedelta
import json
import logging
from typing import Optional

from config import GEMINI_API_KEY, GEMINI_MODEL, TIMEZONE_OFFSET_HOURS
from schemas import MarketSnapshot, FundamentalSignal
from news_scraper import MacroNewsScraper

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False


class FundamentalAgent:
    """
    PRO v4.6 Haqiqiy Makroiqtisodiy Voqealar Zanjiri + Jonli Institutsional Kapital Oqimi Tahlilchisi.
    """

    def __init__(self, api_key: Optional[str] = None, model_name: str = GEMINI_MODEL):
        self.api_key = api_key or GEMINI_API_KEY
        self.model_name = model_name
        self.client = None
        if GENAI_AVAILABLE and self.api_key and self.api_key != "your_gemini_api_key":
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"Gemini Client ishga tushmadi: {e}")

    @staticmethod
    def _analyze_live_institutional_flow(snapshot: MarketSnapshot) -> dict[str, float | str | bool]:
        """
        Grafikdagi jonli pul oqimini (Real-Time Institutional Order Flow) hisoblaydi:
        Yirik o'yinchilar haqiqatda sotayaptimi yoki sotib olyaptimi?
        """
        ltf = snapshot.ltf_df.iloc[:-1] if len(snapshot.ltf_df) > 40 else snapshot.ltf_df
        htf = snapshot.htf_df.iloc[:-1] if len(snapshot.htf_df) > 40 else snapshot.htf_df
        cp = float(snapshot.current_price)
        atr = max(float(snapshot.atr), 1.0)

        sub36 = ltf.tail(36)
        typical = (sub36["high"] + sub36["low"] + sub36["close"]) / 3.0
        vol = sub36["volume"].replace(0, 1.0)
        vwap = float((typical * vol).sum() / vol.sum())
        rvol = round(float(vol.tail(8).max() / max(vol.mean(), 1.0)), 2)

        m15_ema20 = float(ltf["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        m15_ema50 = float(ltf["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        h1_ema20 = float(htf["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        h1_ema50 = float(htf["close"].ewm(span=50, adjust=False).mean().iloc[-1])

        w_old = sub36.iloc[:20]
        w_new = sub36.iloc[20:]
        old_high = float(w_old["high"].max())
        old_low = float(w_old["low"].min())
        new_high = float(w_new["high"].max())
        new_low = float(w_new["low"].min())

        broke_high_upward = (new_high > old_high) and (cp >= vwap)
        strong_pump_above_vwap = (cp > vwap + 0.25 * atr) and (m15_ema20 >= m15_ema50)

        broke_low_downward = (new_low < old_low) and (cp <= vwap)
        strong_dump_below_vwap = (cp < vwap - 0.25 * atr) and (m15_ema20 <= m15_ema50)

        flow_score = 0.0
        if broke_high_upward or strong_pump_above_vwap:
            flow_score += 0.60
        elif cp >= vwap or m15_ema20 >= m15_ema50:
            flow_score += 0.35

        if broke_low_downward or strong_dump_below_vwap:
            flow_score -= 0.60
        elif cp < vwap or m15_ema20 < m15_ema50:
            flow_score -= 0.35

        if cp >= h1_ema20:
            flow_score += 0.20
        else:
            flow_score -= 0.20

        flow_dir = "BULLISH" if flow_score >= 0.20 else ("BEARISH" if flow_score <= -0.20 else "NEUTRAL")
        return {
            "flow_dir": flow_dir,
            "flow_score": round(flow_score, 2),
            "vwap": round(vwap, 2),
            "rvol": rvol,
            "m15_ema20": round(m15_ema20, 2),
            "m15_ema50": round(m15_ema50, 2),
            "h1_ema20": round(h1_ema20, 2),
            "h1_ema50": round(h1_ema50, 2),
            "broke_high_upward": broke_high_upward or strong_pump_above_vwap,
            "broke_low_downward": broke_low_downward or strong_dump_below_vwap,
        }

    def analyze(
        self,
        snapshot: MarketSnapshot,
        headlines: list[str],
        high_impact_imminent: bool = False
    ) -> FundamentalSignal:
        entity_context = MacroNewsScraper.extract_entities_and_context(headlines)
        flow = self._analyze_live_institutional_flow(snapshot)

        system_prompt = (
            "Sen Global Macro Hedge-Fund bosh tahlilchisi va Smart Money Strategsan. "
            "Vazifang XAUUSD (Oltin) va BTCUSD uchun yangiliklarni VA jonli kapital oqimini (VWAP, EMA20/50, Order-Flow) "
            "birlashtirib, bozordagi HAQIQIY yo'nalishni va keyingi qadamni (Next Move) topishdir.\n"
            "MUHIM QOIDA: Jonli Order-Flow (VWAP va M15 EMA20/50) qaysi tomonda bo'lsa, o'sha yo'nalishga ustuvorlik ber!\n\n"
            "Javobni FAQAT quyidagi JSON sxemasida (o'zbek tilida) qaytar:\n"
            "{\n"
            '  "bias": "BULLISH" | "BEARISH" | "NEUTRAL",\n'
            '  "confidence_score": float (-1.0 dan +1.0 gacha),\n'
            '  "macro_reason": "2 gapda aniq fundamental va kapital oqimi xulosasi",\n'
            '  "causal_chain": "1-Voqea -> 2-Kapital oqimi -> 3-Institutlar harakati -> 4-Likvidlik maqsadi",\n'
            '  "key_actors_analysis": ["Ishtirokchi 1 harakati", "Ishtirokchi 2 harakati"],\n'
            '  "geopolitical_hotspots": ["Hudud 1 ta\'siri"],\n'
            '  "predicted_next_moves": ["Asosiy Ssenariy", "Muqobil Ssenariy"],\n'
            '  "key_drivers": ["Driver 1", "Driver 2"]\n'
            "}"
        )

        user_prompt = (
            f"Aktiv: {snapshot.symbol} | Joriy narx: {snapshot.current_price}\n"
            f"Intraday VWAP: {flow['vwap']} | M15 EMA20/50: {flow['m15_ema20']}/{flow['m15_ema50']}\n"
            f"Jonli Order-Flow Yo'nalishi: {flow['flow_dir']} ({flow['flow_score']:+.2f})\n"
            f"DXY H1 trendi: {snapshot.dxy_trend} | US10Y H1 trendi: {snapshot.us10y_trend}\n"
            f"FXStreet sarlavhalari:\n- " + "\n- ".join(headlines[:6])
        )

        if self.client is not None:
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                data = json.loads(response.text)
                return FundamentalSignal(
                    symbol=snapshot.symbol,
                    bias=str(data.get("bias", "NEUTRAL")).upper(),
                    confidence_score=round(float(data.get("confidence_score", 0.0)), 2),
                    macro_reason=str(data.get("macro_reason", "Makroiqtisodiy tahlil yakunlandi.")),
                    high_impact_news_imminent=high_impact_imminent,
                    key_drivers=list(data.get("key_drivers", [])),
                    causal_chain=str(data.get("causal_chain", "")),
                    key_actors_analysis=list(data.get("key_actors_analysis", [])),
                    geopolitical_hotspots=list(data.get("geopolitical_hotspots", [])),
                    predicted_next_moves=list(data.get("predicted_next_moves", [])),
                )
            except Exception as e:
                logger.warning(f"Gemini API xatosi, PRO v4.6 Causal-Graph + Order-Flow dvigateliga o'tilmoqda: {e}")

        return self._dynamic_rule_based_macro_logic(snapshot, headlines, high_impact_imminent, entity_context, flow)

    def _dynamic_rule_based_macro_logic(
        self,
        snapshot: MarketSnapshot,
        headlines: list[str],
        high_impact_imminent: bool,
        entity_context: Optional[dict[str, list[str]]] = None,
        flow: Optional[dict] = None,
    ) -> FundamentalSignal:
        flow = flow or self._analyze_live_institutional_flow(snapshot)
        is_btc = "BTC" in snapshot.symbol.upper()
        tashkent_now = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))
        is_weekend = tashkent_now.weekday() in (5, 6)

        relevant_headlines: list[str] = []
        for h in headlines:
            hl = h.lower()
            if is_btc and ("gold" in hl or "xau" in hl) and not any(k in hl for k in ("bitcoin", "btc", "crypto", "fed", "fomc", "dxy", "cpi", "yield")):
                continue
            relevant_headlines.append(h)
        if not relevant_headlines:
            relevant_headlines = headlines

        text_blob = " ".join(relevant_headlines).lower()
        top_headline = relevant_headlines[0] if relevant_headlines else "Institutional Order-Flow & Macro Liquidity Update"

        macro_score = 0.0
        drivers: list[str] = []

        if not (is_btc and is_weekend):
            if snapshot.dxy_trend == "BEARISH":
                macro_score += 0.25
                drivers.append("DXY H1 pasayishda")
            elif snapshot.dxy_trend == "BULLISH":
                macro_score -= 0.25
                drivers.append("DXY H1 o'sishda")

            if snapshot.us10y_trend == "BEARISH":
                macro_score += 0.15
                drivers.append("US10Y daromadliligi pasaymoqda")
            elif snapshot.us10y_trend == "BULLISH":
                macro_score -= 0.15
                drivers.append("US10Y daromadliligi o'smoqda")

        bullish_words = (
            "rate cut", "dovish", "misses", "cooler cpi", "dollar retreats", "dollar slips",
            "safe-haven demand", "rallies", "climbs", "rebounds", "picks up", "bullish",
            "inflows", "etf inflow", "short-squeeze", "breaks above", "surges"
        )
        bearish_words = (
            "hike bets", "80%", "rate hike", "hawkish", "cap gains", "stalls", "slides",
            "falls", "rout", "bears still in control", "stronger usd", "usd uptick",
            "outflows", "sell-off", "drops below"
        )

        bull_hits = sum(1 for w in bullish_words if w in text_blob)
        bear_hits = sum(1 for w in bearish_words if w in text_blob)

        if bear_hits > bull_hits:
            macro_score -= min(0.25, 0.10 * (bear_hits - bull_hits))
        elif bull_hits > bear_hits:
            macro_score += min(0.25, 0.10 * (bull_hits - bear_hits))

        flow_score = float(flow["flow_score"])
        cp = round(snapshot.current_price, 2)
        vwap = float(flow["vwap"])

        # PRO v4.6: Jonli kapital oqimi (70% vazn) + Makro (30% vazn) — shunda bozor aniq yo'nalishda
        # harakat qilayotganda kechikkan DXY sababli bot 24 soat NEUTRAL bo'lib qotib qolmaydi!
        if flow["broke_high_upward"]:
            final_score = max(0.68, round(0.75 * flow_score + 0.25 * max(0.0, macro_score), 2))
            bias = "BULLISH"
            drivers.append(f"Order-Flow: Narx ({cp:.2f}) VWAP ({vwap:.2f}) ustida Bullish impulsda")
        elif flow["broke_low_downward"]:
            final_score = min(-0.68, round(0.75 * flow_score + 0.25 * min(0.0, macro_score), 2))
            bias = "BEARISH"
            drivers.append(f"Order-Flow: Narx ({cp:.2f}) VWAP ({vwap:.2f}) ostida Bearish bosimda")
        else:
            combined = round(0.70 * flow_score + 0.30 * macro_score, 2)
            if abs(combined) < 0.20:
                final_score = combined
                bias = "NEUTRAL"
            elif combined > 0:
                final_score = max(0.60, min(0.95, combined + 0.20))
                bias = "BULLISH"
            else:
                final_score = min(-0.60, max(-0.95, combined - 0.20))
                bias = "BEARISH"

        final_score = max(-1.0, min(1.0, round(final_score, 2)))

        atr = max(snapshot.atr, 8.5 if "XAU" in snapshot.symbol.upper() else 250.0)
        down_target_1 = round(cp - 1.2 * atr, 2)
        down_target_2 = round(cp - 2.2 * atr, 2)
        up_target_1 = round(cp + 1.2 * atr, 2)
        up_target_2 = round(cp + 2.2 * atr, 2)

        actors = (entity_context or {}).get("actors", []) or [
            "Fed / FOMC (Warsh & Powell)",
            "Wall Street Institutsional Fondlari (BlackRock / CME)",
        ]
        locations = (entity_context or {}).get("locations", []) or [
            "AQSh (Vashington / Nyu-York — Wall Street, CME & Fed)",
        ]

        if bias == "BEARISH":
            causal_chain = (
                f"1 FXStreet: «{top_headline[:70]}» ➡️ "
                f"2 {', '.join(actors[:2])} tomonidan likvidlik nazorati ➡️ "
                f"3 DXY ({snapshot.dxy_trend}) hamda narxning VWAP ({vwap:.2f}) ostida sotuv bosimida qolishi ➡️ "
                f"4 {snapshot.symbol} ning {cp:.2f} dan {down_target_1:.2f} va {down_target_2:.2f} likvidlik hovuzlariga tushish harakati."
            )
            reason = (
                f"FXStreet oqimi («{top_headline[:65]}...») va jonli Order-Flow "
                f"(narx {cp:.2f} < VWAP {vwap:.2f}, M15 EMA20 {flow['m15_ema20']:.2f}) {snapshot.symbol} da sotuv (Distribution) ustunligini tasdiqlamoqda."
            )
            next_moves = [
                f"Asosiy (Bearish): Supply zonasidan narxni {down_target_1:.2f} va {down_target_2:.2f} Sell-Side likvidlik hovuzlarigacha tushirish.",
                f"Muqobil (Reversal): Agar VWAP ({vwap:.2f}) va SL ustida shamcha yopilsa, ssenariy bekor qilinadi."
            ]
        elif bias == "BULLISH":
            causal_chain = (
                f"1 Drayver: «{top_headline[:70]}» ➡️ "
                f"2 {snapshot.symbol} ning VWAP ({vwap:.2f}) va EMA50 ({flow['m15_ema50']:.2f}) ustida {flow['rvol']}x hajm bilan o'sishi ➡️ "
                f"3 {', '.join(actors[:2])} tomonidan pastki likvidlik yig'ilib (Accumulation), xarid orderlarining faollashishi ➡️ "
                f"4 {snapshot.symbol} ning {cp:.2f} dan {up_target_1:.2f} va {up_target_2:.2f} Buy-Side likvidlikka o'sishi."
            )
            reason = (
                f"Jonli Order-Flow (narx {cp:.2f} > VWAP {vwap:.2f}, RVOL {flow['rvol']}x) va "
                f"makro fon («{top_headline[:65]}...») yirik institutsional xaridorlar ustunligini ko'rsatmoqda."
            )
            next_moves = [
                f"Asosiy (Bullish): Demand OB zonasidan narxni {up_target_1:.2f} va {up_target_2:.2f} Buy-Side likvidlik hovuzlarigacha olib chiqish.",
                f"Muqobil (Reversal): Agar VWAP ({vwap:.2f}) va SL tayanchidan pastda yopilsa, o'sish impulsi to'xtaydi."
            ]
        else:
            causal_chain = (
                f"1 FXStreet («{top_headline[:60]}...») va Order-Flow ({cp:.2f} vs VWAP {vwap:.2f}) o'rtasida muvozanat ➡️ "
                f"2 Aniq Breakout tasdiqlanmaguncha kutish rejimi."
            )
            reason = f"Makro fon va M15/H1 kapital oqimi muvozanatda ({final_score:+.2f}). Aniq impuls kutilmoqda."
            next_moves = [f"Kutish: {up_target_1:.2f} ustida Bullish, {down_target_1:.2f} ostida Bearish setup ochiladi."]

        key_actors_analysis = [f"{act}: {bias} yo'nalishdagi asosiy institutsional ishtirokchi." for act in actors[:2]]
        geopolitical_hotspots = [f"{loc}: Faol makroiqtisodiy va likvidlik markazi." for loc in locations[:2]]

        return FundamentalSignal(
            symbol=snapshot.symbol,
            bias=bias,
            confidence_score=final_score,
            macro_reason=reason,
            high_impact_news_imminent=high_impact_imminent,
            key_drivers=drivers,
            causal_chain=causal_chain,
            key_actors_analysis=key_actors_analysis,
            geopolitical_hotspots=geopolitical_hotspots,
            predicted_next_moves=next_moves,
        )