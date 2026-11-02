"""Small thread-safe event hub for Mek runtime modules."""
import logging
import threading
import uuid
from collections import defaultdict


log = logging.getLogger("mek.events")


class EventBus:
    def __init__(self):
        self._lock = threading.RLock()
        self._handlers = defaultdict(dict)

    def subscribe(self, event, handler):
        token = uuid.uuid4().hex
        with self._lock:
            self._handlers[event][token] = handler

        def unsubscribe():
            with self._lock:
                handlers = self._handlers.get(event)
                if handlers:
                    handlers.pop(token, None)
                    if not handlers:
                        self._handlers.pop(event, None)

        return unsubscribe

    def publish(self, event, **payload):
        with self._lock:
            handlers = list(self._handlers.get(event, {}).values())
            handlers.extend(self._handlers.get("*", {}).values())
        results = []
        for handler in handlers:
            try:
                results.append(handler(event, payload))
            except Exception:
                log.exception("Olay handler hatası: %s", event)
        return results