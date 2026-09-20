"""State-checked retail menu navigation using normal input, not guest calls.

    py -3 -B tools/rumble_navigate.py status
    py -3 -B tools/rumble_navigate.py wait track --timeout 60
    py -3 -B tools/rumble_navigate.py route vehicle
    py -3 -B tools/rumble_navigate.py drive --start-node 155 --nodes 5
    py -3 -B tools/rumble_navigate.py race-status
    py -3 -B tools/rumble_navigate.py wait-results --timeout 120
    py -3 -B tools/rumble_navigate.py debug-status
    py -3 -B tools/rumble_navigate.py camera-status

Requires run-ntsc.ps1's retail inspector watches. Writes no files, launches
nothing, never restarts, and stops on unknown states or unsupported runtime work.
Windows input is a development helper, not a dependency of the native port.
The drive example uses a researched Car Go segment after the race countdown.
Node IDs belong to the current track. Route following remains experimental.
"""
import argparse
import ctypes
import json
import math
import os
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
    # Keep race waits on the same validated publication as menu/IOP state.
    # Reopening inspector.json in a predicate can race its atomic replacement.
    for name in ("race_phase", "race_clock"):
        value = watches.get(name)
        if value is not None:
            if len(value) != 4:
                raise ValueError("Invalid inspector watch size: " + name)
            state[name] = struct.unpack("<I", value)[0]
    ports = sample.get("pad", {}).get("ports", [])
    pad = ports[0][0] if ports and ports[0] else {}
    state["pad_reads"] = pad.get("read_count", 0)
    state["pad_released"] = (pad.get("last_read_ok", False) and
                             int(pad.get("buttons", "0"), 0) & 0xffff == 0xffff)
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
        return self.wait_released()["sequence"]

    def hold_until(self, keys, label, predicate, hold_ms=30000):
        """Normal input until verified state, with a hard limit and neutral wait.

        For driving experiments, keys may be ("cross", "left"). The predicate
        receives a checked inspector snapshot and may also read verified guest
        memory. Failure releases input; timeout never pretends the target won.
        The target may be transient: the returned neutral snapshot can differ
        after momentum or other game activity continues during release.
        """
        initial = self.read()
        if predicate(initial):
            return initial
        reached = False

        def observe():
            nonlocal reached
            reached = bool(predicate(self.read()))
            return reached

        research.press_keys(keys, hold_ms, until=observe)
        sample = self.wait_released()
        if not reached:
            raise TimeoutError("Input target not reached: " + label + "; keys released")
        print("Reached input target: " + label, flush=True)
        return sample

    def wait_released(self):
        released = self.read()
        # Posting key-up is asynchronous. Wait for a later guest pad read to
        # observe neutral input before another press can reuse the same key.
        # Fresh image timestamps alone do not prove the game sampled key-up.
        sample = self.wait("controller release sampled", lambda s:
                           s["pad_released"] and s["pad_reads"] > released["pad_reads"],
                           after=released["sequence"])
        return sample

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


class RaceProbe:
    """Read-only retail EE memory for bounded driving experiments on Windows.

    Track links are researched from 175000/177800/1780B0. The first outgoing
    link is a test route, not a reconstruction of AI strategy/shortcut choice.
    No game addresses are called, and no process memory is written.
    """
    def __init__(self, navigator, *, observe_finish=False, camera_only=False):
        if os.name != "nt":
            raise ValueError("Live race probing currently requires the Windows development host")
        from rumble_menu_research import MenuResearch
        self.nav = navigator
        s = self.nav.read()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.kernel
        k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.OpenProcess.restype = ctypes.c_void_p
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

        class Region(ctypes.Structure):
            _fields_ = [("base", ctypes.c_void_p), ("allocation", ctypes.c_void_p),
                        ("allocation_protect", ctypes.c_uint32), ("partition", ctypes.c_uint16),
                        ("size", ctypes.c_size_t), ("state", ctypes.c_uint32),
                        ("protect", ctypes.c_uint32), ("kind", ctypes.c_uint32)]

        k.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(Region), ctypes.c_size_t]
        k.VirtualQueryEx.restype = ctypes.c_size_t
        self.handle = k.OpenProcess(0x410, False, s["pid"])
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            original = MenuResearch("Retail")  # Verifies the owned ELF hash.

            def signature(address):
                for segment in original.segments:
                    offset = address - segment.address
                    if 0 <= offset and offset + 128 <= len(segment.data):
                        return segment.data[offset:offset + 128]
                raise ValueError("Retail signature outside load segments")

            first, second = signature(0x100000), signature(0x1c74d0)
            address, candidates = 0, []
            while address < 0x7fffffffffff:
                region = Region()
                if not k.VirtualQueryEx(self.handle, address, ctypes.byref(region), ctypes.sizeof(region)):
                    break
                following = (region.base or 0) + region.size
                if following <= address:
                    break
                if (region.state == 0x1000 and region.kind == 0x20000 and
                        region.protect in (4, 8, 0x40, 0x80) and 0x2000000 <= region.size <= 0x10000000):
                    block = self.read(region.base, 0x120000)
                    offset = block.find(first)
                    while offset >= 0:
                        candidate = region.base + offset - 0x100000
                        if self.read(candidate + 0x1c74d0, 128) == second:
                            candidates.append(candidate)
                        offset = block.find(first, offset + 1)
                address = following
            if len(candidates) != 1:
                raise ValueError("Expected one verified retail EE memory region")
            self.base = candidates[0]
            if camera_only:
                # Camera inspection must also work in menus, before a race has
                # allocated its player/track. Existing race callers stay strict.
                self.camera_state()
                return
            self.car = self.word(0x1f22a0)
            self.track = self.word(0x1f2740)
            count = struct.unpack("<h", self.guest(self.track + 2, 2))[0]
            if not self.car or not self.track or not 1 < count < 8192:
                raise ValueError("No valid live car/track table")
            self.nodes = self.guest(self.track + 4, count * 32)
            self.count = count
            self.finish_state() if observe_finish else self.state()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def read(self, address, size):
        result, got = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if (not self.kernel.ReadProcessMemory(self.handle, address, result, size, ctypes.byref(got)) or
                got.value != size):
            raise ctypes.WinError(ctypes.get_last_error())
        return result.raw

    def guest(self, address, size):
        if not 0 <= address < 0x2000000 or address + size > 0x2000000:
            raise ValueError("Read outside EE RAM")
        return self.read(self.base + address, size)

    def word(self, address):
        return struct.unpack("<I", self.guest(address, 4))[0]

    def floats(self, address, count):
        values = struct.unpack("<" + "f" * count, self.guest(address, count * 4))
        if not all(math.isfinite(x) and abs(x) < 1e7 for x in values):
            raise ValueError("Invalid live float values")
        return values

    def camera_state(self):
        """Read original camera viewport fields; no input, capture or GS writes.

        Retail1555F0 sets these fields;1557E0 derives its clear scissor from
        them.1670A0 sets the single-player camera to512x448, then the GS uses
        half-height coordinates. These are camera values, NOT a GS packet trace.
        """
        state = self.nav.read()
        slots = {"current": 0x1f1aa0, "player_0": 0x1f1b98,
                 "secondary_or_mirror": 0x1f1b9c}
        pointers = {name: self.word(at) for name, at in slots.items()}
        cameras = {}
        for name, pointer in pointers.items():
            if not pointer:
                cameras[name] = None
                continue
            for _ in range(3):
                block = self.guest(pointer + 0x420, 0x88)
                if block == self.guest(pointer + 0x420, len(block)):
                    break
            else:
                raise ValueError("Camera viewport changed during read; retry later")
            def value(offset):
                result = struct.unpack_from("<f", block, offset - 0x420)[0]
                if not math.isfinite(result) or abs(result) > 1e7:
                    raise ValueError("Invalid camera viewport float")
                return result
            width, height, x, y = (value(at) for at in (0x428, 0x42c, 0x438, 0x43c))
            if not 0 < width <= 8192 or not 0 < height <= 8192:
                raise ValueError("Camera viewport is not initialized")
            cameras[name] = {"address": hex(pointer), "logical_size": [width, height],
                "logical_origin": [x, y], "scaled_size": [value(0x430), value(0x434)],
                "half_size": [value(0x448), value(0x44c)],
                "scale": [value(0x490), value(0x494)], "center": [value(0x420), value(0x424)],
                "derived_clear_scissor": [int(x), int(y / 2), int(x + width - 1),
                                           int(y / 2 + value(0x44c) - 1)]}
        if pointers != {name: self.word(at) for name, at in slots.items()}:
            raise ValueError("Camera ownership changed during read; retry later")
        self.nav.read()  # Recheck PID, liveness and inspector freshness.
        return {"pid": state["pid"], "menu": state["menu"], "cycle": state["cycle"],
                "cameras": cameras, "read_only": True,
                "scope": "Unchanged double-read of camera fields; derived clear bounds are not actual GS state or proof of pixel coverage."}

    def finish_state(self):
        """Read original single-race finish/results state without sending input.

        177140 writes finish ticks and clears player-control word+8;165C80 changes
        phase4 to13 after the finish countdown.166480 invokes1B2FA0/1B17A0
        only in phase13.1B17A0 fills the player's result time/rank. This is
        evidence that results logic ran, not proof the frame rendered correctly.
        """
        self.nav.read()
        if self.word(0x1f22a0) != self.car or self.word(0x1f2740) != self.track:
            raise RuntimeError("Race/car/track changed; finish observation stopped")
        config = self.word(0x1f21c0)
        if (not config or self.word(0x1f21d0) != 1 or self.word(config + 0x28) != 0 or
                self.word(config + 0x2c) != 0 or self.guest(self.car + 0xc6, 1)[0] != 1):
            raise ValueError("Finish observation requires a normal single-player race")
        total = self.word(config + 12)
        slot = self.guest(self.car + 0x137, 1)[0]
        player_input = self.word(self.car + 0x158)
        if not 1 <= total <= 99 or slot >= 8 or not player_input:
            raise ValueError("Invalid race finish configuration/player")
        # Sample again if a frame changed phase while reading. No controller
        # action depends on a partially observed transition.
        for _ in range(3):
            phase = self.guest(0x1f21e4, 1)[0]
            if phase not in (4, 13):
                raise RuntimeError("Outside active race/results; finish observation stopped")
            laps = struct.unpack("<h", self.guest(self.car + 0xf2, 2))[0]
            ticks = self.word(self.car + 0x10c)
            control_word = self.word(player_input + 8)
            rank = self.guest(0x228644 + slot * 24, 1)[0]
            centiseconds = self.word(0x228648 + slot * 24)
            countdown = struct.unpack("<i", self.guest(0x1f21dc, 4))[0]
            clock = self.word(0x1f22b8)
            if (self.guest(0x1f21e4, 1)[0] == phase and
                    struct.unpack("<h", self.guest(self.car + 0xf2, 2))[0] == laps and
                    self.word(self.car + 0x10c) == ticks):
                break
        else:
            raise RuntimeError("Race phase changed repeatedly; retry observation")
        finished = laps >= total and 0 < ticks <= max(0, clock - 3600) and control_word == 0
        return {"phase": phase, "clock": clock, "completed_laps": laps, "total_laps": total,
                "finish_ticks": ticks, "finish_countdown": countdown,
                "player_control_word": control_word, "result_rank": rank,
                "result_centiseconds": centiseconds, "player_finished": finished,
                "results_ready": (finished and phase == 13 and 1 <= rank <= 8 and
                                  centiseconds == (ticks + 1) // 12)}

    def trail_state(self):
        """External equivalent of February's read-only TRAILS console command.

        Retail1BE3A0 allocates two57E50-byte buffers;1BE440 reserves2D0-byte
        entries and increments manager+C. The 500-entry capacity is preserved.
        This observes reserved slots, not how many trails are visible now.
        It does not install the prototype console or write any game state.
        """
        self.nav.read()
        if self.word(0x1f22a0) != self.car or self.word(0x1f2740) != self.track:
            raise RuntimeError("Race changed; debug observation stopped")
        manager = self.word(0x1ed8e8)
        if not manager:
            raise ValueError("Retail light-trail manager is not initialized")
        for _ in range(3):
            header = self.guest(manager, 16)
            buffers, index, current, used = struct.unpack("<4I", header)
            if self.word(0x1ed8e8) == manager and self.guest(manager, 16) == header:
                break
        else:
            raise RuntimeError("Light-trail manager changed; retry observation")
        if (not 0 < buffers <= 0x2000000 - 0xafca0 or buffers % 16 or index not in (0, 1) or
                current != buffers + index * 0x57e50 or used > 500):
            raise ValueError("Unexpected retail light-trail buffer layout/count")
        return {"prototype_command": "TRAILS", "reserved_trails": used, "capacity": 500,
                "implementation": "Read-only Python equivalent; original console is not installed."}

    def state(self, *, allow_finished=False):
        self.nav.read()
        if self.word(0x1f22a0) != self.car or self.word(0x1f2740) != self.track:
            raise RuntimeError("Active race/car/track changed; driving stopped")
        phase = self.guest(0x1f21e4, 1)[0]
        if allow_finished and (phase == 13 or self.word(self.car + 0x10c)):
            finish = self.finish_state()
            if not finish["player_finished"]:
                raise RuntimeError("Finish transition without verified player completion; driving stopped")
            return finish
        if phase != 4 or self.word(0x1f22b8) <= 3600:
            raise RuntimeError("Outside active race; driving stopped")
        physics = self.word(self.car + 0xec)
        body = self.word(physics + 12)
        if not physics or not body:
            raise ValueError("Missing live physics body")
        # Retail177400 increments car+F2 on lap completion. HUD1D0250
        # displays that count+1 and takes the total from race configuration+C.
        race_config = self.word(0x1f21c0)
        if not race_config:
            raise ValueError("Missing live race configuration")
        # 177670 accumulates accepted forward course steps at car+FC,
        # wrapping at the count returned by163CA0. These are course sectors,
        # distinct from the steering-route nodes used by this test driver.
        course_owner = self.word(0x1f1ebc)
        course = self.word(course_owner) if course_owner else 0
        if not course:
            raise ValueError("Missing live course progress table")
        # Retail177800 indexes the eight race entries by car+137; each
        # eight-byte entry has a driver-table ID at+2 (not a model index).
        slot = self.guest(self.car + 0x137, 1)[0]
        if slot >= 8:
            raise ValueError("Player race slot outside the eight-car table")
        driver = self.guest(0x1f2172 + slot * 8, 1)[0]
        if driver >= 36:
            raise ValueError("Player driver ID outside verified retail catalog")
        return {"clock": self.word(0x1f22b8), "driver_id": driver,
                "position": self.floats(self.car + 64, 3),
                "forward": self.floats(body + 0xc0, 3), "speed": self.floats(body + 0x154, 1)[0],
                "controls": self.guest(physics + 0x34, 1)[0],
                "completed_laps": struct.unpack("<h", self.guest(self.car + 0xf2, 2))[0],
                "total_laps": self.word(race_config + 12),
                # The starting grid can lie before the lap origin:177670
                # initializes this signed distance to a small negative value.
                "lap_progress_steps": struct.unpack("<i", self.guest(self.car + 0xfc, 4))[0],
                "lap_length_steps": self.word(course + 8)}

    def point(self, index):
        if not 0 <= index < self.count:
            raise ValueError("Track node outside table")
        point = struct.unpack_from("<3f", self.nodes, index * 32)
        if not all(math.isfinite(x) and abs(x) < 1e7 for x in point):
            raise ValueError("Invalid track point")
        return point

    def successor(self, index):
        self.point(index)
        following = struct.unpack_from("<h", self.nodes, index * 32 + 16)[0]
        if following == index or not 0 <= following < self.count:
            raise ValueError("Test route has no valid next node")
        return following

    def resume_target(self, hint, position):
        # Coasting can pass the last target between tests. Find the closest
        # segment in a small forward window, so resuming does not turn back.
        candidates, index = [], hint
        for _ in range(8):
            following = self.successor(index)
            a, b = self.point(index), self.point(following)
            delta = [b[i] - a[i] for i in range(3)]
            length = sum(v * v for v in delta)
            if length > 1e-6:
                fraction = max(0, min(1, sum((position[i] - a[i]) * delta[i] for i in range(3)) / length))
                distance = sum((position[i] - a[i] - fraction * delta[i]) ** 2 for i in range(3))
                candidates.append((distance, following))
            index = following
        if not candidates or min(candidates)[0] > 30 ** 2:
            raise ValueError("Car is too far from the supplied route hint")
        return min(candidates)[1]


def drive(navigator, start_node, node_limit=5, tick_limit=12000, *, complete_lap=False):
    """Experimental route follower using controller input, not game AI calls."""
    max_nodes, max_ticks = (256, 180000) if complete_lap else (20, 36000)
    if start_node is None or not 1 <= node_limit <= max_nodes or not 120 <= tick_limit <= max_ticks:
        raise ValueError(f"Drive needs --start-node, nodes 1..{max_nodes} and race-ticks 120..{max_ticks}")
    with RaceProbe(navigator) as probe:
        read_state = lambda: probe.state(allow_finished=True)
        state = read_state()
        if state.get("player_finished"):
            raise ValueError("Player has already finished; use race-status or wait-results")
        target = probe.resume_target(start_node, state["position"])
        target_lap = state["completed_laps"] + 1 if complete_lap else None
        if complete_lap and not 0 <= state["completed_laps"] < state["total_laps"]:
            raise ValueError("No remaining lap in the active race")
        deadline = time.monotonic() + 1800  # Hard wall limit even if the game runs slowly.
        log_ticks = 3600 if complete_lap else 1200
        start, last_print, reached = state["clock"], state["clock"] - log_ticks, []
        lap_reached = lambda s: target_lap is not None and s["completed_laps"] >= target_lap
        drive_done = lambda s: s.get("player_finished", False) or lap_reached(s)
        previous_heading, previous_clock = None, None
        wrap = lambda angle: (angle + math.pi) % (2 * math.pi) - math.pi
        while probe.word(0x1f22b8) < start + tick_limit and len(reached) < node_limit:
            state = read_state()
            if drive_done(state):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Driving wall-time limit reached; input released")
            position, forward, clock = state["position"], state["forward"], state["clock"]
            goal = probe.point(target)
            dx, dz = goal[0] - position[0], goal[2] - position[2]
            distance = math.hypot(dx, dz)
            if distance < 3.5 and abs(goal[1] - position[1]) < 3:
                reached.append(target)
                target = probe.successor(target)
                if not complete_lap or len(reached) % 10 == 0:
                    print("Reached route node: " + str(reached[-1]), flush=True)
                continue
            heading = math.atan2(forward[0], -forward[2])
            error = wrap(math.atan2(dx, -dz) - heading)
            rate = (0 if previous_clock is None or clock <= previous_clock else
                    wrap(heading - previous_heading) * 1200 / (clock - previous_clock))
            steering = error - 0.3 * rate
            keys = ["square" if abs(error) > 1.5 and state["speed"] > 3 else "cross"]
            if steering > 0.15:
                keys.append("right")
            elif steering < -0.15:
                keys.append("left")
            if clock - last_print >= log_ticks:
                print(json.dumps(state | {"target": target, "distance": distance, "keys": keys}), flush=True)
                last_print = clock
            previous_heading, previous_clock = heading, clock
            def step_done():
                observed = read_state()
                return drive_done(observed) or observed["clock"] >= clock + 120
            research.press_keys(keys, 3000, until=step_done)
            state = read_state()
            if drive_done(state):
                break
            if state["clock"] < clock + 120:
                raise TimeoutError("Driving step did not advance; input released")
        navigator.wait_released()
        state = read_state()
        return {"reached_nodes": reached, "next_node": target,
                "limit": "finish" if state.get("player_finished") else "lap" if lap_reached(state)
                         else "nodes" if len(reached) >= node_limit else "race_ticks",
                "state": state}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("status", "wait", "route", "drive", "lap", "race-status",
                                       "wait-results", "debug-status", "camera-status"))
    p.add_argument("target", choices=MENUS, nargs="?")
    p.add_argument("--timeout", type=float, default=45, help="Seconds per condition; never triggers a restart")
    p.add_argument("--start-node", type=int, help="Researched route-node hint for the current track")
    p.add_argument("--nodes", type=int, default=5, help="Maximum route points to reach in a driving test")
    p.add_argument("--race-ticks", type=int, help="1200-Hz driving limit (default: drive 12000, lap 180000)")
    a = p.parse_args()
    if not 1 <= a.timeout <= 120 or (a.command in {"wait", "route"} and not a.target):
        p.error("Supply a target for wait/route and a timeout in 1..120")
    try:
        n = Navigator(a.timeout)
        if a.command == "camera-status":
            with RaceProbe(n, camera_only=True) as probe:
                print(json.dumps(probe.camera_state()))
            return
        if a.command in {"race-status", "wait-results", "debug-status"}:
            with RaceProbe(n, observe_finish=True) as probe:
                if a.command == "debug-status":
                    print(json.dumps(probe.trail_state()))
                    return
                if a.command == "wait-results":
                    initial = n.read()
                    n.wait("original race-results state", lambda s: probe.finish_state()["results_ready"],
                           after=initial["sequence"])
                print(json.dumps(probe.finish_state()))
            return
        if a.command in {"drive", "lap"}:
            lap = a.command == "lap"
            print(json.dumps(drive(n, a.start_node, 256 if lap else a.nodes,
                                   a.race_ticks if a.race_ticks is not None else 180000 if lap else 12000,
                                   complete_lap=lap)))
            return
        result = n.read() if a.command == "status" else n.menu(a.target) if a.command == "wait" else n.route(a.target)
        print(json.dumps(compact(result)))
    except (OSError, ValueError, RuntimeError, TimeoutError) as e:
        p.exit(1, str(e) + "\n")


if __name__ == "__main__":
    main()
