"""Local Rumble Racing development controls; no game code or assets included.

From the project root:
    py -3 -B Scripts/rumble_dev.py launch --max-upgrades --ai
    py -3 -B Scripts/rumble_dev.py launch --no-max-upgrades --no-ai
    py -3 -B Scripts/rumble_dev.py launch --720p --no-frame-capture
    py -3 -B Scripts/rumble_dev.py launch --720p --keyboard-profile "<PCSX2 profile path>/Keyboard.ini"
    py -3 -B Scripts/rumble_dev.py prototype-candidates
    py -3 -B Scripts/rumble_dev.py save-spot
    py -3 -B Scripts/rumble_dev.py launch --720p --no-frame-capture --benchmark-spot
    py -3 -B Scripts/rumble_dev.py launch --720p --car Tiberius --track "True Grits" --benchmark-spot

Launch uses the existing native runner. With --car and --track, the opt-in
native adapter calls original race preparation and loading directly, bypasses
intro presentation and continues the completed startup card notice without
keyboard input. Without those arguments, the normal menus remain available.
Direct race currently means one player, eight cars, three laps, Forgiving,
power-ups active. Car/track names or retail catalog IDs are accepted. Dev mode
can select locked catalog entries; it does not change saved unlock records.
Max upgrades means
Elite for the single human player and Rookie for opponents in Single Race.
It applies at the next race setup, not to a running race or memory-card saves.
AI is the separate opt-in No Mercy original NPC driver. Omit --ai for manual
driving. A running runner is never automatically stopped by this script.

save-spot reads a parked car and overwrites Scripts/rumble-benchmark-spot.json.
--benchmark-spot opts into one vehicle recovery reset per fresh race on that
same track, three game seconds after GO. It restores position and direction;
the original recovery also resets motion, active power-ups and skid history.
It is a repeatable location, not a save state of opponents or world objects.
Normal launches do not teleport. No saved game or disc data is modified.

Prototype candidates are an audit, NOT installed/playable menu entries yet.
Requested restoration policy: prototype appearance AND driving behavior,
retail audio; ordinary retail choices retain retail behavior. Model hashes
alone cannot establish physics differences or working restoration.

Future Lua integration boundary: expose named dev options and validated
vehicle identities through the native runtime. There is no Lua interpreter,
game-script execution, or arbitrary memory-write API in this helper.
"""
import argparse
import configparser
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
SPOT = ROOT / "Scripts" / "rumble-benchmark-spot.json"
RETAIL_SHA = "e3c2c19b5fdeeac9fb1f5a9b893346e7892e564796fa2f74fc40ae17a8ade594"


def benchmark_pose(record):
    """Validate a saved retail pose before passing it to the native dev hook."""
    if record.get("schema_version") != 1 or record.get("elf_sha256") != RETAIL_SHA:
        raise ValueError("Benchmark spot belongs to an unverified game build")
    track = record["track_id"]
    if type(track) is not int or not 0 <= track < 15:
        raise ValueError("Invalid benchmark track")
    matrix = record.get("matrix", record.get("research_pose", {}).get("car_matrix_f32"))
    if not isinstance(matrix, list) or len(matrix) != 16 or not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in matrix):
        raise ValueError("Benchmark spot needs a finite 4x4 car pose")
    if any(matrix[i] != 0 for i in (3, 7, 11)) or matrix[15] != 1:
        raise ValueError("Invalid affine pose")
    axes = [matrix[i:i + 3] for i in (0, 4, 8)]
    dot = lambda a, b: sum(x*y for x, y in zip(a, b))
    if any(abs(dot(a, a)-1) > .02 for a in axes) or any(
            abs(dot(axes[a], axes[b])) > .02 for a, b in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Pose orientation is not orthonormal")
    a, b, c = axes
    determinant = sum(a[i]*(b[(i+1)%3]*c[(i+2)%3]-b[(i+2)%3]*c[(i+1)%3]) for i in range(3))
    if determinant < .98 or any(abs(v) > 100000 for v in matrix[12:15]):
        raise ValueError("Reflected pose or position outside development bounds")
    if record["position"] != matrix[12:15]:
        raise ValueError("Saved coordinates disagree with the pose")
    return str(track) + " " + " ".join(format(v, ".9g") for v in matrix)


def save_spot():
    """Read only from the game; overwrite one saved spot, with no history."""
    import time
    from rumble_navigate import Navigator, RaceProbe
    nav = Navigator(10)
    with RaceProbe(nav) as probe:
        first = probe.state()
        time.sleep(.15)
        state = probe.state()
        matrix = probe.floats(probe.car + 0x10, 16)
        if abs(state["speed"]) > .1 or any(abs(a-b) > .025 for a, b in zip(first["position"], matrix[12:15])):
            raise ValueError("Park the car before saving a benchmark spot")
        record = {"schema_version": 1, "name": "user-benchmark-spot", "elf_sha256": RETAIL_SHA,
                  "track_id": state["track_id"], "track_name": state["track_name"],
                  "position": matrix[12:15], "forward": matrix[8:11], "matrix": matrix,
                  "capture_state": state, "source_pid": nav.pid, "captured_unix": time.time()}
        benchmark_pose(record)
    SPOT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {"saved": str(SPOT), "track": record["track_name"], "position": record["position"]}


def race_request(car, track, level):
    from rumble_menu_research import MenuResearch
    catalog = MenuResearch("Retail").catalog()
    def resolve(value, rows, field):
        matches = [r for r in rows if str(r[field]) == value or r["name"].casefold() == value.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Unknown or ambiguous {field}: {value}")
        return matches[0][field]
    driver = resolve(car, catalog["vehicles"], "driver_id")
    course = resolve(track, catalog["tracks"], "track_id")
    return f"{driver} {course} {('rookie', 'pro', 'elite').index(level)}"


def keyboard_bindings(profile):
    """Import only Pad1 keyboard bindings; never modify the PCSX2 profile."""
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    with Path(profile).open(encoding="utf-8-sig") as source:
        config.read_file(source)
    if not config.has_section("Pad1") or config["Pad1"].get("Type") != "DualShock2":
        raise ValueError("Expected a PCSX2 DualShock2 Pad1 profile")
    names = ("Select", "L3", "R3", "Start", "Up", "Right", "Down", "Left",
             "L2", "R2", "L1", "R1", "Triangle", "Circle", "Cross", "Square",
             "LLeft", "LRight", "LUp", "LDown", "RLeft", "RRight", "RUp", "RDown")
    keys = {chr(n): str(n) for n in range(ord('A'), ord('Z') + 1)}
    keys.update({str(n): str(48 + n) for n in range(10)})
    keys.update({f"Numpad{n}": str(320 + n) for n in range(10)})
    keys.update({"Up": "265", "Down": "264", "Left": "263", "Right": "262",
                 "Return": "257", "Tab": "258", "Space": "32", "Escape": "256",
                 "Alt": "342,346", "Control": "341,345", "Shift": "340,344"})
    bindings = []
    for name in names:
        source = config["Pad1"].get(name, "")
        if not source:
            continue
        if source.startswith("SDL-") and "Keyboard/" not in source:
            continue  # Physical gamepad input remains handled by the runtime.
        if not source.startswith("Keyboard/") or source[9:] not in keys:
            raise ValueError(f"Unsupported profile binding for {name}: {source}")
        bindings.append(f"{name}={keys[source[9:]]}")
    if not bindings:
        raise ValueError("This profile contains no supported keyboard bindings")
    return ";".join(bindings)


def prototype_candidates():
    from rumble_menu_research import MenuResearch

    retail = MenuResearch("Retail")
    prototype = MenuResearch("February")
    report = json.loads((ROOT / "analysis/assets-investigation.json").read_text(encoding="utf-8-sig"))
    models = {}
    for build in ("February", "Retail"):
        entry = next(b for b in report["Builds"] if b["Build"] == build)
        archive = next(a for a in entry["Archives"] if a["Path"] == "GLBLDATA.PS2")
        selected = [r for r in archive["Resources"] if r["Type"] == "o3d " and 10000 <= r["Id"] <= 10110]
        if len(selected) != 111 or len({r["Id"] for r in selected}) != 111 or any(r["Error"] for r in selected):
            raise ValueError("Incomplete or ambiguous model inventory")
        models[build] = {r["Id"]: r for r in selected}
    inventory = next(i for i in report["Inventories"] if i["Build"] == "February")
    drivers = {d["Index"]: d for d in inventory["Drivers"]}
    if set(drivers) != set(range(36)):
        raise ValueError("Incomplete prototype driver inventory")
    rows = []
    for vehicle in retail.catalog()["vehicles"]:
        driver, model = vehicle["driver_id"], vehicle["model_index"]
        entry = drivers[driver]
        if entry["ModelIndex"] != model:
            raise ValueError("Prototype/retail model mapping differs from the audited layout")
        name = entry["Name"]
        old_row = prototype.read(0x1f2e00 + driver * 28, 28)
        new_row = retail.read(0x1eac80 + driver * 28, 28)
        if old_row[6] != model or old_row[11] >= 2 or new_row[11] >= 2 or old_row[12] >= 13 or new_row[12] >= 14:
            raise ValueError("Unrecognized driver physics/engine profile")
        physics_equal = (prototype.read(0x1f3840 + old_row[12] * 0x38, 0x38) ==
                         retail.read(0x1eb4d0 + new_row[12] * 0x38, 0x38))
        engine_equal = (prototype.read(0x1f3750 + old_row[11] * 0x74, 0x74) ==
                        retail.read(0x1eb3e0 + new_row[11] * 0x74, 0x74))
        changed = [level for level in range(3) if
                   models["February"][10000 + model * 3 + level]["SHA256"] !=
                   models["Retail"][10000 + model * 3 + level]["SHA256"]]
        if changed or not physics_equal or not engine_equal or name != vehicle["name"]:
            rows.append({"candidate_id": f"february-driver-{driver}",
                         "prototype_name": name, "retail_name": vehicle["name"],
                         "driver_id": driver, "model_index": model,
                         "changed_model_classes": [["Rookie", "Pro", "Elite"][i] for i in changed],
                         "physics_profile_bytes_equal": physics_equal,
                         "engine_simulation_profile_bytes_equal": engine_equal,
                         "behavior_note": "Profile equality alone does not prove identical physics code or handling",
                         "playable": False,
                         "status": "Pending texture, geometry and prototype physics compatibility validation",
                         "audio_policy": "Retail"})
    return {"source": "Existing local analysis inventory; no exports or game modifications",
            "candidates": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("launch", help="Launch the existing retail development runner")
    launch.add_argument("--max-upgrades", action=argparse.BooleanOptionalAction, default=False)
    launch.add_argument("--ai", action=argparse.BooleanOptionalAction, default=False)
    launch.add_argument("--window", action="store_true", help="Show native game window instead of viewer-only mode")
    launch.add_argument("--720p", dest="window_720p", action="store_true",
                        help="Show a 1280x720 window with a 4:3 picture; internal render resolution is unchanged")
    launch.add_argument("--keyboard-profile", type=Path,
                        help="Read keyboard controls from a PCSX2 input profile (no files copied)")
    launch.add_argument("--no-audio", action="store_true", help="Retail diagnostic: skip sound execution/output, retain required protocol replies; restart to change")
    launch.add_argument("--no-frame-capture", action="store_true", help="Disable screenshots/contact sheets; retain text diagnostics")
    launch.add_argument("--glitch-visuals", action=argparse.BooleanOptionalAction, default=False,
                        help="Opt-in scrambled-color/block effect on the displayed image only; restart to change")
    launch.add_argument("--configuration", choices=("Debug", "RelWithDebInfo"), default="RelWithDebInfo")
    launch.add_argument("--benchmark-spot", action="store_true",
                        help="Dev only: on the saved track, reset to the saved pose three game seconds after GO")
    launch.add_argument("--car", help="Dev direct race: retail car name or driver ID; requires --track")
    launch.add_argument("--track", help="Dev direct race: retail track name or track ID; requires --car")
    launch.add_argument("--car-class", choices=("rookie", "pro", "elite"), default="rookie")
    commands.add_parser("save-spot", help="Save the parked player's position and orientation; overwrite one spot file")
    commands.add_parser("prototype-candidates", help="Read-only audit; these are not playable entries yet")
    args = parser.parse_args()
    if args.command == "save-spot":
        print(json.dumps(save_spot(), indent=2))
        return 0
    if args.command == "prototype-candidates":
        print(json.dumps(prototype_candidates(), indent=2))
        return 0
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise RuntimeError("This local launcher needs PowerShell; the native dev hooks are C++")
    command = [shell, "-NoProfile", "-File", str(ROOT / "run-ntsc.ps1"),
               "-Build", "Retail", "-Configuration", args.configuration]
    if not args.window and not args.window_720p:
        command.append("-ViewerOnly")
    if args.window_720p:
        command.append("-Window720p")
    if args.max_upgrades:
        command.append("-DevMaxUpgrades")
    if args.ai:
        command.append("-DevAI")
    if args.no_frame_capture:
        command.append("-NoFrameCapture")
    if args.glitch_visuals:
        command.append("-DevGlitchVisuals")
    environment = os.environ.copy()
    environment.pop("PS2_RUMBLE_DEV_TELEPORT", None)
    environment.pop("PS2_RUMBLE_DEV_RACE", None)
    if bool(args.car) != bool(args.track):
        raise ValueError("Direct race requires both --car and --track")
    if args.car:
        environment["PS2_RUMBLE_DEV_RACE"] = race_request(args.car, args.track, args.car_class)
    if args.benchmark_spot:
        environment["PS2_RUMBLE_DEV_TELEPORT"] = benchmark_pose(json.loads(SPOT.read_text(encoding="utf-8")))
        if args.car and environment["PS2_RUMBLE_DEV_RACE"].split()[1] != environment["PS2_RUMBLE_DEV_TELEPORT"].split()[0]:
            raise ValueError("Direct race track does not match the saved benchmark spot")
    environment["PS2_RUMBLE_DEV_NO_AUDIO"] = "1" if args.no_audio else "0"
    if args.keyboard_profile:
        environment["PS2_PAD_KEYS"] = keyboard_bindings(args.keyboard_profile)
    return subprocess.run(command, cwd=ROOT, env=environment, check=False).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, KeyError, StopIteration) as error:
        print(f"Rumble dev: {error}", file=sys.stderr)
        raise SystemExit(1)
