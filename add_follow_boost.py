#!/usr/bin/env python3
"""Add FOLLOW boost logic to wf_signal_proc.py after FADE check."""
import pathlib

filepath = pathlib.Path('strategies/wf_signal_proc.py')
content = filepath.read_text()

# Add follow boost logic after fade check
follow_boost = '''
    # P2: Hidden partner boost (should_follow=True → confidence boost)
    if _is_follow_whale(signal.whale_name):
        original_conf = signal.confidence
        signal.confidence = min(1.0, signal.confidence * 1.25)  # 25% boost
        log.info(f"FOLLOW hidden partner: {signal.whale_name} | conf {original_conf:.0%} → {signal.confidence:.0%}")
        # Continue processing - don't return, just boost confidence

'''

# Find insertion point after fade check
old_block = '''# P1: Whale profile fade check
    if _is_fade_whale(signal.whale_name):
        log.info(f"FADE whale (profile): {signal.whale_name}")
        # Mark as fade instead of reject - system can use this for counter-trading
        return

    # REJECT: unknown whale signals with zero edge score (noise trades)'''

new_block = '''# P1: Whale profile fade check
    if _is_fade_whale(signal.whale_name):
        log.info(f"FADE whale (profile): {signal.whale_name}")
        # Mark as fade instead of reject - system can use this for counter-trading
        return
''' + follow_boost + '''
    # REJECT: unknown whale signals with zero edge score (noise trades)'''

if '# P2: Hidden partner boost' not in content:
    content = content.replace(old_block, new_block)
    filepath.write_text(content)
    print('✓ Added FOLLOW boost logic')
else:
    print('✓ FOLLOW boost logic already exists')