#!/usr/bin/env python3
"""Graphical physical-monitor arranger, rendered inside a Kilix app tab."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox

import displays as core

BG = "#101827"
PANEL = "#192438"
TEXT = "#e6edf7"
MUTED = "#9cacc4"
ACCENT = "#65d6c4"


def dimensions(output):
    return core.dimensions(output)


def normalize(outputs):
    active = [o for o in outputs if o["enabled"]]
    if active:
        x, y = min(o["x"] for o in active), min(o["y"] for o in active)
        for output in active:
            output["x"] -= x
            output["y"] -= y


class Arranger:
    def __init__(self, root, backend, config, state):
        self.root, self.backend = root, backend
        self.config, self.state_dir = config, state
        self.channel = self.worker = None
        self.drag = None
        self.preview_deadline = None
        root.title("Displays · RC5 preview")
        root.geometry("1100x740")
        root.minsize(850, 680)
        root.configure(bg=BG)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("DejaVu Sans", 11))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("DejaVu Sans", 23, "bold"))
        style.configure("TButton", font=("DejaVu Sans", 10), padding=(14, 10), background="#293b56", foreground=TEXT, borderwidth=0)
        style.map("TButton", background=[("active", "#395273"), ("disabled", "#202b3e")])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG)
        style.map("Accent.TButton", background=[("active", "#9ee9dd"), ("disabled", "#355654")])
        style.configure("TCheckbutton", background=BG, foreground=TEXT, font=("DejaVu Sans", 11))
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TCombobox", padding=7, font=("DejaVu Sans", 11), arrowcolor=TEXT, bordercolor="#395273")
        style.map("TCombobox", fieldbackground=[("readonly", "#293b56")],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", "#293b56")],
                  selectforeground=[("readonly", TEXT)])
        root.option_add("*TCombobox*Listbox.background", PANEL)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", "#395273")
        outer = ttk.Frame(root, padding=26)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        ttk.Label(outer, text="Arrange your displays", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(outer, text="Drag monitors to match your desk. Changes apply only when you preview.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(7, 20))
        tools = ttk.Frame(outer)
        tools.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        for text, command in (("Extend →", self.extend), ("Mirror", self.mirror), ("Reset draft", self.reload)):
            ttk.Button(tools, text=text, command=command).pack(side="left", padx=(0, 9))
        self.canvas = tk.Canvas(outer, bg=PANEL, height=300, highlightthickness=0, cursor="hand2")
        self.canvas.grid(row=3, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        controls = ttk.Frame(outer)
        controls.grid(row=4, column=0, sticky="ew", pady=18)
        self.selected_label = ttk.Label(controls, text="", font=("DejaVu Sans", 12, "bold"))
        self.selected_label.grid(row=0, column=0, sticky="w", columnspan=4, pady=(0, 10))
        self.monitor = ttk.Combobox(controls, state="readonly", width=20)
        self.monitor.grid(row=0, column=2, sticky="w", padx=(0, 18), pady=(0, 10))
        self.monitor.bind("<<ComboboxSelected>>", self.choose_monitor)
        self.mode, self.rate, self.rotation, self.zoom = (tk.StringVar() for _ in range(4))
        self.enabled = tk.BooleanVar()
        self.boxes = []
        for column, (label, variable) in enumerate((("Resolution", self.mode), ("Refresh rate (Hz)", self.rate), ("Rotation", self.rotation), ("Scale", self.zoom))):
            ttk.Label(controls, text=label, style="Muted.TLabel").grid(row=1, column=column, sticky="w", padx=(0, 18))
            box = ttk.Combobox(controls, textvariable=variable, state="readonly", width=16)
            box.grid(row=2, column=column, sticky="w", padx=(0, 18), pady=(6, 0))
            box.bind("<<ComboboxSelected>>", lambda event: self.edit())
            self.boxes.append(box)
        self.boxes[2]["values"] = ("normal", "left", "right", "inverted")
        ttk.Checkbutton(controls, text="Enabled", variable=self.enabled, command=self.edit).grid(row=3, column=0, sticky="w", pady=(12, 0))
        ttk.Button(controls, text="Make primary", command=self.make_primary).grid(row=3, column=1, sticky="w", pady=(12, 0))
        self.status = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status, style="Muted.TLabel", wraplength=1000).grid(row=5, column=0, sticky="w", pady=(0, 14))
        footer = ttk.Frame(outer)
        footer.grid(row=6, column=0, sticky="ew")
        ttk.Label(footer, text="RC5 TEST BUILD  ·  X11", style="Muted.TLabel").pack(side="left")
        self.apply_button = ttk.Button(footer, text="Preview changes", style="Accent.TButton", command=self.apply)
        self.apply_button.pack(side="right")
        self.keep_button = ttk.Button(footer, text="Keep this layout", style="Accent.TButton", command=lambda: self.reply(b"confirm"))
        self.revert_button = ttk.Button(footer, text="Revert now", command=lambda: self.reply(b"revert"))
        self.reload()
        root.protocol("WM_DELETE_WINDOW", self.close)

    def reload(self):
        if self.channel:
            return
        try:
            self.current = self.backend.query()
            self.layout = core.snapshot(self.current)
            self.monitor["values"] = [o["name"] for o in self.layout["outputs"]]
            self.selected = 0
            self.select(0)
            self.status.set("Select a monitor to edit it. Preview reverts automatically unless you choose Keep.")
            self.draw()
        except Exception as exc:
            messagebox.showerror("Cannot read physical displays", str(exc), parent=self.root)

    def select(self, index):
        self.selected = index
        self.monitor.current(index)
        output = self.layout["outputs"][index]
        source = self.current["outputs"][index]
        modes = [m for m in source["modes"] if re.fullmatch(r"\d+x\d+", m)]
        self.selected_label["text"] = f'{index + 1}   {output["name"]}'
        self.boxes[0]["values"] = modes
        self.mode.set(output["mode"] or (modes[0] if modes else ""))
        rates = source["modes"].get(self.mode.get(), [])
        self.boxes[1]["values"] = rates
        self.rate.set(output["rate"] or (rates[0] if rates else ""))
        self.rotation.set(output["rotation"])
        self.zoom_choices = {f"{100 * scale:g}%": scale for scale in core.SCALES}
        current_zoom = f'{100 * output["scale"]:.10g}%'
        self.zoom_choices[current_zoom] = output["scale"]
        self.boxes[3]["values"] = tuple(self.zoom_choices)
        self.zoom.set(current_zoom)
        self.enabled.set(output["enabled"])

    def choose_monitor(self, event):
        if self.channel:
            self.monitor.current(self.selected)
            return
        self.select(self.monitor.current())
        self.draw()

    def edit(self):
        if self.channel:
            self.select(self.selected)
            return
        o = self.layout["outputs"][self.selected]
        rates = self.current["outputs"][self.selected]["modes"].get(self.mode.get(), [])
        if self.rate.get() not in rates:
            self.rate.set(rates[0] if rates else "")
        self.boxes[1]["values"] = rates
        zoom = self.zoom_choices[self.zoom.get()]
        if zoom != o["scale"]:
            o.update(scale=zoom, filter="" if zoom == 1 else "bilinear")
        o.update(mode=self.mode.get(), rate=self.rate.get(), rotation=self.rotation.get(), enabled=self.enabled.get())
        if not o["enabled"]:
            o["primary"] = False
        active = [x for x in self.layout["outputs"] if x["enabled"]]
        if active and not any(x["primary"] for x in active):
            active[0]["primary"] = True
        self.draw()

    def make_primary(self):
        if self.channel:
            return
        chosen = self.layout["outputs"][self.selected]
        if not chosen["enabled"]:
            return
        for output in self.layout["outputs"]:
            output["primary"] = output is chosen
        self.draw()

    def extend(self):
        if self.channel:
            return
        x = 0
        for o in self.layout["outputs"]:
            if o["enabled"]:
                o["x"], o["y"] = x, 0
                x += dimensions(o)[0]
        self.draw()

    def mirror(self):
        if self.channel:
            return
        for o in self.layout["outputs"]:
            o["x"], o["y"] = 0, 0
        self.draw()
        self.status.set("Mirroring uses equal positions. Match each monitor's logical size using its resolution and scale.")

    def draw(self):
        if not hasattr(self, "layout"):
            return
        c = self.canvas
        c.delete("all")
        width, height = max(c.winfo_width(), 400), max(c.winfo_height(), 220)
        outputs = self.layout["outputs"]
        active = [o for o in outputs if o["enabled"]]
        xmax = max((o["x"] + dimensions(o)[0] for o in active), default=1920)
        ymax = max((o["y"] + dimensions(o)[1] for o in active), default=1080)
        if not self.drag:
            self.scale = min((width - 100) / max(xmax, 1), (height - 100) / max(ymax, 1))
            self.origin = ((width - xmax*self.scale)/2, (height - ymax*self.scale)/2)
        for x in range(20, width, 24):
            for y in range(20, height, 24):
                c.create_oval(x, y, x+1, y+1, fill="#2a3951", outline="")
        self.rectangles = []
        for i, o in enumerate(outputs):
            w, h = dimensions(o)
            x, y = self.origin[0]+o["x"]*self.scale, self.origin[1]+o["y"]*self.scale
            if not o["enabled"]:
                x, y, w, h = 14+i*140, height-45, 125/self.scale, 32/self.scale
            rect = (x, y, x+w*self.scale, y+h*self.scale)
            self.rectangles.append(rect)
            c.create_rectangle(*rect, fill="#25495a" if o["enabled"] else "#202b3e", outline=ACCENT if i == self.selected else "#57728e", width=3 if i == self.selected else 1)
            cx, cy = (rect[0]+rect[2])/2, (rect[1]+rect[3])/2
            if o["enabled"]:
                c.create_text(cx, cy-30, text=str(i+1), font=("DejaVu Sans", 28, "bold"), fill=TEXT)
                c.create_text(cx, cy+7, text=o["name"] + ("  ★ PRIMARY" if o["primary"] else ""), font=("DejaVu Sans", 10, "bold"), fill=TEXT)
                c.create_text(cx, cy+30, text=f'{o["mode"]}  ·  {o["rate"]} Hz  ·  {100 * o["scale"]:g}%', font=("DejaVu Sans", 10), fill=MUTED)
            else:
                c.create_text(cx, cy, text=o["name"]+" · off", fill=MUTED)

    def press(self, event):
        if self.channel:
            return
        for i in reversed(range(len(self.rectangles))):
            x1, y1, x2, y2 = self.rectangles[i]
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.select(i)
                o = self.layout["outputs"][i]
                if o["enabled"]:
                    self.drag = (event.x, event.y, o["x"], o["y"])
                self.draw()
                break

    def motion(self, event):
        if not self.drag:
            return
        x, y, ox, oy = self.drag
        o = self.layout["outputs"][self.selected]
        o["x"] = round(ox+(event.x-x)/self.scale)
        o["y"] = round(oy+(event.y-y)/self.scale)
        # Snap adjoining edges and aligned tops/bottoms in display pixels.
        w, h = dimensions(o)
        for other in self.layout["outputs"]:
            if other is o or not other["enabled"]:
                continue
            ow, oh = dimensions(other)
            for edge in (other["x"], other["x"]+ow, other["x"]-w):
                if abs(o["x"]-edge)*self.scale < 16:
                    o["x"] = edge
            for edge in (other["y"], other["y"]+oh, other["y"]-h):
                if abs(o["y"]-edge)*self.scale < 16:
                    o["y"] = edge
        self.draw()

    def release(self, event):
        if self.drag:
            self.drag = None
            normalize(self.layout["outputs"])
            self.draw()

    def apply(self):
        if self.channel or self.worker:
            return
        try:
            normalize(self.layout["outputs"])
            core.validate(self.layout, self.backend.query())
            parent, process = core.start_worker(self.backend, copy.deepcopy(self.layout), self.config, self.state_dir, 20)
            parent.setblocking(False)
            self.channel, self.worker = parent, process
            self.apply_button.state(["disabled"])
            self.status.set("Applying preview… The previous layout is held by an independent rollback worker.")
            self.root.after(100, self.poll)
        except Exception as exc:
            messagebox.showerror("Cannot preview layout", str(exc), parent=self.root)

    def reply(self, value):
        if self.channel:
            try:
                self.channel.send(value)
            except OSError:
                pass
            self.keep_button.pack_forget()
            self.revert_button.pack_forget()
            self.preview_deadline = None
            self.status.set("Finishing display change…")

    def poll(self):
        if not self.channel:
            return
        try:
            message = self.channel.recv(65536)
        except BlockingIOError:
            message = None
        if message == b"ready":
            self.preview_deadline = time.monotonic()+20
            self.keep_button.pack(side="right", padx=9)
            self.revert_button.pack(side="right", padx=9)
        elif message is not None:
            self.channel.close()
            self.channel = None
            self.reap()
            self.keep_button.pack_forget()
            self.revert_button.pack_forget()
            self.apply_button.state(["!disabled"])
            self.preview_deadline = None
            self.reload()
            result = json.loads(message) if message else {"error": "Preview worker ended unexpectedly; inspect the physical displays.", "confirmed": False}
            self.status.set("Layout confirmed and saved." if result["confirmed"] else "Previous layout restored.")
            if result["error"]:
                self.status.set("Display operation failed. Check the monitors before continuing.")
                messagebox.showerror("Display change failed", result["error"], parent=self.root)
            return
        if self.preview_deadline:
            remaining = max(0, int(self.preview_deadline-time.monotonic()))
            self.status.set(f"Keep this layout? Reverting automatically in {remaining}s unless you confirm.")
        self.root.after(100, self.poll)

    def reap(self):
        if self.worker:
            if self.worker.poll() is not None:
                self.worker = None
            else:
                self.root.after(100, self.reap)

    def close(self):
        if self.channel:
            self.channel.close()  # EOF asks the independent worker to revert.
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-display", required=True)
    parser.add_argument("--target-authority", required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    if os.getuid() == 0 or not re.fullmatch(r":\d+(?:\.\d+)?", args.target_display):
        parser.error("Use a local physical X display as the desktop user, without sudo")
    env = {**os.environ, "DISPLAY": args.target_display, "XAUTHORITY": args.target_authority}
    config, state = core.private_dir(args.config_dir), core.private_dir(args.state_dir)
    root = tk.Tk()
    Arranger(root, core.RandR(env), config, state)
    root.mainloop()


if __name__ == "__main__":
    main()
