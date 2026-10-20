"""Engine registry: build a baseline by name or load a Python engine from 'package.module:ClassName'."""

import importlib

from harness.baselines.oracle import OracleEngine
from harness.baselines.simple import AllStoreEngine, NoneEngine, RepeatNEngine
from harness.config import EvalConfig
from harness.contract import BaseEngine
from harness.scenario.model import Scenario

BASELINES = ("none", "all_store", "repeat_n", "oracle")


def make_engine(name: str, scenario: Scenario, cfg: EvalConfig) -> BaseEngine:
    """Build a fresh engine instance for one scenario.

    Args:
        name: A baseline name (`BASELINES` or "all_store_noacl"), or "package.module:ClassName"
            for a Python engine constructed with no arguments.
        scenario: The scenario the engine will run; only the oracle reads it.
        cfg: Evaluation config (repeat_n parameters).

    Returns:
        A new engine; callers must not reuse it across scenarios.

    Raises:
        ValueError: If `name` is not a known baseline and has no ":".
        TypeError: If the imported class does not produce a `BaseEngine`.
    """
    if ":" in name:
        module_name, _, class_name = name.partition(":")
        engine = getattr(importlib.import_module(module_name), class_name)()
        if not isinstance(engine, BaseEngine):
            raise TypeError(f"{name} did not produce a BaseEngine (got {type(engine).__name__})")
        return engine
    match name:
        case "none":
            return NoneEngine()
        case "all_store":
            return AllStoreEngine()
        case "all_store_noacl":
            return AllStoreEngine(acl=False)
        case "repeat_n":
            return RepeatNEngine(cfg.repeat_n.n, cfg.repeat_n.tau)
        case "oracle":
            return OracleEngine(scenario)
    raise ValueError(f"unknown engine {name!r}; expected one of {BASELINES}, 'all_store_noacl', or 'pkg.module:Class'")
