import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vector_talk as vt

NAMES = list(vt.SIGNALS)


@pytest.mark.parametrize("name", NAMES)
def test_roundtrip_clean(name):
    r = vt.classify(vt.synth(name))
    assert r["signal"] == name and r["confidence"] >= 0.8


@pytest.mark.parametrize("name", NAMES)
def test_roundtrip_with_noise_and_leading_silence(name):
    rng = np.random.default_rng(7)
    w = vt.synth(name)
    x = np.concatenate([np.zeros(3000, np.float32), w, np.zeros(2000, np.float32)])
    x = x + rng.normal(0, 0.03, x.size).astype(np.float32)
    assert vt.classify(x)["signal"] == name


def test_white_noise_is_not_a_signal():
    rng = np.random.default_rng(1)
    assert vt.classify(rng.normal(0, 0.3, 16000).astype(np.float32))["signal"] is None


def test_silence_and_empty():
    assert vt.classify(np.zeros(8000, np.float32))["signal"] is None
    assert vt.classify(np.array([], np.float32))["signal"] is None


def test_vocabulary_is_unambiguous():
    """Ninguna pareja comparte (chirps, largo) con frecuencias que se solapen."""
    for a in NAMES:
        for b in NAMES:
            if a >= b:
                continue
            sa, sb = vt.SIGNALS[a], vt.SIGNALS[b]
            if (sa["chirps"] == sb["chirps"] or min(sa["chirps"], sb["chirps"]) >= 4) \
                    and bool(sa.get("long")) == bool(sb.get("long")):
                tol = max(180.0, 0.18 * max(sa["freq"], sb["freq"]))
                assert abs(sa["freq"] - sb["freq"]) > tol, (a, b)


def test_wav_format_accepted_by_vector(tmp_path):
    import wave
    p = vt.to_wav("ven", tmp_path / "x.wav")
    with wave.open(str(p)) as w:
        assert (w.getnchannels(), w.getsampwidth()) == (1, 2)
        assert 8000 <= w.getframerate() <= 16025


class Clock:
    t = 100.0
    def __call__(self): return self.t


def heard(sig, conf=0.9):
    return {"signal": sig, "confidence": conf}


def test_foreign_signal_gets_ack_and_ven_gets_aqui():
    p = vt.ChirpProtocol(Clock())
    assert p.on_heard(heard("objeto")) == "ack"
    assert p.on_heard(heard("ven")) == "aqui"
    assert p.on_heard(heard("ack")) is None          # sin ping-pong


def test_own_echo_ignored_but_later_repeat_answered():
    c = Clock(); p = vt.ChirpProtocol(c)
    p.note_emitted("objeto")
    c.t += 0.3
    assert p.on_heard(heard("objeto")) is None        # eco propio
    c.t += 3.0
    assert p.on_heard(heard("objeto")) == "ack"       # otro robot, mas tarde


def test_echo_of_other_signal_is_not_filtered():
    c = Clock(); p = vt.ChirpProtocol(c)
    p.note_emitted("objeto"); c.t += 0.2
    assert p.on_heard(heard("ven")) == "aqui"


def test_ack_resolves_pending_and_timeout_reports():
    c = Clock(); p = vt.ChirpProtocol(c)
    p.note_emitted("ayuda"); c.t += 1.5
    p.on_heard(heard("ack"))
    assert p.acked == ["ayuda"] and p.ack_timed_out() is None
    p.note_emitted("ayuda"); c.t += 5
    assert p.ack_timed_out() == "ayuda" and p.ack_timed_out() is None


def test_low_confidence_ignored():
    assert vt.ChirpProtocol(Clock()).on_heard(heard("objeto", 0.1)) is None
