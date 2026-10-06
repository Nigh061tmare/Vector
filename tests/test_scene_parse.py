import importlib, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vector-fly"))
fl = importlib.import_module("fly_live")
P = fl._parse_scene


def names(t): return [o["name"] for o in P(t)]


def test_complete_json_multiple_objects():
    t = '{"objects":[{"name":"cat","side":-0.5,"near":0.8},{"name":"lamp","side":0.9,"near":0.2}]}'
    assert names(t) == ["cat", "lamp"]


def test_markdown_fence_and_chatter():
    t = 'Sure!\n```json\n{"objects":[{"name":"door","side":0,"near":0.5}]}\n```\nHope it helps'
    assert names(t) == ["door"]


def test_truncated_json_recovers_complete_objects():
    t = '{"objects":[{"name":"cat","side":-0.5,"near":0.8},{"name":"sofa","side":0.1,"near":0.4},{"name":"ta'
    assert names(t) == ["cat", "sofa"]


def test_string_numbers_clamped_and_nan_safe():
    o = P('{"objects":[{"name":"wall","side":"3","near":"-2"},{"name":"x1","side":"abc","near":"nan"}]}')
    assert o[0]["side"] == 1.0 and o[0]["near"] == 0.0
    assert o[1]["side"] == 0.0 and o[1]["near"] == 0.3


def test_duplicates_and_limit():
    t = '{"objects":[' + ",".join('{"name":"obj%d","side":0,"near":.5}' % i for i in range(9)) + \
        ',{"name":"obj0","side":1,"near":1}]}'
    assert len(P(t)) == 4 and len(set(names(t))) == 4


def test_garbage_and_empty():
    assert P("") == [] and P(None) == [] and P("no objects here") == []


def test_free_text_fallback_still_works():
    assert names('name: "chair" side: -0.3 near: 0.6') == ["chair"]
