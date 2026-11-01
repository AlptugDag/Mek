# -*- coding: utf-8 -*-
"""Beyin: kural tabanlı eşleşmeyen istekleri bir dil modeline yorumlatır.

Öncelik: Gemini anahtarı varsa Gemini, sonra Claude, en son tamamen yerel MLX (Qwen 7B).
Bulut erişilemezse otomatik olarak yerel modele düşer. Son konuşmalar hafızada tutulur.
Yerel model yalnızca gerektiğinde yüklenir ve boşta kalınca bellekten atılır, böylece
kurgu uygulamalarınızın belleğinden çalmaz.
Model yalnızca aşağıdaki araçları önerebilir; araçları Mek çalıştırır.
"""
import base64
import gc
import hashlib
import json
import os
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque

log = logging.getLogger("mek.brain")

TOOLS_DOC = """Araçlar (args JSON):
open_app {"name"} | quit_app {"name"} | open_url {"url"} | set_volume {"pct"}
media {"cmd": "playpause|next|previous|pause"} | run_shortcut {"name", "input"?}
add_reminder {"text", "minutes"?} | add_note {"text"} | timer {"minutes", "text"}
dark_mode {"on"?} | system_status {} | clean {}
shutdown {} | restart {} | sleep {} | lock {} | call {"name"} | look_screen {"question"}
edit {"command": "<Türkçe kurgu komutu, ör. 'boşlukları kes ve girişe reverb ekle'>"}  (kurgu otomasyonu)
app_key {"phrase"}  (öndeki kurgu uygulamasında kısayol: "kes", "render", "işaretçi ekle" gibi)"""

SYSTEM = """Sen Mek'sin: {title} için çalışan, Mac Studio'da yaşayan kişisel asistan ve sohbet arkadaşı.
Karakterin: Jarvis gibi zeki, sıcak, hafif esprili ve iğneleyici ama her zaman saygılı. Gerçek bir insanla
konuşur gibi doğal Türkçe konuş; kalıp cümleler, "size nasıl yardımcı olabilirim" gibi robotik ifadeler yok.
Boş muhabbete, şakaya, saçma sorulara da keyifle katıl, kendi fikrini söyle, gerektiğinde karşı soru sor.
Kullanıcı bir video kurgucu ve seslendirme sanatçısı. Geçmiş konuşmaları ve aşağıdaki hafızayı hatırla,
yeri geldiğinde doğal biçimde onlara gönderme yap.
Kullanıcı hakkında bildiklerin: {memory}
Şu anki bağlam: {context}
{tools}
YALNIZCA şu JSON'la yanıt ver: {{"say": "<kısa Türkçe yanıt>", "actions": [{{"tool": "...", "args": {{}}}}]}}
İstek bir eylem gerektirmiyorsa actions boş liste olsun. Asla dosya silme önerme.
Kullanıcı kendisi hakkında kalıcı bir bilgi verirse ya da "bunu unutma/hatırla" derse JSON'a
"remember": "<tek cümlelik bilgi>" ekle; bir bilgiyi unutmanı isterse "forget": "<o bilgi>" ekle.
Güncel bilgi, haber, fiyat, maç sonucu, hava, bir kişi/ürün/yer hakkında bilgi ya da
emin olmadığın herhangi bir olgu gerekiyorsa cevap uydurma: JSON'a "search": "<Google arama sorgusu>"
ekle, "say" boş kalabilir. Çok adımlı istekleri araçlara bölerek sırayla actions'a yaz (en çok 4).
Sesli okunacağı için yanıtlar genelde 1-3 cümle (sohbet derinleşirse biraz daha uzun olabilir),
madde işareti ve emoji yok. look_screen'i yalnızca kullanıcı ekrana bakmanı isterse kullan.
Kullanıcı Premiere, DaVinci Resolve, After Effects, Photoshop gibi bir uygulamadaysa o uygulamaya
özgü, sürüme uygun pratik yanıt ver; menü yolu ve kısayol söyle. Ekranı görmen gerekiyorsa look_screen kullan."""

SEARCH_SYSTEM = """Sen Mek'sin, {title} için çalışan Türkçe sesli asistan. Google aramasıyla bulduğun
güncel bilgiyle soruyu yanıtla. Sesli okunacak: en fazla 3 kısa cümle, sayıları ve tarihleri net söyle,
kaynak adı, bağlantı, madde işareti ya da emoji yok. Saat ve konum bağlamı: {context}"""


class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        self._model = None
        self._tok = None
        self._last_use = 0.0
        self._lock = threading.Lock()
        self.can_load_local = lambda: True   # Mek, kurgu/bellek durumuna göre bunu belirler
        # Kalıcı sohbet geçmişi ve uzun süreli hafıza (yeniden başlatmalarda kaybolmaz)
        self.hist_path = os.path.expanduser("~/.jarvis/state/history.json")
        self.mem_path = os.path.expanduser("~/.jarvis/state/memory.json")
        self.history = deque(self._load(self.hist_path, []), maxlen=60)
        self.memory = self._load(self.mem_path, [])
        # Aynı soruya tekrar API harcamamak için yanıt önbelleği (eylemsiz, aramasız yanıtlar)
        self.cache_path = os.path.expanduser("~/.jarvis/state/brain_cache.json")
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                self.cache = json.load(f)
        except Exception:
            self.cache = {}

    @staticmethod
    def _load(path, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    @staticmethod
    def _save(path, data):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def remember(self, fact):
        fact = fact.strip()
        if fact and fact not in self.memory:
            self.memory.append(fact)
            self.memory = self.memory[-80:]
            self._save(self.mem_path, self.memory)
            log.info("Hafızaya eklendi: %s", fact)

    def forget(self, what):
        w = what.lower()
        before = len(self.memory)
        self.memory = [m for m in self.memory if w not in m.lower() and m.lower() not in w]
        if len(self.memory) != before:
            self._save(self.mem_path, self.memory)

    def _turns(self, n=16):
        """Son n konuşma; aralarda uzun boşluk varsa modele not düşer."""
        out, prev = [], None
        for h in list(self.history)[-n:]:
            u = h["u"]
            if prev and h["t"] - prev > 3 * 3600:
                u = f"(Önceki konuşmadan {int((h['t'] - prev) // 3600)} saat sonra) {u}"
            out.append((u, h["a"]))
            prev = h["t"]
        return out

    def record(self, user_text, reply):
        """Kurallarla yanıtlanan komutlar da sohbet geçmişine girer (bağlam kopmasın)."""
        if reply:
            self.history.append({"t": time.time(), "u": user_text, "a": reply})
            self._save(self.hist_path, list(self.history))

    def _cache_key(self, text, app):
        return hashlib.sha1(f"{app}|{text.strip().lower()}".encode()).hexdigest()

    def _cache_put(self, key, out):
        self.cache[key] = {"t": time.time(), "out": out}
        if len(self.cache) > 500:
            for k in sorted(self.cache, key=lambda k: self.cache[k]["t"])[:100]:
                del self.cache[k]
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False)
        except Exception:
            pass

    def look(self, question, image_path, context):
        """'Ekrana bak' — ekran görüntüsünü Gemini'ye gösterip soruyu yanıtlar (yalnızca istenince)."""
        if not self.cfg.get("gemini_api_key"):
            return "Ekranı yorumlamak için Gemini anahtarı gerekiyor efendim."
        with open(image_path, "rb") as f:
            img = base64.b64encode(f.read()).decode()
        body = json.dumps({
            "system_instruction": {"parts": [{"text": (
                "Sen Mek'sin, kurgucu ve seslendirme sanatçısına yardım eden Türkçe sesli asistan. "
                "Ekran görüntüsündeki uygulamaya bakıp soruyu pratik, adım adım ama en fazla 4 kısa "
                "cümleyle yanıtla. Menü yollarını ve kısayolları net söyle. Emoji ve madde işareti yok. "
                f"Bağlam: {context}")}]},
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": img}},
                {"text": question or "Ekranda ne görüyorsun, nasıl yardımcı olabilirsin?"}]}],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.3},
        }).encode()
        try:
            data = self._gemini_post(body, 30)
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
        except Exception as e:
            log.error("Ekran yorumlama hatası: %s", e)
            return "Ekranı şu an yorumlayamadım efendim."

    def _budget_ok(self):
        """Günlük Gemini kotasını aşmamak için sayaç (state/gemini_usage.json)."""
        path = os.path.expanduser("~/.jarvis/state/gemini_usage.json")
        today = time.strftime("%Y-%m-%d")
        u = self._load(path, {})
        if u.get("day") != today:
            u = {"day": today, "n": 0, "blocked": False}
        if u.get("blocked") or u["n"] >= self.cfg.get("gemini_daily_budget", 18):
            return False
        u["n"] += 1
        self._save(path, u)
        return True

    def _block_today(self):
        path = os.path.expanduser("~/.jarvis/state/gemini_usage.json")
        self._save(path, {"day": time.strftime("%Y-%m-%d"), "n": 999, "blocked": True})

    def _gemini_post(self, body, timeout):
        """Model listesini sırayla dener; çalışan modeli hatırlar."""
        if not self._budget_ok():
            raise RuntimeError("Gemini günlük kotası doldu")
        models = self.cfg["gemini_models"]
        if getattr(self, "_good_model", None) in models:
            models = [self._good_model] + [m for m in models if m != self._good_model]
        last = None
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            for attempt in range(2):  # yoğunlukta (503) kısa bir bekleyişle bir kez daha dene
                req = urllib.request.Request(url, data=body, headers={
                    "x-goog-api-key": self.cfg["gemini_api_key"], "content-type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        data = json.load(r)
                    self._good_model = model
                    return data
                except urllib.error.HTTPError as e:
                    last = e
                    log.warning("Gemini %s: HTTP %s", model, e.code)
                    if e.code in (400, 401, 403):
                        raise
                    if e.code == 429:  # günlük kota bitti: bugün bir daha deneme
                        self._block_today()
                        raise
                    if e.code not in (429, 500, 503):
                        break
                except Exception as e:  # zaman aşımı
                    last = e
                    log.warning("Gemini %s: %s", model, e)
                time.sleep(1.5)
        raise last

    def _gemini(self, system, text):
        contents = []
        for u, a in self._turns():
            contents += [{"role": "user", "parts": [{"text": u}]},
                         {"role": "model", "parts": [{"text": json.dumps({"say": a, "actions": []},
                                                                         ensure_ascii=False)}]}]
        contents.append({"role": "user", "parts": [{"text": text}]})
        body = json.dumps({
            "system_instruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 800,
                                 "temperature": 0.8, "thinkingConfig": {"thinkingBudget": 0}},
        }).encode()
        data = self._gemini_post(body, 12)
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    def _gemini_search(self, query, question, context):
        """Google Search ile temellendirilmiş yanıt (JSON modu bu araçla birlikte çalışmaz)."""
        body = json.dumps({
            "system_instruction": {"parts": [{"text": SEARCH_SYSTEM.format(
                title=self.cfg["user_title"], context=context)}]},
            "contents": [{"role": "user", "parts": [{"text": f"{question}\n(Arama: {query})"}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"maxOutputTokens": 600, "temperature": 0.3},
        }).encode()
        data = self._gemini_post(body, 30)
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()

    def _claude(self, system, text):
        body = json.dumps({
            "model": self.cfg["anthropic_model"], "max_tokens": 400, "system": system,
            "messages": [m for u, a in self._turns() for m in (
                {"role": "user", "content": u},
                {"role": "assistant", "content": json.dumps({"say": a, "actions": []}, ensure_ascii=False)})]
            + [{"role": "user", "content": text}],
        }).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
            "x-api-key": self.cfg["anthropic_api_key"], "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        return "".join(b.get("text", "") for b in data.get("content", []))

    def _local(self, system, text):
        from mlx_lm import generate, load
        if self._model is None and not self.can_load_local():
            raise RuntimeError("kurgu sürüyor ya da bellek az; yerel beyin yüklenmedi")
        if self._model is None:
            log.info("Yerel model yükleniyor: %s", self.cfg["llm_model"])
            self._model, self._tok = load(self.cfg["llm_model"])
        msgs = [{"role": "system", "content": system}]
        for u, a in self._turns(8):
            msgs += [{"role": "user", "content": u},
                     {"role": "assistant", "content": json.dumps({"say": a, "actions": []}, ensure_ascii=False)}]
        msgs.append({"role": "user", "content": text})
        try:  # Qwen3: düşünme modunu kapat (hızlı, kısa yanıt)
            prompt = self._tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                                   enable_thinking=False)
        except TypeError:
            prompt = self._tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        return generate(self._model, self._tok, prompt=prompt, max_tokens=300)

    def complete(self, system, text, max_tokens=2000):
        """Kurgu işleri için tek seferlik JSON tamamlama (sohbet geçmişi yok).
        Önce Gemini (kota varsa), yoksa yerel model — kullanıcı açıkça istediği için kurgu sırasında da."""
        if self.cfg.get("gemini_api_key"):
            try:
                body = json.dumps({
                    "system_instruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": text}]}],
                    "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens,
                                         "temperature": 0.3},
                }).encode()
                data = self._gemini_post(body, 60)
                return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            except Exception as e:
                log.warning("complete: Gemini kullanılamadı (%s), yerel modele geçiliyor", e)
        with self._lock:
            from mlx_lm import generate, load
            if self._model is None:
                self._model, self._tok = load(self.cfg["llm_model"])
            self._last_use = time.time()
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": text}]
            try:
                prompt = self._tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                                       enable_thinking=False)
            except TypeError:
                prompt = self._tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
            return generate(self._model, self._tok, prompt=prompt, max_tokens=max_tokens)

    def think(self, text, context, app=""):
        key = self._cache_key(text, app)
        hit = self.cache.get(key)
        cacheable = bool(app) and bool(__import__("re").search(r"\bnasıl\b|\bnerede\b|kısayol", text.lower()))
        if cacheable and hit and time.time() - hit["t"] < 7 * 86400:
            log.info("Beyin: önbellek")
            return hit["out"]
        mem = "; ".join(self.memory[-40:]) or "henüz bir şey yok"
        system = SYSTEM.format(title=self.cfg["user_title"], context=context, tools=TOOLS_DOC, memory=mem)
        with self._lock:
            self._last_use = time.time()
            cloud = []
            if self.cfg.get("gemini_api_key"):
                cloud.append(("gemini", self._gemini))
            if self.cfg.get("anthropic_api_key"):
                cloud.append(("claude", self._claude))
            local = [("yerel", self._local)]
            backends = local + cloud if self.cfg.get("brain_mode") == "local_first" else cloud + local
            raw = None
            for name, fn in backends:
                try:
                    raw = fn(system, text)
                    log.info("Beyin: %s", name)
                    break
                except Exception as e:
                    log.error("Beyin %s hatası: %s", name, e)
            if raw is None:
                return {"say": "Kurgu sürerken bellek yemesin diye büyük beyni yüklemiyorum efendim; "
                               "kısa komutlarla buradayım.", "actions": []}
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            out = json.loads(raw[start:end])
            out.setdefault("actions", [])
        except Exception:
            out = {"say": raw.strip()[:300], "actions": []}
        query = out.get("search")
        if query:
            if self.cfg.get("gemini_api_key"):
                try:
                    out["say"] = self._gemini_search(query, text, context) or out.get("say", "")
                    log.info("Arama: %s", query)
                except Exception as e:
                    log.error("Arama hatası: %s", e)
                    out["say"] = out.get("say") or "Şu an arama yapamadım efendim."
            else:
                out["say"] = out.get("say") or "Arama için Gemini anahtarı gerekiyor efendim."
        if out.get("remember"):
            self.remember(str(out["remember"]))
        if out.get("forget"):
            self.forget(str(out["forget"]))
        self.record(text, out.get("say", ""))
        # Güncel bilgi gerektirmeyen, eylemsiz nasıl-yapılır yanıtlarını sakla
        if cacheable and not query and not out.get("actions") and out.get("say"):
            self._cache_put(key, out)
        return out

    def maybe_unload(self, force=False):
        if self._model is not None and (force or time.time() - self._last_use > self.cfg["llm_unload_after_s"]):
            with self._lock:
                self._model = self._tok = None
                gc.collect()
                try:
                    import mlx.core as mx
                    mx.clear_cache()
                except Exception:
                    pass
                log.info("Yerel model bellekten atıldı")
