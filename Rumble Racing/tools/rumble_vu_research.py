"""Inspect the verified retail VU1 upload without exporting game code.
Run these examples from the Rumble Racing folder.

py -3 -B tools/rumble_vu_research.py

Prints addresses, hashes and instruction-category counts only. This is input
research for a future native VU path, not a compiler or a gameplay patch.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import struct

from rumble_menu_research import MenuResearch


@dataclass(frozen=True)
class UploadImage:
    code: bytes
    segments: tuple
    extent: int


def parse_mpg_upload(packet, address):
    """Decode this game's END DMA tag and contiguous MPG-only VIF upload.

    Deliberately reject other chains/commands instead of treating arbitrary
    DMA packets as supported. NUM=0 means 256 instruction pairs. Addresses
    reported below are ELF virtual addresses, not file offsets.
    """
    if len(packet) < 16:
        raise ValueError("Truncated DMA tag")
    tag, dma_address, nop, command = struct.unpack_from("<4I", packet)
    if tag >> 16 != 0x7000 or dma_address or nop:
        raise ValueError("Expected simple END DMA tag with MPG in tag data")
    if len(packet) != 16 + (tag & 0xffff) * 16:
        raise ValueError("DMA payload length mismatch")
    code, segments, cursor, extent = bytearray(16384), [], 16, 0
    while True:
        if command >> 24 != 0x4a:
            raise ValueError("Only MPG commands are supported")
        destination = (command & 0xffff) * 8
        count = ((command >> 16) & 0xff) or 256
        length = count * 8
        if destination != extent or destination + length > len(code):
            raise ValueError("Expected contiguous, non-overlapping VU1 upload")
        if cursor + length > len(packet):
            raise ValueError("Truncated MPG instruction payload")
        code[destination:destination + length] = packet[cursor:cursor + length]
        segments.append((destination, address + cursor, length))
        extent += length
        cursor += length
        if cursor == len(packet):
            break
        if len(packet) - cursor == 8 and packet[cursor:] == bytes(8):
            break  # Final qword alignment, not another upload.
        if cursor + 8 > len(packet):
            raise ValueError("Truncated MPG command pair")
        nop, command = struct.unpack_from("<2I", packet, cursor)
        if nop:
            raise ValueError("Expected NOP before next MPG command")
        cursor += 8
    return UploadImage(bytes(code), tuple(segments), extent)


def retail_image():
    original = MenuResearch("Retail")  # Rejects any other ELF identity.
    # Retail 1B9AE0 sends this DMA chain to VIF1 at startup.
    packet_address = 0x1d8730
    packet = original.read(packet_address, 16 + 0x2d3 * 16)
    image = parse_mpg_upload(packet, packet_address)
    expected = "36e2f47b328c92d0aa7a8865c6bd37b77ff37adb5aadae68cc6bd38b0b87088b"
    if image.extent != 0x2d00 or hashlib.sha256(image.code).hexdigest() != expected:
        raise ValueError("VU1 image differs from the verified live retail upload")
    return image


def compare_hot_output(data, top, constants, *, arithmetic=None):
    """Compare packet fields for the observed retail resume entry 0x1428.

    Caller must verify code identity and a stable completed-batch boundary.
    This models packet dataflow only, not VU flags/registers/pipelines/timing.
    constants contains raw VF0..VF4. Output is counts; an optional arithmetic
    dict receives in-memory exit predictions for separate research comparisons.
    Reject unresearched wrapping/overlap, degenerate counts and exceptional
    operands rather than claiming a complete native-kernel replacement.
    """
    if len(data) != 16384 or len(constants) != 80 or not 0 <= top < 1024:
        raise ValueError("Invalid VU data/constants/TOP")

    def floats(block, saturate=False):
        words = struct.unpack("<" + "I" * (len(block) // 4), block)
        values = []
        for bits in words:
            exponent = (bits >> 23) & 255
            if exponent == 255:
                if not saturate:
                    raise ValueError("Exceptional operand needs separate modeling")
                bits = (bits & 0x80000000) | 0x7f7fffff
            if exponent == 0:
                bits &= 0x80000000  # Runtime normalizeOperand: signed zero.
            values.append(struct.unpack("<f", struct.pack("<I", bits))[0])
        return tuple(values)

    def rounded(value):
        # Double holds an exact product of two binary32 operands. Preserve the
        # separate binary32 multiply/add rounds used by the current runtime.
        if not math.isfinite(value) or abs(value) > float.fromhex("0x1.fffffep127"):
            raise ValueError("Overflow/exceptional result needs separate modeling")
        if abs(value) < 2**-126:
            return math.copysign(0.0, value)
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
        nearest = struct.unpack("<f", struct.pack("<I", bits))[0]
        if abs(nearest) > abs(value):
            bits -= 1  # One binary32 step toward zero, including negatives.
        return struct.unpack("<f", struct.pack("<I", bits))[0]

    sticky = 0

    def flags(value):
        result = 2 if math.copysign(1.0, value) < 0 else 0
        if value == 0:
            return result | 1
        if abs(value) < 2**-126:
            return result | 5
        if abs(value) > float.fromhex("0x1.fffffep127"):
            return result | 8
        return result

    def quad(index):
        if not 0 <= index < 1024:
            raise ValueError("Wrapping batch needs separate modeling")
        return data[index * 16:(index + 1) * 16]

    values = floats(constants)
    matrix = [values[k * 4:k * 4 + 4] for k in range(5)]
    if matrix[0] != (0.0, 0.0, 0.0, 1.0):
        raise ValueError("VF0 constant invariant does not hold")
    header = struct.unpack("<4I", quad(top))
    groups, prefix = header[0] & 0xffff, header[1] & 0xffff
    if not 0 < groups <= 32 or prefix >= 224 or top + 224 + prefix >= 1024:
        raise ValueError("Unresearched batch header")
    cursor, output = top + 1 + prefix, top + 224 + prefix
    counts = dict(groups=groups, vertices=0, positions_matching=0,
                  textures_matching=0, colors_matching=0, flags_matching=0,
                  headers_matching=0, prefix_matching=True)
    counts["prefix_matching"] = all(quad(top + 1 + k) == quad(top + 224 + k)
                                    for k in range(prefix))
    for _ in range(groups):
        count = struct.unpack_from("<I", quad(cursor))[0] & 0x7fff
        if not 2 <= count <= 128:
            raise ValueError("Degenerate/oversized vertex group")
        if cursor + 1 + 3 * count > top + 224 or output + 1 + 3 * count > 1024:
            raise ValueError("Overlapping/wrapping arrays need separate modeling")
        counts["headers_matching"] += quad(cursor) == quad(output)
        for vertex in range(count):
            # The routine consumes XYZ only from these arrays. Their W words
            # may contain packed non-float metadata; do not classify them as
            # arithmetic operands (texture output W is outside this model).
            position = floats(quad(cursor + 1 + vertex)[:12])
            texture = floats(quad(cursor + 1 + count + vertex)[:12])
            color = quad(cursor + 1 + 2 * count + vertex)
            transformed = []
            for lane in range(4):
                acc = rounded(matrix[4][lane])
                sticky |= flags(matrix[4][lane])
                for axis in (2, 1, 0):
                    product = matrix[axis + 1][lane] * position[axis]
                    sticky |= flags(product) | flags(acc + product)
                    acc = rounded(acc + rounded(product))
                transformed.append(acc)
            if not transformed[3]:
                raise ValueError("Zero projection divisor needs separate modeling")
            q = rounded(1.0 / transformed[3])
            for x in transformed:
                sticky |= flags(x * q)
                if vertex:
                    sticky |= flags(x + 0.0)
            for x in texture:
                sticky |= flags(x * q)
            xyz = [max(-2147483648, min(2147483647, int(rounded(x * q) * 16))) & 0xffffffff
                   for x in transformed[:3]]
            stq = struct.pack("<3f", *(rounded(texture[k] * q) for k in range(3)))
            emitted = struct.unpack("<4I", quad(output + 3 + 3 * vertex))
            flag = struct.unpack_from("<I", color, 8)[0] & 1
            counts["positions_matching"] += tuple(xyz) == emitted[:3]
            counts["textures_matching"] += stq == quad(output + 1 + 3 * vertex)[:12]
            counts["colors_matching"] += color == quad(output + 2 + 3 * vertex)
            counts["flags_matching"] += emitted[3] == (0x7fff if flag else 0xffff8000)
            counts["vertices"] += 1
        if arithmetic is not None:
            # Every group has its own unused transform and ADD copy tail.
            lookahead = floats(quad(cursor + 1 + count)[:12])
            acc_tail, transformed_tail = [], []
            for lane in range(4):
                acc = rounded(matrix[4][lane])
                sticky |= flags(matrix[4][lane])
                for axis in (2, 1, 0):
                    product = matrix[axis + 1][lane] * lookahead[axis]
                    sticky |= flags(product) | flags(acc + product)
                    acc = rounded(acc + rounded(product))
                    if axis == 1:
                        acc_tail.append(acc)
                transformed_tail.append(acc)
            for index in range(3, count + 2):
                for x in floats(quad(cursor + 1 + index), saturate=True):
                    sticky |= flags(x + 0.0)
        cursor += 1 + 3 * count
        output += 1 + 3 * count
    if arithmetic is not None:
        # The software-pipelined loop computes one unused transform beyond
        # the final vertex. At this point cursor is just past the last group.
        # That lookahead consumes its first texture quad as a position, while
        # VF28 loads the second texture quad and VF29 adds VF0.x to it.
        raw_tail = quad(cursor - 2 * count + 1)
        mac, current_status = 0, 0
        # Last flag-producing instruction is MULq.xyz VF27,VF30,Q at1598.
        # FTOI4 and later integer/memory instructions do not overwrite MAC.
        for lane, value in zip((8, 4, 2), texture):
            exact = value * q
            flags = 2 if math.copysign(1.0, exact) < 0 else 0
            if exact == 0:
                flags |= 1
            elif abs(exact) < 2**-126:
                flags |= 5
            elif abs(exact) > float.fromhex("0x1.fffffep127"):
                flags |= 8
            current_status |= flags
            for bit in range(4):
                if flags & (1 << bit):
                    mac |= lane << (4 * bit)
        arithmetic.update({
            "vf25": struct.pack("<4f", *transformed_tail),
            "vf26": struct.pack("<4f", *(rounded(x + 0.0) for x in transformed)),
            "vf28": raw_tail,
            "vf29": struct.pack("<4f", *(rounded(x + 0.0) for x in floats(raw_tail, saturate=True))),
            "acc": struct.pack("<4f", *acc_tail),
            "q": struct.pack("<f", q),
            "mac": struct.pack("<I", mac),
            "current_status": struct.pack("<I", current_status),
            "sticky_added": struct.pack("<I", sticky << 6),
        })
    counts["matched"] = (counts["prefix_matching"] and counts["headers_matching"] == groups
                         and all(counts[k] == counts["vertices"] for k in
                                 ("positions_matching", "textures_matching", "colors_matching", "flags_matching")))
    counts["scope"] = "Packet fields only; texture W, VU state/flags, timing and code identity are not validated here."
    return counts


def compare_clip_output(data, top, constants):
    """Research main-packet output of verified retail resume entry 0x1618.

    constants is raw VF0..VF12. Caller verifies code identity and stable data
    after stores drain; PC1618 may still be waiting for XGKICK to finish.
    This is a read-only packet model, not complete VU state/timing emulation.
    The clipping helper's extra packets are NOT modeled. Its early return
    through 1950..1970 is modeled separately from actual polygon clipping.
    Unknown first-two-vertex history and actual clipping leave control words
    untested and main_packet_complete false, never silently count as success.
    """
    if len(data) != 16384 or len(constants) != 208 or not 0 <= top < 1024:
        raise ValueError("Invalid VU data/constants/TOP")

    def floats(block):
        values = []
        for bits in struct.unpack("<" + "I" * (len(block) // 4), block):
            exponent = (bits >> 23) & 255
            if exponent == 255:
                raise ValueError("Exceptional operand needs separate modeling")
            if exponent == 0:
                bits &= 0x80000000
            values.append(struct.unpack("<f", struct.pack("<I", bits))[0])
        return values

    def rounded(value):
        if not math.isfinite(value) or abs(value) > float.fromhex("0x1.fffffep127"):
            raise ValueError("Exceptional result needs separate modeling")
        if abs(value) < 2**-126:
            return math.copysign(0.0, value)
        bits = struct.unpack("<I", struct.pack("<f", value))[0]
        if abs(struct.unpack("<f", struct.pack("<I", bits))[0]) > abs(value):
            bits -= 1
        return struct.unpack("<f", struct.pack("<I", bits))[0]

    def quad(index):
        if not 0 <= index < 1024:
            raise ValueError("Wrapping batch needs separate modeling")
        return data[index * 16:(index + 1) * 16]

    matrix = [floats(constants[k * 16:(k + 1) * 16]) for k in range(13)]
    if matrix[0] != [0.0, 0.0, 0.0, 1.0]:
        raise ValueError("VF0 constant invariant does not hold")

    def transform(position, first):
        result = []
        for lane in range(4):
            acc = rounded(matrix[first + 3][lane])
            for axis in (2, 1, 0):
                acc = rounded(acc + rounded(matrix[first + axis][lane] * position[axis]))
            result.append(acc)
        return result

    def clip(vector):
        # Match the interpreter's signed raw-bit CLIP comparisons, including
        # the subnormal-W threshold. This is not a generic abs(x) > abs(w).
        words = struct.unpack("<4I", struct.pack("<4f", *vector))
        w = words[3]
        limit = (w & 0x7fffffff) if w & 0x7f800000 else 0x7fffff
        result = 0
        for axis, bits in enumerate(words[:3]):
            for negative in (0, 1):
                ordered = bits ^ (negative << 31)
                if ordered & 0x80000000:
                    ordered -= 0x100000000
                if ordered > limit:
                    result |= 1 << (2 * axis + negative)
        return result

    header = struct.unpack("<4I", quad(top))
    groups, prefix = header[0] & 0xffff, header[1] & 0xffff
    if not 0 < groups <= 32 or prefix >= 224:
        raise ValueError("Unresearched batch header")
    cursor, output = top + 1 + prefix, top + 224 + prefix
    counts = dict(groups=groups, vertices=0, positions_matching=0,
                  textures_matching=0, colors_matching=0, flags_matching=0,
                  flags_tested=0, headers_matching=0, prefix_matching=True,
                  inside=0, culled=0, helper=0, disabled=0, unknown=0, guardband=0)
    counts["prefix_matching"] = all(quad(top + 1 + k) == quad(top + 224 + k)
                                    for k in range(prefix))
    for _ in range(groups):
        count = struct.unpack_from("<I", quad(cursor))[0] & 0x7fff
        if (not 2 <= count <= 128 or cursor + 1 + 3 * count > top + 224
                or output + 1 + 3 * count > 1024):
            raise ValueError("Degenerate/overlapping/wrapping arrays")
        counts["headers_matching"] += quad(cursor) == quad(output)
        # Entry CLIP and the inter-group unused lookahead are not modeled.
        # Each group's first two enabled vertices therefore remain unknown.
        history = fine_history = 0
        for vertex in range(count):
            position = floats(quad(cursor + 1 + vertex)[:12])
            raw_texture = quad(cursor + 1 + count + vertex)
            texture = floats(raw_texture[:12])
            color = quad(cursor + 1 + 2 * count + vertex)
            screen = transform(position, 1)
            history = ((history << 6) | clip(transform(position, 5))) & 0xffffff
            fine_history = ((fine_history << 6) | clip(transform(position, 9))) & 0xffffff
            if not screen[3]:
                raise ValueError("Zero projection divisor needs separate modeling")
            q = rounded(1.0 / screen[3])
            xyzw = [max(-2147483648, min(2147483647, int(rounded(x * q) * 16))) & 0xffffffff
                    for x in screen]
            # Unlike 1428, texture W comes from each source texture quad.
            stq = struct.pack("<3f", *(rounded(x * q) for x in texture)) + raw_texture[12:]
            actual = struct.unpack("<4I", quad(output + 3 + 3 * vertex))
            enabled = struct.unpack_from("<I", color, 8)[0] & 1
            if not enabled:
                category = "disabled"
            elif vertex < 2:
                category = "unknown"
            elif not history & 0x3ffff:
                category = "inside"
            elif any((history | mask) == 0xffffff for mask in
                     (0xfdf7df, 0xff7df7, 0xffbefb, 0xffdf7d, 0xffefbe)):
                category = "culled"
            elif not history & 0x20820 and not fine_history & 0x3ffff:
                # Helper rechecks with VF9..12. VI15 += 1 skips the ISW at
                # 17C8, retaining projected W instead of suppressing it.
                category = "guardband"
            else:
                category = "helper"
            counts[category] += 1
            if category not in ("unknown", "helper"):
                wanted = xyzw[3] if category in ("inside", "guardband") else 0x8000
                counts["flags_tested"] += 1
                counts["flags_matching"] += actual[3] == wanted
            counts["positions_matching"] += tuple(xyzw[:3]) == actual[:3]
            counts["textures_matching"] += stq == quad(output + 1 + 3 * vertex)
            counts["colors_matching"] += color == quad(output + 2 + 3 * vertex)
            counts["vertices"] += 1
        cursor += 1 + 3 * count
        output += 1 + 3 * count
    counts["main_packet_complete"] = counts["flags_tested"] == counts["vertices"]
    counts["matched"] = (counts["main_packet_complete"] and counts["prefix_matching"]
                         and counts["headers_matching"] == groups
                         and counts["flags_matching"] == counts["flags_tested"]
                         and all(counts[k] == counts["vertices"] for k in
                                 ("positions_matching", "textures_matching", "colors_matching")))
    counts["scope"] = "Main packet only; clipped extra packets, full VU state and timing are not modeled."
    return counts


def compare_hot_boundary(data, top, vf, vi):
    """Check additional exit invariants at the verified 0x1428 boundary.

    vf/vi are raw 32-vector and 16-integer register banks. Texture W is
    preserved VF27.W: both MULq operations writing VF27 use XYZ masks.
    PC alone is insufficient: it reaches 0x1428 before flushPipelines ends.
    Live callers should also check ebit clear and XGKICK inactive on both
    sides of their stable read. This is still not an atomic pre/post capture.
    Unwritten
    registers, arithmetic vectors, flags, cycles and private pipelines still
    need their own entry/exit contract before replacing the interpreter.
    """
    if len(vf) != 512 or len(vi) != 64:
        raise ValueError("Invalid VU register banks")
    result = compare_hot_output(data, top, vf[:80])
    header = struct.unpack_from("<2I", data, top * 16)
    prefix = header[1] & 0xffff
    cursor, output = top + 1 + prefix, top + 224 + prefix
    texture_w = vf[27 * 16 + 12:28 * 16]
    w_matches = 0
    for _ in range(result["groups"]):
        count = struct.unpack_from("<I", data, cursor * 16)[0] & 0x7fff
        for vertex in range(count):
            at = (output + 1 + 3 * vertex) * 16 + 12
            w_matches += data[at:at + 4] == texture_w
        last_color = data[(cursor + 3 * count) * 16:(cursor + 3 * count + 1) * 16]
        flag = struct.unpack_from("<I", last_color, 8)[0] & 1
        expected_vi = {
            1: 1, 2: cursor + 1 + 2 * count, 3: cursor + 1 + 3 * count,
            4: output + 1 + 3 * count, 5: top + 224,
            6: cursor + 1 + 3 * count, 8: -32768,
            # IADDI at 0x1518 encodes -4, not -3. The branch observes VI4
            # before the delayed final SQI advances it to the packet end.
            9: output + 1 + 3 * count - 4,
            10: 32767 if flag else -32768, 11: 32767, 12: count, 13: 0,
        }
        copied = {
            18: data[cursor * 16:(cursor + 1) * 16],
            30: data[(cursor + 2 * count) * 16:(cursor + 2 * count + 1) * 16],
            31: last_color,
        }
        cursor += 1 + 3 * count
        output += 1 + 3 * count
    actual_vi = struct.unpack("<16i", vi)
    vi_mismatches = [reg for reg, value in expected_vi.items() if actual_vi[reg] != value]
    vf_mismatches = [reg for reg, value in copied.items() if vf[reg * 16:(reg + 1) * 16] != value]
    result.update(texture_w_matching=w_matches, vi_mismatches=vi_mismatches,
                  copied_vf_mismatches=vf_mismatches)
    result["matched"] = (result["matched"] and w_matches == result["vertices"]
                         and not vi_mismatches and not vf_mismatches)
    result["scope"] = ("Packet fields plus selected exit-register invariants only. "
                       "No entry preservation, complete VU state, flags or timing proof.")
    return result


def model_hot_cycles(pairs, counts, prefix):
    """Research the verified hot trace against runtime-decoded usage metadata.

    pairs maps PCs to upper/lower usage dictionaries and suppressedLowerVf.
    Caller validates that metadata against retail_image and the running decoder.
    Assumes idle entry pipelines and one complete packed output packet consumed
    by XGKICK; packet termination must be validated separately before execution.
    This is a dependency model, not a VU executor or PS2 hardware timing proof.
    """
    if (not counts or len(counts) > 32 or prefix < 0
            or any(not 2 <= n <= 128 for n in counts)
            or prefix + len(counts) + 3 * sum(counts) > 223):
        raise ValueError("Unsupported hot-routine layout")
    trace = list(range(0x1428, 0x1460, 8))
    trace += list(range(0x1460, 0x1488, 8)) * prefix
    for count in counts:
        trace += list(range(0x1488, 0x1530, 8))
        trace += list(range(0x1530, 0x1590, 8)) * (count - 1)
        trace += list(range(0x1590, 0x15e0, 8))
    trace += list(range(0x15e0, 0x15f8, 8)) + [0x1418, 0x1420]
    vf = [[0] * 4 for _ in range(32)]
    vi, acc = [0] * 16, [0] * 4
    now = qready = kick_done = kick_issue = 0
    qwords = prefix + len(counts) + 3 * sum(counts)
    for pc in trace:
        pair = pairs[pc]
        lo, up = pair["lower"], pair["upper"]
        for usage in (up, lo):
            if usage["pipeline"] == 4:
                raise ValueError("Unexpected EFU instruction")
            for reg, mask in usage["vfRead"]:
                for lane in range(4):
                    if mask & (8 >> lane):
                        now = max(now, vf[reg][lane])
            for reg in range(1, 16):
                if usage["viRead"] & (1 << reg):
                    now = max(now, vi[reg])
            for lane in range(4):
                if usage["accRead"] & (8 >> lane):
                    now = max(now, acc[lane])
        if lo["pipeline"] == 3 or lo["waitQ"]:
            now = max(now, qready)
        for usage, is_lower in ((lo, True), (up, False)):
            reg, mask = usage["vfWrite"]
            if reg and (not is_lower or reg != pair["suppressed"]):
                for lane in range(4):
                    if mask & (8 >> lane):
                        vf[reg][lane] = now + (usage["vfLatency"] or usage["latency"])
        for reg in range(1, 16):
            if lo["viWrite"] & (1 << reg):
                vi[reg] = now + (lo["viLatency"] or lo["latency"])
        for lane in range(4):
            if up["accWrite"] & (8 >> lane):
                acc[lane] = now + 1
        if lo["pipeline"] == 3:
            qready = now + lo["latency"]
        if pc == 0x15e0:
            kick_issue = now
            # One qword per two cycles; XGKICK's issue cycle contributes one.
            kick_done = now + 2 * qwords - 1
        now += 1
    return {"cycles": max(now, kick_done, qready, max(map(max, vf)), max(vi), max(acc)),
            "kick_cycle": kick_issue, "packet_qwords": qwords}


def model_clip_boundary(entry, before, code, *, max_steps=20000, entry_pc=0x1618):
    """Read-only replay of researched geometry boundaries, including helper 1C68.

    Inputs are one immutable, idle-entry sample (664-byte VU1State and 16KB
    data) and verified retail microcode. Returns proposed state/data/timing;
    never changes the running game. entry_pc selects one researched boundary;
    1618 remains the default. Its observed polygon-splitting path through
    1E48 is modeled; other paths and exceptional arithmetic still reject.
    This is a validation oracle for native dataflow work, not a runtime path.
    """
    if len(entry) != 664 or len(before) != 16384 or code != retail_image().code:
        raise ValueError("Expected exact retail code and complete idle-entry sample")
    vf = [list(struct.unpack_from('<4I', entry, r * 16)) for r in range(32)]
    vi = list(struct.unpack_from('<16i', entry, 512))
    acc = list(struct.unpack_from('<4I', entry, 576))
    q, p, immediate, random, pc, mac, clip, status = struct.unpack_from('<8I', entry, 592)
    top = struct.unpack_from('<I', entry, 640)[0]
    bounds = {0x1c68: (0x1608, 0x1e48), 0x1618: (0x1608, 0x1e48), 0xb38: (0xb28, 0xd88),
              0x400: (0x3f0, 0x598), 0x840: (0x830, 0xaf0),
              0xdb0: (0xdb0, 0xf28), 0x24c0: (0x24b0, 0x27a0), 0x27c8: (0x27b8, 0x2a00)}
    if entry_pc not in bounds or pc != entry_pc or any(entry[632:638]) or entry[648]:
        raise ValueError("Unsupported entry control state")
    data = bytearray(before)
    vf_ready = [[0] * 4 for _ in range(32)]
    vi_ready, acc_ready = [0] * 16, [0] * 4
    pending, trace, kicks, packets = [], [], [], []
    now = qready = kick_done = 0
    pready = efu_ready = 0
    working_clip = clip
    branch = None
    last_target = struct.unpack_from('<I', entry, 652)[0]
    backup = None
    ending = False
    maximum = float.fromhex('0x1.fffffep127')

    def signed(value, bits):
        value &= (1 << bits) - 1
        return value - (1 << bits) if value >> (bits - 1) else value

    def operand(bits):
        exponent = (bits >> 23) & 255
        if exponent == 255:
            raise ValueError("Exceptional operand outside researched domain")
        if not exponent:
            bits &= 0x80000000
        return struct.unpack('<f', struct.pack('<I', bits))[0]

    def rounded(value):
        if not math.isfinite(value) or abs(value) > maximum:
            raise ValueError("Exceptional result outside researched domain")
        if abs(value) < 2**-126:
            return 0x80000000 if math.copysign(1, value) < 0 else 0
        bits = struct.unpack('<I', struct.pack('<f', value))[0]
        if abs(operand(bits)) > abs(value):
            bits -= 1
        return bits

    def flags(value):
        out = 2 if math.copysign(1, value) < 0 else 0
        if value == 0:
            return out | 1
        if abs(value) < 2**-126:
            return out | 5
        if abs(value) > maximum:
            return out | 8
        return out

    def lanes(mask):
        return [i for i in range(4) if mask & (8 >> i)]

    def event(delay, kind, key, value):
        pending.append((now + delay, kind, key, value))

    def advance(target):
        nonlocal now, pending, q, p, mac, clip, status
        for at, kind, key, value in sorted(pending, key=lambda e: (e[0], e[1])):
            if at > target:
                continue
            if kind == 'flags':
                mac, current, sticky = value
                status = (status & 0xff0) | current | ((current | sticky) << 6)
            elif kind == 'clip':
                clip = value
            elif kind == 'q':
                q, di = value if isinstance(value, tuple) else (value, 0)
                status = (status & 0xfcf) | di | (di << 6)
            elif kind == 'p':
                p = value
            elif kind == 'vf':
                r, c = key
                if vf_ready[r][c] == at:
                    vf[r][c] = value
            elif kind == 'acc':
                if acc_ready[key] == at:
                    acc[key] = value
            elif kind == 'vi':
                if vi_ready[key] == at:
                    vi[key] = signed(value, 16)
            elif kind == 'store':
                struct.pack_into('<I', data, key, value & 0xffffffff)
        pending = [e for e in pending if e[0] > target]
        now = target

    def quad(index):
        return list(struct.unpack_from('<4I', data, (index & 1023) * 16))

    for _ in range(max_steps):
        if not bounds[entry_pc][0] <= pc <= bounds[entry_pc][1]:
            raise ValueError(f"Unmodeled clipping path at {pc:x}")
        lo, up = struct.unpack_from('<2I', code, pc)
        op, sp = up & 63, (up & 3) | ((up >> 4) & 124)
        mask, fs, ft, fd = (up >> 21) & 15, (up >> 11) & 31, (up >> 16) & 31, (up >> 6) & 31
        lop, lsp = lo >> 25, (lo & 3) | ((lo >> 4) & 124)
        lm, ls, lt, ld = (lo >> 21) & 15, (lo >> 11) & 31, (lo >> 16) & 31, (lo >> 6) & 15
        vs, vt = ls & 15, lt & 15
        imm = signed(lo, 11)
        literal = bool(up & 0x80000000)
        reads, ireads, areads = [], [], []
        upper_nop = op >= 60 and sp in (0x2f, 0x30)
        if not upper_nop:
            if op >= 60 and sp == 31:
                reads += [(fs, c) for c in range(3)] + [(ft, 3)]
            else:
                reads += [(fs, c) for c in lanes(mask)]
                aop = sp if op >= 60 else op
                if aop < 28:
                    if not (op >= 60 and 16 <= sp <= 23):
                        reads.append((ft, aop & 3))
                elif aop >= 40:
                    reads += [(ft, c) for c in lanes(mask)]
                if 8 <= aop <= 15 or aop in (33, 35, 37, 39, 41, 45):
                    areads = lanes(mask)
        if literal or lo == 0x8000033c:
            pass
        elif lop in (0, 4, 8, 0x1a, 0x24, 0x2c, 0x2d, 0x2e, 0x2f):
            ireads.append(vs)
        elif lop == 1:
            ireads.append(vt); reads += [(ls, c) for c in lanes(lm)]
        elif lop in (5, 0x28, 0x29):
            ireads += [vs, vt]
        elif lop == 0x40:
            if lo & 63 in (0x30, 0x31, 0x34, 0x35):
                ireads += [vs, vt]
            elif lo & 63 == 0x32 or lsp in (0x34, 0x3d, 0x3e):
                ireads.append(vs)
            elif lsp == 0x3f:
                ireads += [vs, vt]
            elif lsp == 0x35:
                ireads.append(vt); reads += [(ls, c) for c in lanes(lm)]
            elif lsp in (0x38, 0x3a):
                reads += [(ls, (lo >> 21) & 3), (lt, (lo >> 23) & 3)]
            elif lsp == 0x3c:
                reads.append((ls, (lo >> 21) & 3))
            elif lsp in (0x30, 0x31):
                # MR32 reads the next source lane, including in-place rotates.
                reads += [(ls, (c + 1) % 4 if lsp == 0x31 else c) for c in lanes(lm)]
            elif lsp == 0x6c:
                ireads.append(vs)
            elif lsp == 0x73:
                reads += [(ls, c) for c in range(3)]
        ready = max([now] + [vf_ready[r][c] for r, c in reads] +
                    [vi_ready[r] for r in ireads if r] + [acc_ready[c] for c in areads])
        if not literal and lop == 0x40 and lsp in (0x38, 0x3a, 0x3b):
            ready = max(ready, qready)
        if not literal and lop == 0x40 and lsp in (0x73, 0x7b):
            ready = max(ready, efu_ready, pready if lsp == 0x7b else 0)
        if not literal and lop == 0x40 and lsp == 0x6c:
            # Match the current C++ pair scheduler's pending-PATH1 wait.
            # Hardware's upper-half progress during a second XGKICK is a
            # separate fidelity question; this is a reference-model contract.
            ready = max(ready, kick_done)
        advance(ready)
        trace.append((pc, now))
        writes = []
        if not upper_nop:
            aop = sp if op >= 60 else op
            if op >= 60 and sp == 31:
                w = vf[ft][3]
                limit = (w & 0x7fffffff) if w & 0x7f800000 else 0x7fffff
                bits = sum((signed(vf[fs][c] ^ (neg << 31), 32) > limit) << (2*c+neg)
                           for c in range(3) for neg in (0, 1))
                working_clip = ((working_clip << 6) | bits) & 0xffffff
                event(4, 'clip', 0, working_clip)
            elif op >= 60 and 16 <= sp <= 19:
                scale = (1, 16, 4096, 32768)[sp - 16]
                for c in lanes(mask):
                    value = rounded(signed(vf[fs][c],32))
                    value = rounded(operand(value)/scale)
                    writes.append(('vf',(ft,c),value,4))
            elif op >= 60 and 20 <= sp <= 23:
                scale = (1, 16, 4096, 32768)[sp - 20]
                for c in lanes(mask):
                    bits = vf[fs][c]
                    # FTOI normalizes exponent-255 operands before conversion.
                    # Either sign necessarily saturates at all four scales.
                    # Keep other arithmetic's researched-domain guard intact.
                    if (bits & 0x7f800000) == 0x7f800000:
                        value = -2147483648 if bits & 0x80000000 else 2147483647
                    else:
                        value = max(-2147483648, min(2147483647, int(operand(bits) * scale)))
                    writes.append(('vf', (ft, c), value & 0xffffffff, 4))
            elif op < 60 and (16 <= op <= 23 or op in (29,31,43,47)):
                maximum_op = 16 <= op <= 19 or op in (29,43)
                for c in lanes(mask):
                    a = operand(vf[fs][c])
                    b = operand(immediate if op in (29,31) else vf[ft][c if op in (43,47) else op & 3])
                    value = a if (a > b if maximum_op else a < b) else b
                    writes.append(('vf',(fd,c),rounded(value),4))
            elif aop <= 15 or 24 <= aop <= 28 or aop in (30,32,33,34,35,36,37,38,39,40,41,42,44,45):
                current = sticky = mac_value = 0
                for c in lanes(mask):
                    a = operand(vf[fs][c])
                    b = operand(q if aop in (28,32,33,36,37) else immediate if aop in (30,34,35,38,39)
                                else vf[ft][c if aop >= 40 else aop & 3])
                    if aop < 4 or aop in (32,34,40):
                        exact = value = a + b
                    elif aop < 8 or aop in (36,38,44):
                        exact = value = a - b
                    elif aop < 16 or aop in (33,35,37,39,41,45):
                        product = a * b; base = operand(acc[c])
                        add = aop < 12 or aop in (33,35,41)
                        exact = base + product if add else base - product
                        value = base + operand(rounded(product)) if add else base - operand(rounded(product))
                        sticky |= flags(product)
                    else:
                        exact = value = a * b
                    f = flags(exact); current |= f
                    for bit in range(4):
                        if f & (1 << bit): mac_value |= (8 >> c) << (4 * bit)
                    value = rounded(value)
                    if f & 5: value = 0x80000000 if math.copysign(1, exact) < 0 else 0
                    writes.append(('acc' if op >= 60 else 'vf', c if op >= 60 else (fd, c), value, 1 if op >= 60 else 4))
                if mask: event(4, 'flags', 0, (mac_value, current, sticky))
            else:
                raise ValueError(f"Unmodeled upper {up:08x} at {pc:x}")

        upper_write_count = len(writes)
        new_branch, iw, delay_branch = None, None, False
        def store(index, values, dest):
            for c in lanes(dest): event(1, 'store', (index & 1023)*16 + c*4, values[c])
        def branch_vi(reg):
            return backup[1] if backup and backup[0] == reg else vi[reg]
        if literal:
            immediate = rounded(operand(lo))
        elif lo == 0x8000033c:
            pass
        elif lop == 0:
            values = quad(vi[vs] + imm)
            writes += [('vf', (lt,c), values[c], 4) for c in lanes(lm)]
        elif lop == 1:
            store(vi[vt] + imm, vf[ls], lm)
        elif lop in (4, 5):
            if lop == 4: iw = (vt, quad(vi[vs] + imm)[lanes(lm)[0] if lm else 3], 4)
            else: store(vi[vs]+imm, [vi[vt] & 65535]*4, lm)
        elif lop == 8:
            iw = (vt, vi[vs] + ((lo & 2047) | ((lo >> 10) & 0x7800)), 1); delay_branch = True
        elif lop == 0x11:  # FCSET replaces a same-issue CLIP result.
            working_clip = lo & 0xffffff
            pending = [e for e in pending if not (e[0] == now + 4 and e[1] == 'clip')]
            event(4, 'clip', 0, working_clip)
        elif lop in (0x12, 0x13):
            iw = (1, int(bool(clip & (lo & 0xffffff))) if lop == 0x12 else int((clip | (lo & 0xffffff)) == 0xffffff), 1)
        elif lop == 0x1a:
            iw = (vt, mac & (vi[vs] & 65535), 1)
        elif lop == 0x1c:  # FCGET reads the visible, delayed CLIP flags.
            iw = (vt, clip & 0xfff, 1)
        elif lop in (0x20, 0x21, 0x24, 0x28, 0x29, 0x2c, 0x2d, 0x2e, 0x2f):
            take = (lop in (0x20,0x21,0x24) or (lop == 0x28 and branch_vi(vs) == branch_vi(vt)) or
                    (lop == 0x29 and branch_vi(vs) != branch_vi(vt)) or
                    (lop == 0x2c and branch_vi(vs) < 0) or (lop == 0x2d and branch_vi(vs) > 0) or
                    (lop == 0x2e and branch_vi(vs) <= 0) or (lop == 0x2f and branch_vi(vs) >= 0))
            if take: new_branch = ((branch_vi(vs) & 65535)*8 if lop == 0x24 else pc+8+imm*8) & 0x3fff
            if lop == 0x21: iw = (vt, (pc+16)//8, 1)
        elif lop == 0x40:
            direct = lo & 63
            if direct in (0x30,0x31,0x34,0x35):
                value = (vi[vs]+vi[vt] if direct == 0x30 else vi[vs]-vi[vt] if direct == 0x31 else
                         vi[vs]&vi[vt] if direct == 0x34 else vi[vs]|vi[vt])
                iw = (ld,value,1); delay_branch = True
            elif direct == 0x32:
                iw = (vt,vi[vs]+signed(lo>>6,5),1); delay_branch = True
            elif lsp in (0x30,0x31,0x34):
                values = (vf[ls] if lsp == 0x30 else
                          vf[ls][1:] + vf[ls][:1] if lsp == 0x31 else quad(vi[vs]))
                writes += [('vf',(lt,c),values[c],4) for c in lanes(lm)]
                if lsp == 0x34: iw = (vs,vi[vs]+1,1); delay_branch = True
            elif lsp == 0x35:
                store(vi[vt],vf[ls],lm); iw = (vt,vi[vt]+1,1); delay_branch = True
            elif lsp == 0x38:
                num, den = operand(vf[ls][(lo>>21)&3]),operand(vf[lt][(lo>>23)&3])
                if not den: raise ValueError('Zero projection divisor')
                event(7,'q',0,rounded(num/den)); qready = now+7
            elif lsp == 0x3a:
                num, radicand = operand(vf[ls][(lo>>21)&3]), operand(vf[lt][(lo>>23)&3])
                den = operand(rounded(math.sqrt(abs(radicand))))
                if not den: raise ValueError('Zero reciprocal square-root divisor')
                event(13,'q',0,(rounded(num/den), 0x10 if radicand < 0 else 0)); qready = now+13
            elif lsp in (0x3b, 0x7b):
                pass
            elif lsp == 0x64:
                writes += [('vf',(lt,c),p,4) for c in lanes(lm)]
            elif lsp == 0x73:
                squares = [operand(rounded(operand(vf[ls][c])**2)) for c in range(3)]
                sum_xy = operand(rounded(squares[0]+squares[1]))
                sum_xyz = operand(rounded(sum_xy+squares[2]))
                length = operand(rounded(math.sqrt(sum_xyz)))
                value = rounded(1/length) if length else rounded(length)
                event(24,'p',0,value); pready = now+24; efu_ready = now+23
            elif lsp == 0x3c:
                iw = (vt,vf[ls][(lo>>21)&3],1); delay_branch = True
            elif lsp == 0x3d:
                writes += [('vf',(lt,c),signed(vi[vs],16) & 0xffffffff,4) for c in lanes(lm)]
            elif lsp == 0x3e:
                iw = (vt,quad(vi[vs])[lanes(lm)[0] if lm else 3],4)
            elif lsp == 0x3f:  # ISWR: zero-extended VI word, one-cycle store.
                store(vi[vs], [vi[vt] & 65535]*4, lm)
            elif lsp == 0x68:
                iw = (vt,top,1)
            elif lsp == 0x6c:
                start = vi[vs] & 1023
                cursor = start
                while True:
                    if cursor >= 1024: raise ValueError('Wrapping GIF packet')
                    tag = int.from_bytes(data[cursor*16:cursor*16+8],'little')
                    count, fmt, regs = tag & 0x7fff, (tag>>58)&3, (tag>>60)&15 or 16
                    if fmt == 3: raise ValueError('Reserved GIF format')
                    cursor += 1 + (count*regs if fmt == 0 else (count*regs+1)//2 if fmt == 1 else count)
                    if cursor>1024: raise ValueError('Wrapping GIF packet')
                    if tag & 0x8000: break
                length = cursor-start
                packets.append({'start':start,'data':bytes(data[start*16:cursor*16])})
                kicks.append((now,start,length)); kick_done=max(kick_done,now+2*length-1)
            else: raise ValueError(f'Unmodeled lower {lo:08x} at {pc:x}')
        else: raise ValueError(f'Unmodeled lower {lo:08x} at {pc:x}')
        backup = (iw[0],vi[iw[0]]) if iw and iw[0] and delay_branch else None
        if iw and iw[0]:
            reg,value,latency=iw; vi_ready[reg]=now+latency; event(latency,'vi',reg,value)
        # The lower half sees pre-upper values; duplicate destinations favor upper.
        upper_regs = {key[0] for kind,key,_,_ in writes[:upper_write_count] if kind == 'vf'}
        for index,(kind,key,value,latency) in enumerate(writes):
            if kind == 'vf':
                reg,c=key
                if not reg or (index >= upper_write_count and reg in upper_regs): continue
                vf_ready[reg][c]=now+latency
            else: acc_ready[key]=now+latency
            event(latency,kind,key,value)
        next_pc = branch if branch is not None else pc+8
        branch = new_branch
        if new_branch is not None: last_target=new_branch
        pc = next_pc
        advance(now+1)
        if ending:
            advance(max([now,kick_done]+[e[0] for e in pending]))
            out=bytearray(entry)
            for r in range(32): struct.pack_into('<4I',out,r*16,*vf[r])
            struct.pack_into('<16i',out,512,*vi)
            struct.pack_into('<4I',out,576,*acc)
            struct.pack_into('<8I',out,592,q,p,immediate,random,pc,mac,clip,status)
            struct.pack_into('<Q',out,624,struct.unpack_from('<Q',entry,624)[0]+now)
            out[632:634]=bytes(2); out[648]=int(branch is not None)
            struct.pack_into('<2I',out,652,last_target,0)
            return {'state':bytes(out),'data':bytes(data),'cycles':now,'trace':trace,'kicks':kicks,'packets':packets}
        ending = bool(up & 0x40000000)
    raise ValueError('Research trace step cap reached')


def model_b38_boundary(entry, before, code, *, max_steps=20000):
    """B38 geometry/texture-coordinate oracle, not a runtime implementation.

    Models perspective division, reciprocal vector length, the conditional
    second-hemisphere coordinate calculation, FMAC flags and pipeline timing.
    Captured and synthetic inputs validate this bounded domain, not every PS2
    numeric edge case. Requires idle entry pipelines just like the 1618 model.
    """
    return model_clip_boundary(entry, before, code, max_steps=max_steps, entry_pc=0xb38)


def model_400_boundary(entry, before, code, *, max_steps=20000):
    """400 position/lighting/texture oracle with exact boundary state and timing.

    Read-only research, restricted to verified retail code and idle pipelines.
    """
    return model_clip_boundary(entry, before, code, max_steps=max_steps, entry_pc=0x400)


def model_840_boundary(entry, before, code, *, max_steps=20000):
    """840 reflection/lighting boundary oracle; read-only research only.

    Restricts control flow to the verified 840 loop and its 830 exit pair.
    Full state, output and timing must be compared to interpreter samples.
    """
    return model_clip_boundary(entry, before, code, max_steps=max_steps, entry_pc=0x840)


def model_db0_boundary(entry, before, code, *, max_steps=20000):
    """DB0 dual-basis projection oracle; exact idle boundary research only."""
    return model_clip_boundary(entry, before, code, max_steps=max_steps, entry_pc=0xdb0)


def model_24c0_boundary(entry, before, code, *, max_steps=20000):
    """Retail 24C0 boundary oracle; read-only research, not a runtime path.

    Covers the 24B0 exit pair and bounded 24C0..27A0 body. Requires verified
    retail code and idle entry; compare complete state/data/cycles to samples.
    """
    return model_clip_boundary(entry, before, code, max_steps=max_steps, entry_pc=0x24c0)


def model_27c8_boundary(entry, before, code, *, max_steps=20000):
    """Retail 27C8 boundary oracle; no runtime replacement or effect identity implied.

    Replays only the verified routine and its E-bit exit/delay slot. Requires
    idle pipelines; callers compare the complete state, data and cycle count.
    """
    return model_clip_boundary(entry, before, code, max_steps=max_steps, entry_pc=0x27c8)


def model_24c0_dataflow(entry, before):
    """Direct four-corner geometry candidate; compare to the 24C0 oracle."""
    if len(entry) != 664 or len(before) != 16384:
        raise ValueError('Incomplete VU boundary')
    vf = [list(struct.unpack_from('<4I', entry, r*16)) for r in range(32)]
    vi = list(struct.unpack_from('<16i', entry, 512))
    q,p,immediate,random,pc,mac,clip,status = struct.unpack_from('<8I', entry,592)
    top = struct.unpack_from('<I',entry,640)[0]
    if pc != 0x24c0 or any(entry[632:638]) or entry[648] or top >= 1024:
        raise ValueError('Unsupported entry')
    data = bytearray(before)
    acc = list(struct.unpack_from('<4I',entry,576))
    sticky = current = mac = 0
    paths, counts = [], []
    maximum = float.fromhex('0x1.fffffep127')

    def signed(word, bits=32):
        word &= (1<<bits)-1
        return word-(1<<bits) if word>>(bits-1) else word

    def number(word):
        e=(word>>23)&255
        if e==255: raise ValueError('Exceptional operand')
        if not e: word &= 0x80000000
        return struct.unpack('<f',struct.pack('<I',word))[0]

    def bits(value):
        if not math.isfinite(value) or abs(value)>maximum: raise ValueError('Exceptional result')
        if abs(value)<2**-126: return 0x80000000 if math.copysign(1,value)<0 else 0
        word=struct.unpack('<I',struct.pack('<f',value))[0]
        if abs(number(word))>abs(value): word-=1
        return word

    def lane_flags(value):
        f=2 if math.copysign(1,value)<0 else 0
        return f | (1 if value==0 else 5 if abs(value)<2**-126 else 8 if abs(value)>maximum else 0)

    def arithmetic(left,right,base=None,mask=15):
        nonlocal sticky,current,mac
        out=list(left); current=mac=0
        for c in range(4):
            if not mask&(8>>c): continue
            a,b=number(left[c]),number(right[c])
            if base is None:
                exact=a*b; value=exact
            else:
                product=a*b; sticky |= lane_flags(product)
                exact=number(base[c])+product
                value=number(base[c])+number(bits(product))
            f=lane_flags(exact); current |= f; sticky |= f
            for bit in range(4):
                if f&(1<<bit): mac |= (8>>c)<<(4*bit)
            out[c]=bits(math.copysign(0,exact)) if f&5 else bits(value)
        return out

    def move(value):
        nonlocal sticky,current,mac
        out=[]; current=mac=0
        for c,word in enumerate(value):
            result=number(word)+number(vf[0][0]); f=lane_flags(result)
            current|=f; sticky|=f
            for bit in range(4):
                if f&(1<<bit): mac|=(8>>c)<<(4*bit)
            out.append(bits(result))
        return out

    def transform(position,first):
        nonlocal acc
        acc=arithmetic(vf[first+3],[vf[0][3]]*4)
        for axis in (2,1): acc=arithmetic(vf[first+axis],[position[axis]]*4,acc)
        return arithmetic(vf[first],[position[0]]*4,acc)

    def push_clip(value):
        nonlocal clip
        w=value[3]; limit=(w&0x7fffffff) if w&0x7f800000 else 0x7fffff
        flags=sum((signed(value[c]^(neg<<31))>limit)<<(2*c+neg) for c in range(3) for neg in (0,1))
        clip=((clip<<6)|flags)&0xffffff

    def quad(index):
        if not 0<=index<1024: raise ValueError('Wrapping data')
        return list(struct.unpack_from('<4I',data,index*16))

    def store(index,value):
        if not 0<=index<1024: raise ValueError('Wrapping output')
        struct.pack_into('<4I',data,index*16,*(x&0xffffffff for x in value))

    def word(index,lane,value):
        row=quad(index); row[lane]=value&65535; store(index,row)


    def put(reg, value, mask):
        for c in range(4):
            if mask & (8 >> c): vf[reg][c] = value[c]

    def add(left, right, mask=15, subtract=False):
        nonlocal sticky,current,mac
        out=list(left);current=mac=0
        for c in range(4):
            if not mask & (8>>c):continue
            exact=number(left[c])-number(right[c]) if subtract else number(left[c])+number(right[c])
            f=lane_flags(exact);current|=f;sticky|=f
            for bit in range(4):
                if f & (1<<bit):mac|=(8>>c)<<(4*bit)
            out[c]=bits(math.copysign(0,exact)) if f&5 else bits(exact)
        return out

    def ftoi(value,scale):
        out=[]
        for raw in value:
            if (raw&0x7f800000)==0x7f800000:
                n=-2147483648 if raw&0x80000000 else 2147483647
            else:n=max(-2147483648,min(2147483647,int(number(raw)*scale)))
            out.append(n&0xffffffff)
        return out

    def push_against(value,limit_raw):
        nonlocal clip
        limit=(limit_raw&0x7fffffff) if limit_raw&0x7f800000 else 0x7fffff
        f=sum((signed(value[c]^(neg<<31))>limit)<<(2*c+neg) for c in range(3) for neg in (0,1))
        clip=((clip<<6)|f)&0xffffff

    def divide(value):
        den=number(value)
        if not den:raise ValueError('Zero projection divisor')
        return bits(number(vf[0][3])/den)

    if vf[0]!=[0,0,0,0x3f800000]:raise ValueError('VF0 invariant')
    prefix=quad(top)[1]&65535
    if prefix>=224:raise ValueError('Unsupported prefix')
    cursor=top+1+prefix
    header=quad(cursor);count=header[0]&32767
    if not 0<count<=64 or cursor+3*count+3>=top+224 or top+224+prefix+1+12*count>1024:
        raise ValueError('Overlapping input/output or unsupported count')
    vi[4]=vi[5]=top+224;vi[6]=top+1;vi[10]=32767
    for _ in range(prefix):
        vf[23]=quad(vi[6]);vi[6]+=1;store(vi[4],vf[23]);vi[4]+=1
    vf[29]=quad(vi[6]+1);vf[24]=quad(vi[6]+3);vf[18]=quad(vi[6]);vi[6]+=1
    put(28,quad(vi[6]+1),13)
    vf[25]=quad(119);vf[10]=quad(120)
    vf[30]=transform(vf[29],1)
    vi[8]=count;vi[11]=-32768;vi[9]=signed(count*4|32768,16)
    put(27,add(vf[0],[vf[28][1]]*4,8,True),8)
    put(27,add(vf[0],[vf[28][0]]*4,4),4)
    put(28,arithmetic(vf[28],[vf[10][2]]*4,mask=1),1)
    q=divide(vf[30][3]);vf[24]=ftoi(vf[24],1)
    store(vi[4],vf[18]);vi[4]+=1;word(vi[4]-1,0,vi[9])
    kick=23+5*prefix;paths=[]
    for _ in range(count):
        put(12,add(vf[30],[vf[25][2]]*4,1,True),1);vf[19]=list(vf[0])
        put(28,arithmetic(vf[28],[vf[10][0]]*4,mask=4),4);vf[18]=list(vf[0])
        put(27,arithmetic(vf[27],[vf[10][0]]*4,mask=4),4);vf[28][2]=vf[0][2]
        put(28,arithmetic(vf[28],[q]*4,mask=1),1);vf[17]=list(vf[0])
        vf[31]=arithmetic(vf[30],[q]*4);vi[4]+=12
        put(19,add(vf[0],[q]*4,2),2);store(vi[4]-11,vf[24])
        put(18,add(vf[0],[q]*4,6),6);store(vi[4]-8,vf[24])
        put(28,arithmetic(vf[28],[vf[28][3]]*4,mask=12),12);store(vi[4]-5,vf[24])
        put(27,arithmetic(vf[27],[vf[28][3]]*4,mask=12),12);store(vi[4]-2,vf[24])
        put(17,add(vf[0],[q]*4,10),10);store(vi[4]-3,vf[19])
        put(16,add(vf[0],[q]*4,14),14);vi[6]+=3
        put(23,add(vf[31],vf[28],14,True),14);vf[29]=quad(vi[6])
        put(22,add(vf[31],vf[28],14),14);vf[12][2]=vf[12][3]
        put(21,add(vf[31],vf[28],14,True),14);store(vi[4]-6,vf[18])
        put(20,add(vf[31],vf[28],14),14)
        put(23,add(vf[23],vf[27],12,True),12);store(vi[4]-9,vf[17])
        put(22,add(vf[22],vf[27],12,True),12)
        put(21,add(vf[21],vf[27],12),12);store(vi[4]-12,vf[16])
        put(20,add(vf[20],vf[27],12),12)
        put(15,add(vf[23],vf[25],12,True),12)
        put(14,add(vf[22],vf[25],12,True),12);vf[24]=quad(vi[6]+2)
        put(13,add(vf[21],vf[25],12,True),12)
        put(12,add(vf[20],vf[25],12,True),12)
        vf[23]=ftoi(vf[23],16);put(28,quad(vi[6]+1),13)
        vf[22]=ftoi(vf[22],16)
        vf[21]=ftoi(vf[21],16);vf[11]=quad(121)
        vf[20]=ftoi(vf[20],16)
        for reg,offset in [(15,1),(14,4),(13,7),(12,10)]:
            push_against(vf[reg],vf[25][3]);store(vi[4]-offset,vf[reg+8])
        vf[30]=transform(vf[29],1);vi[8]-=1
        word(vi[4]-7,3,vi[11]);word(vi[4]-10,3,vi[11])
        for reg in [15,14,13,12]:put(reg,add(vf[reg],vf[11],12,True),12)
        put(15,arithmetic(vf[15],[vf[10][1]]*4,mask=4),4)
        near=bool(clip&0x3cf3ef)
        for reg in [14,13,12]:put(reg,arithmetic(vf[reg],[vf[10][1]]*4,mask=4),4)
        q=divide(vf[30][3])
        for reg in [15,14,13,12]:push_against(vf[reg],vf[10][3])
        vi[7]=vi[11]
        put(27,add(vf[0],[vf[28][1]]*4,8,True),8)
        put(27,add(vf[0],[vf[28][0]]*4,4),4)
        put(28,arithmetic(vf[28],[vf[10][2]]*4,mask=1),1)
        vf[24]=ftoi(vf[24],1)
        masks=[0xdf7df7,0xefbefb,0xf7df7d,0xfbefbe]
        vi[1]=int((clip|masks[0])==0xffffff)
        tested=0;kept=0
        if not near:
            for j in range(4):
                take=bool(vi[1]);tested+=1
                if j<3:vi[1]=int((clip|masks[j+1])==0xffffff)
                if take:break
            else:vi[7]=0;kept=1
        word(vi[4]-1,3,vi[7]);word(vi[4]-4,3,vi[7])
        kick+=54+2*tested+kept;paths.append((tested,kept))
    start=top+224;size=prefix+1+12*count
    # Require the actual GIF chain to match this bounded layout.
    at=start
    while True:
        tag=int.from_bytes(data[at*16:at*16+8],'little')
        n,fmt,regs=tag&32767,(tag>>58)&3,(tag>>60)&15 or 16
        if fmt==3:raise ValueError('Reserved packet format')
        at+=1+(n*regs if fmt==0 else (n*regs+1)//2 if fmt==1 else n)
        if at>start+size:raise ValueError('Packet extent mismatch')
        if tag&32768:break
    if at!=start+size:raise ValueError('Packet extent mismatch')
    cycles=max(kick+5,kick+2*size-1)
    out=bytearray(entry)
    for r in range(32):struct.pack_into('<4I',out,r*16,*vf[r])
    struct.pack_into('<16i',out,512,*vi);struct.pack_into('<4I',out,576,*acc)
    status=(status&0xfc0)|current|(sticky<<6)
    struct.pack_into('<8I',out,592,q,p,immediate,random,0x24c0,mac,clip,status)
    struct.pack_into('<Q',out,624,struct.unpack_from('<Q',entry,624)[0]+cycles)
    out[632:634]=bytes(2);out[648]=0;struct.pack_into('<2I',out,652,0x24b0,0)
    return {'state':bytes(out),'data':bytes(data),'cycles':cycles,'kicks':[(kick,start,size)],'paths':paths}


def model_polygon_boundary_dataflow(entry, before):
    """Direct 1C68 helper replay, including scratch aliasing and exit state.

    Research only. Entry pipelines must be idle and VI15 must return through
    1608's end marker. No instruction dispatch or pipeline event simulation.
    Preserves the original load/store order even when polygon buffers overlap.
    """
    if len(entry) != 664 or len(before) != 16384:
        raise ValueError('Incomplete polygon boundary')
    vf = [list(struct.unpack_from('<4I', entry, r*16)) for r in range(32)]
    vi = list(struct.unpack_from('<16i', entry, 512))
    acc = list(struct.unpack_from('<4I', entry, 576))
    q,p,immediate,random,pc,mac,clip,status = struct.unpack_from('<8I', entry, 592)
    if (pc != 0x1c68 or any(entry[632:638]) or entry[648] or vi[15] != 0x1608//8 or
            not 1 <= vi[11] <= 8 or vi[9] not in (0x41,0x82,0x104,0x208,0x820) or
            vf[0] != [0,0,0,0x3f800000]):
        raise ValueError('Unresearched polygon boundary contract')
    data = bytearray(before)
    maximum = float.fromhex('0x1.fffffep127')
    sticky = current = mac = 0
    divided = False
    paths = []

    def number(word):
        exponent = (word >> 23) & 255
        if exponent == 255: raise ValueError('Exceptional polygon operand')
        if not exponent: word &= 0x80000000
        return struct.unpack('<f',struct.pack('<I',word))[0]

    def bits(value):
        if not math.isfinite(value) or abs(value)>maximum: raise ValueError('Exceptional polygon result')
        if abs(value)<2**-126: return 0x80000000 if math.copysign(1,value)<0 else 0
        word=struct.unpack('<I',struct.pack('<f',value))[0]
        if abs(number(word))>abs(value): word-=1
        return word

    def flags(value):
        return (2 if math.copysign(1,value)<0 else 0) | (1 if value==0 else 5 if abs(value)<2**-126 else 8 if abs(value)>maximum else 0)

    def arithmetic(left,right,kind='mul',base=None,mask=15):
        nonlocal sticky,current,mac
        result=list(left);current=mac=0
        for c in range(4):
            if not mask & (8>>c): continue
            a,b=number(left[c]),number(right[c])
            if kind=='add': exact=value=a+b
            elif kind=='sub': exact=value=a-b
            elif base is not None:
                product=a*b;sticky |= flags(product)
                exact=number(base[c])+product
                value=number(base[c])+number(bits(product))
            else: exact=value=a*b
            f=flags(exact);current |= f;sticky |= f
            for bit in range(4):
                if f & (1<<bit): mac |= (8>>c)<<(4*bit)
            result[c]=bits(math.copysign(0,exact)) if f&5 else bits(value)
        return result

    def move(value): return arithmetic(value,[vf[0][0]]*4,'add')

    def transform(position):
        nonlocal acc
        acc=arithmetic(vf[12],[vf[0][3]]*4)
        for axis in (2,1): acc=arithmetic(vf[9+axis],[position[axis]]*4,base=acc)
        return arithmetic(vf[9],[position[0]]*4,base=acc)

    def push_clip(value):
        nonlocal clip
        w=value[3];limit=(w&0x7fffffff) if w&0x7f800000 else 0x7fffff
        f=0
        for c in range(3):
            for negative in range(2):
                word=value[c]^(negative<<31)
                signed=word-(1<<32) if word&0x80000000 else word
                f |= (signed>limit)<<(2*c+negative)
        clip=((clip<<6)|f)&0xffffff

    def quad(index):
        if not 0<=index<1024: raise ValueError('Wrapping polygon read')
        return list(struct.unpack_from('<4I',data,index*16))

    def store(index,value):
        if not 0<=index<1024: raise ValueError('Wrapping polygon store')
        struct.pack_into('<4I',data,index*16,*value)

    def write_word(index,lane,value):
        row=quad(index);row[lane]=value&65535;store(index,row)

    def read_vi(index,lane):
        value=quad(index)[lane]&65535
        return value-65536 if value&32768 else value

    for j in range(3): vf[14+j]=quad(vi[14]+j)
    vi[14]=63
    vf[19]=quad(vi[13]+2);vf[30]=quad(vi[13]+5)
    vf[17]=transform(vf[16]);vi[10]=0
    write_word(120,0,vi[12]);write_word(120,1,vi[13])
    vf[13]=transform(vf[19]);push_clip(vf[17])
    cycles=14
    for _ in range(vi[11]):
        push_clip(vf[13])
        vf[19]=quad(vi[13]+2);vf[18]=quad(vi[13]+1);vf[17]=quad(vi[13])
        vf[13]=transform(vf[30]);vi[11]-=1
        vi[1]=clip&0xfff;vi[8]=vi[1]&vi[9];vi[1]=vi[10]-8;vi[13]+=3
        append_current=False
        if vi[8]==vi[9]: path='outside';cost=12
        elif vi[8]==0:
            append_current=vi[1]<0
            path='inside' if append_current else 'inside-cap';cost=20 if append_current else 16
        else:
            vf[31]=arithmetic(vf[19],vf[16],'sub')
            vf[26]=arithmetic(vf[21],vf[16],'sub')
            vf[28]=arithmetic(vf[18],vf[15],'sub')
            vf[27]=arithmetic(vf[17],vf[14],'sub')
            vf[23]=arithmetic(vf[31],vf[20]);vf[29]=arithmetic(vf[26],vf[20])
            for axis in (1,2):
                vf[23]=arithmetic(vf[23],[vf[23][axis]]*4,'add',mask=8)
                vf[29]=arithmetic(vf[29],[vf[29][axis]]*4,'add',mask=8)
            denominator=number(vf[23][0])
            if not denominator: raise ValueError('Zero polygon divisor')
            q=bits(number(vf[29][0])/denominator);divided=True
            if vi[1]>=0: path='cross-cap';cost=34
            else:
                acc=move(vf[14]);vf[27]=arithmetic(vf[27],[q]*4,base=acc);vi[1]=vi[10]-7
                acc=move(vf[15]);vi[8]&=vi[14];vf[28]=arithmetic(vf[28],[q]*4,base=acc)
                acc=move(vf[16]);vi[10]+=1;vf[31]=arithmetic(vf[31],[q]*4,base=acc)
                for register in (27,28,31): store(vi[12],vf[register]);vi[12]+=1
                append_current=vi[8]<=0 and vi[1]<0
                path='cross-out' if vi[8]>0 else 'cross-in' if append_current else 'cross-in-cap'
                cost=49 if vi[8]>0 else 55 if append_current else 51
        if append_current:
            for register in (17,18,19): store(vi[12],vf[register]);vi[12]+=1
            vi[10]+=1
        vf[16]=move(vf[19]);vf[30]=quad(vi[13]+5)
        vf[15]=move(vf[18]);vf[14]=move(vf[17])
        paths.append(path);cycles+=cost
    vi[14]=vi[12]-3;vi[12]=read_vi(120,1);vi[13]=read_vi(120,0);vi[11]=vi[10]
    vi[15]=read_vi(121,3);cycles+=9
    out=bytearray(entry)
    for register in range(32):struct.pack_into('<4I',out,register*16,*vf[register])
    struct.pack_into('<16i',out,512,*vi);struct.pack_into('<4I',out,576,*acc)
    status=(status & (0xfc0 if divided else 0xff0))|current|(sticky<<6)
    struct.pack_into('<8I',out,592,q,p,immediate,random,0x1618,mac,clip,status)
    struct.pack_into('<Q',out,624,struct.unpack_from('<Q',entry,624)[0]+cycles)
    out[632:634]=bytes(2);out[648]=0;struct.pack_into('<2I',out,652,0x1608,0)
    return {'state':bytes(out),'data':bytes(data),'cycles':cycles,'paths':paths}


def model_polygon_plane(vertices, matrix, plane_mask, normal, plane_point):
    """Pure 1C68 polygon-plane output research; no instruction dispatch.

    Vertices are raw float-word (colour, texture, position) triples. Matrix is
    VF9..12, normal/point are the object-space clipping plane's VF20/VF21.
    Reproduces the eight-vertex output cap and separate toward-zero rounding.
    This models polygon attributes only, not VU exit state, flags, packet
    submission or timing. Requires non-overlapping source/destination buffers:
    an eight-vertex input at scratch24 overlaps output45 in the original
    routine and cannot be replaced with a snapshot of its initial vertices.
    It must not replace runtime execution by itself.
    """
    if not 1 <= len(vertices) <= 8 or plane_mask not in (0x41, 0x82, 0x104, 0x208, 0x820):
        raise ValueError('Unresearched polygon count or clipping plane')
    vectors = list(matrix) + [normal, plane_point] + [v for row in vertices for v in row]
    if len(matrix) != 4 or any(len(row) != 3 for row in vertices) or any(len(v) != 4 for v in vectors):
        raise ValueError('Incomplete polygon attributes or matrix')
    maximum = float.fromhex('0x1.fffffep127')

    def number(word):
        exponent = (word >> 23) & 255
        if exponent == 255:
            raise ValueError('Exceptional polygon operand')
        if not exponent:
            word &= 0x80000000
        return struct.unpack('<f', struct.pack('<I', word))[0]

    def bits(value):
        if not math.isfinite(value) or abs(value) > maximum:
            raise ValueError('Exceptional polygon result')
        if abs(value) < 2**-126:
            return 0x80000000 if math.copysign(1, value) < 0 else 0
        word = struct.unpack('<I', struct.pack('<f', value))[0]
        if abs(number(word)) > abs(value):
            word -= 1
        return word

    def difference(a, b):
        return [bits(number(x) - number(y)) for x, y in zip(a, b)]

    def transform(position):
        result = [bits(number(x) * 1.0) for x in matrix[3]]
        for axis in (2, 1, 0):
            result = [bits(number(result[c]) + number(bits(number(matrix[axis][c]) * number(position[axis]))))
                      for c in range(4)]
        return result

    def outside(position):
        projected = transform(position)
        w = projected[3]
        limit = w & 0x7fffffff if w & 0x7f800000 else 0x7fffff
        flag = plane_mask & 63
        bit = flag.bit_length() - 1
        value = projected[bit // 2] ^ ((bit & 1) << 31)
        signed_value = value - (1 << 32) if value & 0x80000000 else value
        return signed_value > limit

    def dot_plane(delta):
        product = [bits(number(delta[c]) * number(normal[c])) for c in range(3)]
        return bits(number(bits(number(product[0]) + number(product[1]))) + number(product[2]))

    output = []
    previous = vertices[-1]
    previous_outside = outside(previous[2])
    for current in vertices:
        current_outside = outside(current[2])
        if not (previous_outside and current_outside):
            if previous_outside != current_outside:
                deltas = [difference(a, b) for a, b in zip(current, previous)]
                denominator = number(dot_plane(deltas[2]))
                if denominator == 0:
                    raise ValueError('Zero polygon intersection divisor')
                amount = bits(number(dot_plane(difference(plane_point, previous[2]))) / denominator)
                if len(output) < 8:
                    intersection = []
                    for base, delta in zip(previous, deltas):
                        # ADDA first normalizes base + VF0.x, including -0.
                        intersection.append([bits(number(bits(number(base[c]) + 0.0)) +
                                                  number(bits(number(delta[c]) * number(amount))))
                                             for c in range(4)])
                    output.append(intersection)
            if not current_outside and len(output) < 8:
                output.append([list(attribute) for attribute in current])
        # The original helper advances all three attributes with ADDx VF0.x.
        previous = [[bits(number(value) + 0.0) for value in attribute] for attribute in current]
        previous_outside = current_outside
    return output


def model_clip_dataflow(entry, before, *, split_polygons=False):
    """Direct geometry calculation for comparison with model_clip_boundary.

    No instruction dispatch or pipeline simulation. Paths record each vertex's
    branch category for the dependency/transfer timing calculation. Rejects
    actual polygon splitting unless explicitly requested. The experimental
    splitting path returns owned packet bytes and derived guest timing against
    the current reference scheduler (not a claim of hardware-cycle fidelity).
    Rejects exceptional arithmetic; writes no files/runtime state.
    """
    if len(entry) != 664 or len(before) != 16384:
        raise ValueError('Incomplete VU boundary')
    vf = [list(struct.unpack_from('<4I', entry, r*16)) for r in range(32)]
    vi = list(struct.unpack_from('<16i', entry, 512))
    q,p,immediate,random,pc,mac,clip,status = struct.unpack_from('<8I', entry,592)
    top = struct.unpack_from('<I',entry,640)[0]
    if pc != 0x1618 or any(entry[632:638]) or entry[648] or top >= 1024:
        raise ValueError('Unsupported entry')
    data = bytearray(before)
    acc = list(struct.unpack_from('<4I',entry,576))
    sticky = current = mac = 0
    paths, counts = [], []
    packets, polygon_passes, split_events = [], [], []
    split_used = False
    maximum = float.fromhex('0x1.fffffep127')

    def signed(word, bits=32):
        word &= (1<<bits)-1
        return word-(1<<bits) if word>>(bits-1) else word

    def number(word, saturate=False):
        e=(word>>23)&255
        if e==255:
            if not saturate: raise ValueError('Exceptional operand')
            word=(word&0x80000000)|0x7f7fffff
        if not e: word &= 0x80000000
        return struct.unpack('<f',struct.pack('<I',word))[0]

    def bits(value):
        if not math.isfinite(value) or abs(value)>maximum: raise ValueError('Exceptional result')
        if abs(value)<2**-126: return 0x80000000 if math.copysign(1,value)<0 else 0
        word=struct.unpack('<I',struct.pack('<f',value))[0]
        if abs(number(word))>abs(value): word-=1
        return word

    def lane_flags(value):
        f=2 if math.copysign(1,value)<0 else 0
        return f | (1 if value==0 else 5 if abs(value)<2**-126 else 8 if abs(value)>maximum else 0)

    def arithmetic(left,right,base=None,mask=15):
        nonlocal sticky,current,mac
        out=list(left); current=mac=0
        for c in range(4):
            if not mask&(8>>c): continue
            a,b=number(left[c]),number(right[c])
            if base is None:
                exact=a*b; value=exact
            else:
                product=a*b; sticky |= lane_flags(product)
                exact=number(base[c])+product
                value=number(base[c])+number(bits(product))
            f=lane_flags(exact); current |= f; sticky |= f
            for bit in range(4):
                if f&(1<<bit): mac |= (8>>c)<<(4*bit)
            out[c]=bits(math.copysign(0,exact)) if f&5 else bits(value)
        return out

    def move(value, normalize_lookahead=False):
        nonlocal sticky,current,mac
        out=[]; current=mac=0
        for c,word in enumerate(value):
            # The lookahead ADD-to-zero can read a packed all-ones lane.
            # Normalize it exactly as the runtime before arithmetic; this
            # does not widen transform/product overflow handling. VU manual p26
            # has a wider finite range; runtime clamping is a separate fidelity
            # limitation, not a hardware-derived interpretation of exponent255.
            result=number(word,saturate=normalize_lookahead)+number(vf[0][0]); f=lane_flags(result)
            current|=f; sticky|=f
            for bit in range(4):
                if f&(1<<bit): mac|=(8>>c)<<(4*bit)
            out.append(bits(result))
        return out

    def transform(position,first):
        nonlocal acc
        acc=arithmetic(vf[first+3],[vf[0][3]]*4)
        for axis in (2,1): acc=arithmetic(vf[first+axis],[position[axis]]*4,acc)
        return arithmetic(vf[first],[position[0]]*4,acc)

    def push_clip(value):
        nonlocal clip
        w=value[3]; limit=(w&0x7fffffff) if w&0x7f800000 else 0x7fffff
        flags=sum((signed(value[c]^(neg<<31))>limit)<<(2*c+neg) for c in range(3) for neg in (0,1))
        clip=((clip<<6)|flags)&0xffffff

    def quad(index):
        if not 0<=index<1024: raise ValueError('Wrapping data')
        return list(struct.unpack_from('<4I',data,index*16))

    def store(index,value):
        if not 0<=index<1024: raise ValueError('Wrapping output')
        struct.pack_into('<4I',data,index*16,*(x&0xffffffff for x in value))

    def word(index,lane,value):
        row=quad(index); row[lane]=value&65535; store(index,row)

    def read_vi(index,lane): return signed(quad(index)[lane],16)

    def emit_packet(start):
        cursor=start
        while True:
            tag=int.from_bytes(data[cursor*16:cursor*16+8],'little')
            count,fmt,regs=tag&32767,(tag>>58)&3,(tag>>60)&15 or 16
            if fmt==3: raise ValueError('Reserved split packet format')
            cursor+=1+(count*regs if fmt==0 else (count*regs+1)//2 if fmt==1 else count)
            if not start<cursor<=1024: raise ValueError('Wrapping split packet')
            if tag&32768: break
        packets.append({'start':start,'data':bytes(data[start*16:cursor*16])})

    def polygon_pass():
        nonlocal vf,vi,acc,q,mac,clip,current,sticky,status,data
        # Reuse the independent direct helper, not the instruction oracle.
        # Its idle return/drain timing is intentionally not used for a nested
        # invocation; surrounding pipeline timing remains separate research.
        nested=bytearray(entry)
        for register in range(32):struct.pack_into('<4I',nested,register*16,*vf[register])
        saved_return=vi[15];vi[15]=0x1608//8
        struct.pack_into('<16i',nested,512,*vi);vi[15]=saved_return
        struct.pack_into('<4I',nested,576,*acc)
        struct.pack_into('<8I',nested,592,q,p,immediate,random,0x1c68,mac,clip,
                         (status&0xfc0)|current|(sticky<<6))
        nested[632:638]=bytes(6);nested[648]=0
        result=model_polygon_boundary_dataflow(bytes(nested),bytes(data))
        raw=result['state'];data=bytearray(result['data'])
        vf=[list(struct.unpack_from('<4I',raw,r*16)) for r in range(32)]
        vi=list(struct.unpack_from('<16i',raw,512));acc=list(struct.unpack_from('<4I',raw,576))
        q=struct.unpack_from('<I',raw,592)[0]
        mac,clip,status=struct.unpack_from('<3I',raw,612)
        current=status&15;sticky=status>>6
        polygon_passes.append({'plane':vi[9],'paths':result['paths']})
        return result['cycles']-3 # Nested return has no standalone E-bit drain.

    def split_polygon(bad_near, positions):
        nonlocal split_used,q,acc
        split_used=True;vi[13]=24
        events=[]
        if bad_near:
            vi[9]=0x820;vf[20]=quad(12);vf[21]=quad(13)
            elapsed=41+polygon_pass();vi[4]=15
        else:
            elapsed=50
            vi[4]=0
            for mask in (0x8208,0x4104,0x2082,0x1041):
                vi[1]=int(bool(clip&mask));vi[4]=(vi[4]<<1)|vi[1]
        vi[2]=22;vi[3]=4;vi[9]=0x41
        for _ in range(4):
            vi[10]=vi[11]-3;vi[1]=vi[9]&vi[4];vi[3]-=1
            if vi[10]<0:
                elapsed+=4;break
            elapsed+=9
            vf[20]=quad(vi[2])
            if vi[1]:
                vf[21]=quad(vi[2]+1);elapsed+=2+polygon_pass()
            vi[9]*=2;vi[2]-=2
        vi[12]=vi[13];vi[10]=vi[11]-3;vi[13]=vi[7]+1
        elapsed+=6
        if vi[10]>=0:
            elapsed+=18+11*(vi[11]-1)
            vf[29]=quad(vi[12]+2);vi[10]=vi[11]-1
            vf[26]=transform(vf[29],1)
            vf[29]=quad(vi[12]+5)
            if not number(vf[26][3]): raise ValueError('Zero split projection divisor')
            q=bits(number(vf[0][3])/number(vf[26][3]))
            vf[21]=transform(vf[29],1);vf[30]=quad(vi[12]+1);vf[29]=quad(vi[12]+8)
            for _ in range(vi[11]-1):
                vf[28]=arithmetic(vf[26],[q]*4);vf[31]=quad(vi[12])
                vf[27][:3]=arithmetic(vf[30],[q]*4,mask=14)[:3]
                if not number(vf[21][3]): raise ValueError('Zero split projection divisor')
                q=bits(number(vf[0][3])/number(vf[21][3]));vf[26]=list(vf[21])
                vf[21]=transform(vf[29],1);vi[12]+=3;vi[10]-=1
                vf[31]=[max(-2147483648,min(2147483647,int(number(x))))&0xffffffff for x in vf[31]]
                vf[28]=[max(-2147483648,min(2147483647,int(number(x)*16)))&0xffffffff for x in vf[28]]
                vf[30]=quad(vi[12]+1);vf[29]=quad(vi[12]+8)
                for register in (27,31,28):store(vi[13],vf[register]);vi[13]+=1
            vf[28]=arithmetic(vf[26],[q]*4);vf[31]=quad(vi[12])
            vf[27][:3]=arithmetic(vf[30],[q]*4,mask=14)[:3]
            vf[31]=[max(-2147483648,min(2147483647,int(number(x))))&0xffffffff for x in vf[31]]
            vf[28]=[max(-2147483648,min(2147483647,int(number(x)*16)))&0xffffffff for x in vf[28]]
            for register in (27,31,28):store(vi[13],vf[register]);vi[13]+=1
            emitted=True
        else:
            emitted=False
            # Restore/return plus two cycles waiting for VF21 at 1AB8 CLIP.
            elapsed+=13
        # Restore caller state from its original scratch-save words.
        vi[10]=read_vi(119,2);vi[8]=read_vi(119,0);vi[9]=read_vi(119,1)
        vi[13]=read_vi(119,3);vi[4]=read_vi(120,2);vi[3]=read_vi(120,3);vi[2]=read_vi(121,2)
        vf[20]=transform(positions[1],5);vf[21]=transform(positions[2],5)
        if not number(vf[24][3]):raise ValueError('Zero restored projection divisor')
        q=bits(number(vf[0][3])/number(vf[24][3]))
        push_clip(vf[20]);push_clip(vf[21])
        if emitted:
            vi[12]=top;vi[1]=read_vi(top,1);vi[11]=signed(vi[11]|vi[10],16)
            word(vi[7],0,vi[11]);word(vi[12],1,0)
            if vi[1]:
                vi[11]=signed(read_vi(vi[12]+1,0)|vi[10],16);word(vi[13],0,vi[11])
                vi[11]=vi[13];vi[13]+=vi[1];emit_packet(vi[11])
                # Prefix header ILW at1BF8 adds three dependency cycles.
                elapsed+=28;events.append((elapsed,len(packets)-1))
                elapsed+=max(2,2*(len(packets[-1]['data'])//16)-1)
            else:elapsed+=20
            vi[12]=69;emit_packet(vi[7]);events.append((elapsed,len(packets)-1))
            elapsed+=5 if vi[7]==69 else 6
            vi[7]=94 if vi[7]==69 else 69
        vi[15]=read_vi(121,3);vi[9]=1
        split_events.append({'cost':elapsed+30,'kicks':events})

    if vf[0] != [0,0,0,0x3f800000]: raise ValueError('VF0 invariant')
    groups,prefix=(x&65535 for x in quad(top)[:2])
    if not 0<groups<=32 or prefix>=224 or top+224+prefix>=1024: raise ValueError('Unsupported layout')
    vi[4]=vi[13]=top+224; vi[5]=groups; vi[7]=69; vi[9]=1
    for index in range(prefix): store(vi[4]+index,quad(top+1+index))
    vi[4]+=prefix; cursor=top+1+prefix
    for group in range(groups):
        header=quad(cursor); count=header[0]&32767
        if not 2<=count<=128 or cursor+1+3*count>top+224 or vi[4]+1+3*count>1024:
            raise ValueError('Overlapping or degenerate group')
        counts.append(count); group_paths=[]
        vf[18]=header; store(vi[4],header); vi[4]+=1; vi[5]-=1
        vi[8]=count; vi[10]=-32768
        vi[2]=cursor+1+count; vi[3]=cursor+1+2*count
        vf[24]=transform(quad(cursor+1),1)
        vf[25]=transform(quad(cursor+1),5)
        if not number(vf[24][3]): raise ValueError('Zero divisor')
        q=bits(number(vf[0][3])/number(vf[24][3])); push_clip(vf[25])
        for vertex in range(count):
            next_position=quad(cursor+vertex+2)
            vf[31]=quad(vi[3]); vf[30]=quad(vi[2]); vi[3]+=1;vi[2]+=1
            vi[12]=(vf[31][2]&65535)&vi[9]; vi[1]=int(bool(clip&0x3ffff)); vi[8]-=1
            projected=arithmetic(vf[24],[q]*4)
            vf[30]=arithmetic(vf[30],[q]*4,mask=14)
            vf[27]=transform(next_position,1)
            vf[25]=transform(next_position,5)
            if not number(vf[27][3]): raise ValueError('Zero divisor')
            q=bits(number(vf[0][3])/number(vf[27][3]))
            vf[26]=[max(-2147483648,min(2147483647,int(number(x)*16)))&0xffffffff for x in projected]
            vf[22]=quad(cursor+vertex+3)
            store(vi[4],vf[30]); store(vi[4]+1,vf[31]);store(vi[4]+2,vf[26])
            vf[24]=move(vf[27])
            suppress=False; path=1
            if not vi[12]: suppress=True;path=0
            elif vi[1]:
                for plane,mask in enumerate((0xfdf7df,0xff7df7,0xffbefb,0xffdf7d,0xffefbe)):
                    vi[1]=int((clip|mask)==0xffffff)
                    if vi[1]:
                        suppress=True;path=2+plane
                        # A taken branch still executes the next FCOR in its
                        # delay slot. Its result survives in VI1 at return.
                        if plane<4:
                            vi[1]=int((clip|(0xfdf7df,0xff7df7,0xffbefb,0xffdf7d,0xffefbe)[plane+1])==0xffffff)
                        break
                else:
                    if vertex<2: raise ValueError('Unknown helper vertex history')
                    path=7; bad_near=bool(clip&0x20820)
                    positions=[quad(cursor+1+vertex-2+j) for j in range(3)]
                    textures=[quad(cursor+1+count+vertex-2+j) for j in range(3)]
                    colors=[quad(cursor+1+2*count+vertex-2+j) for j in range(3)]
                    vf[19],vf[20],vf[21]=positions
                    for j in range(3): vf[14+j]=transform(positions[j],9)
                    vi[15]=0x17c8//8
                    word(119,3,vi[13]);word(119,0,vi[8]);word(119,1,vi[9]);word(119,2,vi[10])
                    word(120,2,vi[4]);word(120,3,vi[3]);word(121,2,vi[2]);word(121,3,vi[15])
                    for j in range(3):
                        vf[26+j]=[bits(signed(x)) for x in colors[j]]
                        store(24+j*3,vf[26+j]);store(25+j*3,textures[j]);store(26+j*3,positions[j])
                        push_clip(vf[14+j])
                    vf[20]=transform(positions[1],5);vf[21]=transform(positions[2],5)
                    vi[11]=3;vi[12]=45;vi[14]=30
                    if bad_near or clip&0x3ffff:
                        if not split_polygons: raise ValueError('Polygon splitting not enabled in this research call')
                        split_polygon(bad_near,positions);suppress=True;path=8
                    else:
                        vi[1]=0;vi[15]+=vi[9];vi[9]=1
                        push_clip(vf[20]);push_clip(vf[21])
            if suppress: word(vi[4]+2,3,vi[10])
            vf[23]=move(vf[22],normalize_lookahead=True);push_clip(vf[25]);vi[4]+=3
            group_paths.append(path)
        cursor=vi[3];vi[6]=cursor;paths.append(group_paths)
    # The prior helper drains the lookahead dependency; otherwise it adds one
    # issue stall. First iteration begins with that dependency already ready.
    cost=(16,17,20,22,24,26,28,71)
    kick_cycle=24+5*prefix+17*(groups-1)+2
    intermediate_kicks=[];event_index=0
    # Initial group setup places its first 16F8 at24+5*prefix. Later groups
    # add17cycles each. The final two cycles are the group exit before XGKICK.
    iteration_cycle=24+5*prefix
    for group_paths in paths:
        previous=7
        for path in group_paths:
            if path==8:
                event=split_events[event_index];event_index+=1
                duration=event['cost']-int(previous==7)
                for offset,packet_index in event['kicks']:
                    packet=packets[packet_index]
                    intermediate_kicks.append((iteration_cycle+27-int(previous==7)+offset,
                                               packet['start'],len(packet['data'])//16))
            else:duration=cost[path]-int(previous==7)
            kick_cycle+=duration;iteration_cycle+=duration
            previous=path
        iteration_cycle+=17
    start=vi[13];cursor=start
    while True:
        tag=int.from_bytes(data[cursor*16:cursor*16+8],'little')
        count,fmt,regs=tag&32767,(tag>>58)&3,(tag>>60)&15 or 16
        if fmt==3:raise ValueError('Reserved GIF format')
        cursor+=1+(count*regs if fmt==0 else (count*regs+1)//2 if fmt==1 else count)
        if cursor>1024:raise ValueError('Wrapping GIF chain')
        if tag&32768:break
        if cursor==1024:raise ValueError('Unterminated GIF chain')
    qwords=cursor-start
    if intermediate_kicks:
        kick_cycle=max(kick_cycle,max(issue+2*length-1 for issue,_,length in intermediate_kicks))
    cycles=max(kick_cycle+5,kick_cycle+2*qwords-1)
    out=bytearray(entry)
    for r in range(32):struct.pack_into('<4I',out,r*16,*vf[r])
    struct.pack_into('<16i',out,512,*vi);struct.pack_into('<4I',out,576,*acc)
    status=(status&0xfc0)|current|(sticky<<6)
    struct.pack_into('<8I',out,592,q,p,immediate,random,0x1618,mac,clip,status)
    struct.pack_into('<Q',out,624,struct.unpack_from('<Q',entry,624)[0]+cycles)
    out[632:634]=bytes(2);out[648]=0;struct.pack_into('<2I',out,652,0x1608,0)
    emit_packet(start)
    return {'state':bytes(out),'data':bytes(data),'paths':paths,'counts':counts,'prefix':prefix,
            'cycles':cycles,'timing_complete':True,
            'kicks':intermediate_kicks+[(kick_cycle,start,qwords)],
            'packets':packets,'polygon_passes':polygon_passes,'split':split_used}


def describe(image):
    """Static instruction candidates; neither reachability nor execution counts."""
    upper_ops, lower_ops = Counter(), Counter()
    direct, indirect, kicks, ends, immediates = [], [], [], [], 0
    branches = {0x20: "B", 0x21: "BAL", 0x28: "IBEQ", 0x29: "IBNE",
                0x2c: "IBLTZ", 0x2d: "IBGTZ", 0x2e: "IBLEZ", 0x2f: "IBGEZ"}
    for pc in range(0, image.extent, 8):
        lower, upper = struct.unpack_from("<2I", image.code, pc)
        upper_ops[f"0x{upper & 63:02x}"] += 1
        if upper & 0x40000000:
            ends.append(hex(pc))
        if upper & 0x80000000:
            immediates += 1
            continue  # Lower word is a literal, never a branch/instruction.
        op = lower >> 25
        lower_ops[f"0x{op:02x}"] += 1
        if op in branches:
            immediate = lower & 0x7ff
            if immediate & 0x400:
                immediate -= 0x800
            target = (pc + 8 + immediate * 8) & 0x3fff
            direct.append({"pc": hex(pc), "op": branches[op], "target": hex(target),
                           "target_in_upload": target < image.extent})
        elif op in (0x24, 0x25):
            indirect.append({"pc": hex(pc), "op": "JR" if op == 0x24 else "JALR",
                             "vi_source": (lower >> 11) & 15})
        elif op == 0x40 and lower & 63 >= 0x3c:
            special = (lower & 3) | ((lower >> 4) & 0x7c)
            if special == 0x6c:
                kicks.append(hex(pc))
    return {
        "read_only": True,
        "image_sha256": hashlib.sha256(image.code).hexdigest(),
        "image_bytes": len(image.code), "uploaded_bytes": image.extent,
        "instruction_pairs": image.extent // 8,
        "segments": [{"vu_byte_address": hex(vu), "elf_virtual_address": hex(elf),
                      "length": size} for vu, elf, size in image.segments],
        "upper_primary_opcode_counts": dict(sorted(upper_ops.items())),
        "lower_primary_opcode_counts": dict(sorted(lower_ops.items())),
        "immediate_lower_words": immediates,
        "end_bit_candidates": ends, "xgkick_candidates": kicks,
        "direct_branch_count": len(direct),
        "direct_branches_outside_upload": [row for row in direct if not row["target_in_upload"]],
        "indirect_branch_candidates": indirect,
        "scope": "Static scan of uploaded bytes; includes unreachable instructions. "
                 "E bits include delay slots and do not define function boundaries. "
                 "Dynamic entry points, branch delays, pipelines and transfer timing "
                 "still require validation before native execution. No code is generated.",
    }


if __name__ == "__main__":
    print(json.dumps(describe(retail_image()), indent=2))
