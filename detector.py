import re
import base58 as _base58

EVM_PATTERN = re.compile(r'\b(0x[a-fA-F0-9]{40})\b')
SOL_PATTERN = re.compile(r'\b([1-9A-HJ-NP-Za-km-z]{32,44})\b')

# Well-known Solana program addresses to ignore
_SOL_NOISE = {
    "So11111111111111111111111111111111111111112",   # wrapped SOL
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL token program
    "11111111111111111111111111111111",              # system program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe8bv", # associated token program
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s", # metaplex metadata
}


def _is_solana_mint(candidate: str) -> bool:
    # Real Solana public keys encode to exactly 32 bytes and are 43-44 base58 chars
    if not (43 <= len(candidate) <= 44):
        return False
    if candidate in _SOL_NOISE:
        return False
    try:
        decoded = _base58.b58decode(candidate)
        if len(decoded) != 32:
            return False
    except Exception:
        return False
    # Entropy guard — real addresses don't repeat a handful of chars
    if len(set(candidate)) < 10:
        return False
    return True


def extract_cas(text: str) -> list[tuple[str, str]]:
    """Return list of (address, chain) where chain is 'EVM' or 'SOL'."""
    results: list[tuple[str, str]] = []

    for addr in EVM_PATTERN.findall(text):
        results.append((addr, "EVM"))

    # Scrub EVM matches before SOL scan to avoid hex overlap
    cleaned = EVM_PATTERN.sub(" ", text)
    for candidate in SOL_PATTERN.findall(cleaned):
        if _is_solana_mint(candidate):
            results.append((candidate, "SOL"))

    return results
