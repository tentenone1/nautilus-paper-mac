"""
Sybil Position Aggregator — v2
Queries Polymarket data API for all sybil wallets, filters ACTIVE positions only,
aggregates by group into meta-whale exposures.

Output: research/sybil_positions.json
"""

import json
import logging
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

from scripts.sybil_config import get_config

config = get_config()

DATA_API_BASE = config.api.data_api_base

# Paths for entity cluster integration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_DB = os.path.join(BASE_DIR, "research", "trades.db")
ENTITY_CLUSTERS_PATH = os.path.join(BASE_DIR, "research", config.paths.entity_clusters_file)
STATE_FILE = os.path.join(BASE_DIR, config.paths.research_dir,
                          "sybil_position_state.json")

# Hardcoded sybil groups replaced by config — wallet data loaded from
# config.groups via _build_groups().


def _build_groups() -> dict:
    """Convert config.groups into the {group_id: {priority, wallets}} format."""
    result = {}
    for gid, gdef in config.groups.items():
        result[gid] = {
            "priority": gdef.priority.upper(),
            "wallets": gdef.addresses_dict(),
        }
    return result


def load_entity_clusters() -> dict:
    """Load entity clusters from entity_clusters.json and convert to sybil groups.

    For each cluster with 3+ wallets, creates a dynamic sybil group.
    Maps readable names to addresses using trades.db.
    Returns a dict matching SYBIL_GROUPS format.
    """
    if not os.path.exists(ENTITY_CLUSTERS_PATH):
        logger.info("No entity_clusters.json found, using hardcoded groups only")
        return {}

    if not os.path.exists(TRADES_DB):
        logger.warning("trades.db not found, cannot map names to addresses")
        return {}

    try:
        with open(ENTITY_CLUSTERS_PATH, "r", encoding="utf-8") as f:
            clusters_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load entity clusters: {e}")
        return {}

    # Build name -> address map from trades.db
    try:
        db = sqlite3.connect(TRADES_DB)
        rows = db.execute("""
            SELECT DISTINCT whale_name, whale_address FROM trades
            WHERE whale_address IS NOT NULL AND whale_address != ''
            AND whale_name NOT LIKE 'unknown%'
        """).fetchall()
        db.close()
    except sqlite3.Error as e:
        logger.warning(f"Failed to query trades.db: {e}")
        return {}

    name_to_addr: dict[str, str] = {}
    for name, addr in rows:
        clean_name = name.strip().lower()
        clean_addr = addr.strip().lower()
        if clean_name and clean_addr and clean_addr.startswith("0x"):
            name_to_addr[clean_name] = clean_addr
            name_to_addr[name.strip()] = addr.strip()

    dynamic_groups: dict = {}
    cluster_id = 0

    for cluster in clusters_data.get("clusters", []):
        entities = cluster.get("entities", [])
        if len(entities) < 3:
            continue

        cluster_id += 1
        wallets: dict[str, str] = {}
        for entity in entities:
            clean = entity.strip()
            if clean.startswith("0x"):
                # Raw address — use as-is for coordinator
                wallets[clean] = "coordinator"
            else:
                # Named entity — look up address
                addr = name_to_addr.get(clean) or name_to_addr.get(clean.lower())
                if addr:
                    wallets[addr] = clean
                else:
                    wallets[clean] = clean

        if len(wallets) >= 3:
            group_id = f"entity_cluster_{cluster_id}"
            dynamic_groups[group_id] = {
                "priority": "AUTO",
                "wallets": wallets,
            }
            logger.info(
                f"Loaded entity cluster {group_id}: {len(wallets)} wallets, "
                f"from cluster of {len(entities)} entities"
            )

    if not dynamic_groups:
        logger.info("No entity clusters with 3+ wallets found")
    else:
        logger.info(f"Loaded {len(dynamic_groups)} dynamic entity clusters")

    return dynamic_groups


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


def _position_key(condition_id: str, address: str, outcome: str) -> str:
    """Create a unique key for a position: {condition_id}|{address}|{outcome}."""
    return f"{condition_id}|{address}|{outcome}"


def load_previous_state() -> dict:
    """Load previous scan state from sybil_position_state.json.

    Returns the parsed state dict, or empty state on first run.
    """
    if not os.path.exists(STATE_FILE):
        logger.info("No previous state found (first run), skipping delta detection")
        return {"last_scan": None, "positions": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        logger.info(f"Loaded previous state: {len(state.get('positions', {}))} positions from {state.get('last_scan')}")
        return state
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load previous state: {e}, treating as first run")
        return {"last_scan": None, "positions": {}}


def save_current_state(positions: dict, scan_time: str) -> None:
    """Save current scan state to sybil_position_state.json for next comparison."""
    state = {
        "last_scan": scan_time,
        "positions": positions,
    }
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        logger.info(f"Saved state: {len(positions)} positions to {STATE_FILE}")
    except OSError as e:
        logger.warning(f"Failed to save state: {e}")


def compute_delta(
    current_positions: list[dict],
    previous_state: dict,
) -> tuple[dict, dict]:
    """Compare current positions against previous state to detect deltas.

    Delta categories:
      - NEW: position key not in previous state
      - INCREASED: current size > previous size * 1.2 (20%+ growth)
      - REDUCED: current size < previous size * 0.5 (50%+ drop)
      - UNCHANGED: size changed but within thresholds

    Returns:
        (current_keyed, delta_counts) where:
          current_keyed: {position_key: position_dict} with 'delta' field added
          delta_counts: dict of {delta_type: count}
    """
    prev_positions = previous_state.get("positions", {})
    current_keyed: dict[str, dict] = {}
    delta_counts = {"NEW": 0, "INCREASED": 0, "REDUCED": 0, "UNCHANGED": 0}

    for pos in current_positions:
        cond_id = pos.get("conditionId", "unknown")
        address = pos.get("_wallet_address", "unknown")
        outcome = pos.get("outcome", "unknown")
        key = _position_key(cond_id, address, outcome)
        size = float(pos.get("size", 0) or 0)

        if key not in prev_positions:
            delta = "NEW"
        else:
            prev_size = float(prev_positions[key].get("size_usd", 0) or 0)
            if prev_size == 0 and size > 0:
                delta = "NEW"
            elif size > prev_size * 1.2:
                delta = "INCREASED"
            elif size < prev_size * 0.5:
                delta = "REDUCED"
            else:
                delta = "UNCHANGED"

        pos_with_delta = dict(pos)
        pos_with_delta["delta"] = delta
        current_keyed[key] = pos_with_delta
        delta_counts[delta] += 1

    return current_keyed, delta_counts


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


def _fetch_wallet_positions(addr: str, label: str) -> dict:
    """Fetch positions for a wallet and separate active positions."""
    positions = fetch_positions(addr)
    # Annotate each position with wallet identity for later aggregation
    for p in positions:
        p["_wallet_label"] = label
        p["_wallet_address"] = addr
    active = [p for p in positions if is_active_position(p)]
    return {
        "label": label,
        "address": addr,
        "total_positions": len(positions),
        "active_positions": len(active),
        "active": active,
    }


def aggregate_by_group() -> tuple[dict, dict]:
    """Query all sybil wallets, filter active positions, aggregate by group.

    Merges hardcoded SYBIL_GROUPS with dynamic entity clusters from
    entity_clusters.json. Dynamic groups are loaded on each run.
    """
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "groups": {},
        "summary": {},
    }

    # Merge hardcoded groups with dynamic entity clusters
    all_groups = _build_groups()
    dyn_groups = load_entity_clusters()
    for gid, ginfo in dyn_groups.items():
        if gid not in all_groups:
            all_groups[gid] = ginfo
            logger.info(f"Merged dynamic group {gid}: {len(ginfo['wallets'])} wallets")

    # Load previous scan state for delta detection
    previous_state = load_previous_state()
    total_delta_counts = {"NEW": 0, "INCREASED": 0, "REDUCED": 0, "UNCHANGED": 0, "CLOSED": 0}
    current_state_positions: dict = {}  # {position_key: {size_usd, market_title, label}}

    for group_id, group_info in all_groups.items():
        all_active_positions = []
        wallet_results = {}

        wallet_items = list(group_info["wallets"].items())
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(_fetch_wallet_positions, addr, label): (addr, label)
                for addr, label in wallet_items
            }
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=30)
                    wallet_results[result["label"]] = result
                    all_active_positions.extend(result["active"])
                except Exception as e:
                    addr, label = futures[future]
                    logger.warning(f"Failed to fetch {label}: {e}")

        # Compute delta for each active position
        current_keyed, delta_counts = compute_delta(all_active_positions, previous_state)

        # Detect CLOSED positions (in previous but not in current)
        current_keys = set(current_keyed.keys())
        for prev_key, prev_pos in previous_state.get("positions", {}).items():
            if prev_key not in current_keys:
                delta_counts["CLOSED"] = delta_counts.get("CLOSED", 0) + 1
                # Create a synthetic entry for closed position reporting
                parts = prev_key.split("|")
                if len(parts) == 3:
                    cond_id, addr, outcome = parts
                    closed_entry = {
                        "conditionId": cond_id,
                        "_wallet_address": addr,
                        "_wallet_label": prev_pos.get("label", addr[:10]),
                        "outcome": outcome,
                        "size": 0.0,
                        "avgPrice": 0,
                        "curPrice": 0,
                        "currentValue": 0,
                        "title": prev_pos.get("market_title", ""),
                        "delta": "CLOSED",
                        "prev_size_usd": prev_pos.get("size_usd", 0),
                    }
                    current_keyed[prev_key] = closed_entry

        # Accumulate delta counts across all groups
        for delta_type, count in delta_counts.items():
            total_delta_counts[delta_type] += count

        # Collect current state positions (exclude CLOSED — they're no longer active)
        for key, pos in current_keyed.items():
            if pos.get("delta") != "CLOSED":
                current_state_positions[key] = {
                    "size_usd": round(float(pos.get("size", 0) or 0), 2),
                    "market_title": pos.get("title", ""),
                    "label": pos.get("_wallet_label", ""),
                }

        # Aggregate positions by market (conditionId)
        # Uses current_keyed which includes both active and closed positions
        market_agg = {}
        for pos in current_keyed.values():
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
            wallet_entry = {
                "label": pos["_wallet_label"],
                "address": pos["_wallet_address"],
                "outcome": outcome,
                "size_usd": size,
                "avg_price": pos.get("avgPrice", 0),
                "current_price": pos.get("curPrice", 0),
                "position_value": pos.get("currentValue", 0),
                "delta": pos.get("delta", "UNCHANGED"),
            }
            # Include previous size for CLOSED positions
            if pos.get("delta") == "CLOSED":
                wallet_entry["prev_size_usd"] = pos.get("prev_size_usd", 0)
            market_agg[cond_id]["wallets"].append(wallet_entry)
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
        "total_groups": len(all_groups),
        "total_wallets": sum(len(g["wallets"]) for g in all_groups.values()),
        "total_active_exposure_usd": round(total_all, 2),
        "delta_summary": {
            "new_positions": total_delta_counts.get("NEW", 0),
            "increased": total_delta_counts.get("INCREASED", 0),
            "reduced": total_delta_counts.get("REDUCED", 0),
            "closed": total_delta_counts.get("CLOSED", 0),
        },
    }

    return result, current_state_positions


def main():
    output_dir = os.path.join(BASE_DIR, config.paths.research_dir)
    output_path = os.path.join(output_dir, config.paths.positions_file)

    logger.info("Starting sybil position aggregation (v2)...")
    result, current_state = aggregate_by_group()

    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    # Save current state for next scan's delta detection
    save_current_state(current_state, result["generated_at"])

    for gid, gdata in result["groups"].items():
        logger.info(
            f"{gid}: {gdata['wallet_count']} wallets, "
            f"${gdata['total_active_exposure_usd']:,.0f} active exposure, "
            f"{gdata['active_position_count']} active positions, "
            f"{gdata['market_count']} markets"
        )

    logger.info(f"Output: {output_path}")
    print(json.dumps(result["summary"], indent=2))

    ds = result["summary"].get("delta_summary", {})
    if ds:
        logger.info(
            f"Delta summary: NEW={ds.get('new_positions', 0)}, "
            f"INCREASED={ds.get('increased', 0)}, "
            f"REDUCED={ds.get('reduced', 0)}, "
            f"CLOSED={ds.get('closed', 0)}"
        )


if __name__ == "__main__":
    main()
