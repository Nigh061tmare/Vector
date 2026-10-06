import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vector_vad as vv

BLK = 2000                                  # 125 ms @16 kHz
rng = np.random.default_rng(0)


def pcm(x): return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
def noise(a, n=BLK): return pcm(rng.normal(0, a, n))
def voice(a=0.2, n=BLK):
    t = np.arange(n) / vv.SR
    return pcm(a * np.sin(2 * np.pi * 220 * t) + rng.normal(0, 0.01, n))


def run(blocks):
    v = vv.EnergyVAD(); out = []
    for b in blocks:
        u = v.feed(b)
        if u is not None: out.append(u)
    return v, out


def test_detects_one_utterance_with_preroll():
    blocks = [noise(0.004)] * 20 + [voice()] * 8 + [noise(0.004)] * 10
    _, out = run(blocks)
    assert len(out) == 1
    assert len(out[0]) // 2 / vv.SR > 8 * 0.125          # voz + pre-roll + hangover


def test_pure_noise_never_triggers_even_loud_ambient():
    _, out = run([noise(0.02)] * 200)
    assert out == []


def test_floor_adapts_to_rising_noise_then_voice_still_detected():
    blocks = [noise(0.005)] * 20 + [noise(0.03)] * 60 + [voice(0.4)] * 8 + [noise(0.03)] * 10
    _, out = run(blocks)
    # El escalon de ruido produce, como mucho, UNA frase espuria mientras el suelo
    # se adapta (el STT la descarta); la voz real debe llegar entera despues.
    assert 1 <= len(out) <= 2
    loud = np.abs(np.frombuffer(out[-1], dtype="<i2")).max()
    assert loud > 0.3 * 32768


def test_short_click_rejected():
    blocks = [noise(0.004)] * 20 + [voice(0.5, 800)] + [noise(0.004)] * 10
    assert run(blocks)[1] == []


def test_continuous_speech_is_force_cut():
    v = vv.EnergyVAD(); cuts = 0
    for b in [noise(0.004)] * 10 + [voice()] * 120:          # 15 s de voz
        cuts += v.feed(b) is not None
    assert cuts >= 1


def test_two_phrases_separated_by_silence():
    seq = [noise(0.004)] * 15 + [voice()] * 6 + [noise(0.004)] * 10 + [voice()] * 6 + [noise(0.004)] * 10
    assert len(run(seq)[1]) == 2


def test_wake_word_variants_and_rest():
    assert vv.has_wake_word("Héctor, ven aquí") == (True, "ven aquí")
    assert vv.has_wake_word("oye vektor para")[0]
    assert vv.has_wake_word("hola que tal") == (False, "hola que tal")
    assert not vv.has_wake_word("")[0]
