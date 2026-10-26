"""Deterministic text utilities: tokenizer, Jaccard similarity, and a small BM25 index."""

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[가-힣]+")
_HANGUL_RE = re.compile(r"[가-힣]+")


def tokenize(text: str) -> list[str]:
    """Split text into deterministic search tokens.

    Latin text is lowercased into `[a-z0-9_]+` words. A Hangul run is kept whole and followed by
    its character bigrams, so a query word can match the same word with a particle attached.

    Args:
        text: Text to tokenize.

    Returns:
        Tokens in text order.
    """
    tokens: list[str] = []
    for m in _TOKEN_RE.finditer(text.lower()):
        word = m.group()
        tokens.append(word)
        if _HANGUL_RE.fullmatch(word):
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity of the two texts' token sets; 0.0 when both are empty."""
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class BM25:
    """Small in-memory BM25 index with a positive IDF (works for tiny corpora).

    Args:
        k1: Term-frequency saturation.
        b: Length normalisation.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._tf: dict[str, Counter[str]] = {}
        self._len: dict[str, int] = {}
        self._df: Counter[str] = Counter()

    def add(self, doc_id: str, text: str) -> None:
        """Index one document.

        Raises:
            ValueError: If `doc_id` was already added.
        """
        if doc_id in self._tf:
            raise ValueError(f"duplicate doc id: {doc_id}")
        tokens = tokenize(text)
        tf = Counter(tokens)
        self._tf[doc_id] = tf
        self._len[doc_id] = len(tokens)
        self._df.update(tf.keys())

    def scores(self, query: str) -> dict[str, float]:
        """Score every document against `query`.

        Query tokens are visited in sorted order so float sums do not depend on hash seeds.

        Returns:
            Document id to score, for documents scoring above zero only.
        """
        n = len(self._tf)
        q_tokens = sorted(set(tokenize(query)))  # fixed order: float sums must not depend on hash seed
        if n == 0 or not q_tokens:
            return {}
        avgdl = sum(self._len.values()) / n
        out: dict[str, float] = {}
        for doc_id, tf in self._tf.items():
            score = 0.0
            for tok in q_tokens:
                f = tf.get(tok, 0)
                if f == 0:
                    continue
                df = self._df[tok]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                norm = 1 - self.b + (self.b * self._len[doc_id] / avgdl if avgdl else 0.0)
                score += idf * f * (self.k1 + 1) / (f + self.k1 * norm)
            if score > 0:
                out[doc_id] = score
        return out
