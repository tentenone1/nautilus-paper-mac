#!/usr/bin/env python3
"""Fix the corrupted rfind line in whale_follower.py"""

with open('strategies/whale_follower.py', 'rb') as f:
    data = f.read()

# Find the corrupted pattern: raw.rfind(" on its own line, then </Close> on next, then ") on third
# The pattern we need to find spans these bytes
# We want to replace 3 lines (1055, 1056, 1057) with a single corrected line

# The CORRECT version should be:
# last_close = raw.rfind("\n
# </Close>
# \")
# i.e., a string literal: "\n</Close>\n" - newline, </Close> tag, newline

# Let's find the location of "if last_close" and work backwards
target = b'                if last_close'
pos = data.find(target)
if pos < 0:
    print("Could not find 'if last_close'")
    exit(1)

# We need to go back to find the start of line 1055
# Count back from 'if last_close' to find the newline before '                last_close'
# Working backwards from 'if last_close' position
search_back = data[:pos]
last_newline_pos = search_back.rfind(b'\n')
print(f"Newline before 'if last_close' at byte {last_newline_pos}")

# The line before 'if last_close' is line 1058 which starts with '                if last_close != -1:'
# Actually let's trace more carefully...

# What we want to do:
# Line 1058 in display (1-indexed) starts with '                if last_close != -1:'
# But in our file, the structure is:
# [line 1055] '                last_close = raw.rfind("\n'
# [line 1056] '\n'
# [line 1057] '\")\n'
# [line 1058] '                if last_close != -1:\n'

# So the bytes from 'last_close = raw.rfind(' to just before 'if last_close' should be:
# last_close = raw.rfind("\n</Close>\n")\n

# Find the 'last_close = raw.rfind(' pattern
llfind = data.find(b'last_close = raw.rfind(')
print(f"last_close = raw.rfind( found at {llfind}")
if llfind < 0:
    print("Could not find pattern")
    exit(1)

# The section we need to replace goes from llfind to just before 'if last_close'
# Let's find where we should cut
old_end_search = data[:pos]
# Find the newline just before the line containing 'if last_close != -1:'
# Actually let's find the start of that line
line_start_for_if = old_end_search.rfind(b'\n') + 1
print(f"Line start for 'if last_close' at byte {line_start_for_if}")

# Now find start of 'last_close = raw.rfind(' line
line_start_last_close = data[:llfind].rfind(b'\n') + 1
print(f"Line start for 'last_close = raw.rfind(' at byte {line_start_last_close}")

# The OLD bytes span from line_start_last_close to line_start_for_if (exclusive, so to that newline)
# Let me trace byte by byte from llfind backwards to find where this statement actually starts
# Search for the newline before '                last_close'
search_from = data[:llfind]
last_nl = search_from.rfind(b'\n')
print(f"Previous newline before llfind at {last_nl}")

# So line_start = last_nl + 1
line_start = last_nl + 1
print(f"Line content starts at byte {line_start}")

# The statement from line_start to line_start_for_if (exclusive) should be replaced
# Let's verify by printing the content
chunk = data[line_start:pos]
print(f"Chunk to replace ({len(chunk)} bytes):")
print(repr(chunk))

# Now create the correct replacement
# We want: '                last_close = raw.rfind("' + '\n' + '</Close>' + '\n' + '")\n'
# i.e. a string containing: newline, </Close> tag, newline
correct_chunk = b'                last_close = raw.rfind("\n</Close>\n")\n'

print(f"\nCorrect chunk ({len(correct_chunk)} bytes):")
print(repr(correct_chunk))

# Replace
new_data = data[:line_start] + correct_chunk + data[pos:]
with open('strategies/whale_follower.py', 'wb') as f:
    f.write(new_data)

print("\nFile updated!")

# Verify
import subprocess
result = subprocess.run(['python3', '-m', 'py_compile', 'strategies/whale_follower.py'], 
                       capture_output=True, text=True)
if result.returncode == 0:
    print("Syntax OK!")
else:
    print(f"Syntax Error: {result.stderr}")
