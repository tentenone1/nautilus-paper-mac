#!/usr/bin/env python3
"""Add should_follow logic to wf_signal_proc.py."""
import pathlib

filepath = pathlib.Path('strategies/wf_signal_proc.py')
content = filepath.read_text()

# Add _is_follow_whale function after _is_fade_whale
follow_function = '''

def _is_follow_whale(whale_name: str) -> bool:
    """Check if whale has should_follow=True in profiles (hidden partner)."""
    for profile in _WHALE_PROFILES.get("profiles", []):
        stats = profile.get("stats", {})
        if stats.get("name") == whale_name:
            profile_data = profile.get("profile", {})
            return bool(profile_data.get("should_follow", False))
    return False


'''

if 'def _is_follow_whale' not in content:
    # Insert after _is_fade_whale function
    content = content.replace(
        'def _get_strategy_confidence(strategy_name: str) -> float | None:',
        follow_function + 'def _get_strategy_confidence(strategy_name: str) -> float | None:'
    )
    filepath.write_text(content)
    print('✓ Added _is_follow_whale function')
else:
    print('✓ _is_follow_whale already exists')