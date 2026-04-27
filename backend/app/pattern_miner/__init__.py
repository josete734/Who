"""Pattern miner: generate probable username/email variants and verify them."""
from app.pattern_miner.miner import mine_patterns
from app.pattern_miner.username_variants import generate_username_variants
from app.pattern_miner.email_patterns import generate_email_variants
from app.pattern_miner.verifier import verify_candidate, VerifierResult

__all__ = [
    "mine_patterns",
    "generate_username_variants",
    "generate_email_variants",
    "verify_candidate",
    "VerifierResult",
]
