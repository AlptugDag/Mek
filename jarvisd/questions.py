# -*- coding: utf-8 -*-
"""Mek'in sorduğu soruların yanıt mekanizması.

Her soru açık seçeneklerle gelir (ör. "kapat" / "dokunma"); her seçeneğin ne yapacağı bellidir.
Yanıt nereden gelirse gelsin aynı yoldan yorumlanır:
  - Terminal:     mek cevap evet   |  mek evet  |  mek hayır  |  mek soru (bekleyen soruyu gösterir)
  - Menü çubuğu:  "Mek ❓" -> soru penceresindeki düğmeler
  - Siri:         "Hey Siri, Mek" -> "evet, kapat"  (kestirme yanıtı Mek'e iletir)
Yorumlama sırası: seçenek numarası/adı/eş anlamlıları -> genel evet/hayır kalıpları -> (belirsizse)
cihaz üzerindeki Apple Intelligence ile sınıflandırma. Hâlâ belirsizse hiçbir şey yapılmaz ve seçenekler
yeniden söylenir. Her yanıttan sonra Mek ne anladığını ve ne yaptığını açıkça söyler.
Süresi dolan soru, varsayılan (her zaman zararsız olan) seçenekle kapanır.
"""
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("mek.questions")

YES_WORDS = ("evet", "tamam", "olur", "yap", "onay", "onayla", "onaylıyorum", "kabul", "peki", "tabii", "tabi",
             "elbette", "aynen", "lütfen", "hadi", "evt", "e", "yes", "ok", "okey")
NO_WORDS = ("hayır", "hayir", "yok", "istemiyorum", "vazgeç", "vazgec", "iptal", "boşver", "bosver", "kalsın",
            "kalsin", "gerek yok", "sonra", "şimdi değil", "simdi degil", "dokunma", "bırak", "birak", "no")
ORDINALS = {"1": 0, "bir": 0, "birinci": 0, "ilk": 0, "ilki": 0, "2": 1, "iki": 1, "ikinci": 1, "ikincisi": 1,
            "3": 2, "üç": 2, "üçüncü": 2, "4": 3, "dört": 3, "dördüncü": 3}


def norm(text):
    text = (text or "").replace("İ", "i").replace("I", "ı").lower()
    text = re.sub(r"[^\wçğıöşü ]+", " ", text)
    return " " + re.sub(r"\s+", " ", text).strip() + " "


def has_phrase(text_n, phrase):
    return f" {norm(phrase).strip()} " in text_n


class Option:
    def __init__(self, key, label, run=None, words=(), polarity=None):
        self.key, self.label, self.run, self.words, self.polarity = key, label, run, tuple(words), polarity


class Question:
    def __init__(self, qid, text, options, default=None, ttl=900, speak_text=None):
        self.qid = qid
        self.text = text
        self.options = options
        self.default = default or options[-1].key  # son seçenek "dokunma" türü, zararsız olmalı
        self.created = time.time()
        self.expires = self.created + ttl
        self.speak_text = speak_text

    def option(self, key):
        return next((o for o in self.options if o.key == key), None)

    def choices_text(self):
        return " / ".join(f"{i}) {o.label}" for i, o in enumerate(self.options, 1))


class QuestionBox:
    def __init__(self, notify=None, speak=None, classify=None, state_path=None):
        self.notify = notify        # notify(text, urgent=False, title=...)
        self.speak = speak
        self.classify = classify    # classify(question, options) -> key | None  (Apple Intelligence)
        self.state_path = state_path
        self.items = []
        self.lock = threading.Lock()
        self.asked = {}             # qid -> son sorulma zamanı (aynı şeyi tekrar tekrar sormamak için)

    # ------------------------------------------------------------------ soru
    def ask(self, q, repeat_after=6 * 3600, announce=True):
        with self.lock:
            if any(i.qid == q.qid for i in self.items):
                return False
            if time.time() - self.asked.get(q.qid, 0) < repeat_after:
                return False
            self.asked[q.qid] = time.time()
            self.items.append(q)
            self._persist()
        log.info("Soru: %s [%s]", q.text, q.choices_text())
        if announce and self.notify:
            hint = " Yanıt: menü çubuğunda Mek ❓, 'Hey Siri, Mek' ya da `mek cevap ...`."
            self.notify(q.text + " " + q.choices_text() + "." + hint, title="Mek soruyor",
                        spoken_text=q.speak_text or (q.text + " " + self._spoken_choices(q)))
        return True

    @staticmethod
    def _spoken_choices(q):
        labels = [o.label for o in q.options]
        return ("'" + "' ya da '".join(labels) + "' diyebilirsiniz.") if labels else ""

    def pending(self):
        self.expire()
        with self.lock:
            return self.items[0] if self.items else None

    def expire(self):
        now = time.time()
        done = []
        with self.lock:
            for q in list(self.items):
                if now > q.expires:
                    self.items.remove(q)
                    done.append(q)
            if done:
                self._persist()
        for q in done:
            opt = q.option(q.default)
            log.info("Soru zaman aşımı, varsayılan: %s -> %s", q.qid, q.default)
            if opt and opt.run:
                try:
                    opt.run()
                except Exception:
                    log.exception("Varsayılan seçenek çalıştırılamadı")

    # ------------------------------------------------------------------ yanıt
    def interpret(self, q, text, use_ai=True):
        """Yanıtı bir seçeneğe eşler. (anahtar | None, belirsiz_mi)"""
        t = norm(text)
        words = t.split()
        for w in words[:2]:
            if w in ORDINALS and ORDINALS[w] < len(q.options) and len(words) <= 3:
                return q.options[ORDINALS[w]].key, False
        hits = []
        for o in q.options:
            phrases = (o.key, o.label, *o.words)
            if any(has_phrase(t, p) for p in phrases if p):
                hits.append(o.key)
        if len(hits) == 1:
            return hits[0], False
        if not hits:
            yes = any(has_phrase(t, w) for w in YES_WORDS)
            no = any(has_phrase(t, w) for w in NO_WORDS)
            if yes != no:
                pol = "yes" if yes else "no"
                cands = [o.key for o in q.options if o.polarity == pol]
                if len(cands) == 1:
                    return cands[0], False
        if use_ai and self.classify:
            try:
                key = self.classify(q, text)
            except Exception:
                key = None
            if key and q.option(key):
                return key, False
        return None, True

    def answer(self, text, qid=None, use_ai=True):
        """(işlendi_mi, yanıt metni)."""
        q = self.pending() if qid is None else next((i for i in self.items if i.qid == qid), None)
        if not q:
            return False, "Yanıt bekleyen bir sorum yok."
        key, _ = self.interpret(q, text, use_ai=use_ai)
        if not key:
            return True, (f"Yanıtınızı tam anlayamadım, bu yüzden hiçbir şey yapmadım. Soru: {q.text} "
                          f"Seçenekler: {q.choices_text()}.")
        with self.lock:
            if q in self.items:
                self.items.remove(q)
                self._persist()
        opt = q.option(key)
        log.info("Yanıt: %s -> %s (%r)", q.qid, key, text)
        result = ""
        if opt.run:
            try:
                result = opt.run() or ""
            except Exception as e:
                log.exception("Seçenek çalıştırılamadı")
                result = f"Ama uygularken hata oldu: {e}"
        return True, f"Anladım: {opt.label}. {result}".strip()

    def summary(self):
        q = self.pending()
        if not q:
            return "Yanıt bekleyen soru yok."
        return f"{q.text} Seçenekler: {q.choices_text()}. Yanıt: `mek cevap <seçenek>`"

    # ------------------------------------------------------------------ menü çubuğu için durum
    def _persist(self):
        if not self.state_path:
            return
        data = None
        if self.items:
            q = self.items[0]
            data = {"id": q.qid, "text": q.text, "expires": q.expires,
                    "options": [{"key": o.key, "label": o.label} for o in q.options]}
        try:
            if data is None:
                if os.path.exists(self.state_path):
                    os.unlink(self.state_path)
                return
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except OSError:
            pass


def ai_classifier(ai_bin, run):
    """Apple Intelligence ile serbest yanıtı seçeneklerden birine eşler; emin değilse None."""
    def classify(q, text):
        if not os.access(ai_bin, os.X_OK):
            return None
        opts = "\n".join(f"- {o.key}: {o.label}" for o in q.options)
        instr = ("Bir asistanın sorusuna kullanıcının verdiği yanıtı sınıflandırıyorsun. Yalnızca JSON döndür: "
                 '{"option":"<anahtar>"} ya da emin değilsen {"option":null}. Tahmin yürütme.')
        prompt = f"Soru: {q.text}\nSeçenekler:\n{opts}\nKullanıcının yanıtı: {text}"
        r = run([ai_bin, instr], input=prompt, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        m = re.search(r"\{.*\}", r.stdout, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0)).get("option")
        except ValueError:
            return None
    return classify
