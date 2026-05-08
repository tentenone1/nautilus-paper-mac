"""
Sybil Position Aggregator — v2
Queries Polymarket data API for all sybil wallets, filters ACTIVE positions only,
aggregates by group into meta-whale exposures.

Output: research/sybil_positions.json
"""

import json
import logging
import os
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_API_BASE = "https://data-api.polymarket.com"

SYBIL_GROUPS = {
    "sybil_group_1": {
        "priority": "HIGH",
        "wallets": {
            "0x492442eab586f242b53bda933fd5de859c8a3782": "coordinator",
            "0xacb206b460a17382a734de8d931cc176307eb989": "AppleTime67",
            "0xe26cacfaa3f695a2a239e5918936b10d56f188cf": "Dvitaminbets",
            "0xd106952ebf30a3125affd8a23b6c1f30c35fc79c": "Herdonia",
            "0x437961a3b2684a4835da753e894d4b5cffdb2e16": "NewTeamSosed4",
            "0xf39651f0addaad0221806d828197064b97feed0d": "Pajamapants",
            "0xa71093cafc0c099b4ccab24c3cb8018d817923c4": "Talvez10",
            "0xa8e089ade142c95538e06196e09c85681112ad50": "Wannac",
            "0x0767aa79d578aead1c849fd9f0fdc6cdb50336b0": "beetlepimp",
            "0x1117eade222413335b7ec959e5b48c1d3dbc3532": "benwyatt",
            "0xa5ea13a81d2b7e8e424b182bdc1db08e756bd96a": "bossoskil1",
            "0x29b52d98ac9ef9414b04164246c95bc63d74cc6c": "loitterer",
            "0x84cfffc3f16dcc353094de30d4a45226eccd2f63": "mooseborzoi",
            "0x32ccd9015a900fde62040162a04bedf093c668b3": "pilotlady",
            "0xe48109602719f95c247fec255ffb71bab3f985a3": "trade-via-Gravia",
        },
    },
    "sybil_group_2": {
        "priority": "MODERATE",
        "wallets": {
            "0x5d1d9cfd66ee3068c2a8a57dedf1e1b006dcafd2": "coordinator",
            "0x3b5c629f114098b0dee345fb78b7a3a013c7126e": "SMCAOMCRL",
            "0xe96fb5321534971aed483029d2712917fda9ff4b": "meifei123",
        },
    },
    "sybil_group_3": {
        "priority": "LOW",
        "wallets": {
            "0x9495425feeb0c250accb89275c97587011b19a27": "LaBradfordSmith22",
            "0xba389f76b0119aed07c53c9029852664bd97e406": "joblessfinalboss",
            "0x39d3c773be30fcc73161fc6768f46d563a779ef0": "matanovik",
        },
    },
}


def is_active_position(pos: dict) -> bool:
    """Check if a position is still open (not resolved/redeemed)."""
    if pos.get("redeemable", False):
        return False
    price = pos.get("curPrice", 0) or 0
    if price == 0 or price == 1:
        return False
    pnl = pos.get("percentPnl", 0) or 0
    if abs(pnl) >= 99.0:
        return False
    return True


def fetch_positions(address: str, timeout: int = 15) -> list[dict]:
    """Fetch positions for a wallet from Polymarket data API."""
    url = f"{DATA_API_BASE}/positions?user={address}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.warning(f"Failed to fetch positions for {address}: {e}")
        return []


def aggregate_by_group() -> dict:
    """Query all sybil wallets, filter active positions, aggregate by group."""
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "groups": {},
        "summary": {},
    }

    for group_id, group_info in SYBIL_GROUPS.items():
        all_active_positions = []
        wallet_results = {}

        for addr, label in group_info["wallets"].items():
            logger.info(f"Fetching positions for {label} ({addr})")
            positions = fetch_positions(addr)
            active = [p for p in positions if is_active_position(p)]
            wallet_results[label] = {
                "address": addr,
                "total_positions": len(positions),
                "active_positions": len(active),
                "active": active,
            }
            all_active_positions.extend(active)
            time.sleep(0.5)

        # Aggregate active positions by market (conditionId)
        market_agg = {}
        for pos in all_active_positions:
            cond_id = pos.get("conditionId", "unknown")
            if cond_id not in market_agg:
                market_agg[cond_id] = {
                    "condition_id": cond_id,
                    "market_title": pos.get("title", ""),
                    "market_slug": pos.get("slug", ""),
                    "event_slug": pos.get("eventSlug", ""),
                    "end_date": pos.get("endDate", ""),
                    "wallets": [],
                    "total_size_usd": 0.0,
                    "yes_size_usd": 0.0,
                    "no_size_usd": 0.0,
                    "outcome_sizes": {},
                }
            outcome = pos.get("outcome", "unknown")
            size = float(pos.get("size", 0) or 0)
            market_agg[cond_id]["wallets"].append({
                "label": label,
                "address": addr,
                "outcome": outcome,
                "size_usd": size,
                "avg_price": pos.get("avgPrice", 0),
                "current_price": pos.get("curPrice", 0),
                "position_value": pos.get("currentValue", 0),
            })
            market_agg[cond_id]["total_size_usd"] += size
            if outcome.lower() == "yes":
                market_agg[cond_id]["yes_size_usd"] += size
            elif outcome.lower() == "no":
                market_agg[cond_id]["no_size_usd"] += size
            if outcome not in market_agg[cond_id]["outcome_sizes"]:
                market_agg[cond_id]["outcome_sizes"][outcome] = 0.0
            market_agg[cond_id]["outcome_sizes"][outcome] += size

        # Round values
        for m in market_agg.values():
            m["total_size_usd"] = round(m["total_size_usd"], 2)
            m["yes_size_usd"] = round(m["yes_size_usd"], 2)
            m["no_size_usd"] = round(m["no_size_usd"], 2)
            for k, v in m["outcome_sizes"].items():
                m["outcome_sizes"][k] = round(v, 2)

        total_exposure = sum(m["total_size_usd"] for m in market_agg.values())
        result["groups"][group_id] = {
            "priority": group_info["priority"],
            "wallet_count": len(group_info["wallets"]),
            "total_active_exposure_usd": round(total_exposure, 2),
            "active_position_count": len(all_active_positions),
            "market_count": len(market_agg),
            "markets": sorted(market_agg.values(), key=lambda x: x["total_size_usd"], reverse=True),
            "wallet_details": wallet_results,
        }

    total_all = sum(g["total_active_exposure_usd"] for g in result["groups"].values())
    result["summary"] = {
        "total_groups": len(SYBIL_GROUPS),
        "total_wallets": sum(len(g["wallets"]) for g in SYBIL_GROUPS.values()),
        "total_active_exposure_usd": round(total_all, 2),
    }

    return result


def main():
    output_dir = "/home/elon-1/workspace/nautilus-trading/research"
    output_path = os.path.join(output_dir, "sybil_positions.json")

    logger.info("Starting sybil position aggregation (v2)...")
    result = aggregate_by_group()

    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    for gid, gdata in result["groups"].items():
        logger.info(
            f"{gid}: {gdata['wallet_count']} wallets, "
            f"${gdata['total_active_exposure_usd']:,.0f} active exposure, "
            f"{gdata['active_position_count']} active positions, "
            f"{gdata['market_count']} markets"
        )

    logger.info(f"Output: {output_path}")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
