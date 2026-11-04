"""Screen Lock page for the Tk display settings and GTK power manager."""
import screenlock


def tk_page(parent):
    import tkinter as tk
    from tkinter import ttk

    frame = ttk.Frame(parent, padding=26)
    data = screenlock.load()
    policy = tk.StringVar(value=screenlock.POLICIES[data["policy"]])
    idle = tk.StringVar(value=str(data["idle_seconds"]))
    status = tk.StringVar(value=screenlock.TIMING)
    ttk.Label(frame, text="Screen Lock", font=("DejaVu Sans", 20, "bold")).pack(anchor="w", pady=(0, 18))
    ttk.Label(frame, text=screenlock.DETAIL, wraplength=650).pack(anchor="w", pady=(0, 18))
    ttk.Label(frame, text="Automatic locking").pack(anchor="w")
    ttk.Combobox(frame, textvariable=policy, values=list(screenlock.POLICIES.values()), state="readonly", width=32).pack(anchor="w", pady=(6, 18))
    ttk.Label(frame, text="Idle timeout in seconds (0 disables the idle timer)").pack(anchor="w")
    ttk.Spinbox(frame, from_=0, to=86400, increment=60, textvariable=idle, width=12).pack(anchor="w", pady=(6, 18))
    ttk.Label(frame, text="Follow session defaults uses the existing session policy and timeout.", wraplength=650).pack(anchor="w", pady=(0, 18))

    def apply():
        try:
            selected = next(key for key, label in screenlock.POLICIES.items() if label == policy.get())
            screenlock.save(selected, int(idle.get()))
            status.set("Saved. " + screenlock.TIMING)
        except (OSError, ValueError) as error:
            status.set(str(error))

    ttk.Button(frame, text="Save screen-lock settings", command=apply).pack(anchor="w")
    ttk.Label(frame, textvariable=status, wraplength=650).pack(anchor="w", pady=18)
    return frame
