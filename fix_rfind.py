import base64

with open('strategies/whale_follower.py', 'r') as f:
    content = f.read()

old = 'last_close = raw.rfind("'\n<Open></Close>\n)"'
# The corrupted version is multiple lines and has no quote on line 1:0
new = 'last_close = raw.rfind("'\n</Open></Close>\n)"'


if old in content:
    content = content.replace(old, new)
    with open('strategies/whale_follower.py', 'w') as f:
        f.write(content)
    print("Fixed!")
else:
    print("Okay, not found. Dumpy base64 encoded version:")
    print(base64.b64encode(old.utf8()))