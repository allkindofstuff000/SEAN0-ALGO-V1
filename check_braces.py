import re

with open(r'C:\Users\ALGO\Desktop\SEAN-ALGO\web\static\index.html', encoding='utf-8') as f:
    html = f.read()

m = re.search(r'<script type="text/babel">(.*?)</script>', html, re.DOTALL)
s = m.group(1)

depth = 0
in_str = None
results = []

for lineno, line in enumerate(s.splitlines(), 1):
    line_start_depth = depth
    i = 0
    while i < len(line):
        c = line[i]
        if in_str:
            if c == '\\':
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in ('"', "'", '`'):
            in_str = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
        i += 1
    if depth != line_start_depth:
        results.append((lineno, line_start_depth, depth, line.strip()[:90]))

print(f"Final depth: {depth}")
print(f"Total lines that changed depth: {len(results)}")
print("\nLast 8 depth-changing lines:")
for lineno, d_before, d_after, txt in results[-8:]:
    print(f"  L{lineno:4d}  {d_before:3d} -> {d_after:3d}  | {txt}")
