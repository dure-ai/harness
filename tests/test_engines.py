import pytest

from harness.baselines.oracle import OracleEngine
from harness.baselines.simple import AllStoreEngine, NoneEngine, RepeatNEngine
from harness.config import EvalConfig
from harness.engines import BASELINES, make_engine
from tests.dummy_engine import Dummy
from tests.factories import T, ev, fact, probe, scenario

SC = scenario({"f": fact()}, [ev("e1", T("10-01 10:00"), fact="f")], [probe("p", "block", T("10-02 10:00"), ["f"])])


def test_baselines_constant():
    assert BASELINES == ("none", "all_store", "repeat_n", "oracle")


def test_make_engine_baselines():
    cfg = EvalConfig()
    assert isinstance(make_engine("none", SC, cfg), NoneEngine)
    e = make_engine("all_store", SC, cfg)
    assert isinstance(e, AllStoreEngine) and e.acl is True
    e = make_engine("all_store_noacl", SC, cfg)
    assert isinstance(e, AllStoreEngine) and e.acl is False
    assert isinstance(make_engine("oracle", SC, cfg), OracleEngine)


def test_make_engine_repeat_n_uses_config():
    cfg = EvalConfig(repeat_n={"n": 4, "tau": 0.7})
    e = make_engine("repeat_n", SC, cfg)
    assert isinstance(e, RepeatNEngine) and (e.n, e.tau) == (4, 0.7)


def test_make_engine_import_path():
    assert isinstance(make_engine("tests.dummy_engine:Dummy", SC, EvalConfig()), Dummy)


def test_make_engine_import_path_not_an_engine_raises():
    with pytest.raises(TypeError):
        make_engine("tests.factories:KST", SC, EvalConfig())


def test_make_engine_unknown_raises():
    with pytest.raises(ValueError):
        make_engine("nope", SC, EvalConfig())
