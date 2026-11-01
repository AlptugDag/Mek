#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mek ekran göstergesi: menü çubuğunda durum simgesi + Siri benzeri yüzen balon.

  dalga (gri)      dinliyor, uyandırma kelimesini bekliyor
  dalga (kırmızı)  konuşmanızı kaydediyor
  balon            'Mek' duyuldu / düşünüyor / konuşuyor, duyduğu cümleyle birlikte
"""
import json
import os
import subprocess

import objc
from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSBackingStoreBuffered,
                    NSColor, NSFont, NSImage, NSMakeRect, NSMenu, NSMenuItem, NSPanel, NSScreen,
                    NSStatusBar, NSTextField, NSVariableStatusItemLength, NSView,
                    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowStyleMaskBorderless,
                    NSWindowStyleMaskNonactivatingPanel, NSStatusWindowLevel, NSTextAlignmentCenter,
                    NSImageSymbolConfiguration)
from Foundation import NSObject, NSTimer
from Quartz import CGColorCreateGenericGray

STATE = os.path.expanduser("~/.jarvis/state/ui.json")
MEK = os.path.expanduser("~/.local/bin/mek")

LOOK = {  # state -> (SF Symbol, renk, balon metni)
    "listening": ("waveform", None, None),
    "hearing": ("waveform", None, None),
    "awake": ("waveform.circle.fill", "orange", "Dinliyorum…"),
    "thinking": ("ellipsis.circle.fill", "blue", "Düşünüyorum…"),
    "speaking": ("speaker.wave.2.fill", "green", None),
    "paused": ("mic.slash", None, None),
    "busy_mic": ("mic.slash.fill", "orange", None),
    "recording": ("record.circle", "red", None),
}
COLORS = {"red": NSColor.systemRedColor, "orange": NSColor.systemOrangeColor,
          "blue": NSColor.systemBlueColor, "green": NSColor.systemGreenColor}


class App(NSObject):
    def applicationDidFinishLaunching_(self, _):
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        menu = NSMenu.alloc().init()
        for title, sel in (("Durum", "status:"), ("Dinlemeyi aç / kapat", "toggle:"),
                           ("Kayıt modu", "recmode:"), ("Günlüğü aç", "openlog:")):
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            mi.setTarget_(self)
            menu.addItem_(mi)
        self.item.setMenu_(menu)
        self.last = None
        self._build_hud()
        self.tick_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.15, self, "tick:", None, True)

    def _build_hud(self):
        w, h = 520, 64
        scr = NSScreen.mainScreen().frame()
        rect = NSMakeRect(scr.origin.x + (scr.size.width - w) / 2, scr.origin.y + scr.size.height - h - 60, w, h)
        self.hud = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False)
        self.hud.setLevel_(NSStatusWindowLevel)
        self.hud.setOpaque_(False)
        self.hud.setBackgroundColor_(NSColor.clearColor())
        self.hud.setIgnoresMouseEvents_(True)
        self.hud.setHasShadow_(True)
        self.hud.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces)
        bg = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        bg.setWantsLayer_(True)
        bg.layer().setBackgroundColor_(CGColorCreateGenericGray(0.08, 0.88))
        bg.layer().setCornerRadius_(h / 2)
        self.label = NSTextField.labelWithString_("")
        self.label.setFrame_(NSMakeRect(20, 18, w - 40, 28))
        self.label.setFont_(NSFont.systemFontOfSize_weight_(17, 0.3))
        self.label.setTextColor_(NSColor.whiteColor())
        self.label.setAlignment_(NSTextAlignmentCenter)
        self.label.cell().setLineBreakMode_(4)  # sondan kırp
        bg.addSubview_(self.label)
        self.hud.setContentView_(bg)

    def tick_(self, _):
        try:
            with open(STATE, encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {"state": "paused", "text": ""}
        key = (st.get("state"), st.get("text"))
        if key == self.last:
            return
        self.last = key
        sym, color, hud_text = LOOK.get(st.get("state"), LOOK["listening"])
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(sym, "Mek")
        if color:
            cfg = NSImageSymbolConfiguration.configurationWithHierarchicalColor_(COLORS[color]())
            img = img.imageWithSymbolConfiguration_(cfg)
            img.setTemplate_(False)
        else:
            img.setTemplate_(True)
        self.item.button().setImage_(img)
        text = st.get("text") or hud_text
        if st.get("state") in ("awake", "thinking", "speaking") and text:
            self.label.setStringValue_(text)
            self.hud.orderFrontRegardless()
        else:
            self.hud.orderOut_(None)

    def _mek(self, *args):
        subprocess.Popen([MEK, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def status_(self, _):
        self._mek("durum nasıl")

    def toggle_(self, _):
        self._mek("__toggle_listen__")

    def recmode_(self, _):
        self._mek("__toggle_rec__")

    def openlog_(self, _):
        subprocess.Popen(["open", "-a", "Console", os.path.expanduser("~/.jarvis/mek.log")])


if __name__ == "__main__":
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = App.alloc().init()
    app.setDelegate_(delegate)
    app.run()
