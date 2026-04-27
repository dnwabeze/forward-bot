// Mirrors detector.py — same EVM + Solana CA extraction logic

const EVM_PATTERN = /\b(0x[a-fA-F0-9]{40})\b/g;
const SOL_PATTERN = /\b([1-9A-HJ-NP-Za-km-z]{32,44})\b/g;

const SOL_NOISE = new Set([
  'So11111111111111111111111111111111111111112',
  'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA',
  '11111111111111111111111111111111',
  'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe8bv',
  'metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s',
]);

const BASE58_RE = /^[1-9A-HJ-NP-Za-km-z]+$/;

function isSolanaMint(candidate) {
  if (candidate.length < 43 || candidate.length > 44) return false;
  if (SOL_NOISE.has(candidate)) return false;
  if (new Set(candidate).size < 10) return false;
  if (!BASE58_RE.test(candidate)) return false;
  return true;
}

export function extractCAs(text) {
  const results = [];

  // EVM addresses
  for (const m of text.matchAll(EVM_PATTERN)) {
    results.push({ ca: m[1], chain: 'EVM' });
  }

  // Solana — scan cleaned text so hex chars don't collide with EVM matches
  const cleaned = text.replace(EVM_PATTERN, ' ');
  for (const m of cleaned.matchAll(SOL_PATTERN)) {
    if (isSolanaMint(m[1])) {
      results.push({ ca: m[1], chain: 'SOL' });
    }
  }

  return results;
}
