# -*- coding: utf-8 -*-
"""Mek'in anlık durumunu ekran göstergesi (menubar.py) için dosyaya yazar."""
import json
import os
import threading
import time

from config import STATE_DIR

PATH = os.path.join(STATE_DIR, "ui.json")
_lock = threading.Lock()
_state = {"state": "listening", "text": "", "t": 0.0}


def set(state, text=None):
    """state: listening | hearing | awake | thinking | speaking | paused | recording"""
    with _lock:
        if _state["state"] == state and (text is None or text == _state["text"]) and os.path.exists(PATH):
            return
        _state["state"] = state
        if text is not None:
            _state["text"] = text
        _state["t"] = time.time()
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False)
        os.replace(tmp, PATH)


def current():
    return _state["state"]
