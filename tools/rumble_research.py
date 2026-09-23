"""Build-specific research queries and explicit runtime navigation.
Queries are read-only except runtime --diff; nav sends input to the verified runner.

Examples: py -3 -B tools/rumble_research.py function 0x1af9e0
          py -3 -B tools/rumble_research.py function 0x9650 --module iop --body
          py -3 -B tools/rumble_research.py runtime --diff
          py -3 -B tools/rumble_research.py movie --verify-decode
          py -3 -B tools/rumble_research.py picture --build Retail
          py -3 -B tools/rumble_research.py banks --build Retail --verify-decode
          py -3 -B tools/rumble_research.py music --verify-decode
          py -3 -B tools/rumble_research.py environment --id 5 --preset 0
          py -3 -B tools/rumble_research.py model --build February --id 10061
No native bindings, third-party packages, or player dependencies.
"""
import argparse
import csv
import configparser
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
FEB = ROOT / "Extracted_Assets/Rumble Racing (Feb 7, 2001 prototype)"
RETAIL = ROOT / "Extracted_Assets/Rumble Racing (USA retail)"
IDENTITIES = {
    "ee": (FEB / "SLUS_201.74", "44e74a35dd123e3504f81eded1db68844e2e082edaa908ea47a7ea1ec9a924a0"),
    "iop": (FEB / "MODULES/AUDIO.IRX", "ce0981dd87c52f2253fe472e066237cdd574ca05c7dee3a460da4c190b8cc413"),
    "retail": (RETAIL / "SLUS_201.74", "e3c2c19b5fdeeac9fb1f5a9b893346e7892e564796fa2f74fc40ae17a8ade594"),
    "retail-iop": (RETAIL / "MODULES/AUDIO.IRX", "301f101c72c29ebecf368457b39de5a3ac65dbbdf2cff62e7b55470fb7e07f95"),
}
CACHE = ROOT / "analysis/research-state.json"
REPORT = ROOT / "PS2Recomp/out/build/ps2xRuntime/inspector.json"


def read_inspector_report():
    """Read a fresh publication, allowing only its short replacement window."""
    # Windows may briefly deny/open-miss the atomic file replacement. Never
    # substitute a cached snapshot; callers must still check age and identity.
    for attempt in range(6):
        try:
            return json.loads(REPORT.read_text(encoding="utf-8-sig"))
        except (PermissionError, FileNotFoundError):
            if attempt == 5:
                raise
            time.sleep(0.05)


@dataclass
class Function:
    name: str
    address: int
    end: int | None
    source: Path
    line: int
    body: str

    def calls(self):
        return sorted(set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", self.body)) -
                      {self.name, "if", "while", "for", "switch", "sizeof"})


def verify(module):
    path, expected = IDENTITIES[module]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError(f"{module} build differs from its verified analysis; refusing to apply its symbols")
    return digest


def environment_query(args):
    """Read original retail effect parameters; never substitute audio processing."""
    digest = verify("retail-iop")
    data = IDENTITIES["retail-iop"][0].read_bytes()
    if data[:6] != b"\x7fELF\x01\x01":
        raise ValueError("Expected little-endian ELF32 AUDIO.IRX")
    phoff = struct.unpack_from("<I", data, 28)[0]
    phsize, phcount = struct.unpack_from("<HH", data, 42)
    if phsize < 32 or phoff + phsize * phcount > len(data):
        raise ValueError("Invalid ELF program table")

    def read(address, count):
        for index in range(phcount):
            kind, offset, virtual, _, size, _, _, _ = struct.unpack_from(
                "<8I", data, phoff + index * phsize)
            if kind == 1 and virtual <= address and address + count <= virtual + size:
                start = offset + address - virtual
                if start + count > len(data):
                    raise ValueError("Effect table exceeds file segment")
                return data[start:start + count]
        raise ValueError("Effect address is not file-backed")

    address = 0xf410 + (args.id * 4 + args.preset) * 16
    mode, depth, delay, feedback = struct.unpack("<4i", read(address, 16))
    ramp_step = struct.unpack("<I", read(0xf810, 4))[0]
    modes = ("off", "room", "studio1", "studio2", "studio3", "hall", "space", "echo", "delay", "pipe")
    if not 0 <= mode < len(modes) or not 0 <= depth <= 0x10000 or not ramp_step:
        raise ValueError("Unexpected original effect parameters")
    # 890 supplies equal Q16 targets; 94C approaches by f810 per update;
    # A6C converts the current depths to signed SPU effect-volume registers.
    q16 = [min(depth, ramp_step * n) for n in range(1, 5)]
    startup = []
    current_mode, current_depth, enabled = 0, 0, True
    for update in range(1, 21):
        target = depth if enabled else 0
        current_depth += max(-ramp_step, min(ramp_step, target - current_depth))
        if current_mode != mode or not enabled:
            if current_depth == 0:
                current_mode, enabled = mode, True
            else:
                enabled = False
        startup.append({"update": update, "mode": current_mode, "depth_q16": current_depth,
                        "depth_register": (current_depth * 0x3fff) >> 16,
                        "target_enabled": enabled})
    return {"module_sha256": digest, "environment": args.id, "preset": args.preset,
            "table_address": hex(address), "mode": modes[mode], "mode_id": mode,
            "target_depth_q16": depth, "steady_depth_register_lr": (depth * 0x3fff) >> 16,
            "delay": delay, "feedback": feedback, "ramp_step_q16": ramp_step,
            "startup_controller_updates": startup,
            "nominal_ramp_from_zero_q16": q16,
            "nominal_ramp_depth_registers": [(value * 0x3fff) >> 16 for value in q16],
            "notes": ["Delay and feedback are effect-mode parameters, not wet-output gains.",
                      "Depth is an enabled steady-state target; disable ramps toward zero.",
                      "Ramp samples assume the requested mode is already selected; mode changes fade down first.",
                      "This query does not render audio or implement reverb, routing, or effect memory."]}


def reverb_network_reference(args):
    """Synthetic half-rate network experiment, independent of game assets."""
    registers = [2, 3, 0x6000, 0x5000, 0x3000, 0xe000, 0x1000, 0x2000,
                 0x4000, 0x3000, 40, 90, 39, 89, 38, 88, 35, 85, 50, 100,
                 49, 99, 48, 98, 45, 95, 70, 120, 80, 130, 0x6000, 0x7000]
    coefficients = [v if v < 32768 else v - 65536 for v in registers]
    memory = [0] * 1024
    clip = lambda x: min(32767, max(-32768, x))
    multiply = lambda x, field: x * coefficients[field] // 32768
    def step(frame, side, sample, write_enabled):
        def read_units(units, previous=0):
            return memory[(frame + units * 4 + previous) % len(memory)]
        incoming = multiply(sample, 30 + side)
        pending = []
        for source, destination in ((16 + side, 10 + side), (24 + (side ^ 1), 18 + side)):
            prior = read_units(registers[destination], -1)
            value = prior + multiply(incoming + multiply(read_units(registers[source]), 7) - prior, 2)
            pending.append((registers[destination], clip(value)))
        value = sum(multiply(read_units(registers[tap + side]), field)
                    for tap, field in ((12, 3), (14, 4), (20, 5), (22, 6)))
        for size, volume, destination in ((0, 8, 26 + side), (1, 9, 28 + side)):
            delayed = read_units(registers[destination] - registers[size])
            stored = value - multiply(delayed, volume)
            value = delayed + multiply(stored, volume)
            pending.append((registers[destination], clip(stored)))
        if write_enabled:
            for destination, value_to_store in pending:
                memory[(frame + destination * 4) % len(memory)] = value_to_store
        return clip(value)

    impulses = {0: (20000, -17000), 17: (32767, 32767), 23: (-40000, 50000)}
    full_rate = getattr(args, 'full_rate', False)
    if full_rate:
        # Offline convolution with unbounded history, independently of the C++
        # streaming ring. Left and right network updates occur on alternate ticks.
        taps = [-1,0,2,0,-10,0,35,0,-103,0,266,0,-616,0,1332,0,-2960,0,10246,
                16384,10246,0,-2960,0,1332,0,-616,0,266,0,-103,0,35,0,-10,0,2,0,-1]
        inputs = [tuple(map(clip, impulses.get(tick, (0, 0)))) for tick in range(4096)]
        def convolution(history, tick, channel, coefficients, delay):
            return clip(sum(coefficient * history[tick - age - delay][channel]
                            for age, coefficient in enumerate(coefficients)
                            if tick >= age + delay) // 32768)
        sparse = []
        for tick in range(len(inputs)):
            side = tick % 2
            down = convolution(inputs, tick, side, taps, 1)
            value = step(tick // 2, side, down, not 600 <= tick < 800)
            sparse.append((value, 0) if side == 0 else (0, value))
        up_taps = [clip(tap * 2) for tap in taps]
        output = [convolution(sparse, tick, side, up_taps, 0)
                  for tick in range(len(inputs)) for side in (0, 1)]
    else:
        output = [step(frame, side, clip(impulses.get(frame, (0, 0))[side]), not 300 <= frame < 400)
                  for frame in range(2048) for side in (0, 1)]
    raw = struct.pack('<' + 'h' * len(output), *output)
    fnv = 14695981039346656037
    for value in raw:
        fnv = ((fnv ^ value) * 1099511628211) & ((1 << 64) - 1)
    return {"frames": len(output) // 2, "pcm_fnv1a64": fnv,
            "pcm_sha256": hashlib.sha256(raw).hexdigest(),
            "first_nonzero_frame": next(i // 2 for i, value in enumerate(output) if value),
            "peak": max(abs(value) for value in output),
            "scope": ("Synthetic 48-kHz resampled network" if full_rate else "Synthetic 24-kHz network") +
                     "; no depth gain, game audio, or device output."}


def exported_functions(module):
    if module == "retail":
        source = ROOT / "CSV Map/retail/map.csv"
        with source.open(encoding="utf-8-sig", newline="") as stream:
            return [Function(row["Name"], int(row["Start"], 16), int(row["End"], 16), source, line, "")
                    for line, row in enumerate(csv.DictReader(stream), 2)]
    source = ROOT / "analysis" / {"iop": "prototype-audio-iop.c", "retail-iop": "retail-audio-iop.c",
                                  "ee": "prototype-debug-functions.c"}[module]
    text = source.read_text(encoding="utf-8-sig")
    headers = list(re.finditer(r"^/\* (\w+) at ([0-9a-fA-F]{8})(?:;| \*/)", text, re.M))
    result = []
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        result.append(Function(match[1], int(match[2], 16), None, source,
                               text.count("\n", 0, match.start()) + 1, text[match.start():end].strip()))
    if module == "ee":
        by_address = {f.address: f for f in result}
        with (ROOT / "CSV Map/map-ntsc.csv").open(encoding="utf-8-sig", newline="") as stream:
            for line, row in enumerate(csv.DictReader(stream), 2):
                address, end = int(row["Start"], 16), int(row["End"], 16)
                if address in by_address:
                    by_address[address].end = end
                else:
                    result.append(Function(row["Name"], address, end, ROOT / "CSV Map/map-ntsc.csv", line, ""))
    return result


def select_functions(functions, query):
    if re.fullmatch(r"0x[0-9a-fA-F]+", query):
        address = int(query, 16)
        # IOP exports do not specify extents: never invent an interior match.
        return [f for f in functions if f.address == address or
                (f.end is not None and f.address <= address < f.end)]
    exact = [f for f in functions if f.name.lower() == query.lower()]
    return exact or [f for f in functions if query.lower() in f.name.lower()]


def function_query(args):
    digest = verify(args.module)
    functions = exported_functions(args.module)
    matches = select_functions(functions, args.query)
    results = []
    for f in matches[:args.limit]:
        item = {"name": f.name, "address": hex(f.address), "end_exclusive": hex(f.end) if f.end else None,
                "source": f"{f.source}:{f.line}", "decompiled": bool(f.body),
                "calls_in_export": f.calls()[:24],
                "callers_in_export": [other.name for other in functions if f.name in other.calls()][:24]}
        if args.body:
            lines = f.body.splitlines()
            item["body"] = "\n".join(lines[:args.lines])
            item["omitted_body_lines"] = max(0, len(lines) - args.lines)
        results.append(item)
    return {"build": "USA retail" if args.module.startswith("retail") else "February 7, 2001", "module": args.module, "binary_sha256": digest,
            "address_space": "IOP module-relative, base zero" if args.module in {"iop", "retail-iop"} else "EE virtual",
            "scope": "Partial decompiler exports; textual calls are clues, not a complete verified call graph. IOP interior addresses require Ghidra.",
            "match_count": len(matches), "results": results}


def process_state(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return "alive (identity unverified)"
        except ProcessLookupError:
            return "stopped"
        except PermissionError:
            return "unknown"
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    api.OpenProcess.restype = ctypes.c_void_p
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    handle = api.OpenProcess(0x1000, 0, pid)
    if not handle:
        return "stopped" if ctypes.get_last_error() == 87 else "unknown"
    try:
        buffer, size = ctypes.create_unicode_buffer(32768), ctypes.c_uint32(32768)
        if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return "unknown"
        runtime = ROOT / "PS2Recomp/out/build/ps2xRuntime"
        expected = {(runtime / config / "ps2EntryRunner.exe").resolve()
                    for config in ("Debug", "RelWithDebInfo")}
        return "runner alive" if Path(buffer.value).resolve() in expected else "PID belongs to another executable"
    finally:
        api.CloseHandle(handle)


def changes(previous, current):
    if not previous or previous.get("process_id") != current["process_id"] or current["sequence"] < previous.get("sequence", 0):
        return {"baseline": "new process or first sample"}
    return {key: {"before": previous.get(key), "after": value} for key, value in current.items()
            if key not in {"sequence", "captured_unix_ms"} and previous.get(key) != value}


def runtime_query(args):
    report = read_inspector_report()
    if report.get("schema_version") != 1:
        raise ValueError("Unsupported inspector schema")
    sample = report.get("snapshot")
    if not sample or sample.get("error"):
        raise ValueError("Inspector has no valid sample")
    disc_root = Path(sample["disc"]["root"]).resolve()
    module = {FEB.resolve(): "ee", RETAIL.resolve(): "retail"}.get(disc_root)
    if module is None:
        raise ValueError("Runtime disc root has no verified symbol map")
    verify(module)
    functions = exported_functions(module)
    metrics = {m["name"]: m["value"] for service in sample["iop"]["services"]
               if "Rumble" in service["name"] for m in service["metrics"]}
    state = {"build": "USA retail" if module == "retail" else "February 7, 2001",
             "process_id": report["process_id"], "sequence": sample["sequence"],
             "captured_unix_ms": sample["captured_unix_ms"], "runtime_state": report["runtime_state"],
             "cpu": {key: sample["cpu"][key] for key in ("pc", "ra", "sp")},
             "audio": metrics, "graphics": sample["graphics"], "disc_error": sample["disc"]["last_error"]}
    result = {"process": process_state(report["process_id"]),
              "sample_age_seconds": round((time.time() * 1000 - sample["captured_unix_ms"]) / 1000, 2),
              "functions": {key: [f.name for f in select_functions(functions, value)] for key, value in state["cpu"].items() if key != "sp"}}
    if args.diff:
        previous = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else None
        result["changes"] = changes(previous, state)
        # Ignore counters for the change summary, but use sequence/time for freshness.
        temp = CACHE.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
            temp.replace(CACHE)
        finally:
            temp.unlink(missing_ok=True)
    else:
        result["state"] = state
    result["recent_warnings"] = [entry["text"].strip() for entry in sample["logs"]["entries"]
                                 if re.search(r"warning|unsupported|error|pending command", entry["text"], re.I)][-4:]
    if result["sample_age_seconds"] > 5 or result["process"] != "runner alive":
        result["note"] = "This may be a stale snapshot; do not infer live progress from it."
    return result


NAV_KEYS = {
    "cross": (0x58, 0x2d, False), "circle": (0x43, 0x2e, False),
    "triangle": (0x56, 0x2f, False), "square": (0x5a, 0x2c, False),
    "start": (0x0d, 0x1c, False), "up": (0x26, 0x48, True),
    "down": (0x28, 0x50, True), "left": (0x25, 0x4b, True), "right": (0x27, 0x4d, True),
}


def configure_navigation_profile(path):
    """Use the same explicit PCSX2 keyboard profile supplied to the launcher."""
    config = configparser.ConfigParser(interpolation=None)
    with Path(path).open(encoding="utf-8-sig") as stream:
        config.read_file(stream)
    names = {"cross": "Cross", "circle": "Circle", "triangle": "Triangle", "square": "Square",
             "start": "Start", "up": "Up", "down": "Down", "left": "Left", "right": "Right"}
    codes = {chr(n): (n, False) for n in range(65, 91)}
    codes.update({str(n): (48 + n, False) for n in range(10)})
    codes.update({f"Numpad{n}": (0x60 + n, False) for n in range(10)})
    codes.update({"Return": (13, False), "Space": (32, False), "Tab": (9, False),
                  "Up": (0x26, True), "Down": (0x28, True), "Left": (0x25, True), "Right": (0x27, True)})
    user = ctypes.WinDLL("user32", use_last_error=True)
    bindings = {}
    for action, name in names.items():
        source = config["Pad1"].get(name, "")
        if not source.startswith("Keyboard/") or source[9:] not in codes:
            raise ValueError("Navigation needs a supported keyboard binding for " + name)
        vk, extended = codes[source[9:]]
        scan = user.MapVirtualKeyW(vk, 0) & 255
        if not scan:
            raise ValueError("No scan code for " + name)
        bindings[action] = (vk, scan, extended)
    NAV_KEYS.update(bindings)  # Atomic validation before replacing any binding.


def navigation_state():
    report = read_inspector_report()
    sample = report.get("snapshot")
    if report.get("schema_version") != 1 or not sample or sample.get("error"):
        raise ValueError("No valid inspector sample")
    metrics = {m["name"]: m["value"] for service in sample["iop"]["services"]
               if "Rumble" in service["name"] for m in service["metrics"]}
    return {"process_id": report["process_id"], "runtime_state": report["runtime_state"],
            "sequence": sample["sequence"], "pc": sample["cpu"]["pc"],
            "age_seconds": round(time.time() - sample["captured_unix_ms"] / 1000, 2),
            "disc_root": sample["disc"]["root"], "metrics": metrics}


def press_key(key, hold_ms=1000, *, until=None):
    """Paired input, released on the optional verified condition or time limit."""
    return press_keys((key,), hold_ms, until=until)


def press_keys(keys, hold_ms=1000, *, until=None):
    """Hold a controller chord; release every posted key, including on failure.

    Development-only normal input (for example, ("cross", "left")), with the
    same bounded condition wait as press_key. Does not write guest state.
    """
    if os.name != "nt":
        raise ValueError("Window input helper currently supports the Windows development host only")
    # Slow debug gameplay can take longer than a short menu press to poll input.
    # Keep holds bounded and always release them through the finally block.
    keys = tuple(keys)
    if (not 1 <= len(keys) <= 4 or len(set(keys)) != len(keys) or
            any(key not in NAV_KEYS for key in keys) or not 50 <= hold_ms <= 30000):
        raise ValueError("Expected 1..4 distinct known keys and a hold within 50..30000 ms")
    before = navigation_state()
    pid = before["process_id"]
    if process_state(pid) != "runner alive" or before["runtime_state"] != "running" or before["age_seconds"] > 5:
        raise ValueError("Input requires the live expected runner and a fresh inspector sample")
    if before["metrics"].get("unsupported_commands", 0):
        raise ValueError("Input stopped because the runtime has unsupported work")
    module = {FEB.resolve(): "ee", RETAIL.resolve(): "retail"}.get(Path(before["disc_root"]).resolve())
    if module is None:
        raise ValueError("Unexpected runtime disc root")
    verify(module)
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)
    user.EnumWindows.argtypes = [callback_type, ctypes.c_ssize_t]
    user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    user.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t]
    windows = []
    @callback_type
    def collect(window, unused):
        owner = ctypes.c_uint32()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid:
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(window, title, len(title))
            if title.value.startswith("PS2-Recomp"):
                windows.append(window)
        return 1
    if not user.EnumWindows(collect, 0) or len(windows) != 1:
        raise ValueError("Expected exactly one PS2-Recomp window owned by the runner")
    window = windows[0]
    pressed = []
    try:
        for key in keys:
            virtual_key, scan, extended = NAV_KEYS[key]
            down = 1 | (scan << 16) | (int(extended) << 24)
            if not user.PostMessageW(window, 0x100, virtual_key, down):
                raise ctypes.WinError(ctypes.get_last_error())
            pressed.append((virtual_key, down))
        deadline = time.monotonic() + hold_ms / 1000
        while True:
            if until is not None and until():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining) if until is not None else remaining)
    finally:
        # The release is guaranteed even if the wait is interrupted. Never send
        # it to a different process if the original window disappeared.
        owner = ctypes.c_uint32()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid:
            release_error = None
            for virtual_key, down in reversed(pressed):
                if not user.PostMessageW(window, 0x101, virtual_key, down | 0xc0000000):
                    release_error = ctypes.WinError(ctypes.get_last_error())
            if release_error is not None:
                raise release_error
    return before


def wait_for_state(predicate, process_id, after_sequence, timeout=10):
    """Wait for fresh evidence from the same live runner; timeout never restarts it."""
    if not 0 < timeout <= 20:
        raise ValueError("Wait must be greater than zero and at most 20 seconds")
    deadline, latest = time.monotonic() + timeout, None
    while True:
        state = navigation_state()
        if state["process_id"] != process_id:
            raise ValueError("Runner changed during wait")
        actual = process_state(process_id)
        if actual != "runner alive" or state["runtime_state"] != "running":
            return {"matched": False, "reason": actual, "state": state}
        latest = state
        if state["sequence"] > after_sequence and state["age_seconds"] <= 5 and predicate(state):
            return {"matched": True, "reason": "fresh state satisfies predicate", "state": state}
        if time.monotonic() >= deadline:
            return {"matched": False, "reason": "timeout; same runner remains live", "state": latest}
        time.sleep(0.25)


def navigation_query(args):
    if not 0 < args.wait_seconds <= 20:
        raise ValueError("Wait must be greater than zero and at most 20 seconds")
    before = navigation_state()
    if args.until_metric:
        name, equals, value = args.until_metric.partition("=")
        if not equals or name not in before["metrics"]:
            raise ValueError("Expected an existing inspector metric=value")
        expected = int(value, 0)
        predicate = lambda s: s["metrics"].get(name) == expected
        condition = args.until_metric
    elif args.until_pc:
        expected = int(args.until_pc, 0)
        predicate = lambda s: int(s["pc"], 0) == expected
        condition = f"pc={expected:#x}"
    else:
        predicate = lambda s: True
        condition = "next fresh inspector sample; does not establish a screen transition"
    if args.key:
        before = press_key(args.key, args.hold_ms)
    result = wait_for_state(predicate, before["process_id"], before["sequence"], args.wait_seconds)
    state = result["state"]
    # Keep command output compact; full diagnostics remain in the same inspector.
    state["metrics"] = {k: v for k, v in state["metrics"].items() if k.startswith("bank_voice") or
                        k in {"unsupported_commands", "last_opcode", "movie_playing", "movie_closes", "music_playing"} or
                        (args.until_metric and k == args.until_metric.partition("=")[0])}
    return {"key": args.key, "condition": condition, **result,
            "frame_to_inspect": str(REPORT.with_suffix(".png")),
            "scope": "Normal window input; no guest function calls or game-state patches. Inspect the frame to confirm the menu."}


def movie_records(data):
    """Read the February UStream record layout without writing extracted data."""
    offset = 0
    while offset < len(data):
        if len(data) - offset < 8:
            raise ValueError(f"Truncated stream header at {offset:#x}")
        tag, size = struct.unpack_from("<II", data, offset)
        if tag == 0x46494C4C:  # FILL occupies the rest of this read buffer.
            end = (offset // 0x6000 + 1) * 0x6000
            if end > len(data):
                raise ValueError("Truncated FILL buffer")
            yield "FILL", offset, data[offset:end]
            offset = end
            continue
        if size < 8 or offset + size > len(data) or size > 0x6000 - offset % 0x6000:
            raise ValueError(f"Invalid record extent at {offset:#x}: {size}")
        name = tag.to_bytes(4, "big").decode("ascii", errors="replace")
        yield name, offset, data[offset:offset + size]
        offset += size


def decode_movie_channel(data, history=(0, 0)):
    """Offline PSX ADPCM experiment, for the observed continuous flag-2 blocks.

    Matches FFmpeg's integer predictor division and unclipped history model:
    https://ffmpeg.org/doxygen/7.0/adpcm_8c_source.html (ADPCM_PSX).
    This is a decoder comparison, not a claim of bit-exact SPU2 emulation.
    """
    return decode_adpcm_blocks(data, {2}, history)


def decode_adpcm_blocks(data, allowed_flags, history=(0, 0)):
    """Finite block decoding only; playback looping/envelopes are separate."""
    if len(data) % 16:
        raise ValueError("Partial ADPCM block")
    coefficients = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))
    for offset in range(0, len(data), 16):
        if data[offset] >> 4 > 4 or data[offset + 1] not in allowed_flags:
            raise ValueError(f"Unverified ADPCM filter/flags at {offset:#x}")
    previous, older = history
    samples = []
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        shift = block[0] & 15
        a, b = coefficients[block[0] >> 4]
        for index in range(28):
            nibble = (block[2 + index // 2] >> (4 * (index & 1))) & 15
            signed = nibble - 16 if nibble & 8 else nibble
            predictor = previous * a + older * b
            # Explicit truncation toward zero, without floating-point division.
            predicted = abs(predictor) // 64 * (-1 if predictor < 0 else 1)
            value = ((signed * 4096) >> shift) + predicted
            older, previous = previous, value
            samples.append(max(-32768, min(32767, value)))
    return struct.pack(f"<{len(samples)}h", *samples), (previous, older)


def movie_query(args):
    verify("ee")
    verify("iop")
    asset = f"DATA/OPENING/{args.asset}"
    path = FEB / asset
    data = path.read_bytes()
    counts, audio, video = {}, [], []
    for tag, offset, record in movie_records(data):
        counts[tag] = counts.get(tag, 0) + 1
        if tag == "VAGM":
            if len(record) != 0x5FC0:
                raise ValueError("Unverified movie audio chunk size")
            total, index = struct.unpack_from("<HH", record, 0x14)
            if index != len(audio):
                raise ValueError("Movie audio chunks are out of order")
            audio.append((total, record))
        elif tag == "MPG2":
            if len(record) < 0x40:
                raise ValueError("Truncated MPEG header")
            logical = struct.unpack_from("<I", record, 0x14)[0] - (16 if record[0x18] == 0 else 0)
            transfer = (logical + 19) & ~15
            if logical < 0 or transfer > len(record) - 0x40 + 16 or offset + 0x40 + transfer > len(data):
                raise ValueError("Invalid MPEG DMA extent")
            video.append(data[offset + 0x40:offset + 0x40 + transfer])
    if not audio or any(total != len(audio) for total, _ in audio):
        raise ValueError("Incomplete movie audio sequence")
    result = {"build": "February 7, 2001", "asset": asset,
              "asset_sha256": hashlib.sha256(data).hexdigest(), "records": counts,
              "audio_sample_rate": 22050, "channels": 2, "channel_bytes_per_chunk": 0x2FC0,
              "sample_frames_per_chunk": 0x2FC0 // 16 * 28,
              "scope": "Offline analysis only; no audio device, runtime command, or buffer-release event is driven."}
    tools = ROOT / "PS2Recomp/out/build/ThirdParty/ffmpeg-prefix/src/ffmpeg_external/bin"
    def run_decoder(program, arguments, payload):
        executable = tools / (program + (".exe" if os.name == "nt" else ""))
        if not executable.is_file():
            raise ValueError(f"Existing decoder tool unavailable: {executable}")
        completed = subprocess.run([str(executable), "-v", "error", *arguments], input=payload,
                                   capture_output=True, timeout=30)
        if completed.returncode or completed.stderr.strip():
            raise ValueError(f"{program} validation failed: {completed.stderr.decode(errors='replace')[:800]}")
        return completed.stdout
    channel_reports = []
    for channel in range(2):
        chunks = [record[0x40 + channel * 0x2FC0:0x40 + (channel + 1) * 0x2FC0] for _, record in audio]
        history, outputs, reset_outputs = (0, 0), [], []
        for chunk in chunks:
            pcm, history = decode_movie_channel(chunk, history)
            outputs.append(pcm)
            reset_outputs.append(decode_movie_channel(chunk)[0])
        pcm = b"".join(outputs)
        whole, _ = decode_movie_channel(b"".join(chunks))
        if pcm != whole:
            raise ValueError("Chunked ADPCM decoding lost history")
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        reset_samples = struct.unpack(f"<{len(pcm) // 2}h", b"".join(reset_outputs))
        report = {"channel": channel, "samples": len(samples), "seconds": len(samples) / 22050,
                  "pcm_sha256": hashlib.sha256(pcm).hexdigest(), "minimum": min(samples), "maximum": max(samples),
                  "samples_changed_by_resetting_each_chunk": sum(a != b for a, b in zip(samples, reset_samples))}
        if args.verify_decode:
            header = bytearray(48)
            header[:4] = b"VAGp"
            struct.pack_into(">III", header, 4, 0x20, 0, sum(map(len, chunks)))
            struct.pack_into(">I", header, 16, 22050)
            reference = run_decoder("ffmpeg", ["-i", "pipe:0", "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
                                    header + b"".join(chunks))
            if reference != pcm:
                raise ValueError(f"ADPCM experiment differs from FFmpeg on channel {channel}")
            report["ffmpeg_sample_exact_match"] = True
        channel_reports.append(report)
    result["audio"] = channel_reports
    if args.verify_decode:
        result["video"] = json.loads(run_decoder("ffprobe", ["-f", "mpegvideo", "-i", "pipe:0", "-count_frames",
            "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json"], b"".join(video)))
    return result


def music_query(args):
    """Verify the retail frontend's original music; keep PCM in memory only."""
    verify("retail")
    asset = RETAIL / "DATA/FEND/FE2.TRK"
    source = asset.read_bytes()
    records = [data for tag, _, data in movie_records(source) if tag == "VAGM"]
    if not records or any(len(data) != 0x5fc0 for data in records):
        raise ValueError("Unsupported music packet layout")
    for index, data in enumerate(records):
        total, sequence, channel_bytes = struct.unpack_from("<HHH", data, 20)
        if total != len(records) or sequence != index or channel_bytes != 0x2fc0:
            raise ValueError("Music sequence/count/channel layout differs")
    ffmpeg = ROOT / "PS2Recomp/out/build/ThirdParty/ffmpeg-prefix/src/ffmpeg_external/bin"
    ffmpeg /= "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    channels = []
    for channel in range(2):
        chunks = [data[0x40 + channel * 0x2fc0:0x40 + (channel + 1) * 0x2fc0] for data in records]
        history, output = (0, 0), bytearray()
        for chunk in chunks:
            pcm, history = decode_movie_channel(chunk, history)
            output.extend(pcm)
        report = {"channel": channel, "sample_frames": len(output) // 2,
                  "seconds_at_22050": len(output) / 2 / 22050,
                  "pcm_sha256": hashlib.sha256(output).hexdigest()}
        if args.verify_decode:
            encoded = b"".join(chunks)
            vag = bytearray(48)
            vag[:4] = b"VAGp"
            struct.pack_into(">III", vag, 4, 0x20, 0, len(encoded))
            struct.pack_into(">I", vag, 16, 22050)
            decoded = subprocess.run([str(ffmpeg), "-v", "error", "-i", "pipe:0", "-f", "s16le",
                                      "-acodec", "pcm_s16le", "pipe:1"], input=vag + encoded,
                                     capture_output=True, timeout=30)
            if decoded.returncode or decoded.stderr.strip() or decoded.stdout != output:
                raise ValueError(f"Frontend music decoder differs from FFmpeg on channel {channel}")
            report["ffmpeg_sample_exact_match"] = True
        channels.append(report)
    return {"build": "USA retail", "asset": "DATA/FEND/FE2.TRK",
            "asset_sha256": hashlib.sha256(source).hexdigest(), "chunks": len(records),
            "channel_bytes_per_chunk": 0x2fc0, "audio": channels,
            "scope": "Original finite stream decoded in memory; no exported audio, live playback or loop transition verified."}


def stream_resources(path, wanted):
    """Read selected SHOC payloads in memory; no asset extraction files.

    February SM_ParseBufs/loadStreamChunk/Stream_DecompressChunk define the
    format. Compare SHA256 with the independent C# asset report before use.
    """
    data = path.read_bytes()
    position, resource, output = 0, None, bytearray()

    def finish():
        if resource is None:
            return None
        if len(output) != resource[3]:
            raise ValueError("Incomplete selected stream resource")
        return (*resource[:3], bytes(output))

    while position < len(data):
        if position + 4 > len(data):
            raise ValueError("Truncated stream tag")
        tag = data[position:position + 4][::-1]
        if tag == b"FILL":
            position = (position // 0x6000 + 1) * 0x6000
            if position > len(data):
                raise ValueError("FILL extends past stream")
            continue
        if position + 8 > len(data):
            raise ValueError("Truncated stream block")
        size = struct.unpack_from("<I", data, position + 4)[0]
        end = position + size
        if size < 8 or end > len(data):
            raise ValueError("Invalid stream block extent")
        if tag == b"SHOC":
            if size < 20:
                raise ValueError("Short SHOC")
            kind = data[position + 16:position + 20][::-1]
            if kind == b"SHDR":
                result = finish()
                if result is not None:
                    yield result
                if size < 56:
                    raise ValueError("Short SHDR")
                type_name = data[position + 24:position + 28][::-1].decode("ascii")
                identity, count = struct.unpack_from("<II", data, position + 28)
                resource = (type_name, identity, position, count) if type_name in wanted else None
                if resource is not None and count > 128 * 1024 * 1024:
                    raise ValueError("Implausible selected payload size")
                output = bytearray()
            elif resource is not None and kind == b"SDAT":
                count = min(size - 20, resource[3] - len(output))
                output.extend(data[position + 20:position + 20 + count])
            elif resource is not None and kind == b"Rdat":
                if size < 24:
                    raise ValueError("Short Rdat")
                target = len(output) + struct.unpack_from("<I", data, position + 20)[0]
                if target > resource[3]:
                    raise ValueError("Decoded extent exceeds resource")
                cursor = position + 24
                while len(output) < target:
                    if cursor + 2 > end:
                        raise ValueError("Truncated compression token")
                    a, b = data[cursor:cursor + 2]
                    cursor += 2
                    word, count = (a << 8) | b, (a >> 4) & 7
                    if word & 0x8800 == 0x8800:
                        if count == 0:
                            count = word & 0x7ff
                            if not count or cursor + count > end or len(output) + count > target:
                                raise ValueError("Invalid literal extent")
                            output.extend(data[cursor:cursor + count])
                            cursor += count
                        else:
                            distance, count = count | ((word >> 5) & 0x38), b + 3
                            if distance > len(output) or len(output) + count > target:
                                raise ValueError("Invalid repeated-byte extent")
                            output.extend(bytes([output[-distance]]) * count)
                    else:
                        if count == 7:
                            if cursor == end:
                                raise ValueError("Missing extended length")
                            count = data[cursor] + 7
                            cursor += 1
                        count += 3
                        reverse = bool(word & 0x8000)
                        source = len(output) - (word & 0xfff) + (2 if reverse else 0)
                        if (len(output) + count > target or not 0 <= source < len(output) or
                                (reverse and source - count + 1 < 0)):
                            raise ValueError("Invalid dictionary extent")
                        for _ in range(count):
                            output.append(output[source])
                            source += -1 if reverse else 1
        position = end
    result = finish()
    if result is not None:
        yield result


def bank_layout(header, body):
    """Retail IOP 0x28B0/0x2688/0x3198: program directory and 20-byte tones."""
    if len(header) < 16 or struct.unpack_from("<I", header)[0] != 2:
        raise ValueError("Unsupported VKH header/version")
    body_bytes = struct.unpack_from("<I", header, 12)[0]
    directory_end = 16 + header[4] * 4
    if directory_end > len(header) or body_bytes != len(body) or len(body) % 16:
        raise ValueError("VKH directory or sample-body size mismatch")
    programs, sample_starts = [], set()
    for program in range(header[4]):
        offset = struct.unpack_from("<I", header, 16 + program * 4)[0]
        if not offset:
            programs.append(None)
            continue
        if offset < directory_end or offset + 4 > len(header):
            raise ValueError("Program offset outside header")
        tone_count = header[offset]
        if not tone_count or offset + 4 + tone_count * 20 > len(header):
            raise ValueError("Tone extent outside header")
        tones = []
        for tone in range(tone_count):
            at = offset + 4 + tone * 20
            start = struct.unpack_from("<I", header, at + 16)[0]
            if start >= len(body) or start % 16:
                raise ValueError("Unaligned or out-of-range ADPCM sample")
            sample_starts.add(start)
            tones.append({"sample_offset": start,
                          "adsr1": struct.unpack_from("<H", header, at + 10)[0],
                          "adsr2": struct.unpack_from("<H", header, at + 12)[0],
                          "pitch": struct.unpack_from("<H", header, at + 14)[0]})
        programs.append(tones)
    samples = []
    for start in sorted(sample_starts):
        at, loop = start, None
        flag_counts = {}
        while at < len(body):
            control, flags = body[at:at + 2]
            if control >> 4 > 4 or control & 15 > 12 or flags not in {0, 1, 2, 3, 4, 6}:
                raise ValueError(f"Unverified sample block at {at:#x}")
            flag_counts[flags] = flag_counts.get(flags, 0) + 1
            if flags & 4:
                loop = at
            at += 16
            if flags & 1:
                break
        if at == start or not body[at - 15] & 1:
            raise ValueError("Sample has no terminal block within body")
        repeats = bool(body[at - 15] & 2)
        if repeats and loop is None:
            raise ValueError("Looping sample has no observed loop-start marker")
        samples.append({"offset": start, "bytes": at - start, "frames": (at - start) // 16 * 28,
                        "flags": flag_counts, "repeats": repeats,
                        "loop_start_frame": (loop - start) // 16 * 28 if repeats else None})
    return {"programs": programs, "samples": samples}


def render_bank_one_shot(encoded, pitch, adsr1, adsr2, volume=0x1fff):
    """Research renderer: linear interpolation, ADSR, no hardware/reverb claim."""
    if not 0 < pitch <= 0x3fff or not 0 <= volume <= 0x3fff:
        raise ValueError("Unsupported pitch/volume")
    raw, _ = decode_adpcm_blocks(encoded, {0, 1, 2, 3, 4, 6})
    source = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    frames = (len(source) * 4096 + pitch - 1) // pitch
    if frames > 262144:
        raise ValueError("One-shot exceeds owned PCM limit")
    output, level, phase, counter = bytearray(), 0, 0, 0
    for frame in range(frames):
        if phase == 0:
            shift, step, decreasing, exponential = (adsr1 >> 10) & 31, (adsr1 >> 8) & 3, False, bool(adsr1 & 0x8000)
        elif phase == 1:
            shift, step, decreasing, exponential = (adsr1 >> 4) & 15, 0, True, True
        else:
            shift, step, decreasing, exponential = (adsr2 >> 8) & 31, (adsr2 >> 6) & 3, bool(adsr2 & 0x4000), bool(adsr2 & 0x8000)
        delta = (-8 + step if decreasing else 7 - step) * (1 << max(0, 11 - shift))
        increment = 0x8000 >> max(0, shift - 11)
        if exponential and not decreasing and level > 0x6000:
            delta //= 4 if shift < 10 else 2 if shift == 10 else 1
            increment //= 4 if shift >= 11 else 2 if shift == 10 else 1
        elif exponential and decreasing:
            delta = (delta * level) >> 15
        if phase == 1 or (shift * 4 + step) != 127:
            increment = max(increment, 1)
        counter += increment
        if counter >= 0x8000:
            counter -= 0x8000
            level = max(0, min(32767, level + delta))
        if (phase == 0 and level == 32767) or (phase == 1 and level <= ((adsr1 & 15) + 1) * 0x800):
            phase += 1
            counter = 0
        index, fraction = divmod(frame * pitch, 4096)
        numerator = source[index] * (4096 - fraction) + (source[index + 1] if index + 1 < len(source) else 0) * fraction
        value = (abs(numerator) // 4096) * (-1 if numerator < 0 else 1)
        value = (((value * level) >> 15) * (volume * 2)) >> 15
        output.extend(struct.pack("<hh", value, value))
    return bytes(output)


def sound_query(args):
    verify("retail")
    verify("retail-iop")
    resources = list(stream_resources(RETAIL / "DATA/FEND/FE2.TRK", {"Cshd", "Cvkh", "Cvkb"}))
    found = []
    for tag, _, _, table in resources:
        if tag != "Cshd":
            continue
        for entry in range(struct.unpack_from("<H", table, 2)[0]):
            bank, count, offset = struct.unpack_from("<HHI", table, 4 + entry * 8)
            for index in range(count):
                descriptor = table[offset + index * 36:offset + (index + 1) * 36]
                aliases = struct.unpack_from("<4H", descriptor, 8)
                if args.id not in aliases:
                    continue
                header = next(d for t, i, _, d in resources if t == "Cvkh" and i == bank)
                body = next(d for t, i, _, d in resources if t == "Cvkb" and i == bank)
                layout = bank_layout(header, body)
                tone = layout["programs"][descriptor[2]][0] # IOP 0xDBF0 selects tone zero.
                sample = next(s for s in layout["samples"] if s["offset"] == tone["sample_offset"])
                data = body[sample["offset"]:sample["offset"] + sample["bytes"]]
                item = {"definition_id": descriptor[0], "bank": bank, "program": descriptor[2],
                        "aliases": aliases, "tone": tone, "sample": sample,
                        "encoded_sha256": hashlib.sha256(data).hexdigest()}
                if not sample["repeats"]:
                    pcm = render_bank_one_shot(data, tone["pitch"], tone["adsr1"], tone["adsr2"])
                    item.update({"provisional_frames_48000": len(pcm) // 4,
                                 "provisional_pcm_sha256": hashlib.sha256(pcm).hexdigest()})
                found.append(item)
    return {"build": "USA retail", "requested_alias": args.id, "matches": found,
            "scope": "Frontend archive matches only; independent one-shot gain 0x1fff. Linear interpolation/envelope model is provisional; loops and hardware equivalence unverified. No files exported."}


def banks_query(args):
    """Verify the two real frontend banks, retaining all data only in memory."""
    roots = {"February": FEB, "Retail": RETAIL,
             "March": ROOT / "Extracted_Assets/Rumble Racing (Mar 27, 2001 prototype)"}
    root = roots[args.build]
    if args.build in {"February", "Retail"}:
        verify("ee" if args.build == "February" else "retail")
    else:
        if hashlib.sha256((root / "MODULES/AUDIO.IRX").read_bytes()).hexdigest() != IDENTITIES["retail-iop"][1]:
            raise ValueError("March AUDIO.IRX no longer matches verified retail module")
    archive_path = "DATA/FEND/FE2.TRK"
    resources = list(stream_resources(root / archive_path, {"Cshd", "Cvkh", "Cvkb"}))
    # Independent parser's saved SHA256 values; no silent trust of this decoder.
    report = json.loads((ROOT / "analysis/assets-investigation.json").read_text(encoding="utf-8-sig"))
    archive = next(a for b in report["Builds"] if b["Build"] == args.build for a in b["Archives"]
                   if a["Path"].replace("\\", "/") == archive_path)
    expected = {entry["Offset"]: entry["SHA256"].lower() for entry in archive["Resources"]}
    for _, _, offset, data in resources:
        if hashlib.sha256(data).hexdigest() != expected.get(offset):
            raise ValueError(f"Stream decoder differs from independent asset report at {offset:#x}")
    banks = []
    for kind, bank_id, _, header in resources:
        if kind != "Cvkh":
            continue
        bodies = [data for tag, identity, _, data in resources if tag == "Cvkb" and identity == bank_id]
        if len(bodies) != 1:
            raise ValueError("Missing or ambiguous matching sample body")
        body = bodies[0]
        layout = bank_layout(header, body)
        descriptors = None
        for tag, _, _, table in resources:
            if tag != "Cshd":
                continue
            if len(table) < 4 or struct.unpack_from("<H", table)[0] != 7:
                raise ValueError("Unsupported Cshd table")
            count = struct.unpack_from("<H", table, 2)[0]
            if 4 + 8 * count > len(table):
                raise ValueError("Invalid Cshd directory")
            for entry in range(count):
                identity, n, offset = struct.unpack_from("<HHI", table, 4 + 8 * entry)
                if offset < 4 + 8 * count or offset + n * 36 > len(table):
                    raise ValueError("Invalid Cshd descriptors")
                if identity == bank_id and descriptors is None:
                    descriptors = [table[offset + j * 36:offset + (j + 1) * 36] for j in range(n)]
        if descriptors is None:
            raise ValueError("Bank has no sound-description table entry")
        if any(d[2] >= len(layout["programs"]) or layout["programs"][d[2]] is None for d in descriptors):
            raise ValueError("Sound descriptor references missing program")
        entry = {"bank_id": bank_id, "header_bytes": len(header), "body_bytes": len(body),
                 "header_sha256": hashlib.sha256(header).hexdigest(),
                 "body_sha256": hashlib.sha256(body).hexdigest(),
                 "program_slots": len(layout["programs"]),
                 "programs_present": sum(p is not None for p in layout["programs"]),
                 "tones": sum(len(p) for p in layout["programs"] if p is not None),
                 "descriptors": len(descriptors),
                 "distinct_sound_ids": len({struct.unpack_from("<H", d)[0] for d in descriptors}),
                 "unique_samples": len(layout["samples"]),
                 "looping_samples": sum(s["repeats"] for s in layout["samples"]),
                 "sample_flags": sorted({flag for s in layout["samples"] for flag in s["flags"]}),
                 "first_pass_frames": sum(s["frames"] for s in layout["samples"])}
        if args.verify_decode:
            tool = ROOT / "PS2Recomp/out/build/ThirdParty/ffmpeg-prefix/src/ffmpeg_external/bin"
            tool /= "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            for sample in layout["samples"]:
                data = body[sample["offset"]:sample["offset"] + sample["bytes"]]
                pcm, _ = decode_adpcm_blocks(data, {0, 1, 2, 3, 4, 6})
                vag = bytearray(48)
                vag[:4] = b"VAGp"
                struct.pack_into(">I", vag, 4, 32)
                struct.pack_into(">I", vag, 12, len(data))
                # Fixed reference rate: raw sample equality is independent of
                # each tone's actual pitch, which the playback layer applies.
                struct.pack_into(">I", vag, 16, 48000)
                result = subprocess.run([str(tool), "-v", "error", "-i", "pipe:0", "-f", "s16le",
                                         "-acodec", "pcm_s16le", "pipe:1"], input=vag + data,
                                        capture_output=True, timeout=30)
                if result.returncode or result.stderr.strip() or result.stdout != pcm:
                    raise ValueError(f"Bank {bank_id} sample {sample['offset']:#x} differs from FFmpeg")
            entry["ffmpeg_all_first_pass_samples_match"] = True
        banks.append(entry)
    return {"build": args.build, "archive": archive_path,
            "selected_resource_hashes_match_independent_report": len(resources), "banks": banks,
            "scope": "Offline first-pass ADPCM and metadata verification only; live bank loading, repeated-loop predictor behavior, pitch/envelope/mixing and audible playback are not verified."}


def picture_query(args):
    """Decode one original opening IPUM in memory; never alter/export assets."""
    roots = {"February": FEB, "March": ROOT / "Extracted_Assets/Rumble Racing (Mar 27, 2001 prototype)",
             "Retail": RETAIL}
    path = roots[args.build] / "DATA/OPENING" / args.asset
    data = path.read_bytes()
    if not 24 <= len(data) <= 32 * 1024 * 1024 or data[:4] != b"ipum":
        raise ValueError("Expected a bounded IPUM picture")
    declared, width, height, frames = struct.unpack_from("<IHHI", data, 4)
    if declared + 8 != len(data) or frames != 1 or not (0 < width <= 2048 and 0 < height <= 2048):
        raise ValueError("Unsupported IPUM size, dimensions, or frame count")
    if width % 16 or height % 16 or data[-8:] != bytes.fromhex("000001b0000001b1"):
        raise ValueError("Expected whole macroblocks and verified frame/sequence end markers")
    tools = ROOT / "PS2Recomp/out/build/ThirdParty/ffmpeg-prefix/src/ffmpeg_external/bin"
    executable = tools / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    # FFmpeg's IPU parser splits at frame end B0. A standalone final B1 becomes
    # a second, four-byte packet and the decoder rejects it as a frame. Exclude
    # only that checked sequence-end marker from this single-frame experiment.
    decoded = subprocess.run([str(executable), "-v", "error", "-f", "ipu", "-i", "pipe:0",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
        input=data[:-4], capture_output=True, timeout=30)
    if decoded.returncode or decoded.stderr.strip() or len(decoded.stdout) != width * height * 4:
        raise ValueError(f"IPUM decode failed: {decoded.stderr.decode(errors='replace')[:800]}")
    return {"build": args.build, "asset": args.asset, "asset_bytes": len(data),
            "asset_sha256": hashlib.sha256(data).hexdigest(), "width": width, "height": height,
            "frames": frames, "macroblocks": width // 16 * (height // 16),
            "rgba_bytes": len(decoded.stdout), "rgba_sha256": hashlib.sha256(decoded.stdout).hexdigest(),
            "frame_end_offset": len(data) - 8, "sequence_end_offset": len(data) - 4,
            "scope": "Offline FFmpeg RGBA reference only; no IPU DMA, PS2 color/alpha equivalence, or live upload verified."}


def model_layout(data):
    """Inspect raw O3D trees/material references without relocating guest data.

    Derived from retail 169E00/12C860/12C970. Texture group numbers are raw
    relative references, not resolved runtime groups or proof of compatibility.
    Geometry packets are hashed, not interpreted as vertices by this query.
    """
    def unpack(fmt, source, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(source):
            raise ValueError("Truncated model structure")
        return struct.unpack_from(fmt, source, offset)

    objects, parts, fragments = [], [], []
    position, part = 0, None
    while position < len(data):
        tag, size = unpack("<4sI", data, position)
        if size < 16 or position + size > len(data):
            raise ValueError("Invalid model chunk extent")
        chunk = data[position:position + size]
        if tag == b"traP":
            part = unpack("<i", chunk, 8)[0]
        elif tag == b" dmG":
            main = unpack("<4i", chunk, 16)
            alternate = unpack("<4i", chunk, 32)
            if any(not 0 <= n < 32 for n in main) or any(not -1 <= n < 32 for n in alternate):
                raise ValueError("Invalid model object slot reference")
            parts.append({"part": part, "main_slots": main, "alternate_slots": alternate})
        elif tag == b" fbO":
            slot = unpack("<I", chunk, 8)[0]
            if slot >= 32 or any(o["slot"] == slot for o in objects):
                raise ValueError("Invalid or duplicate model object slot")
            raw = chunk[16:]
            if unpack("<4sI4sI", raw, 0) != (b"OBF ", 0x103, b"HEAD", 8):
                raise ValueError("Unrecognized raw object header")
            expected = unpack("<h", raw, 16)[0]
            if not 1 <= expected <= 4096:
                raise ValueError("Invalid object node count")
            cursor, nodes, references, node_layout = 24, 0, set(), []

            def node(depth):
                nonlocal cursor, nodes
                if depth > 128 or nodes >= expected:
                    raise ValueError("Object tree exceeds its declared bounds")
                nodes += 1
                if unpack("<4sI", raw, cursor) != (b"ELHE", 96):
                    raise ValueError("Unrecognized tree node header")
                cursor += 8
                children, materials, words = unpack("<hhI", raw, cursor)
                unpack("<96s", raw, cursor)
                if children < 0 or materials < 0:
                    raise ValueError("Negative object tree count")
                cursor += 96
                list_tag, list_bytes = unpack("<4sI", raw, cursor)
                if list_tag != b"ELTL" or list_bytes < materials * 4 or list_bytes % 4:
                    raise ValueError("Object material-list size mismatch")
                cursor += 8
                indices = unpack(f"<{materials}I", raw, cursor)
                # The stored list includes alignment padding. The original
                # parser advances its declared size, not just active entries.
                cursor += list_bytes
                if unpack("<4sI", raw, cursor) != (b"ELDA", words * 4):
                    raise ValueError("Object geometry-packet size mismatch")
                cursor += 8
                if words * 4 > len(raw) - cursor:
                    raise ValueError("Truncated geometry packet")
                packet = raw[cursor:cursor + words * 4]
                for index in indices:
                    if index * 4 + 64 > len(packet):
                        raise ValueError("Material reference outside geometry packet")
                    texture = unpack("<I", packet, index * 4)[0]
                    group = unpack("<i", packet, index * 4 + 16)[0]
                    if group != -1:
                        references.add((group, texture))
                node_layout.append((depth, children, materials, words))
                cursor += words * 4
                for _ in range(children):
                    node(depth + 1)

            node(0)
            if nodes != expected or cursor != len(raw):
                raise ValueError("Object tree count or final extent mismatch")
            objects.append({"slot": slot, "nodes": nodes, "bytes": len(raw),
                            "raw_sha256": hashlib.sha256(raw).hexdigest(),
                            "node_layout": node_layout,
                            "texture_references": sorted(references)})
        elif tag == b"FpxE":
            count = unpack("<I", chunk, 8)[0]
            if 16 + count * 0x7f0 != len(chunk):
                raise ValueError("Fragment array size mismatch")
            for index in range(count):
                at = 16 + index * 0x7f0
                fragments.append((unpack("<i", chunk, at + 32)[0],
                                  unpack("<I", chunk, at + 16)[0]))
        else:
            raise ValueError(f"Unrecognized model chunk {tag!r}")
        position += size
    slots = {obj["slot"] for obj in objects}
    missing = sorted({n for p in parts for n in (*p["main_slots"], *p["alternate_slots"])
                      if n >= 0 and n not in slots})
    refs = sorted(set(fragments).union(*(set(o["texture_references"]) for o in objects)))
    return {"parts": parts, "objects": objects, "fragment_count": len(fragments),
            "texture_references": refs, "unpopulated_object_slots": missing}


def model_query(args):
    build_root = FEB if args.build == "February" else RETAIL
    verify("ee" if args.build == "February" else "retail")
    inventory = json.loads((ROOT / "analysis/assets-investigation.json").read_text(encoding="utf-8-sig"))
    build = next(b for b in inventory["Builds"] if b["Build"] == args.build)
    archive = next(a for a in build["Archives"] if a["Path"] == "GLBLDATA.PS2")
    rows = [r for r in archive["Resources"] if r["Type"] == "o3d " and r["Id"] == args.id]
    if len(rows) != 1 or rows[0]["Error"]:
        raise ValueError("Missing or ambiguous inventoried model")
    found = [d for t, identity, _, d in stream_resources(build_root / "GLBLDATA.PS2", {"o3d "})
             if identity == args.id]
    if len(found) != 1:
        raise ValueError("Missing or ambiguous model payload")
    digest = hashlib.sha256(found[0]).hexdigest()
    if digest.lower() != rows[0]["SHA256"].lower():
        raise ValueError("Model differs from independently verified inventory")
    return {"build": args.build, "resource_id": args.id, "sha256": digest,
            **model_layout(found[0]),
            "scope": "Read-only tree/material audit; no resolved textures, rendered geometry, physics transfer or installed vehicle"}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    query = sub.add_parser("function", help="Find a build-specific EE or AUDIO.IRX function")
    query.add_argument("query")
    query.add_argument("--module", choices=IDENTITIES, default="ee")
    query.add_argument("--body", action="store_true")
    query.add_argument("--limit", type=int, choices=range(1, 21), default=5)
    query.add_argument("--lines", type=int, choices=range(1, 201), default=80)
    runtime = sub.add_parser("runtime", help="Summarize runtime evidence and optional differences")
    runtime.add_argument("--diff", action="store_true", help="Overwrite one previous-state cache")
    nav = sub.add_parser("nav", help="Send a normal keypress and wait for fresh runtime evidence; write no files")
    nav.add_argument("key", choices=NAV_KEYS, nargs="?")
    nav.add_argument("--hold-ms", type=int, default=1000)
    nav.add_argument("--wait-seconds", type=float, default=5)
    condition = nav.add_mutually_exclusive_group()
    condition.add_argument("--until-metric", help="Wait for an exact existing inspector metric=value")
    condition.add_argument("--until-pc", help="Wait for an exact guest PC")
    movie = sub.add_parser("movie", help="Analyze opening movie data in memory; write no files")
    movie.add_argument("--asset", choices=("EAGAMES.FLM", "INTRO.FLM"), default="EAGAMES.FLM")
    movie.add_argument("--verify-decode", action="store_true", help="Compare with the existing FFmpeg tools via pipes")
    picture = sub.add_parser("picture", help="Decode a single opening IPUM picture via pipes; write no files")
    picture.add_argument("--build", choices=("February", "March", "Retail"), default="Retail")
    picture.add_argument("--asset", choices=("MC_STATE.LSC", "SPLASH1.LSC"), default="MC_STATE.LSC")
    sound = sub.add_parser("sound", help="Trace a retail frontend sound alias and render a provisional one-shot in memory")
    sound.add_argument("--id", type=int, choices=range(1, 384), default=70)
    banks = sub.add_parser("banks", help="Inspect frontend sound banks in memory; write no files")
    banks.add_argument("--build", choices=("February", "March", "Retail"), default="Retail")
    banks.add_argument("--verify-decode", action="store_true", help="Compare every unique sample's first pass with FFmpeg")
    music = sub.add_parser("music", help="Verify retail frontend music in memory; write no files")
    music.add_argument("--verify-decode", action="store_true", help="Compare both complete channels with FFmpeg")
    model = sub.add_parser("model", help="Inspect verified raw global model trees and texture references; write no files")
    model.add_argument("--build", choices=("February", "Retail"), default="Retail")
    model.add_argument("--id", type=int, required=True)
    environment = sub.add_parser("environment", help="Read verified retail effect mode/depth/delay/feedback; write no files")
    environment.add_argument("--id", type=int, choices=range(16), required=True)
    environment.add_argument("--preset", type=int, choices=range(4), default=0)
    sub.add_parser("reverb-reference", help="Run synthetic reverb experiments in memory").add_argument("--full-rate", action="store_true", help="Include 48-kHz FIR resampling and alternating channel clocks")
    args = parser.parse_args()
    try:
        queries = {"function": function_query, "runtime": runtime_query, "movie": movie_query,
                   "picture": picture_query, "banks": banks_query, "music": music_query, "sound": sound_query, "model": model_query,
                   "environment": environment_query, "reverb-reference": reverb_network_reference, "nav": navigation_query}
        print(json.dumps(queries[args.command](args), indent=2))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Research query failed: {error}\n")


if __name__ == "__main__":
    main()
