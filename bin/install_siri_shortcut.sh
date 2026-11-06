#!/bin/bash
# Mek — Siri Kestirme Kurulum Aracı
# macOS Kestirmeler (Shortcuts) uygulamasına "Mek" sesli komut kestirmesini kurar.
set -e

DIR="$(cd "$(dirname "$0")"/.. && pwd)"
PYTHON="$HOME/.jarvis/venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

echo "Mek Siri Kestirmesi oluşturuluyor..."
RAW_PATH="/tmp/Mek_raw.shortcut"
SHORTCUT_PATH="/tmp/Mek.shortcut"

"$PYTHON" - <<'EOF'
import plistlib, uuid

u1 = str(uuid.uuid4()).upper()
u2 = str(uuid.uuid4()).upper()

shortcut_data = {
    "WFWorkflowMinimumClientVersion": 900,
    "WFWorkflowClientVersion": "2600.0.0",
    "WFWorkflowIcon": {
        "WFWorkflowIconGlyphNumber": 59844,
        "WFWorkflowIconStartColor": 4282601983
    },
    "WFWorkflowTypes": ["NCWidget", "WatchKit", "Siri"],
    "WFWorkflowInputContentItemClasses": ["WFStringContentItem"],
    "WFWorkflowActions": [
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.ask",
            "WFWorkflowActionParameters": {
                "UUID": u1,
                "WFAskActionPrompt": "Mek'e ne söylemek istersiniz?",
                "WFInputType": "Text",
                "WFAllowsMultilineText": False
            }
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
            "WFWorkflowActionParameters": {
                "UUID": u2,
                "WFURL": {
                    "Value": {
                        "string": "http://127.0.0.1:45728/siri?q=￼",
                        "attachmentsByRange": {
                            "{33, 1}": {
                                "OutputName": "Belirtilen Metin",
                                "OutputUUID": u1,
                                "Type": "ActionOutput"
                            }
                        }
                    },
                    "WFSerializationType": "WFTextTokenString"
                }
            }
        },
        {
            "WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
            "WFWorkflowActionParameters": {
                "WFText": {
                    "Value": {
                        "string": "￼",
                        "attachmentsByRange": {
                            "{0, 1}": {
                                "OutputName": "URL’nin İçeriği",
                                "OutputUUID": u2,
                                "Type": "ActionOutput"
                            }
                        }
                    },
                    "WFSerializationType": "WFTextTokenString"
                }
            }
        }
    ]
}

with open("/tmp/Mek_raw.shortcut", "wb") as f:
    plistlib.dump(shortcut_data, f, fmt=plistlib.FMT_BINARY)
EOF

if command -v shortcuts >/dev/null 2>&1; then
    shortcuts sign --mode people-who-know-me --input "$RAW_PATH" --output "$SHORTCUT_PATH" 2>/dev/null || cp "$RAW_PATH" "$SHORTCUT_PATH"
    open "$SHORTCUT_PATH"
else
    open "$RAW_PATH"
fi

echo "Kurulum hazır! Artık Siri'ye 'Hey Siri, Mek' diyerek Mek'i doğrudan sesle yönetebilirsiniz."
