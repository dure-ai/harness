"""Registered external datasets, by name."""

from harness.bench.dataset import Dataset
from harness.bench.datasets.longmemeval import LongMemEvalS

DATASETS: dict[str, Dataset] = {ds.name: ds for ds in (LongMemEvalS(),)}
