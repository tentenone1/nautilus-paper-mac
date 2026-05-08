#!/usr/bin/env python3
"""Apply P1 integration fixes to wf_signal_proc.py."""
import re
from pathlib import Path

filepath = Path('strategies/wf_signal_proc.py')
content = filepath.read_text()

# 1. Add imports after existing imports (after line ~14)
import_block = '''
# ---------------------------------------------------------------------------
# P1 Integration - Manipulation Playbook, Whale Profiles, Jailbreak Strategies
# ---------------------------------------------------------------------------
from pathlib import Path as _Path

_MANIP_PLAYBOOK_PATH = _Path(__file__).resolve().parents[2] / "research" / "manipulation_playbook.json"
_WHALE_PROFILES_PATH = _Path(__file__).resolve().parents[2] / "research" / "whale_profiles.json"
_JAILBREAK_PATH = _Path(__file__).resolve().parents[2] / "research" / "jailbreak_strategies.json"

try:
    with open(_MANIP_PLAYBOOK_PATH, "r", encoding="utf-8") as f:
        _MANIPULATION_PLAYBOOK = json.load(f)
except FileNotFoundError:
    _MANIPULATION_PLAYBOOK = {"tactics": []}

try:
    with open(_WHALE_PROFILES_PATH, "r", encoding="utf-8") as f:
        _WHALE_PROFILES = json.load(f)
except FileNotFoundError:
    _WHALE_PROFILES = {"profiles": []}

try:
    with open(_JAILBREAK_PATH, "r", encoding="utf-8") as f:
        _JAILBREAK_STRATEGIES = json.load(f)
except FileNotFoundError:
    _JAILBREAK_STRATEGIES = {"strategies": []}

'''

# Insert after "from components.paper_execution import set_fill_price, get_fill_price"
if 'from components.paper_execution import set_fill_price, get_fill_price' in content:
    content = content.replace(
        'from components.paper_execution import set_fill_price, get_fill_price',
        'from components.paper_execution import set_fill_price, get_fill_price\n' + import_block
    )

# 2. Add helper functions after imports, before on_signal
helper_functions = '''

def _is_manipulation_signal(signal_data: dict) -> bool:
    """Check if signal matches manipulation playbook pattern."""
    whale_sig = signal_data.get("whale_sig", "") or signal_data.get("whale_name", "")
    if not whale_sig:
        return False
    for tactic in _MANIPULATION_PLAYBOOK.get("tactics", []):
        pattern = tactic.get("whale_sig", "")
        if pattern and pattern.lower() in whale_sig.lower():
            return True
    return False


def _is_fade_whale(whale_name: str) -> bool:
    """Check if whale has should_fade=True in profiles."""
    for profile in _WHALE_PROFILES.get("profiles", []):
        stats = profile.get("stats", {})
        if stats.get("name") == whale_name:
            profile_data = profile.get("profile", {})
            return bool(profile_data.get("should_fade", False))
    return False


def _get_strategy_confidence(strategy_name: str) -> float | None:
    """Get confidence for a jailbreak strategy."""
    for strat in _JAILBREAK_STRATEGIES.get("strategies", []):
        if strat.get("name") == strategy_name:
            return float(strat.get("confidence", 0))
    return None

'''

# Insert before def on_signal
content = content.replace('def on_signal(', helper_functions + '\ndef on_signal(')

# 3. Add checks in on_signal after blacklist checks (around line 106-110)
# Find the blacklist check block and insert after it
old_blacklist_block = '''    # REJECT: blacklisted whales
    if signal.whale_name in WHALE_BLACKLIST:
        log.info(f"REJECT blacklisted whale: {signal.whale_name}")
        return
    mc = getattr(signal, "market_category", "") or ""
    if signal.whale_name in SPORTS_WHALE_BLACKLIST and mc.lower() == "sports":
        log.info(f"REJECT sports-blacklisted whale: {signal.whale_name}")
        return'''

new_blacklist_block = '''    # REJECT: blacklisted whales
    if signal.whale_name in WHALE_BLACKLIST:
        log.info(f"REJECT blacklisted whale: {signal.whale_name}")
        return
    mc = getattr(signal, "market_category", "") or ""
    if signal.whale_name in SPORTS_WHALE_BLACKLIST and mc.lower() == "sports":
        log.info(f"REJECT sports-blacklisted whale: {signal.whale_name}")
        return

    # P1: Manipulation playbook check
    if _is_manipulation_signal({"whale_name": signal.whale_name, "whale_sig": getattr(signal, "whale_address", "")}):
        log.info(f"REJECT manipulation pattern: {signal.whale_name}")
        return

    # P1: Whale profile fade check
    if _is_fade_whale(signal.whale_name):
        log.info(f"FADE whale (profile): {signal.whale_name}")
        # Mark as fade instead of reject - system can use this for counter-trading
        return'''

content = content.replace(old_blacklist_block, new_blacklist_block)

# Write updated content
filepath.write_text(content)
print(f"✓ Updated {filepath} ({len(content)} chars)")
print(f"✓ Added: manipulation playbook loader, whale profiles fade check, helper functions")
print(f"✓ Integrated checks after blacklist validation")