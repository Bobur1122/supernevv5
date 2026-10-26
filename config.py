"""
1-QISM: Konfiguratsiya va global sozlamalar (config.py)
Multi-Agent AI Trading System — TradingView (FOREXCOM:XAUUSD) va FXStreet News integratsiyasi
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# TradingView (FOREX.com) rasmiy tikerlari — Asosiy narx manbasi
TV_SCANNER_URL: str = "https://scanner.tradingview.com/cfd/scan"
TV_TICKERS: dict[str, str] = {
    "XAUUSD": "FOREXCOM:XAUUSD",
    "USDX": "TVC:DXY",
    "BTCUSD": "BITSTAMP:BTCUSD",
}

# COMEX Futures (GCW00) va TradingView FOREX.com Spot (XAUUSD) o'rtasidagi basis farqi
COMEX_TO_FOREXCOM_OFFSET: float = float(os.getenv("COMEX_TO_FOREXCOM_OFFSET", "-13.10"))

# MetaTrader 5 sozlamalari (ixtiyoriy zaxira manba)
MT5_LOGIN: int = int(os.getenv("MT5_LOGIN", "12345678"))
MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "your_mt5_password")
MT5_SERVER: str = os.getenv("MT5_SERVER", "MetaQuotes-Demo")
MT5_PATH: str = os.getenv("MT5_PATH", "")

# Gemini AI sozlamalari
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "your_gemini_api_key")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Telegram Bot sozlamalari (Foydalanuvchi botiga ulangan)
TELEGRAM_BOT_TOKEN: str = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8522051984:AAFUNietKZGCJ6kvoy2dgYVHOgCVT3Sh56w",
)
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "6637983416")

# FXStreet News manbalari (har 1 daqiqada tekshiriladi)
FXSTREET_NEWS_URL: str = os.getenv("FXSTREET_NEWS_URL", "https://www.fxstreet.com/news")
FXSTREET_RSS_URL: str = os.getenv("FXSTREET_RSS_URL", "https://www.fxstreet.com/rss/news")
FXSTREET_GOOGLE_RSS_URL: str = (
    "https://news.google.com/rss/search?q=site:fxstreet.com/news+"
    "(Gold+OR+XAUUSD+OR+DXY+OR+Dollar+OR+Fed+OR+FOMC+OR+CPI+OR+NFP+OR+Bitcoin)&hl=en-US&gl=US&ceid=US:en"
)

# Savdo qilinadigan aktivlar ro'yxati
SYMBOLS: dict[str, str] = {
    "GOLD": "XAUUSD",
    "DXY": "USDX",
    "BTC": "BTCUSD",
}

# Taymfreym sozlamalari (Multi-Timeframe)
HTF: str = "H1"   # Katta taymfreym: Asosiy Order Block va trend strukturasi
LTF: str = "M15"  # Kichik taymfreym: Liquidity Sweep + CHoCH kirish nuqtasi

# Risk boshqaruvi sozlamalari
ACCOUNT_BALANCE_DEFAULT: float = float(os.getenv("ACCOUNT_BALANCE", "10000.0"))
RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))  # Har bir bitim uchun 1% risk
MIN_RR_RATIO: float = 2.0  # Minimal Risk:Reward nisbati (1:2)
MIN_CONFIDENCE_SCORE: float = 0.35  # Minimal Fundamental Bias ishonchlilik darajasi

# Toshkent vaqti (UTC+5) bo'yicha Killzone sessiyalari
TIMEZONE_OFFSET_HOURS: int = 5
KILLZONES_TASHKENT: dict[str, tuple[int, int]] = {
    "LONDON": (12, 15),    # 12:00 - 15:00 (Toshkent vaqti)
    "NEW_YORK": (17, 20),  # 17:00 - 20:00 (Toshkent vaqti)
}

# FXStreet News bo'limini tekshirish va sikl aylanish vaqti (soniyalarda) - har 1 daqiqa (60 soniya)
NEWS_CHECK_INTERVAL_SECONDS: int = 60
LOOP_INTERVAL_SECONDS: int = 60