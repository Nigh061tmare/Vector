import os, sys, threading, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import vector_network_guard as ng


class VectorUnauthenticatedException(Exception):  # mismo nombre que el SDK
    pass


def test_detects_401_by_type_text_and_cause():
    assert ng.es_error_401(VectorUnauthenticatedException("x"))
    assert ng.es_error_401(RuntimeError("StatusCode.UNAUTHENTICATED"))
    assert ng.es_error_401(RuntimeError("HTTP 401"))
    try:
        try:
            raise VectorUnauthenticatedException("inner")
        except Exception as inner:
            raise RuntimeError("wrapper sin pistas") from inner
    except RuntimeError as e:
        assert ng.es_error_401(e)
    assert not ng.es_error_401(RuntimeError("Unable to establish a connection"))
    assert not ng.es_error_401(RuntimeError("timeout"))


def test_redaction_hides_secrets():
    r = ng.redactar("abcdefgh-secret-guid")
    assert r.startswith("abcd") and "secret" not in r


def test_atomic_write(tmp_path):
    f = tmp_path / "sdk_config.ini"
    ng.escribir_atomico(f, "a"); ng.escribir_atomico(f, "b")
    assert f.read_text() == "b" and not list(tmp_path.glob("*.tmp"))


def _healer(results):
    t = [0.0]; calls = []
    def renovar(ip):
        calls.append(ip); return results.pop(0)
    return ng.TokenHealer(renovar=renovar, clock=lambda: t[0]), t, calls


def test_cooldown_after_success_reuses_result():
    h, t, calls = _healer([True])
    assert h.heal("1.2.3.4") and h.heal("1.2.3.4") and h.heal("1.2.3.4")
    assert len(calls) == 1
    t[0] += ng.RENEW_COOLDOWN_OK_S + 1
    h._renovar = lambda ip: calls.append(ip) or True
    h.heal("1.2.3.4"); assert len(calls) == 2


def test_exponential_backoff_on_failure_is_capped():
    h, t, calls = _healer([False] * 20)
    waits = []
    for _ in range(12):
        t[0] += 10_000                       # siempre pasado el backoff
        h.heal(); waits.append(h._next_ok - t[0])
    assert waits[0] == ng.RENEW_BACKOFF_MIN_S
    assert waits[1] == 2 * ng.RENEW_BACKOFF_MIN_S
    assert max(waits) == ng.RENEW_BACKOFF_MAX_S


def test_concurrent_clients_trigger_single_renewal():
    gate = threading.Event(); calls = []
    def slow(ip):
        calls.append(1); gate.wait(1.0); return True
    h = ng.TokenHealer(renovar=slow)
    res = []
    ths = [threading.Thread(target=lambda: res.append(h.heal())) for _ in range(8)]
    [x.start() for x in ths]; gate.set(); [x.join(3) for x in ths]
    assert len(calls) == 1 and res == [True] * 8
