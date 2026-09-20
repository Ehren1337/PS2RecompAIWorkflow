"""State-checked retail menu navigation using normal input, not guest calls.

    py -3 -B tools/rumble_navigate.py status
    py -3 -B tools/rumble_navigate.py wait track --timeout 60
    py -3 -B tools/rumble_navigate.py route vehicle

Requires run-ntsc.ps1's retail inspector watches. Writes no files, launches
nothing, never restarts, and stops on unknown states or unsupported runtime work.
Windows input is a development helper, not a dependency of the native port.
"""
import argparse
import json
from pathlib import Path
import struct
import time

import rumble_research as research

MENUS = {"main": 100, "mode": 200, "vehicle": 1200, "track": 1300}


def snapshot():
    # Windows can briefly deny an open while the inspector replaces its file.
    # Retry that publication window, but retain the normal age/PID checks and
    # fail on persistent I/O errors instead of acting on a cached snapshot.
    for attempt in range(6):
        try:
            report = json.loads(research.REPORT.read_text(encoding="utf-8-sig"))
            break
        except (PermissionError, FileNotFoundError):
            if attempt == 5:
                raise
            time.sleep(0.05)
    sample = report.get("snapshot")
    if report.get("schema_version") != 1 or not sample or sample.get("error"):
        raise ValueError("No valid inspector sample")
    if Path(sample["disc"]["root"]).resolve() != research.RETAIL.resolve():
        raise ValueError("Expected verified retail runtime")
    watches = {w["name"]: bytes.fromhex(w["bytes"]) for w in sample["memory_watches"] if "bytes" in w}
    state = {"pid": report["process_id"], "sequence": sample["sequence"],
             "runtime": report["runtime_state"], "pc": int(sample["cpu"]["pc"], 0),
             "age": time.time() - sample["captured_unix_ms"] / 1000,
             "metrics": {m["name"]: m["value"] for service in sample["iop"]["services"]
                         if "Rumble" in service["name"] for m in service["metrics"]},
             "menu": None, "pending": None, "ready": False}
    # Directly watched globals are available even before the frontend allocates
    # its state. A null frontend pointer during startup is expected.
    if "rumble_cycle" not in watches or "rumble_transition" not in watches:
        raise ValueError("Retail menu watches missing; launch with current run-ntsc.ps1")
    state["cycle"] = struct.unpack("<I", watches["rumble_cycle"])[0]
    state["transition"] = struct.unpack_from("<h", watches["rumble_transition"], 4)[0]
    head = watches.get("rumble_frontend")
    if head is not None:
        state["menu"], state["previous"], state["pending"] = struct.unpack_from("<hhh", head)
        state["selected"] = head[8]
        state["ready"] = (state["menu"] in MENUS.values() and state["pending"] == -1
                          and not head[6] and not head[7] and state["transition"] == 0
                          and struct.unpack_from("<h", head, 10)[0] <= 0)
    return state


def compact(s):
    return {k: s[k] for k in ("pid", "sequence", "cycle", "menu", "pending", "transition", "ready")} | {
        "pc": hex(s["pc"]), "metrics": {k: v for k, v in s["metrics"].items()
        if k in {"unsupported_commands", "movie_playing", "movie_closes", "music_playing", "engine_updates"}}}


class Navigator:
    def __init__(self, timeout):
        research.verify("retail")
        self.timeout = timeout
        self.pid = snapshot()["pid"]

    def read(self):
        s = snapshot()
        if s["pid"] != self.pid:
            raise RuntimeError("Runner changed; route stopped without sending more input")
        if research.process_state(self.pid) != "runner alive" or s["runtime"] != "running":
            raise RuntimeError("Expected runner is no longer running")
        if s["age"] > 5:
            raise RuntimeError("Inspector is stale; no input sent")
        if s["metrics"].get("unsupported_commands", 0):
            raise RuntimeError("Runtime has unsupported work; route stopped: " + json.dumps(compact(s)))
        return s

    def wait(self, label, predicate, *, after=-1, advancing=None):
        print("Waiting: " + label, flush=True)
        deadline, previous = time.monotonic() + self.timeout, None
        while True:
            s = self.read()
            fresh = s["sequence"] > after
            progress = advancing is None or (previous is not None and
                s["sequence"] > previous["sequence"] and advancing(s) != advancing(previous))
            if fresh and predicate(s) and progress:
                print("Reached: " + label + " " + json.dumps(compact(s)), flush=True)
                return s
            if fresh and (previous is None or s["sequence"] > previous["sequence"]):
                previous = s if predicate(s) else None
            if time.monotonic() >= deadline:
                raise TimeoutError("Wait expired; runner left untouched: " + json.dumps(compact(s)))
            time.sleep(0.2)

    def menu(self, name, after=-1):
        return self.wait(name + " menu ready and cycling",
                         lambda s: s["menu"] == MENUS[name] and s["ready"],
                         after=after, advancing=lambda s: s["cycle"])

    def press(self, key, hold_ms=1000):
        # Revalidate immediately before the paired keypress. The shared helper
        # verifies the executable/window identity and always releases the key.
        self.read()
        research.press_key(key, hold_ms)
        return self.read()["sequence"]

    def route(self, target):
        s = self.read()
        if (not s["metrics"].get("music_playing") and not s["metrics"].get("movie_closes")
                and not s["metrics"].get("movie_playing")):
            self.wait("memory-card Continue", lambda s: s["pc"] == 0x129380)
            self.press("cross")
        while self.read()["metrics"].get("movie_closes", 0) < 2:
            s = self.wait("opening movie started", lambda s: bool(s["metrics"].get("movie_playing")))
            closes = s["metrics"].get("movie_closes", 0)
            if closes == 0:
                # Retail startup 0x1837CC passes a null skip callback for
                # OPENING/EAGAMES.FLM. Normal input cannot skip that movie.
                print("EA logo has no input callback; waiting for its normal end", flush=True)
                seq = s["sequence"]
            else:
                # 0x183B24 supplies 0x1A7650 for INTRO.FLM; it polls Start or
                # Cross on either pad. Send Start immediately on observed start.
                seq = self.press("start", 2000)
            self.wait("movie ended", lambda s: s["metrics"].get("movie_closes", 0) > closes, after=seq)
        # Only traverse verified frontend edges; do not confirm track selection
        # into loading until that next route has been investigated and validated.
        edges = {100: ("main", "mode"), 200: ("mode", "vehicle"), 1200: ("vehicle", "track")}
        for _ in range(4):
            s = self.read()
            if s["menu"] == MENUS[target]:
                return self.menu(target)
            if s["pending"] in MENUS.values():
                name = next(name for name, code in MENUS.items() if code == s["pending"])
                self.menu(name)
                continue
            if s["menu"] not in edges:
                # Startup-to-frontend initialization may still be in progress.
                if not s["metrics"].get("music_playing"):
                    self.menu("main")
                    continue
                raise RuntimeError("No verified route from current menu: " + json.dumps(compact(s)))
            source, destination = edges[s["menu"]]
            if MENUS[target] < MENUS[source]:
                raise RuntimeError("Backward routes are not implemented; current runner left untouched")
            s = self.menu(source)
            if source in {"main", "mode"} and s["selected"] != 0:
                raise RuntimeError("Route requires the verified default menu selection")
            seq = self.press("cross")
            self.menu(destination, after=seq)
        raise RuntimeError("Route step limit reached")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("status", "wait", "route"))
    p.add_argument("target", choices=MENUS, nargs="?")
    p.add_argument("--timeout", type=float, default=45, help="Seconds per condition; never triggers a restart")
    a = p.parse_args()
    if not 1 <= a.timeout <= 120 or (a.command != "status" and not a.target):
        p.error("Supply a target for wait/route and a timeout in 1..120")
    try:
        n = Navigator(a.timeout)
        result = n.read() if a.command == "status" else n.menu(a.target) if a.command == "wait" else n.route(a.target)
        print(json.dumps(compact(result)))
    except (OSError, ValueError, RuntimeError, TimeoutError) as e:
        p.exit(1, str(e) + "\n")


if __name__ == "__main__":
    main()
