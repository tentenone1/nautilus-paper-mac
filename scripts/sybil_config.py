"""
Sybil Config Loader — centralized configuration for all sybil pipeline scripts.

Usage:
    from scripts.sybil_config import SybilConfig, get_config
    
    config = get_config()  # Loads from config/sybil_groups.yaml
    groups = config.groups  # Dict of sybil group definitions
    thresholds = config.thresholds  # Signal generation thresholds
"""

from __future__ import annotations

import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Module-level cache
_CONFIG_CACHE: Optional[SybilConfig] = None


@dataclass(frozen=True)
class WalletDef:
    """Single wallet definition."""
    address: str
    pseudonym: str


@dataclass(frozen=True)
class SybilGroupDef:
    """Definition of a sybil group."""
    group_id: str
    label: str
    priority: str
    wallets: list[WalletDef]

    def addresses_dict(self) -> dict[str, str]:
        """Return {address: pseudonym} mapping for position aggregator."""
        return {w.address: w.pseudonym for w in self.wallets}

    def pseudonym_list(self) -> list[str]:
        """Return list of pseudonyms for LLM analysis."""
        return [w.pseudonym for w in self.wallets]


@dataclass(frozen=True)
class ThresholdConfig:
    """Signal generation thresholds."""
    no_bias_fade_min_no_usd: float
    no_bias_fade_min_wallets: int
    concentrated_follow_min_yes_usd: float
    concentrated_follow_min_wallets: int
    concentrated_follow_min_yes_ratio: float
    manipulation_fade_min_wallets: int
    manipulation_fade_max_avg_bet_usd: float


@dataclass(frozen=True)
class ApiConfig:
    """API endpoint configuration."""
    data_api_base: str
    trades_limit: int
    request_timeout: int
    positions_endpoint: str
    trades_endpoint: str


@dataclass(frozen=True)
class LlmConfig:
    """LLM analysis configuration."""
    url: str
    model: str
    timeout: int
    fallback_strategy: str


@dataclass(frozen=True)
class PathConfig:
    """Output file paths."""
    research_dir: str
    positions_file: str
    signals_file: str
    intelligence_file: str
    llm_strategy_file: str
    entity_clusters_file: str

    def research_path(self, base_dir: Path) -> Path:
        """Return full path to research directory."""
        return base_dir / self.research_dir

    def positions_path(self, base_dir: Path) -> Path:
        """Return full path to positions JSON."""
        return base_dir / self.research_dir / self.positions_file

    def signals_path(self, base_dir: Path) -> Path:
        """Return full path to signals JSON."""
        return base_dir / self.research_dir / self.signals_file


@dataclass(frozen=True)
class SybilConfig:
    """Complete sybil pipeline configuration."""
    groups: dict[str, SybilGroupDef]
    thresholds: ThresholdConfig
    api: ApiConfig
    llm: LlmConfig
    paths: PathConfig
    loaded_from: str


def load_config(config_path: Optional[Path] = None) -> SybilConfig:
    """Load configuration from YAML file.
    
    Args:
        config_path: Optional explicit path. Defaults to config/sybil_groups.yaml.
    
    Returns:
        Frozen SybilConfig dataclass.
    
    Raises:
        FileNotFoundError: If config file doesn't exist.
        yaml.YAMLError: If YAML parsing fails.
    """
    if config_path is None:
        script_dir = Path(__file__).parent
        config_path = script_dir.parent / "config" / "sybil_groups.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # Parse groups
    groups: dict[str, SybilGroupDef] = {}
    for gid, gdata in raw.get("groups", {}).items():
        wallets = [
            WalletDef(address=w["address"], pseudonym=w["pseudonym"])
            for w in gdata.get("wallets", [])
        ]
        groups[gid] = SybilGroupDef(
            group_id=gid,
            label=gdata.get("label", ""),
            priority=gdata.get("priority", "low"),
            wallets=wallets,
        )

    # Parse thresholds
    th = raw.get("thresholds", {})
    thresholds = ThresholdConfig(
        no_bias_fade_min_no_usd=th.get("no_bias_fade", {}).get("min_no_usd", 50000.0),
        no_bias_fade_min_wallets=th.get("no_bias_fade", {}).get("min_wallets", 5),
        concentrated_follow_min_yes_usd=th.get("concentrated_follow", {}).get("min_yes_usd", 150000.0),
        concentrated_follow_min_wallets=th.get("concentrated_follow", {}).get("min_wallets", 2),
        concentrated_follow_min_yes_ratio=th.get("concentrated_follow", {}).get("min_yes_ratio", 0.15),
        manipulation_fade_min_wallets=th.get("manipulation_fade", {}).get("min_wallets", 10),
        manipulation_fade_max_avg_bet_usd=th.get("manipulation_fade", {}).get("max_avg_bet_usd", 400.0),
    )

    # Parse API config
    api_raw = raw.get("api", {})
    api = ApiConfig(
        data_api_base=api_raw.get("data_api_base", "https://data-api.polymarket.com"),
        trades_limit=api_raw.get("trades_limit", 100),
        request_timeout=api_raw.get("request_timeout", 15),
        positions_endpoint=api_raw.get("positions_endpoint", "/positions"),
        trades_endpoint=api_raw.get("trades_endpoint", "/v1/trades"),
    )

    # Parse LLM config
    llm_raw = raw.get("llm", {})
    llm = LlmConfig(
        url=llm_raw.get("url", "http://localhost:8080/v1/chat/completions"),
        model=llm_raw.get("model", "Qwen3.6-35B-A3B"),
        timeout=llm_raw.get("timeout", 300),
        fallback_strategy=llm_raw.get("fallback_strategy", "rule-based"),
    )

    # Parse paths
    paths_raw = raw.get("paths", {})
    paths = PathConfig(
        research_dir=paths_raw.get("research_dir", "research"),
        positions_file=paths_raw.get("positions_file", "sybil_positions.json"),
        signals_file=paths_raw.get("signals_file", "sybil_signal_queue.json"),
        intelligence_file=paths_raw.get("intelligence_file", "sybil_intelligence.json"),
        llm_strategy_file=paths_raw.get("llm_strategy_file", "sybil_llm_strategy.json"),
        entity_clusters_file=paths_raw.get("entity_clusters_file", "entity_clusters.json"),
    )

    return SybilConfig(
        groups=groups,
        thresholds=thresholds,
        api=api,
        llm=llm,
        paths=paths,
        loaded_from=str(config_path),
    )


def get_config() -> SybilConfig:
    """Get cached config or load fresh.
    
    Returns cached config if already loaded, otherwise loads from default path.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = load_config()
    return _CONFIG_CACHE


def reload_config(config_path: Optional[Path] = None) -> SybilConfig:
    """Force reload of configuration.
    
    Clears cache and loads fresh config. Useful for testing or config updates.
    """
    global _CONFIG_CACHE
    _CONFIG_CACHE = None
    return load_config(config_path)