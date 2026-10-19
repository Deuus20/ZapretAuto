"""Stress the app's periodic operations and print RSS/handle/thread deltas per series.

Usage: python tools/leakcheck.py
A column that keeps growing from series to series means a leak (found the curl_cffi stream=True leak).
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import psutil  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
from app import tester, vpn  # noqa: E402
from app.gui.worker import Worker  # noqa: E402

me = psutil.Process()
workers = []


def snap():
    return round(me.memory_info().rss / 2**20, 1), me.num_handles(), me.num_threads()


def pump(sec=0.2):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def series(name, fn, n, rounds=3):
    fn()
    for k in range(rounds):
        pump()
        a = snap()
        for _ in range(n):
            fn()
        pump(1)
        b = snap()
        print(f"{name:20} {k + 1}/{rounds} x{n}: rss {a[0]}->{b[0]}  handles {a[1]}->{b[1]}  threads {a[2]}->{b[2]}",
              flush=True)


def worker_cycle():
    w = Worker(lambda log: None)
    w.finished.connect(lambda: workers.remove(w) if w in workers else None)
    workers.append(w)
    w.start()
    w.wait(1000)
    app.processEvents()


def probe():
    try:
        tester.fetch("https://github.com", 2048, 5)
    except Exception:  # noqa: BLE001
        pass


series("QThread worker", worker_cycle, 200)
series("egress_adapter", tester.egress_adapter, 200)
series("vpn.write_exclude", vpn.write_exclude, 20)
series("fetch (curl_cffi)", probe, 50)
