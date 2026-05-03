"""Whale Discovery Pipeline Configuration."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "whale_discovery.db")

# API endpoints
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# Scanning parameters
MIN_POSITION_SIZE = 5000  # Minimum $5K position to track
MIN_ALPHA_SCORE = 70      # Minimum alpha score for signals
MAX_AGE_HOURS = 24        # Signal TTL
SCAN_INTERVAL = 60        # Seconds between position scans
DISCOVERY_INTERVAL = 21600  # 6 hours between full whale discovery scans

# Known whales — 2-3 per category for diversification
# All addresses verified from V1 trading_intelligence.db
KNOWN_WHALES = [
    # === TOP PERFORMERS (proven high PnL, high volume) ===
    {
        "address": "0x03e8a544e97eeff5753bc1e90d46e5ef22af1697",
        "name": "weflyhigh",
        "alpha_score": 80,
        "category": "top_performer",
        "notes": "$863K PnL, 86% ROI, highest earner",
    },
    {
        "address": "0x8f037a2e4fd49d11267f4ab874ab7ba745ac64d6",
        "name": "Anointed-Connect",
        "alpha_score": 70,
        "category": "top_performer",
        "notes": "$269K PnL, $468K volume",
    },

    # === HIGH EFFICIENCY (high alpha per dollar risked) ===
    {
        "address": "0x4bbe10ba5b7f6df147c0dae17b46c44a6e562cf3",
        "name": "How.Dare.You",
        "alpha_score": 90,
        "category": "high_efficiency",
        "notes": "Alpha=90, highest score. $62K PnL on $41K volume",
    },
    {
        "address": "0xe24838258b572f1771dffba3bcdde57a78def293",
        "name": "redskinrick",
        "alpha_score": 80,
        "category": "high_efficiency",
        "notes": "Alpha=80, emerging. $31K PnL on $13K volume",
    },

    # === HIGH PROFILE (consistent across many markets) ===
    {
        "address": "0x7e3a1f95c558f39a51ff334d789e3e039b553246",
        "name": "KaneAnalytics",
        "alpha_score": 80,
        "category": "high_profile",
        "notes": "Alpha=80, $65K PnL, well-known analyst",
    },
    {
        "address": "0xc660ae71765d0d9eaf5fa8328c1c959841d2bd28",
        "name": "TutiFromFactsOfLife",
        "alpha_score": 80,
        "category": "high_profile",
        "notes": "Alpha=80, $52K PnL, active trader",
    },
    {
        "address": "0xb45a797faa52b0fd8adc56d30382022b7b12192c",
        "name": "bcda",
        "alpha_score": 80,
        "category": "high_profile",
        "notes": "Alpha=80, $67K PnL, $95K volume",
    },

    # === DIRECTIONAL EDGE (strong conviction bets on specific domains) ===
    {
        "address": "0x96489abcb9f583d6835c8ef95ffc923d05a86825",
        "name": "anoin123",
        "alpha_score": 85,
        "category": "directional_edge",
        "notes": "Alpha=85, geopolitics specialist. $45K PnL",
    },
    {
        "address": "0x388537259dc9e693c1c9b96fdf07a63f6b7aca77",
        "name": "easypredict",
        "alpha_score": 75,
        "category": "directional_edge",
        "notes": "Alpha=75, crypto focus. $38K PnL, $248K volume",
    },

    # === EMERGING (new whales with strong early signals) ===
    {
        "address": "0xa7d1ae08567d192db3c3cf1858c62c76dc8cb824",
        "name": "Cinibengales",
        "alpha_score": 70,
        "category": "emerging",
        "notes": "Alpha=70, emerging. $43K PnL, active in sports",
    },
    {
        "address": "0x9cb990f1862568a63d8601efeebe0304225c32f2",
        "name": "jtwyslljy",
        "alpha_score": 80,
        "category": "high_efficiency",
        "notes": "Alpha=80, $48K PnL on $43K volume",
    },
    {
        "address": "0xcb6ed9332a8fd1b930893c705dd234f37aa248e6",
        "name": "0xCb6Ed9332A8FD...",
        "alpha_score": 65,
        "category": "emerging",
        "notes": "Alpha=65, $51K PnL, $172K volume, active trader",
    },
    {
        "address": "0xa80e3fe5e7a445fa047fe6de1e27f9a15217b94b",
        "name": "bin8888",
        "alpha_score": 65,
        "category": "emerging",
        "notes": "Alpha=65, $25K PnL, crypto focus",
    },

    # === CRYPTO SPECIALISTS (from wallet_discovery + signal_trades) ===
    {
        "address": "0x388537259dc9e693c1c9b96fdf07a63f6b7aca77",
        "name": "easypredict",
        "alpha_score": 75,
        "category": "crypto_specialist",
        "notes": "Alpha=75, crypto focus. $38K PnL, $248K volume. Trades BTC/ETH price markets",
    },
    {
        "address": "0x36309c6e27fdce6f03ba86f514a6fbd4f0a3694c",
        "name": "WangXingYu",
        "alpha_score": 70,
        "category": "crypto_specialist",
        "notes": "Alpha=70, crypto specialist. 28 crypto trades detected",
    },

    # === GEOPOLITICS SPECIALISTS (from wallet_discovery + signal_trades) ===
    {
        "address": "0x96489abcb9f583d6835c8ef95ffc923d05a86825",
        "name": "anoin123",
        "alpha_score": 85,
        "category": "geopolitics_specialist",
        "notes": "Alpha=85, geopolitics specialist. Trades Iran, Russia/Ukraine, US-China markets. $45K PnL",
    },
    {
        "address": "0x5968454ab9fa39745d5d4adf78f2dfb7f72f2922",
        "name": "WestCoastWhale",
        "alpha_score": 75,
        "category": "mixed_non_sports",
        "notes": "Active in geopolitics + politics markets",
    },
]

# Category summary
CATEGORIES = {
    "top_performer": "Proven high PnL, high volume traders",
    "high_efficiency": "High alpha per dollar risked — sharp traders",
    "high_profile": "Consistent across many markets, well-known",
    "directional_edge": "Strong conviction bets on specific domains",
    "emerging": "New whales with strong early signals",
}

# === Whale Tiering Configuration ===
# Kelly fractions and position caps per tier (from whale_tiering.py)
TIER_DEFAULTS = {
    "elite": {"kelly_fraction": 0.30, "max_position_cap": 2500, "max_exposure": 5000},
    "strong": {"kelly_fraction": 0.20, "max_position_cap": 1500, "max_exposure": 3000},
    "moderate": {"kelly_fraction": 0.15, "max_position_cap": 1000, "max_exposure": 2000},
    "low": {"kelly_fraction": 0.10, "max_position_cap": 500, "max_exposure": 1000},
    "minimal": {"kelly_fraction": 0.05, "max_position_cap": 250, "max_exposure": 500},
}

DEFAULT_TIER = "moderate"
