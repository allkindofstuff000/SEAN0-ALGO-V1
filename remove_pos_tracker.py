import sys
sys.stdout.reconfigure(encoding='utf-8')

path = 'C:/Users/ALGO/Desktop/SEAN-ALGO/web/static/index.html'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

orig_len = len(content)

# ── Step 1: Remove CSS block ──
css_start = content.find('    /* \u2500\u2500 TP/SL Position Tracker')
css_end   = content.find('    /* \u2500\u2500 Settings Modal', css_start)
assert css_start != -1 and css_end != -1, 'FAIL step1'
content = content[:css_start] + content[css_end:]
print('Step 1 OK: CSS block removed')

# ── Step 2: Remove IcoPos SVG line ──
idx = content.find('const IcoPos  = () => <svg')
assert idx != -1, 'FAIL step2'
line_end = content.find('\n', idx) + 1
content = content[:idx] + content[line_end:]
print('Step 2 OK: IcoPos line removed')

# ── Step 3: Remove the 6 pos/drag refs ──
REFS_START = '  const posTPLineRef    = useRef(null);'
REFS_END   = "  const dragFieldRef    = useRef(null); // 'tp' | 'sl' | 'entry'"
idx_s = content.find(REFS_START)
idx_e = content.find(REFS_END, idx_s)
assert idx_s != -1 and idx_e != -1, 'FAIL step3'
end_of_line = content.find('\n', idx_e) + 1
content = content[:idx_s] + content[end_of_line:]
print('Step 3 OK: refs removed')

# ── Step 4: Remove position state block ──
STATE_START = '  // \u2500\u2500 Position tracker \u2500'
STATE_END   = "  const [posForm,      setPosForm]     = useState({ dir:'long', entry:'', tp:'', sl:'', qty:'1' });"
idx_s = content.find(STATE_START)
idx_e = content.find(STATE_END, idx_s)
assert idx_s != -1 and idx_e != -1, 'FAIL step4'
end_of_line = content.find('\n', idx_e) + 1
content = content[:idx_s] + content[end_of_line:]
print('Step 4 OK: state block removed')

# ── Step 5: Remove position useEffect + startPriceDrag ──
EFF_START = '  // \u2500\u2500 Position tracker: keep posRef in sync + draw price lines \u2500'
EFF_END   = '  // \u2500\u2500 Update Y-coordinates for position overlay boxes'
idx_s = content.find(EFF_START)
idx_e = content.find(EFF_END, idx_s)
assert idx_s != -1 and idx_e != -1, 'FAIL step5'
content = content[:idx_s] + content[idx_e:]
print('Step 5 OK: useEffect + startPriceDrag removed')

# ── Step 6: Remove _updatePosCoords function ──
UPCOORDS_START = '  // \u2500\u2500 Update Y-coordinates for position overlay boxes \u2500'
UPCOORDS_END   = '  // \u2500\u2500 Chart creation (once on mount)'
idx_s = content.find(UPCOORDS_START)
idx_e = content.find(UPCOORDS_END, idx_s)
assert idx_s != -1 and idx_e != -1, 'FAIL step6'
content = content[:idx_s] + content[idx_e:]
print('Step 6 OK: _updatePosCoords removed')

# ── Step 7: Fix chart subscriptions ──
OLD7 = '      _updatePosCoords();\n    });\n    chart.timeScale().subscribeVisibleTimeRangeChange(() => _updatePosCoords());'
NEW7 = '    });'
assert content.find(OLD7) != -1, 'FAIL step7'
content = content.replace(OLD7, NEW7, 1)
print('Step 7 OK: subscription calls removed')

# ── Step 8: Remove tick handler PnL block ──
OLD8 = (
    '            // Update position P&L\n'
    '            try {\n'
    '              const pos = posRef.current;\n'
    '              if (pos) {\n'
    "                const mult = pos.dir === 'long' ? 1 : -1;\n"
    '                setOpenPnl((c.close - pos.entry) * pos.qty * mult);\n'
    '                _updatePosCoords();\n'
    '              }\n'
    '            } catch {}'
)
assert content.find(OLD8) != -1, 'FAIL step8'
content = content.replace(OLD8, '', 1)
print('Step 8 OK: PnL update block removed')

# ── Step 9: Remove TP/SL toolbar button (with preceding separator) ──
# The block starts with a <div className="tv-sep" /> separator then the button
TPSL_BTN_START = '        <div className="tv-sep" />\n        <button\n          className={`tv-pos-btn'
TPSL_BTN_END   = '          <IcoPos />TP/SL\n        </button>'
idx_s = content.find(TPSL_BTN_START)
idx_e = content.find(TPSL_BTN_END, idx_s)
assert idx_s != -1 and idx_e != -1, 'FAIL step9 idx_s=%d idx_e=%d' % (idx_s, idx_e)
end_after = content.find('\n', idx_e + len(TPSL_BTN_END)) + 1
content = content[:idx_s] + content[end_after:]
print('Step 9 OK: TP/SL toolbar button removed')

# ── Step 10: Remove TV-Style Draggable Position Overlay JSX block ──
OVERLAY_START = '        {/* \u2500\u2500 TV-Style Draggable Position Overlay \u2500\u2500 */}'
# The block ends with: })()}\n and then the closing </div> of tv-chart-wrap
OVERLAY_IIFE_END = '        })()}\n'
idx_s = content.find(OVERLAY_START)
idx_e = content.find(OVERLAY_IIFE_END, idx_s)
assert idx_s != -1 and idx_e != -1, 'FAIL step10 idx_s=%d idx_e=%d' % (idx_s, idx_e)
# Remove from OVERLAY_START through end of OVERLAY_IIFE_END line
end_overlay = idx_e + len(OVERLAY_IIFE_END)
content = content[:idx_s] + content[end_overlay:]
print('Step 10 OK: overlay JSX removed')

# ── Step 11: Remove Position Setup Modal JSX block ──
MODAL_START  = '      {/* \u2500\u2500 Position Setup Modal \u2500\u2500 */}'
MODAL_END    = '      {/* \u2500\u2500 Settings Modal \u2500\u2500 */}'
idx_s = content.find(MODAL_START)
idx_e = content.find(MODAL_END, idx_s)
assert idx_s != -1 and idx_e != -1, 'FAIL step11 idx_s=%d idx_e=%d' % (idx_s, idx_e)
content = content[:idx_s] + content[idx_e:]
print('Step 11 OK: Position Setup Modal removed')

print()
print('All steps done.')
print('Original length: %d chars' % orig_len)
print('New length:      %d chars' % len(content))
print('Removed:         %d chars' % (orig_len - len(content)))

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('File saved successfully.')
