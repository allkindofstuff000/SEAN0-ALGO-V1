
import sys
sys.stdout.reconfigure(encoding='utf-8')
src = open('static/index.html', encoding='utf-8').read()
script_start = src.find('<script type="text/babel">')
script_end   = src.rfind('</script>')
jsx = src[script_start:script_end]
depth = 0
i = 0
last_open_pos = 0
while i < len(jsx):
    ch = jsx[i]
    if ch in ('"', "'"):
        q = ch; i += 1
        while i < len(jsx) and jsx[i] != q:
            if jsx[i] == chr(92): i += 1
            i += 1
    elif ch == chr(96):
        i += 1
        while i < len(jsx) and jsx[i] != chr(96):
            if jsx[i] == chr(92): i += 1
            i += 1
    elif ch == chr(123):
        depth += 1; last_open_pos = i
    elif ch == chr(125):
        depth -= 1
    i += 1
print("Final depth:", depth)
if depth != 0:
    line_n = jsx[:last_open_pos].count(chr(10)) + 1
    abs_line = src[:script_start].count(chr(10)) + line_n
    print(f"Unmatched open at abs line: {abs_line}")
    lines = src.split(chr(10))
    for l in range(abs_line-4, abs_line+4):
        if 0 <= l < len(lines):
            print(f"{l+1:4d}: {lines[l][:160]}")
else:
    print("[OK] Braces balanced")
