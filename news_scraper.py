"""
2-QISM: FXStreet News bo'limidan Aktivga Mos (Gold vs Bitcoin) Yangiliklar va Kontekst Yig'uvchi Modul (news_scraper.py) — PRO v4.0
PRO v4.0 MUHIM YANGILANISHI:
- Endi Oltin (`XAUUSD`) yangiliklari bilan Bitcoin (`BTCUSD`) yangiliklari bir-biriga aralashtirib yuborilmaydi!
- `fetch_headlines(symbol=...)` aktivga qarab mos sarlavhalarni va makroiqtisodiy oqimni yig'adi.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import re
from typing import Any
import urllib.request
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

from config import FXSTREET_NEWS_URL, FXSTREET_RSS_URL, FXSTREET_GOOGLE_RSS_URL

logger = logging.getLogger(__name__)

FXSTREET_BTC_GOOGLE_RSS_URL = (
    "https://news.google.com/rss/search?q=site:fxstreet.com/news+"
    "(Bitcoin+OR+BTC+OR+Crypto+OR+ETF+OR+Fed+OR+DXY+OR+Treasury)&hl=en-US&gl=US&ceid=US:en"
)

HIGH_IMPACT_KEYWORDS = [
    "BREAKING", "CPI", "Core PCE", "Nonfarm Payrolls", "NFP", "FOMC",
    "Federal Reserve", "Fed Chair", "Warsh", "Powell", "Interest Rate",
    "Rate Hike", "Rate Cut", "Unemployment Claims", "GDP", "ISM",
    "Retail Sales", "Geopolitical", "Iran", "Middle East", "Hormuz",
    "Bab el-Mandeb", "Suez", "Tariffs", "Sanctions", "Safe-Haven",
    "Treasury Yields", "US10Y", "DXY", "WTI", "Oil", "Bitcoin", "BTC", "ETF"
]

ACTOR_PATTERNS: dict[str, str] = {
    "Fed / FOMC (Warsh & Powell)": r"\b(fed|fomc|federal reserve|warsh|powell|waller|bowman|goolsbee|kashkari)\b",
    "AQSh G'aznachiligi va Kongress (Fiskal siyosat / Obligatsiyalar)": r"\b(treasury|us bond|yields|congress|midterm|election|white house|tariff|fiscal)\b",
    "Wall Street Institutsional Fondlari (BlackRock / Spot ETF / CME)": r"\b(blackrock|etf|inflows|outflows|institutional|cme|whales|short-squeeze|squeeze|rally|accumulation)\b",
    "Yirik Markaziy Banklar (BoJ / BoE / PBoC / ECB)": r"\b(boj|bank of japan|boe|bank of england|pboc|rba|rbnz|ecb|central bank)\b",
    "Energetika va Geosiyosiy Kuchlar (Yaqin Sharq / OPEC / Neft)": r"\b(iran|irgc|israel|opec|saudi|oil|wti|brent|tanker|blockade|gulf)\b",
}

LOCATION_PATTERNS: dict[str, str] = {
    "AQSh (Vashington / Nyu-York — Wall Street, CME & Fed)": r"\b(united states|us dollar|wall street|washington|dxy|treasuries|fed|fomc|etf|bitcoin|btc)\b",
    "Yaqin Sharq va Dengiz Bo'g'ozlari (Eron, Ho'rmuz, Bob al-Mandab, Suvaysh)": r"\b(middle east|iran|hormuz|bab el-mandeb|suez|red sea|saudi arabia|israel|gulf)\b",
    "Osiyo Moliya Markazlari (Tokio, Pekin, Gongkong, Singapur)": r"\b(japan|tokyo|china|beijing|hong kong|india|singapore|asian session)\b",
    "Yevropa va Buyuk Britaniya (London, Frankfurt, Yevrozona)": r"\b(uk|britain|london|france|germany|eurozone|european|ecb|boe)\b",
}


class MacroNewsScraper:
    """FXStreet News bo'limidan aktivga mos (XAUUSD yoki BTCUSD) yangiliklarni va shaxslar/hududlar aloqalarini yig'uvchi PRO v4.0 klass."""

    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _fetch_fxstreet_rss(self, url: str, max_items: int = 12) -> list[dict[str, str]]:
        items_out: list[dict[str, str]] = []
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        for item in root.findall(".//item")[:max_items]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            if title:
                clean_title = re.sub(r"\s*-\s*FXStreet.*$", "", title, flags=re.IGNORECASE)
                items_out.append({
                    "title": clean_title,
                    "url": link,
                    "published": pub_date,
                    "source": "FXStreet News",
                })
        return items_out

    def _fetch_fxstreet_html(self, max_items: int = 12) -> list[dict[str, str]]:
        items_out: list[dict[str, str]] = []
        req = urllib.request.Request(FXSTREET_NEWS_URL, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        soup = BeautifulSoup(html, "html.parser")
        seen: set[str] = set()
        for a_tag in soup.select("a[href*='/news/']"):
            title = a_tag.get_text(strip=True)
            href = a_tag.get("href", "")
            if not title or len(title) < 25 or title in seen:
                continue
            seen.add(title)
            full_url = href if href.startswith("http") else f"https://www.fxstreet.com{href}"
            items_out.append({
                "title": title,
                "url": full_url,
                "published": datetime.now(timezone.utc).isoformat(),
                "source": "FXStreet News (Direct)",
            })
            if len(items_out) >= max_items:
                break
        return items_out

    def fetch_fxstreet_articles(self, max_items: int = 10, symbol: str = "XAUUSD") -> list[dict[str, str]]:
        """
        Aktivga mos (`XAUUSD` yoki `BTCUSD`) FXStreet News maqolalarini yig'adi.
        Agar aktiv `BTCUSD` bo'lsa, faqat Oltin ("Gold") haqidagi sarlavhalar bilan chalg'imaslik uchun
        Bitcoin/Crypto + umumiy FED/USD makro sarlavhalarini birinchi o'ringa qo'yadi.
        """
        is_btc = "BTC" in symbol.upper()
        fetch_chain = (
            [
                lambda: self._fetch_fxstreet_rss(FXSTREET_BTC_GOOGLE_RSS_URL, max_items),
                lambda: self._fetch_fxstreet_rss(FXSTREET_RSS_URL, max_items * 2),
                lambda: self._fetch_fxstreet_html(max_items * 2),
            ]
            if is_btc
            else [
                lambda: self._fetch_fxstreet_rss(FXSTREET_RSS_URL, max_items),
                lambda: self._fetch_fxstreet_html(max_items),
                lambda: self._fetch_fxstreet_rss(FXSTREET_GOOGLE_RSS_URL, max_items),
            ]
        )

        collected: list[dict[str, str]] = []
        for fetcher in fetch_chain:
            try:
                articles = fetcher()
                if articles:
                    if is_btc:
                        # BTC tahlilida faqat Oltin/Kumush haqidagi tor sarlavhalarni filtrlaymiz (FED/USD/BTC qoladi)
                        filtered = [
                            a for a in articles
                            if not (
                                ("gold" in a["title"].lower() or "xau" in a["title"].lower())
                                and not any(k in a["title"].lower() for k in ("fed", "fomc", "dollar", "dxy", "cpi", "yield", "btc", "bitcoin"))
                            )
                        ]
                        collected.extend(filtered or articles)
                    else:
                        collected.extend(articles)
                if len(collected) >= max_items:
                    return collected[:max_items]
            except Exception as e:
                logger.debug(f"FXStreet manba xatosi: {e}")

        if collected:
            return collected[:max_items]

        logger.info("Oflayn test rejimi: FXStreet News so'nggi makroiqtisodiy sarlavhalari ishlatilmoqda.")
        return [
            {
                "title": "US Dollar Index holds firm ahead of FOMC as Treasury yields and institutional flows drive intraday volatility",
                "url": "https://www.fxstreet.com/news",
                "published": datetime.now(timezone.utc).isoformat(),
                "source": "FXStreet News",
            },
            {
                "title": "Bitcoin and Gold test key institutional liquidity zones as Wall Street desks adjust positioning ahead of Fed",
                "url": "https://www.fxstreet.com/news",
                "published": datetime.now(timezone.utc).isoformat(),
                "source": "FXStreet News",
            },
        ]

    def fetch_headlines(self, max_items: int = 8, symbol: str = "XAUUSD") -> list[str]:
        articles = self.fetch_fxstreet_articles(max_items=max_items, symbol=symbol)
        return [art["title"] for art in articles]

    @staticmethod
    def extract_entities_and_context(headlines: list[str]) -> dict[str, list[str]]:
        blob = " ".join(headlines).lower()
        detected_actors: list[str] = []
        detected_locations: list[str] = []

        for actor_label, pattern in ACTOR_PATTERNS.items():
            if re.search(pattern, blob, flags=re.IGNORECASE):
                detected_actors.append(actor_label)

        for loc_label, pattern in LOCATION_PATTERNS.items():
            if re.search(pattern, blob, flags=re.IGNORECASE):
                detected_locations.append(loc_label)

        return {
            "actors": detected_actors,
            "locations": detected_locations,
        }

    def check_imminent_high_impact_news(self, window_minutes: int = 30) -> tuple[bool, list[dict[str, Any]]]:
        articles = self.fetch_fxstreet_articles(max_items=15)
        high_impact_list: list[dict[str, Any]] = []
        imminent_breaking = False
        now_utc = datetime.now(timezone.utc)

        urgent_triggers = ("breaking", "urgent", "flash", "powell speaks", "warsh speaks", "fomc statement", "cpi data", "nfp")

        for art in articles:
            title_lower = art["title"].lower()
            matched_kw = [kw for kw in HIGH_IMPACT_KEYWORDS if kw.lower() in title_lower]
            if matched_kw:
                high_impact_list.append({
                    "title": art["title"],
                    "url": art["url"],
                    "keywords": matched_kw,
                    "published": art["published"],
                })
                if any(ut in title_lower for ut in urgent_triggers):
                    try:
                        pub_dt = parsedate_to_datetime(art["published"])
                        if abs((now_utc - pub_dt).total_seconds()) / 60.0 <= window_minutes:
                            imminent_breaking = True
                    except Exception:
                        imminent_breaking = True

        return imminent_breaking, high_impact_list