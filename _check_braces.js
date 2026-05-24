const fs = require('fs');
const src = fs.readFileSync('web/static/index.html', 'utf8');
const match = src.match(/<script type="text\/babel">([\s\S]*?)<\/script>/);
const code = match[1];
let depth = 0, i = 0, opens = 0, closes = 0;
const DQ = '"', SQ = "'", BT = '`', BS = '\\';
while (i < code.length) {
  const ch = code[i];
  if (ch === '/' && code[i+1] === '/') {
    while (i < code.length && code[i] !== '\n') i++;
  } else if (ch === '/' && code[i+1] === '*') {
    i += 2;
    while (i < code.length && !(code[i] === '*' && code[i+1] === '/')) i++;
    i += 2;
  } else if (ch === DQ) {
    i++;
    while (i < code.length && code[i] !== DQ) { if (code[i] === BS) i++; i++; }
  } else if (ch === SQ) {
    i++;
    while (i < code.length && code[i] !== SQ) { if (code[i] === BS) i++; i++; }
  } else if (ch === BT) {
    i++;
    while (i < code.length && code[i] !== BT) { if (code[i] === BS) i++; i++; }
  } else if (ch === '{') {
    opens++; depth++;
  } else if (ch === '}') {
    closes++; depth--;
  }
  i++;
}
console.log('opens=' + opens + ' closes=' + closes + ' diff=' + (opens - closes) + ' final_depth=' + depth);
if (opens === closes) {
  console.log('[OK] Braces are balanced (excluding strings/comments)');
} else {
  console.log('[WARN] Brace mismatch!');
}
