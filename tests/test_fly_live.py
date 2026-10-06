import importlib, os, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "vector-fly"))
os.environ["VECTOR_SNAPSHOT_URL"] = "http://127.0.0.1:9/api/snapshot"  # puerto cerrado
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1"
fl = importlib.import_module("fly_live")


def test_supervisor_restarts_crashing_worker(monkeypatch):
    monkeypatch.setattr(fl, "SUPERVISOR_BACKOFF_S", 0.01)
    flag = {"on": True, "err": ""}; n = [0]
    def worker():
        n[0] += 1
        if n[0] >= 3: flag["on"] = False
        raise RuntimeError("boom")
    fl._supervised("t", worker, flag)
    assert n[0] == 3 and "boom" in flag["err"]


def test_camera_survives_missing_vector_and_no_webcam(monkeypatch):
    """Sin nucleo y sin webcam el hilo debe seguir vivo (antes salia para siempre)."""
    monkeypatch.setattr(fl, "USE_WEBCAM_FALLBACK", False)
    monkeypatch.setattr(fl, "SUPERVISOR_BACKOFF_S", 0.01)
    real = time.sleep
    monkeypatch.setattr(fl.time, "sleep", lambda s: real(min(s, 0.01)))
    fl._CAM.update(on=True, err="")
    t = threading.Thread(target=fl._camera_loop, daemon=True); t.start()
    for _ in range(100):                      # sondeo (hasta 5 s) en vez de espera fija
        if "camara" in fl._CAM["err"].lower(): break
        real(0.05)
    real(0.3)
    alive = t.is_alive(); fl._CAM["on"] = False; t.join(2)
    assert alive
    assert "camara" in fl._CAM["err"].lower()


def test_webcam_failure_does_not_stop_bridge(monkeypatch):
    import cv2
    class Closed:
        def isOpened(self): return False
        def release(self): pass
    monkeypatch.setattr(cv2, "VideoCapture", lambda *a, **k: Closed())
    monkeypatch.setattr(fl, "USE_WEBCAM_FALLBACK", True)
    monkeypatch.setattr(fl, "SUPERVISOR_BACKOFF_S", 0.01)
    real = time.sleep
    monkeypatch.setattr(fl.time, "sleep", lambda s: real(min(s, 0.01)))
    fl._CAM.update(on=True, err="")
    t = threading.Thread(target=fl._camera_loop, daemon=True); t.start()
    real(0.8)
    alive = t.is_alive(); fl._CAM["on"] = False; t.join(2)
    assert alive
