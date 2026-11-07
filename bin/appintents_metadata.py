#!/usr/bin/env python3
"""Xcode'suz App Intents meta verisi üretici.

Xcode'un ``appintentsmetadataprocessor`` aracı yalnızca tam Xcode kurulumuyla
gelir.  Bu makinede yalnızca Command Line Tools var; buna karşın ``swiftc``
``-emit-const-values`` ile App Intents tiplerinin tüm derleme zamanı
bilgisini ``.swiftconstvalues`` dosyasına yazabiliyor.

Bu betik o dosyayı okuyup macOS'un App Intents veritabanının beklediği
``Metadata.appintents/extract.actionsdata`` (+ ``version.json``) biçimine
çevirir.  Böylece Siri, uygulamanın niyetlerini (intent) Kestirmeler
uygulamasına hiç dokunmadan doğrudan görür.

Kullanım:
    appintents_metadata.py --constvalues Mek.swiftconstvalues \
        --bundle /Applications/Mek.app --module Mek [--app-name Mek]

Desteklenen alt küme (Mek'in ihtiyacı kadar, bilinçli olarak sınırlı):
  * ``AppIntent`` uyumlu struct'lar  -> ``actions``
  * ``@Parameter`` ile verilen String / Bool / Int / Double parametreleri
  * ``AppShortcutsProvider`` uyumlu struct -> ``autoShortcuts``
Entity, enum ve query desteklenmez; bunlar kullanıldığında betik
sessizce atlamaz, açıkça hata verir.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import sys

# extract.actionsdata biçim sürümü ve araç sürümü. macOS'un ayrıştırıcısı
# version.json'daki "version" alanına bakar; "toolsVersion" yalnızca bilgi
# amaçlıdır. Sistem uygulamalarıyla aynı değerleri kullanıyoruz.
FORMAT_VERSION = "3.0"
DEFAULT_TOOLS_VERSION = "27A225b"

ANY_PLATFORM = {"LNPlatformNameWildcard": {"introducedVersion": "*"}}

# valueType -> primitive typeIdentifier eşlemesi (sistem uygulamalarından
# tersine mühendislikle çıkarıldı).
PRIMITIVE_TYPE_IDS = {
    "Swift.String": 0,
    "Swift.Bool": 1,
    "Swift.Int": 2,
    "Swift.Double": 7,
    "Foundation.Date": 8,
    "Foundation.URL": 11,
}

INTENT_PROTOCOL = "AppIntents.AppIntent"
PROVIDER_PROTOCOL = "AppIntents.AppShortcutsProvider"
PARAMETER_WRAPPER = "AppIntents.IntentParameter"

# Desteklemediğimiz ama sessizce yanlış meta veri üretmemize yol açacak
# protokoller.
UNSUPPORTED_PROTOCOLS = {
    "AppIntents.AppEntity": "AppEntity",
    "AppIntents.AppEnum": "AppEnum",
    "AppIntents.EntityQuery": "EntityQuery",
    "AppIntents.EntityStringQuery": "EntityStringQuery",
    "AppIntents.DynamicOptionsProvider": "DynamicOptionsProvider",
}


class MetadataError(Exception):
    """Meta veri üretilemediğinde yükseltilir."""


def _localized(text: str) -> dict:
    """extract.actionsdata'nın yerelleştirilebilir metin sarmalayıcısı.

    ``table`` verilmediğinde sistem ``key`` değerini olduğu gibi gösterir;
    bizim .strings tablomuz olmadığı için istediğimiz tam olarak bu.
    """
    return {"alternatives": [], "key": text}


def _conformances(record: dict) -> set[str]:
    names = set(record.get("conformances") or [])
    for c in record.get("allConformances") or []:
        if isinstance(c, dict) and c.get("protocolName"):
            names.add(c["protocolName"])
    return names


def _props(record: dict) -> dict:
    """Etikete göre özellik sözlüğü (son tanım kazanır)."""
    out = {}
    for p in record.get("properties") or []:
        label = p.get("label")
        if label:
            out[label] = p
    return out


def _raw_literal(prop: dict | None, default=None):
    if not prop:
        return default
    if prop.get("valueKind") != "RawLiteral":
        return default
    return prop.get("value", default)


def _bool_literal(prop: dict | None, default: bool = False) -> bool:
    val = _raw_literal(prop)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() == "true"


def _arg(initcall: dict, label: str) -> dict | None:
    for a in (initcall.get("value") or {}).get("arguments") or []:
        if a.get("label") == label:
            return a
    return None


def _short_name(fully_qualified: str) -> str:
    return fully_qualified.rsplit(".", 1)[-1]


# ---------------------------------------------------------------------------
# Parametreler
# ---------------------------------------------------------------------------

def _unwrap_optional(swift_type: str) -> tuple[str, bool]:
    """``Swift.Optional<Swift.String>`` -> ``("Swift.String", True)``."""
    prefix = "Swift.Optional<"
    if swift_type.startswith(prefix) and swift_type.endswith(">"):
        return swift_type[len(prefix):-1].strip(), True
    return swift_type, False


def _parameter_entry(prop: dict, intent_name: str) -> dict:
    """``@Parameter`` sarmalayıcılı bir özelliği parametre kaydına çevirir."""
    name = prop["label"]
    swift_type, wrapped_optional = _unwrap_optional(prop.get("type") or "")
    type_id = PRIMITIVE_TYPE_IDS.get(swift_type)
    if type_id is None:
        raise MetadataError(
            f"{intent_name}.{name}: desteklenmeyen parametre tipi {swift_type!r}. "
            f"Desteklenenler: {', '.join(sorted(PRIMITIVE_TYPE_IDS))}"
        )

    # Başlık ve varsayılan değer, IntentParameter(...) çağrısından okunur.
    title = name
    # isOptional = Siri'nin değeri sormak zorunda OLMADIĞI durum: tip Optional
    # ise ya da bir varsayılan değer verilmişse.
    is_optional = wrapped_optional
    initcall = prop if prop.get("valueKind") == "InitCall" else None
    if initcall:
        title_arg = _arg(initcall, "title")
        if title_arg and title_arg.get("valueKind") == "RawLiteral":
            title = title_arg.get("value") or name
        default_arg = _arg(initcall, "default")
        if default_arg and default_arg.get("valueKind") not in (None, "NilLiteral"):
            is_optional = True

    return {
        "capabilities": 0,
        "dynamicOptionsSupport": 0,
        "inputConnectionBehavior": 0,
        "isInput": False,
        "isOptional": is_optional,
        "name": name,
        "resolvableInputTypes": [],
        "title": _localized(title),
        "typeSpecificMetadata": [],
        "valueType": {"primitive": {"wrapper": {"typeIdentifier": type_id}}},
    }


def _intent_parameters(record: dict, intent_name: str) -> list[dict]:
    params = []
    for prop in record.get("properties") or []:
        wrappers = prop.get("propertyWrappers") or []
        if not any(w.get("type") == PARAMETER_WRAPPER for w in wrappers):
            continue
        if prop.get("isStatic") == "true":
            continue
        params.append(_parameter_entry(prop, intent_name))
    # Derleyici sırası kaynak sırasıdır; parametre özeti buna dayanır.
    return params


# ---------------------------------------------------------------------------
# Eylemler (actions)
# ---------------------------------------------------------------------------

def _action_entry(record: dict) -> tuple[str, dict]:
    fq = record.get("typeName") or ""
    mangled = record.get("mangledTypeName")
    if not fq or not mangled:
        raise MetadataError(f"Eksik tip adı/mangled ad: {record!r:.200}")
    identifier = _short_name(fq)

    props = _props(record)
    title = _raw_literal(props.get("title"))
    if not title:
        raise MetadataError(
            f"{identifier}: `static var title` düz bir metin sabiti olmalı "
            "(değişken ya da birleştirilmiş metin olamaz)."
        )
    description = _raw_literal(props.get("description"))
    open_app = _bool_literal(props.get("openAppWhenRun"), False)
    params = _intent_parameters(record, identifier)

    description_metadata: dict = {"searchKeywords": []}
    if description:
        description_metadata["descriptionText"] = _localized(description)

    entry = {
        "assistantDefinedSchemas": [],
        "assistantDefinedSchemaTraits": [],
        "authenticationPolicy": 0,
        "availabilityAnnotations": dict(ANY_PLATFORM),
        "descriptionMetadata": description_metadata,
        "effectiveBundleIdentifiers": [],
        "fullyQualifiedTypeName": fq,
        "identifier": identifier,
        "isAuthPolExplicit": False,
        "isDiscoverable": True,
        "mangledTypeName": mangled,
        "mangledTypeNameByBundleIdentifier": {},
        "mangledTypeNameByBundleIdentifierV2": {},
        "mangledTypeNameV2": mangled,
        "openAppWhenRun": open_app,
        "outputFlags": 0,
        "parameters": params,
        "presentationStyle": 0,
        "requiredCapabilities": [],
        # 1 = arka planda çalışır, 2 = uygulamayı öne getirir.
        "supportedModes": 2 if open_app else 1,
        "systemProtocolMetadata": [],
        "systemProtocolMetadataV2": [],
        "systemProtocols": [],
        "title": _localized(title),
        "typeSpecificMetadata": [],
        "visibilityMetadata": {"assistantOnly": False, "isDiscoverable": True},
    }

    if params:
        # Parametre özeti olmadan Kestirmeler/Siri parametreleri göstermez.
        summary = " ".join("${%s}" % p["name"] for p in params)
        entry["actionConfiguration"] = {
            "actionSummary": {
                "wrapper": {
                    "otherParameterIdentifiers": [],
                    "summaryString": {
                        "formatString": f"{title} {summary}".strip(),
                        "parameterIdentifiers": [p["name"] for p in params],
                    },
                }
            }
        }
    return identifier, entry


# ---------------------------------------------------------------------------
# App Shortcuts (Siri cümleleri)
# ---------------------------------------------------------------------------

def _phrase_from_segments(segments: list[dict], intent_fq: str) -> str:
    """İnterpolasyon parçalarını ``${...}`` şablonuna çevirir."""
    parts = []
    for seg in segments:
        kind = seg.get("valueKind")
        if kind == "RawLiteral":
            parts.append(str(seg.get("value") or ""))
        elif kind == "Enum":
            name = (seg.get("value") or {}).get("name")
            if name != "applicationName":
                raise MetadataError(
                    f"{intent_fq}: cümlede desteklenmeyen \\(.{name}) kullanımı."
                )
            parts.append("${applicationName}")
        elif kind == "KeyPath":
            path = (seg.get("value") or {}).get("path") or ""
            parts.append("${%s}" % path.lstrip("$"))
        else:
            raise MetadataError(
                f"{intent_fq}: cümlede desteklenmeyen parça türü {kind!r}."
            )
    phrase = "".join(parts)
    # Derleyici interpolasyonun sonuna boş bir RawLiteral ekler.
    return " ".join(phrase.split())


def _shortcut_entry(initcall: dict) -> dict:
    intent_arg = _arg(initcall, "intent")
    if not intent_arg:
        raise MetadataError("AppShortcut(intent:) argümanı okunamadı.")
    intent_fq = intent_arg.get("type") or ""
    action_id = _short_name(intent_fq)

    phrases_arg = _arg(initcall, "phrases")
    phrase_templates = []
    for item in (phrases_arg or {}).get("value") or []:
        if item.get("valueKind") != "InterpolatedStringLiteral":
            raise MetadataError(
                f"{action_id}: Siri cümleleri \"...\\(.applicationName)...\" "
                "biçiminde yazılmalı."
            )
        segments = (item.get("value") or {}).get("segments") or []
        phrase = _phrase_from_segments(segments, intent_fq)
        if "${applicationName}" not in phrase:
            raise MetadataError(
                f"{action_id}: her Siri cümlesi \\(.applicationName) içermek "
                f"zorundadır, şu cümle içermiyor: {phrase!r}"
            )
        phrase_templates.append(_localized(phrase))
    if not phrase_templates:
        raise MetadataError(f"{action_id}: en az bir Siri cümlesi gerekir.")

    entry = {
        "actionIdentifier": action_id,
        "availabilityAnnotations": dict(ANY_PLATFORM),
        "phraseTemplates": phrase_templates,
    }

    short_title = _arg(initcall, "shortTitle")
    if short_title and short_title.get("valueKind") == "RawLiteral":
        entry["shortTitle"] = _localized(short_title.get("value") or action_id)
    image = _arg(initcall, "systemImageName")
    if image and image.get("valueKind") == "RawLiteral":
        entry["systemImageName"] = image.get("value")
    return entry


def _collect_shortcuts(record: dict) -> list[dict]:
    props = _props(record)
    prop = props.get("appShortcuts")
    if not prop:
        raise MetadataError(
            f"{record.get('typeName')}: `static var appShortcuts` bulunamadı."
        )
    value = prop.get("value") or {}
    members = value.get("members") or []
    shortcuts = []
    for member in members:
        kind = member.get("kind")
        if kind == "buildExpression":
            element = member.get("element") or {}
            if element.get("valueKind") != "InitCall":
                raise MetadataError(
                    "appShortcuts içinde yalnızca doğrudan AppShortcut(...) "
                    "çağrıları desteklenir (koşul/döngü kullanılamaz)."
                )
            shortcuts.append(_shortcut_entry(element))
        else:
            raise MetadataError(
                f"appShortcuts içinde desteklenmeyen yapı: {kind!r} "
                "(if/for gibi yapılar derleme zamanında çözülemiyor)."
            )
    if not shortcuts:
        raise MetadataError("appShortcuts boş; Siri hiçbir cümle görmez.")
    return shortcuts


# ---------------------------------------------------------------------------
# Üst düzey
# ---------------------------------------------------------------------------

def build_metadata(const_values: list[dict], tools_version: str = DEFAULT_TOOLS_VERSION) -> dict:
    """``.swiftconstvalues`` kayıtlarından extract.actionsdata sözlüğü üretir."""
    actions: dict[str, dict] = {}
    auto_shortcuts: list[dict] = []
    provider_mangled: str | None = None

    for record in const_values:
        if record.get("kind") not in ("struct", "class", "enum"):
            continue
        conf = _conformances(record)

        for proto, label in UNSUPPORTED_PROTOCOLS.items():
            if proto in conf:
                raise MetadataError(
                    f"{record.get('typeName')}: {label} bu üreticide "
                    "desteklenmiyor. Entity/enum/query kullanmak için tam "
                    "Xcode kurulumu gerekir."
                )

        if INTENT_PROTOCOL in conf:
            identifier, entry = _action_entry(record)
            if identifier in actions:
                raise MetadataError(f"Aynı adlı iki intent: {identifier}")
            actions[identifier] = entry

        if PROVIDER_PROTOCOL in conf:
            if provider_mangled is not None:
                raise MetadataError(
                    "Birden fazla AppShortcutsProvider bulundu; yalnızca biri "
                    "olabilir."
                )
            provider_mangled = record.get("mangledTypeName")
            auto_shortcuts = _collect_shortcuts(record)

    if not actions:
        raise MetadataError(
            "Hiç AppIntent bulunamadı. swiftc'ye -emit-const-values ve "
            "-const-gather-protocols-file verildiğinden emin olun."
        )

    # Her kısayolun işaret ettiği intent gerçekten var mı?
    for sc in auto_shortcuts:
        if sc["actionIdentifier"] not in actions:
            raise MetadataError(
                f"Kısayol bilinmeyen bir intent'e işaret ediyor: "
                f"{sc['actionIdentifier']}"
            )

    data = {
        "actions": actions,
        "assistantEntities": [],
        "assistantIntentNegativePhrases": [],
        "assistantIntents": [],
        "autoShortcuts": auto_shortcuts,
        "entities": {},
        "enums": [],
        "generator": {"name": "mek-appintents-metadata", "version": tools_version},
        "negativePhrases": [],
        "queries": {},
        "shortcutTileColor": 14,
        "version": 1,
    }
    if provider_mangled:
        data["autoShortcutProviderMangledName"] = provider_mangled
    return data


def write_bundle(metadata: dict, bundle_path: str, tools_version: str = DEFAULT_TOOLS_VERSION) -> str:
    """Metadata.appintents klasörünü .app paketinin içine yazar."""
    resources = os.path.join(bundle_path, "Contents", "Resources")
    if not os.path.isdir(resources):
        raise MetadataError(f"Resources klasörü yok: {resources}")
    out_dir = os.path.join(resources, "Metadata.appintents")
    os.makedirs(out_dir, exist_ok=True)
    # Sistem ayrıştırıcısı boşluksuz, ayırıcıları sıkıştırılmış JSON bekliyor
    # (sistem uygulamalarındaki dosyalar tek satır).
    with open(os.path.join(out_dir, "extract.actionsdata"), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(os.path.join(out_dir, "version.json"), "w", encoding="utf-8") as fh:
        json.dump({"toolsVersion": tools_version, "version": FORMAT_VERSION}, fh, indent=2)
    return out_dir


def verify_app_name(bundle_path: str, metadata: dict) -> None:
    """``${applicationName}`` çözülebiliyor mu diye Info.plist'i denetler.

    Siri cümleleri uygulama adını kullanır; ad yoksa cümleler hiç kaydolmaz.
    """
    info = os.path.join(bundle_path, "Contents", "Info.plist")
    if not os.path.isfile(info):
        raise MetadataError(f"Info.plist yok: {info}")
    with open(info, "rb") as fh:
        plist = plistlib.load(fh)
    if not (plist.get("CFBundleDisplayName") or plist.get("CFBundleName")):
        raise MetadataError(
            "Info.plist'te CFBundleName/CFBundleDisplayName yok; "
            "${applicationName} çözülemez ve Siri cümleleri kaydolmaz."
        )
    if not plist.get("CFBundleIdentifier"):
        raise MetadataError("Info.plist'te CFBundleIdentifier yok.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--constvalues", required=True,
                    help="swiftc -emit-const-values-path ile üretilen dosya")
    ap.add_argument("--bundle", required=True, help=".app paketinin yolu")
    ap.add_argument("--tools-version", default=DEFAULT_TOOLS_VERSION)
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="Üretilen JSON'u okunur biçimde yazdır")
    args = ap.parse_args(argv)

    try:
        with open(args.constvalues, encoding="utf-8") as fh:
            const_values = json.load(fh)
    except FileNotFoundError:
        print(f"hata: const-values dosyası yok: {args.constvalues}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"hata: const-values dosyası bozuk: {exc}", file=sys.stderr)
        return 1

    try:
        metadata = build_metadata(const_values, args.tools_version)
        verify_app_name(args.bundle, metadata)
        out_dir = write_bundle(metadata, args.bundle, args.tools_version)
    except MetadataError as exc:
        print(f"hata: {exc}", file=sys.stderr)
        return 1

    if args.do_print:
        print(json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True))
    names = ", ".join(sorted(metadata["actions"]))
    phrases = sum(len(s["phraseTemplates"]) for s in metadata["autoShortcuts"])
    print(f"App Intents meta verisi yazıldı: {out_dir}")
    print(f"  intent: {names}")
    print(f"  Siri cümlesi: {phrases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
