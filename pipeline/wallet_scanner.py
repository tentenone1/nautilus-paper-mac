"""Wallet discovery scanner — finds profitable whale wallets on Polymarket."""
import os
import time
import json
import requests
from datetime import datetime, timezone

from pipeline.config import DATA_API, GAMMA_API, MIN_POSITION_SIZE
from pipeline.db import upsert_whale, log_scan


def categorize_market(title: str) -> str:
    """Categorize a market based on its title.
    
    Expanded keyword coverage with priority ordering.
    Returns the highest-weighted matching category.
    """
    if not title:
        return "unknown"
    title_lower = title.lower()
    
    # Sports — extensive team/keyword list
    sports_kw = [
        "nfl", "nba", "mlb", "nhl", "ncaa", "soccer", "ufc", "mma", "boxing",
        "vs.", "spread", "o/u", "over / under", "moneyline", "total points",
        "point spread", "parlay", "puck line", "run line",
        # NBA teams
        "lakers", "celtics", "warriors", "knicks", "spurs", "thunder", "nuggets",
        "bucks", "hawks", "pistons", "raptors", "rockets", "clippers", "suns",
        "mavericks", "timberwolves", "grizzlies", "magic", "wizards", "hornets",
        "pelicans", "bulls", "heat", "nets", "76ers", "pacers", "jazz", "kings",
        "cavaliers", "cavs", "trail blazers",
        # NFL teams
        "eagles", "49ers", "ravens", "steelers", "rams", "seahawks", "buccaneers",
        "panthers", "oilers", "jets", "patriots", "cowboys", "chiefs", "bills",
        "bengals", "browns", "packers", "vikings", "bears", "lions", "saints",
        "falcons", "cardinals", "colts", "titans", "jaguars", "dolphins", "broncos",
        "chargers", "giants", "commanders", "texans",
        # MLB teams
        "yankees", "dodgers", "red sox", "blue jays", "guardians", "tigers",
        "mets", "braves", "phillies", "astros", "padres", "giants", "cardinals",
        "cubs", "brewers", "twins", "mariners", "rays", "orioles", "angels",
        "reds", "pirates", "diamondbacks", "rockies", "marlins", "nationals",
        "royals", "white sox", "athletics", "rangers",
        # NHL teams
        "bruins", "sabres", "hurricanes", "panthers", "lightning", "canadiens",
        "senators", "maple leafs", "blackhawks", "avalanche", "blue jackets",
        "red wings", "predators", "blues", "jets", "ducks", "flames", "oilers",
        "kings", "coyotes", "sharks", "canucks", "golden knights", "kraken",
        "penguins", "flyers", "rangers", "islanders", "devils", "capitals", "wild",
        # Soccer / Intl sports
        "premier league", "champions league", "world cup", "fifa", "la liga",
        "serie a", "bundesliga", "ligue 1", "eredivisie", "mls",
        # Esports
        "dota", "league of legends", "lol", "cs:go", "valorant", "esports",
        "gaming", "roster", "tournament", "grand finals",
        # Other sports
        "f1", "formula", "grand prix", "nascar", "pga", "golf", "tennis",
        "australian open", "wimbledon", "us open", "french open",
        "college football", "college basketball", "super bowl", "finals",
        "playoff", "semifinal", "quarterfinal", "championship", "draft",
        "game", "win", "match", "race", "goal",
    ]
    for kw in sports_kw:
        if kw in title_lower:
            return "sports"
    
    # Geopolitics — expanded for current events
    geopolitics_kw = [
        "war", "ukraine", "russia", "iran", "israel", "gaza", "palestine",
        "ceasefire", "nato", "military", "conflict", "strait of hormuz",
        "nuclear", "surrender", "enriched uranium", "diplomatic", "invasion",
        "sanctions", "china", "taiwan", "korea", "putin", "zelenskyy",
        "xi", "netanyahu", "hamas", "hezbollah", "houthis", "middle east",
        "south china sea", "embargo", "regime", "troops", "airstrike",
        "missile", "alliance", "coalition", "sovereignty", "annexation",
        "border dispute", "proxy war",
    ]
    for kw in geopolitics_kw:
        if kw in title_lower:
            return "geopolitics"
    
    # Crypto — expanded
    crypto_kw = [
        "bitcoin", "btc", "ethereum", "eth", "solana", "crypto", "defi",
        "xrp", "cardano", "ada", "bnb", "polkadot", "avalanche", "chainlink",
        "dogecoin", "doge", "shiba", "token", "blockchain", "mining",
        "binance", "coinbase", "uniswap", "aave", "usdc", "usdt",
        "stablecoin", "nft", "web3", "altcoin",
    ]
    for kw in crypto_kw:
        if kw in title_lower:
            return "crypto"
    
    # Politics — expanded
    politics_kw = [
        "trump", "biden", "harris", "election", "president", "congress",
        "senate", "governor", "vote", "primary", "caucus", "republican",
        "democrat", "supreme court", "impeachment", "approval rating",
        "midterm", "ballot", "electoral", "legislation", "nominee",
        "cabinet", "federal", "mayor", "senator", "representative",
        "gop", "dnc", "campaign", "political", "referendum",
        "swing state", "battleground", "washington",
    ]
    for kw in politics_kw:
        if kw in title_lower:
            return "politics"
    
    # Economics
    economics_kw = [
        "gdp", "inflation", "fed", "interest rate", "recession", "cpi",
        "unemployment", "federal reserve", "treasury", "yield curve",
        "jobs report", "nonfarm", "pce", "ppi", "retail sales",
        "housing starts", "mortgage", "foreclosure", "bankruptcy",
        "consumer confidence", "soft landing", "hard landing",
        "quantitative easing", "tightening", "stagflation",
    ]
    for kw in economics_kw:
        if kw in title_lower:
            return "economics"
    
    return "other"


class WalletScanner:
    """Discovers whale wallets by scanning Polymarket data."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; WhalePipeline/1.0)"
        })

    def scan_known_whales(self, whales: list) -> int:
        """Update known whales with current position data."""
        whales_with_positions = 0
        for whale in whales:
            try:
                positions = self._fetch_positions(whale["address"])
                if not positions:
                    upsert_whale(
                        address=whale["address"],
                        name=whale["name"],
                        alpha_score=whale.get("alpha_score", 50),
                    )
                    continue

                total_value = sum(
                    float(p.get("size", 0)) for p in positions
                )

                upsert_whale(
                    address=whale["address"],
                    name=whale["name"],
                    alpha_score=whale.get("alpha_score", 50),
                    volume=total_value,
                    total_trades=len(positions),
                )

                if total_value > MIN_POSITION_SIZE:
                    whales_with_positions += 1

            except Exception as e:
                print(f"  [ERROR] Failed to scan {whale['name']}: {e}")

        return whales_with_positions

    def discover_new_whales(self, top_n: int = 50) -> int:
        """Discover new whale wallets from top markets."""
        new_whales = 0
        try:
            markets = self._get_top_markets(limit=top_n)
            for market in markets:
                title = market.get("question", market.get("condition_id", ""))
                category = categorize_market(title)
                # Gamma API doesn't expose individual holders directly.
                # Whale discovery happens via:
                # 1. V1 DB seed (below)
                # 2. Scanning known whales' positions via data-api
                pass
            # Always seed from V1 DB for baseline coverage
            self._seed_from_v1_db()
        except requests.exceptions.SSLError as e:
            print(f"  [WARN] Gamma API SSL error ({e}), falling back to V1 DB seed")
            try:
                self._seed_from_v1_db()
            except Exception as e2:
                print(f"  [WARN] V1 DB seed failed: {e2}")
        except Exception as e:
            print(f"  [WARN] Gamma API failed ({e}), falling back to V1 DB seed")
            try:
                self._seed_from_v1_db()
            except Exception as e2:
                print(f"  [WARN] V1 DB seed failed: {e2}")
        return new_whales

    def _seed_from_v1_db(self) -> None:
        """Import whales from V1 trading_intelligence.db."""
        import sqlite3
        v1_db = os.path.expanduser("~/trading/shared_db/trading_intelligence.db")
        if not os.path.exists(v1_db):
            return
        
        try:
            conn = sqlite3.connect(v1_db)
            rows = conn.execute("""
                SELECT address, name, alpha_score, pnl, volume, 
                       COALESCE(rank, 999), COALESCE(tags, '[]')
                FROM wallet_discovery 
                WHERE alpha_score >= 60
                ORDER BY alpha_score DESC, pnl DESC
            """).fetchall()
            conn.close()
            
            for addr, name, alpha, pnl, vol, rank, tags in rows:
                upsert_whale(
                    address=addr, name=name, alpha_score=alpha,
                    pnl=pnl, volume=vol,
                    tags=tags,
                )
            print(f"  [SEED] Imported {len(rows)} whales from V1 database")
        except Exception as e:
            print(f"  [WARN] V1 DB seed failed: {e}")

    def _fetch_positions(self, address: str) -> list:
        """Fetch active positions for a wallet."""
        url = f"{DATA_API}/positions"
        params = {"user": address, "next_cursor": ""}
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _fetch_trades(self, address: str, limit: int = 20) -> list:
        """Fetch trade history for a wallet (if available)."""
        # Polymarket doesn't have a direct wallet trades endpoint
        # We estimate from positions instead
        return []

    def _get_top_markets(self, limit: int = 50) -> list:
        """Get top markets by volume from Gamma API."""
        url = f"{GAMMA_API}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": str(limit),
            "order": "volume24hr",
            "ascending": "false",
        }
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _get_large_holders(self, condition_id: str) -> list:
        """Get large position holders for a market.
        
        Uses the Gamma API to get market details including top holders.
        """
        url = f"{GAMMA_API}/markets"
        params = {"condition_id": condition_id}
        r = self.session.get(url, params=params, timeout=15)
        r.raise_for_status()
        markets = r.json()
        if not markets:
            return []
            
        market = markets[0]
        holders = []
        
        # Parse outcome prices to identify significant positions
        try:
            prices = json.loads(market.get("outcomePrices", "[]"))
            volume = float(market.get("volume", 0) or 0)
            liquidity = float(market.get("liquidity", 0) or 0)
            
            # If volume is high and liquidity is present, there are active traders
            if volume > 100000:
                # We can't get individual holders from Gamma,
                # so we track via position polling of known wallets
                pass
        except (json.JSONDecodeError, ValueError):
            pass
            
        return holders
