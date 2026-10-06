import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vector_facts as vf


def test_extract_patterns():
    assert vf.extract("Vector, recuerda que mi gato se llama Misu") == "mi gato se llama Misu"
    assert vf.extract("acuérdate de que mañana vienen mis padres.") == "mañana vienen mis padres"
    assert vf.extract("me llamo Jose Luis") == "me llamo Jose Luis"
    assert vf.extract("hola que tal") is None and vf.extract("recuerda que") is None


def test_add_dedup_and_cap():
    m = {}
    assert vf.add(m, "mi gato se llama Misu", 1)
    assert not vf.add(m, "el gato se llama Misu", 2) and len(m["hechos"]) == 1
    for i in range(300): vf.add(m, f"dato unico numero{i} palabra{i}", i)
    assert len(m["hechos"]) == vf.MAX_FACTS


def test_retrieve_relevance_accents_and_recency():
    m = {}
    vf.add(m, "mi gato se llama Misu", 1); vf.add(m, "mañana vienen mis padres", 2)
    assert vf.retrieve(m, "como se llama el gato")[0].startswith("mi gato")
    assert vf.retrieve(m, "cuando vienen los PADRES?")[0].startswith("mañana")
    assert vf.retrieve(m, "")[0].startswith("mañana")        # sin query: lo mas reciente
    assert vf.retrieve(m, "coches") == []


def test_recall_query_and_reply():
    ok, rest = vf.is_recall_query("¿qué te dije del gato?")
    assert ok and "gato" in rest
    m = {}; vf.add(m, "mi gato se llama Misu", 1)
    assert "Misu" in vf.reply_for_recall(m, rest)
    assert "No recuerdo" in vf.reply_for_recall({}, "x")
    assert not vf.is_recall_query("hola")[0]


def test_context_snippet():
    m = {}; vf.add(m, "mi gato se llama Misu", 1)
    assert vf.context(m, "gato").startswith("Sabe:") and vf.context({}, "x") == ""
