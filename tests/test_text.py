import os
import subprocess
import sys

from harness.text import BM25, jaccard, tokenize


def test_tokenize_english():
    assert tokenize("Fix the Vendor_module!") == ["fix", "the", "vendor_module"]


def test_tokenize_korean_bigrams():
    assert {"벤더", "더모", "모듈"} <= set(tokenize("벤더모듈 수정"))


def test_tokenize_hangul_run_then_bigrams_in_order():
    assert tokenize("벤더모 a") == ["벤더모", "벤더", "더모", "a"]


def test_tokenize_single_hangul_char_emits_only_itself():
    assert tokenize("수 x") == ["수", "x"]


def test_jaccard():
    assert jaccard("a b", "b c") == 1 / 3
    assert jaccard("", "!!") == 0.0
    assert jaccard("a a", "a") == 1.0


def test_bm25_ranks_matching_doc_first():
    idx = BM25()
    idx.add("d1", "fix the vendor module bug")
    idx.add("d2", "vendor")
    idx.add("d3", "unrelated database migration")
    scores = idx.scores("vendor module")
    assert "d3" not in scores
    assert max(scores, key=scores.get) == "d1"
    assert scores["d1"] > scores["d2"] > 0


def test_bm25_korean_query_matches():
    idx = BM25()
    idx.add("k1", "벤더모듈 수정은 금지")
    idx.add("k2", "배포 일정 공유")
    scores = idx.scores("벤더 모듈 고쳐도 되나")
    assert scores["k1"] > 0
    assert "k2" not in scores


def test_bm25_empty_index_or_query():
    idx = BM25()
    assert idx.scores("anything") == {}
    idx.add("d", "text")
    assert idx.scores("") == {}
    assert idx.scores("!!!") == {}


def test_bm25_positive_idf_on_tiny_corpus():
    idx = BM25()
    idx.add("d1", "vendor")
    assert idx.scores("vendor")["d1"] > 0


_DETERMINISM_SCRIPT = """
import random
from harness.text import BM25

rng = random.Random(7)
words = [f"w{i}" for i in range(25)] + ["벤더모듈", "수정", "금지"]
idx = BM25()
for i in range(40):
    idx.add(f"d{i}", " ".join(rng.choice(words) for _ in range(rng.randint(5, 30))))
query = " ".join(words)
print(repr(sorted(idx.scores(query).items())))
"""


def test_bm25_scores_identical_across_hash_seeds():
    outputs = set()
    for seed in ("1", "2", "3", "4", "5", "6"):
        proc = subprocess.run(
            [sys.executable, "-c", _DETERMINISM_SCRIPT],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, check=True,
        )
        outputs.add(proc.stdout)
    assert len(outputs) == 1
