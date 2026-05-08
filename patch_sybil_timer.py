#!/usr/bin/env python3
"""Patch whale_follower.py to add sybil monitoring in _on_exit_timer()."""
import pathlib

filepath = pathlib.Path('strategies/whale_follower.py')
content = filepath.read_text()

# Add import after existing imports
import_addition = '''
# P2: Sybil monitoring integration
try:
    from scripts.sybil_monitor_wrapper import run_sybil_monitoring
except ImportError:
    run_sybil_monitoring = None

'''

# Find insertion point after "from components.paper_execution import set_fill_price, get_fill_price"
if 'from components.paper_execution import set_fill_price, get_fill_price' in content:
    content = content.replace(
        'from components.paper_execution import set_fill_price, get_fill_price\n',
        'from components.paper_execution import set_fill_price, get_fill_price\n' + import_addition
    )

# Add sybil monitoring call in _on_exit_timer() after resolution polling
sybil_monitor_block = '''
        # P2: Sybil intelligence monitoring (every timer tick)
        if run_sybil_monitoring:
            try:
                sybil_report = run_sybil_monitoring()
                if sybil_report and not sybil_report.get("error"):
                    groups_active = len(sybil_report.get("meta_whale_groups", {}).get("groups", []))
                    if groups_active > 0:
                        self.log.info(f"[SYBIL] {groups_active} meta-whale groups tracked")
            except Exception as e:
                self.log.error(f"Sybil monitoring failed: {e}")

'''

# Find insertion point - after the resolution polling block (around line 1788)
# Look for the pattern: "# Instrument recycle:" which comes after resolution polling
if '        # Instrument recycle:' in content:
    content = content.replace(
        '        # Instrument recycle:',
        sybil_monitor_block + '        # Instrument recycle:'
    )

filepath.write_text(content)
print(f"✓ Patched {filepath} ({len(content)} chars)")
print(f"✓ Added: sybil monitoring import and call in _on_exit_timer()")