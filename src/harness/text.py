"""Deterministic text utilities: tokenizer, Jaccard similarity, and a small BM25 index."""

import math
import re
import unicodedata
from collections import Counter
from typing import Literal

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


_MIN_SECRET_CHARS = 4


def _is_separator(ch: str) -> bool:
    """Whitespace, an underscore, a Unicode dash or hyphen (category Pd), or the minus sign U+2212."""
    return ch.isspace() or ch in {"_", "\u2212"} or unicodedata.category(ch) == "Pd"


def normalize_secret(text: str) -> str:
    """Normalize text for secret matching: NFKC, casefold, then drop separators.

    The dropped separators are whitespace, underscores, every Unicode dash or hyphen (category
    Pd: U+2010-U+2015, U+2011 non-breaking hyphen, U+FE63, U+FF0D, ...) and the minus sign
    U+2212. These are the characters a rewrite swaps freely (`vault anchor 4102`,
    `vault-anchor 4102`, `vault_anchor_4102`) without changing what a reader sees. Dots, slashes
    and other punctuation stay, since they carry meaning in versions, paths and hosts.

    Args:
        text: Text or marker to normalize.

    Returns:
        The normalized text.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if not _is_separator(ch))


def secret_needle(marker: str) -> tuple[Literal["plain", "normalized"], str] | None:
    """How `contains_secret` looks for `marker`: in which form of the text, and the needle.

    A marker that normalizes to nothing never matches (None). One that normalizes to fewer than
    4 characters is looked for as a plain NFKC + casefold substring ("plain"), others as a
    `normalize_secret` substring ("normalized").
    """
    needle = normalize_secret(marker)
    if not needle:
        return None
    if len(needle) < _MIN_SECRET_CHARS:
        return "plain", unicodedata.normalize("NFKC", marker).casefold()
    return "normalized", needle


def secret_forms(text: str) -> tuple[str, str]:
    """The two forms of `text` that `secret_needle` needles are looked for in: (plain, normalized).

    For checking many markers against one text: compute these once, then test
    `needle in forms[0 if kind == "plain" else 1]`.
    """
    return unicodedata.normalize("NFKC", text).casefold(), normalize_secret(text)


def contains_secret(text: str, marker: str) -> bool:
    """Whether a secret marker appears in text once both are normalized.

    A marker that normalizes to fewer than 4 characters is too short to survive separator
    removal (`a-b` would match any `ab`), so it is matched as a plain NFKC + casefold substring
    with separators kept. A marker that normalizes to nothing (`---`, blank) never matches.
    See `secret_needle`.

    Args:
        text: Text to search.
        marker: The secret string.

    Returns:
        True when the marker is found.
    """
    found = secret_needle(marker)
    if found is None:
        return False
    kind, needle = found
    if kind == "plain":
        return needle in unicodedata.normalize("NFKC", text).casefold()
    return needle in normalize_secret(text)


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity of the two texts' token sets; 0.0 when both are empty."""
    return set_jaccard(set(tokenize(a)), set(tokenize(b)))


def set_jaccard(sa: set[str], sb: set[str]) -> float:
    """`jaccard` of two token sets already made, for comparing one text with many."""
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def set_jaccard_at_least(sa: set[str], sb: set[str], tau: float) -> bool:
    """Whether `set_jaccard(sa, sb) >= tau`, without the intersection when the sizes rule it out.

    The Jaccard ratio is at most min(|a|, |b|) / max(|a|, |b|), and float division is monotonic,
    so when that bound is below `tau` the ratio is too. Otherwise the union's size is taken as
    |a| + |b| - |a & b|, the same integer `set_jaccard` divides by, so the answer is identical.
    """
    la, lb = len(sa), len(sb)
    if la == 0 and lb == 0:
        return 0.0 >= tau
    if min(la, lb) / max(la, lb) < tau:
        return False
    inter = len(sa & sb)
    return inter / (la + lb - inter) >= tau


class JaccardIndex:
    """Incremental similarity join: the earlier sets with Jaccard >= `tau` with each new set.

    It avoids comparing a new set with every earlier one, yet `add` gives the same answer as
    checking every earlier set with `set_jaccard_at_least`, which still makes the final call;
    the filters below only skip sets that cannot pass it.

    Prefix filtering: with all sets sorted in one fixed token order, two sets that share at least
    `a` tokens share one among the first |A| - a + 1 tokens of each, and a pair with Jaccard
    >= tau shares at least ceil(tau * |A|) tokens. Only those prefixes are indexed and probed.
    Positional filtering (PPJoin): at a shared prefix token at positions i and j, the pair shares
    at most (shared tokens found so far) + 1 + min(|A| - i - 1, |B| - j - 1) tokens, since every
    shared token earlier in the order has been found by then; a pair whose bound is below the
    overlap tau * (|A| + |B|) / (1 + tau) that Jaccard >= tau needs is dropped.

    The order puts the tokens in fewest earlier sets first, so prefixes hold rare tokens and
    probe short lists. It is re-taken, and every set re-indexed, each time the number of sets
    doubles; between two re-orderings every set is indexed in the same order, which the filters
    need. Bounds are taken a hair loose, so float rounding can only keep a pair, never drop one.

    Args:
        tau: Similarity threshold; at or below 0 every pair passes and each set is compared
            with every earlier one.
    """

    _FIRST_REORDER: int = 32
    _EPS: float = 1e-9

    def __init__(self, tau: float) -> None:
        self.tau: float = tau
        self._sets: list[set[str]] = []
        self._df: Counter[str] = Counter()  # token -> number of sets holding it
        self._rank: dict[str, int] = {}  # the document frequencies the current order uses
        self._index: dict[str, list[tuple[int, int]]] = {}  # token -> (set, position in its order)
        self._next_reorder: int = self._FIRST_REORDER

    def __len__(self) -> int:
        return len(self._sets)

    def _prefix(self, tokens: set[str]) -> list[str]:
        """The first tokens of `tokens` in the current order that the prefix filter needs."""
        if not tokens:
            return []
        rank = self._rank
        ordered = sorted(tokens, key=lambda t: (rank.get(t, 0), t))
        shared = max(1, math.ceil(self.tau * len(tokens) - self._EPS))
        return ordered[: max(0, len(tokens) - shared + 1)]

    def _candidates(self, tokens: set[str]) -> list[int]:
        """Earlier sets that pass the prefix and positional filters with `tokens`, in order."""
        la, sets, ratio = len(tokens), self._sets, self.tau / (1 + self.tau)
        found: dict[int, int] = {}  # set -> shared prefix tokens found so far
        dropped: set[int] = set()
        for i, t in enumerate(self._prefix(tokens)):
            for j, pos in self._index.get(t, ()):
                if j in dropped:
                    continue
                lb = len(sets[j])
                count = found.get(j, 0)
                if count + 1 + min(la - i - 1, lb - pos - 1) < math.ceil(ratio * (la + lb) - self._EPS):
                    dropped.add(j)
                    _ = found.pop(j, None)
                else:
                    found[j] = count + 1
        return sorted(found)

    def add(self, tokens: set[str]) -> list[int]:
        """Add `tokens` and return the positions of the earlier sets similar to it, ascending."""
        position = len(self._sets)
        candidates = self._candidates(tokens) if self.tau > 0 else range(position)
        similar = [j for j in candidates if set_jaccard_at_least(tokens, self._sets[j], self.tau)]
        self._sets.append(tokens)
        if self.tau > 0:
            self._df.update(tokens)
            if len(self._sets) >= self._next_reorder:
                self._reorder()
            else:
                self._insert(position, tokens)
        return similar

    def _insert(self, position: int, tokens: set[str]) -> None:
        for i, t in enumerate(self._prefix(tokens)):
            self._index.setdefault(t, []).append((position, i))

    def _reorder(self) -> None:
        self._rank = dict(self._df)
        self._index = {}
        for position, tokens in enumerate(self._sets):
            self._insert(position, tokens)
        self._next_reorder *= 2


def counted(text: str) -> tuple[Counter[str], int]:
    """A text's term counts and token count, as `BM25.add` indexes it."""
    tokens = tokenize(text)
    return Counter(tokens), len(tokens)


class BM25:
    """Small in-memory BM25 index with a positive IDF (works for tiny corpora).

    Args:
        k1: Term-frequency saturation.
        b: Length normalisation.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1: float = k1
        self.b: float = b
        self._tf: dict[str, Counter[str]] = {}
        self._len: dict[str, int] = {}
        self._df: Counter[str] = Counter()

    def add(self, doc_id: str, text: str) -> None:
        """Index one document.

        Raises:
            ValueError: If `doc_id` was already added.
        """
        self.add_counted(doc_id, *counted(text))

    def add_counted(self, doc_id: str, tf: Counter[str], length: int) -> None:
        """Index one document from its term counts and token count (see `counted`).

        A caller that indexes the same text for many queries tokenizes it once this way. `tf` is
        not copied and must not change afterwards.

        Raises:
            ValueError: If `doc_id` was already added.
        """
        if doc_id in self._tf:
            raise ValueError(f"duplicate doc id: {doc_id}")
        self._tf[doc_id] = tf
        self._len[doc_id] = length
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
