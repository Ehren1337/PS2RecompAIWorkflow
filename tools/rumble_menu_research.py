"""Rumble Racing menu/function research. Read-only; no input or guest calls.

py -3 -B tools/rumble_menu_research.py label Continue
py -3 -B tools/rumble_menu_research.py function FE_HandleET_CarSelect --build February
py -3 -B tools/rumble_menu_research.py catalog

Menu captions are not function names. String references identify data and code
to investigate, not a callable action. No handler/argument mapping is assumed.
"""
import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent.parent
BUILDS = {
    "Retail": ("Rumble Racing (USA retail)", "CSV Map/retail/map.csv",
               "e3c2c19b5fdeeac9fb1f5a9b893346e7892e564796fa2f74fc40ae17a8ade594"),
    "February": ("Rumble Racing (Feb 7, 2001 prototype)", "CSV Map/map-ntsc.csv",
                 "44e74a35dd123e3504f81eded1db68844e2e082edaa908ea47a7ea1ec9a924a0"),
}


@dataclass(frozen=True)
class Segment:
    address: int
    flags: int
    data: bytes


class MenuResearch:
    def __init__(self, build):
        self.build = build
        folder, map_path, expected = BUILDS[build]
        data = (ROOT / "Extracted_Assets" / folder / "SLUS_201.74").read_bytes()
        self.digest = hashlib.sha256(data).hexdigest()
        if self.digest != expected:
            raise ValueError("ELF identity differs from the verified Rumble Racing build")
        if data[:6] != b"\x7fELF\x01\x01":
            raise ValueError("Expected little-endian ELF32")
        offset = struct.unpack_from("<I", data, 28)[0]
        size, count = struct.unpack_from("<HH", data, 42)
        if size < 32 or offset + size * count > len(data):
            raise ValueError("Invalid program-header extent")
        self.segments = []
        for index in range(count):
            kind, file_offset, address, _, length, _, flags, _ = struct.unpack_from(
                "<8I", data, offset + size * index)
            if kind != 1:
                continue
            if file_offset + length > len(data):
                raise ValueError("Invalid load-segment extent")
            self.segments.append(Segment(address, flags, data[file_offset:file_offset + length]))
        with (ROOT / map_path).open(encoding="utf-8-sig", newline="") as source:
            self.functions = [{"name": row["Name"], "start": int(row["Start"], 16),
                               "end": int(row["End"], 16)} for row in csv.DictReader(source)]

    def containing(self, address):
        # Exported ranges can overlap and may omit Ghidra body blocks. Report all
        # range matches, without presenting the map as a verified control-flow graph.
        return [f["name"] for f in self.functions if f["start"] <= address < f["end"]]

    def read(self, address, size):
        if size < 0:
            raise ValueError("Negative ELF read size")
        for segment in self.segments:
            offset = address - segment.address
            if 0 <= offset and offset + size <= len(segment.data):
                return segment.data[offset:offset + size]
        raise ValueError("Read outside verified ELF load segments")

    def catalog(self):
        """Retail selection identities from owned ELF/assets, never game calls.

        1948B0 uses the normal track order and text IDs;191A80 displays names
        from the driver table. These are menu identities, not model indices or
        route-node IDs. Unlock state and loading readiness are separate.
        """
        if self.build != "Retail":
            raise ValueError("Selection catalog is verified for USA retail only")
        from rumble_research import stream_resources
        path = ROOT / "Extracted_Assets" / BUILDS[self.build][0] / "DATA/FEND/FE2.TRK"
        resources = [data for tag, identity, offset, data in stream_resources(path, {"TxtR"})
                     if identity == 0]
        if len(resources) != 1 or hashlib.sha256(resources[0]).hexdigest() != (
                "d55292d49b3f1a7ea25ca03bf6d3d40ce7233a9c8109801e7801662aeabac95d"):
            raise ValueError("Frontend text resource differs from verified retail data")
        strings = {}
        for record in resources[0].split(b"\0"):
            if not record:
                continue
            key, separator, text = record.partition(b" ")
            if not separator or not key.isdigit() or int(key) in strings:
                raise ValueError("Invalid/duplicate retail text identity")
            strings[int(key)] = text.decode("ascii")
        order = struct.unpack("<15I", self.read(0x1ecaf0, 60))
        if sorted(order) != list(range(15)):
            raise ValueError("Unexpected normal track order")
        tracks = []
        for position, identity in enumerate(order):
            group, variant = divmod(identity, 2)
            tracks.append({"menu_position": position, "track_id": identity,
                           "group": group, "variant": variant,
                           "name": strings[5001 + group * 10 + variant]})
        vehicles = []
        for identity in range(36):
            row = self.read(0x1eac80 + identity * 28, 28)
            address = struct.unpack_from("<I", row)[0]
            name = bytearray()
            for offset in range(64):
                value = self.read(address + offset, 1)[0]
                if not value:
                    break
                name.append(value)
            else:
                raise ValueError("Unterminated driver name")
            vehicles.append({"driver_id": identity, "name": name.decode("ascii"),
                             "model_index": row[6]})
        return {"tracks": tracks, "vehicles": vehicles,
                "availability": "Catalog membership does not imply unlocked or tested content.",
                "invocation": "Read-only identities; no selection, unlock changes or direct race launch."}

    def words(self):
        for segment in self.segments:
            for offset in range((-segment.address) % 4, len(segment.data) - 3, 4):
                yield segment.address + offset, struct.unpack_from("<I", segment.data, offset)[0], segment

    def label(self, caption, limit):
        needle = caption.encode("ascii")
        if not needle or b"\0" in needle:
            raise ValueError("Expected a nonempty ASCII caption")
        strings = []
        for segment in self.segments:
            offset = segment.data.find(needle + b"\0")
            while offset >= 0:
                if offset == 0 or segment.data[offset - 1] == 0:
                    strings.append(segment.address + offset)
                offset = segment.data.find(needle + b"\0", offset + 1)
        references = {address: [] for address in strings}
        for address, value, segment in self.words():
            if value in references:
                references[value].append({"address": f"0x{address:08x}",
                    "kind": "aligned word equals string address; pointer candidate",
                    "containing_exported_ranges": self.containing(address)})
        return {"caption": caption, "string_count": len(strings), "strings": [
            {"address": f"0x{address:08x}", "pointer_candidate_count": len(references[address]),
             "pointer_candidates": references[address][:limit]} for address in strings[:limit]],
            "handler_status": "unresolved: confirm the table consumer and event branch in Ghidra",
            "coverage": "Exact NUL-terminated ASCII ELF strings and aligned absolute pointer candidates only; "
                        "asset text, constructed addresses and indirect references are not searched."}

    def function(self, query, limit):
        if query.lower().startswith("0x"):
            address = int(query, 16)
            matches = [f for f in self.functions if f["start"] <= address < f["end"]]
        else:
            matches = [f for f in self.functions if f["name"].lower() == query.lower()]
            if not matches:
                matches = [f for f in self.functions if query.lower() in f["name"].lower()]
        entries = {f["start"] for f in matches}
        calls, callers = {a: [] for a in entries}, {a: [] for a in entries}
        for address, word, segment in self.words():
            if not segment.flags & 1 or word >> 26 != 3:
                continue
            target = ((address + 4) & 0xf0000000) | ((word & 0x03ffffff) << 2)
            edge = {"instruction": f"0x{address:08x}", "target": f"0x{target:08x}",
                    "target_exported_ranges": self.containing(target)}
            for function in matches:
                if function["start"] <= address < function["end"]:
                    calls[function["start"]].append(edge)
            if target in entries:
                callers[target].append({"instruction": f"0x{address:08x}",
                                        "source_exported_ranges": self.containing(address)})
        return {"match_count": len(matches), "functions": [
            {"name": f["name"], "entry": f"0x{f['start']:08x}", "end": f"0x{f['end']:08x}",
             "direct_call_candidate_count": len(calls[f["start"]]),
             "direct_call_candidates": calls[f["start"]][:limit],
             "direct_caller_candidate_count": len(callers[f["start"]]),
             "direct_caller_candidates": callers[f["start"]][:limit]} for f in matches[:limit]],
            "coverage": "JAL encodings in executable segments and exported function ranges; "
                        "embedded data may resemble instructions. Indirect calls/tail branches are excluded. "
                        "Names and addresses belong only to the selected build; no retail transfer is assumed.",
            "invocation": "disabled: handler identity, arguments and required game state need verification"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("label", "function", "catalog"))
    parser.add_argument("query", nargs="?")
    parser.add_argument("--build", choices=BUILDS, default="Retail")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.limit <= 30:
        parser.error("--limit must be in 1..30")
    if (args.mode == "catalog") != (args.query is None):
        parser.error("catalog takes no query; label and function require a query")
    research = MenuResearch(args.build)
    result = research.catalog() if args.mode == "catalog" else getattr(research, args.mode)(args.query, args.limit)
    print(json.dumps({"build": args.build, "elf_sha256": research.digest,
                      "read_only": True, **result}, indent=2))


if __name__ == "__main__":
    main()
