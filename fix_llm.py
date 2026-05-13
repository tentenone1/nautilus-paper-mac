#!/usr/bin/env python3
with open('strategies/whale_follower.py', 'r') as f:
    lines = f.readlines()

# Fix the corrupted rfind line
# Current state (lines 1054-1057, 0-indexed 1053-1056):
# 1054: '                # Extract score after last </think> (MiniMax thinking tags)\n'
# 1055: '                last_close = raw.rfind("\n'
# 1056: '\n'
# 1057: '\")\n'
# Should be:
# 1054: '                # Extract score after last </think> (MiniMax thinking tags)\n'
# 1055: '                last_close = raw.rfind("\n
</think>\n")\n'

# Replace lines 1055-1057 (0-indexed 1054-1056) with single correct line
lines[1054] = '                last_close = raw.rfind("\n</think>\n")\n'

# The string literal "\n
</think>\n" contains a newline, the closing </think> tag, and a newline

with open('strategies/whale_follower.py', 'w') as f:
    f.writelines(lines)

print("Fixed lines 1055-1057")

# Verify
with open('strategies/whale_follower.py', 'r') as f:
    lines = f.readlines()
for i in range(1053, 1062):
    print(f'{i+1}: {repr(lines[i])}')