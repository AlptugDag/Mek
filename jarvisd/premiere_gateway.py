#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mek Premiere Gateway (Premiere Pro & After Effects Canlı İki Yönlü Köprü)
Mek asistanı ile Adobe Premiere Pro CEP eklentisi arasında anlık TCP IPC bağlantısı kurar.
Kullanıcı mikrofona 'hey mek şunu yap' dediğinde veya terminalden komut verdiğinde
Premiere Pro timeline'ını analiz eder ve gerçek zamanlı kurgu müdahalelerini uygular.
"""

import os
import sys
import json
import socket
import threading
import time
import logging
from typing import Dict, Any, Optional

log = logging.getLogger("mek.premiere_gateway")

GATEWAY_PORT = 45732
GATEWAY_HOST = "127.0.0.1"


class PremiereGateway:
    """Premiere Pro CEP paneliyle anlık iki yönlü köprü yöneten sunucu."""

    def __init__(self, kurgu_mgr=None, port: int = GATEWAY_PORT):
        self.kurgu = kurgu_mgr
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.active_client: Optional[socket.socket] = None
        self.client_lock = threading.Lock()
        self.running = False
        self.server_thread: Optional[threading.Thread] = None
        self.last_timeline_state: Dict[str, Any] = {}
        self.pending_responses: Dict[str, Any] = {}
        self.response_events: Dict[str, threading.Event] = {}
        self.req_counter = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self.server_thread = threading.Thread(target=self._run_server, daemon=True, name="MekPremiereGateway")
        self.server_thread.start()
        log.info("Mek Premiere Gateway başlatıldı (Port: %d)", GATEWAY_PORT)

    def stop(self):
        self.running = False
        with self.client_lock:
            if self.active_client:
                try: self.active_client.close()
                except Exception: pass
                self.active_client = None
        if self.server_socket:
            try: self.server_socket.close()
            except Exception: pass
            self.server_socket = None
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=0.5)

    def _run_server(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((GATEWAY_HOST, self.port))
            self.server_socket.listen(2)
            self.server_socket.settimeout(1.0)
        except Exception as e:
            log.warning("Mek Premiere Gateway soket açma hatası: %s", e)
            return

        while self.running:
            try:
                client, addr = self.server_socket.accept()
                with self.client_lock:
                    if self.active_client:
                        try: self.active_client.close()
                        except Exception: pass
                    self.active_client = client
                log.info("Premiere Pro CEP eklentisi bağlandı: %s", addr)
                t = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    log.debug("Gateway accept döngüsü: %s", e)
                break

    def _handle_client(self, client: socket.socket):
        client.settimeout(None)
        buffer = ""
        while self.running:
            try:
                data = client.recv(8192)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self._process_message(line)
            except Exception as e:
                log.debug("Premiere Pro bağlantı koptu: %s", e)
                break
        with self.client_lock:
            if self.active_client == client:
                self.active_client = None
        try: client.close()
        except Exception: pass
        log.info("Premiere Pro CEP bağlantısı kapandı.")

    def _process_message(self, line: str):
        try:
            payload = json.loads(line)
        except Exception:
            return

        msg_type = payload.get("type")
        if msg_type == "status_update":
            self.last_timeline_state = payload.get("state", {})
        elif msg_type == "response":
            req_id = payload.get("req_id")
            if req_id and req_id in self.response_events:
                self.pending_responses[req_id] = payload.get("data")
                self.response_events[req_id].set()

    def is_connected(self) -> bool:
        with self.client_lock:
            return self.active_client is not None

    def send_command(self, action: str, params: Optional[Dict[str, Any]] = None, timeout: float = 8.0) -> Dict[str, Any]:
        """Premiere Pro'ya ExtendScript komutu gönderir ve sonucunu bekler."""
        with self.client_lock:
            client = self.active_client
        if not client:
            return {"ok": False, "error": "PREMIERE_NOT_CONNECTED",
                    "message": "Adobe Premiere Pro CEP eklentisi bağlı değil. Lütfen Premiere Pro'da Window > Extensions > Alptuguide Studio panelini açın."}

        self.req_counter += 1
        req_id = f"cmd_{int(time.time()*1000)}_{self.req_counter}"
        evt = threading.Event()
        self.response_events[req_id] = evt

        msg = json.dumps({
            "type": "command",
            "req_id": req_id,
            "action": action,
            "params": params or {}
        }) + "\n"

        try:
            client.sendall(msg.encode("utf-8"))
        except Exception as e:
            self.response_events.pop(req_id, None)
            return {"ok": False, "error": "SEND_ERROR", "message": f"Premiere Pro'ya komut iletilemedi: {e}"}

        ok = evt.wait(timeout=timeout)
        self.response_events.pop(req_id, None)
        if not ok:
            return {"ok": False, "error": "TIMEOUT", "message": "Premiere Pro'dan yanıt zaman aşımına uğradı."}

        res = self.pending_responses.pop(req_id, {})
        return res if isinstance(res, dict) else {"ok": True, "result": res}
