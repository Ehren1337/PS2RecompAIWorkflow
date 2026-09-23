"""Bounded numeric timing trace for this retail runner; no input or image capture.
Run these examples from the Rumble Racing folder.

    py -3 -B tools/rumble_profile.py --trigger race-start
    py -3 -B tools/rumble_profile.py --trigger now --seconds 15
    py -3 -B tools/rumble_profile.py --trigger slowdown --pre 3 --stacks
    py -3 -B tools/rumble_profile.py --label-existing

Windows/PDB research helper, not a runtime dependency. Uses the existing VU
profiling counters (launch with PS2_VU_PROFILE=1) and Diligent worker counters.
The native preparation/playback split also needs PS2_VU_STAGE_PROFILE=1.
Optional sampled draw stages need PS2_GS_STAGE_PROFILE=1 (about 1/256 draws).
Writes one fixed report, overwriting it. 50-ms samples are NOT per-frame GPU
timings. Cross-thread reads are best effort and cannot alone establish causation.
Optional stacks briefly suspend each sampled thread, always resume it, and report
their measured overhead. Run without concurrent builds or benchmark tests.
"""
import argparse, collections, functools, json, math, os, random, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

# Labels describe the sampled host operation, not an unverified scene object.
# Nearest matching stack frame wins: a GS wait below VU geometry remains a
# GS wait, rather than charging that sample to the outer geometry routine.
ACTIVITY_LABELS = {
    'geometry': 'Geometry transforms and projection',
    'clipping': 'Geometry clipping',
    'lighting': 'Lighting and reflections',
    'vu': 'Vector-unit execution',
    'vif': 'Geometry data upload / VIF',
    'dma': 'DMA and GIF transfer processing',
    'draw_setup': 'Graphics state and draw preparation',
    'submit': 'Graphics command submission',
    'render': 'Renderer command processing',
    'present': 'Display presentation / readback',
    'audio': 'Audio processing',
    'guest': 'Recompiled game code (function not yet named)',
    'wait_queue': 'Waiting for graphics queue / its lock',
    'wait_transfer': 'Waiting for graphics transfer',
    'wait_present': 'Waiting for display presentation',
    'wait_render': 'Waiting for renderer operation',
    'wait_worker': 'Renderer worker waiting for queued work',
    'wait_scheduler': 'Waiting for scheduler event / frame pacing',
    'wait': 'Other wait / synchronization',
    'unknown': 'Unclassified (inspect original stack)',
}
NATIVE_LABELS = {
    '0x1428': 'Vertex transform and projection',
    '0x1618': 'Geometry clipping',
    '0x400': 'Lighting',
    '0x840': 'Lighting with reflections',
    '0xb38': 'Reflections',
    '0xdb0': 'Dual-basis geometry',
    '0x24c0': 'Quad geometry',
}

def label_stack(names, thread):
    """Return a category and its evidence-frame index; never infer an object."""
    waiting = any(any(token in frame for token in (
        'WaitFor', 'WaitOnAddress', 'SleepConditionVariable', 'AcquireSRWLock',
        'Cnd_wait', 'Mtx_lock', 'condition_variable')) for frame in names[:3])
    if waiting:
        for i, frame in enumerate(names):
            if 'GSDiligentBackend::' in frame:
                if 'Enqueue' in frame or 'Submit' in frame: return 'wait_queue', i
                if 'Present' in frame: return 'wait_present', i
                if 'Transfer' in frame or 'UploadImage' in frame: return 'wait_transfer', i
                if '::Run' in frame and thread == 'renderer': return 'wait_worker', i
                return 'wait_render', i
            if 'EeScheduler::waitForEvent' in frame:
                return 'wait_scheduler', i
        return 'wait', 0
    rules = (
        ('clipping', ('rumble_vu::buildClip', 'rumble_vu::buildPolygon')),
        ('lighting', ('rumble_vu::buildReflect', 'rumble_vu::buildLit')),
        ('geometry', ('rumble_vu::buildPacket', 'rumble_vu::buildDualBasis', 'rumble_vu::buildQuad')),
        ('present', ('GS::latchHostPresentationFrame', 'GSDiligentDevice::Present',
                     'GSDiligentDevice::PollPresentation')),
        ('submit', ('GSDiligentBackend::Enqueue', 'GSDiligentBackend::Submit',
                    'VU1Interpreter::applyNative', 'VU1Interpreter::progressXgkick')),
        ('render', ('GSDiligentDevice::', 'GSDiligentBackend::Draw')),
        ('audio', ('RumbleAudio::', 'PS2Audio::', 'ps2_audio')),
        ('vif', ('PS2Memory::processVIF',)),
        ('dma', ('GifArbiter::', 'PS2Memory::submitGifPacket', 'PS2Memory::processPendingTransfers')),
        ('draw_setup', ('GS::',)),
        ('vu', ('VU1Interpreter::', 'PS2Runtime::executeVU0Microprogram')),
        ('guest', ('sub_00', 'FUN_00')),
    )
    for i, frame in enumerate(names):
        for category, tokens in rules:
            if any(token in frame for token in tokens): return category, i
    return 'unknown', None

def add_activity_labels(report):
    """Postprocess after capture: zero extra pauses or live memory reads."""
    counts = {}; examples = {}
    for row in report['samples']:
        speed = row.get('rolling_speed_percent')
        bucket = ('unclassified' if speed is None else
                  'slow' if speed < report['args']['slow_percent'] else
                  'recovered_speed' if speed >= report['args']['recover_percent'] else 'transition')
        for thread, trace in row.get('stacks', {}).items():
            category, evidence = label_stack(trace['functions'], thread)
            trace['activity'] = category
            trace['activity_frame'] = evidence
            counts.setdefault(bucket, {}).setdefault(thread, collections.Counter())[category] += 1
            if evidence is not None:
                examples.setdefault((bucket, thread, category), trace['functions'][evidence])
    report['activity_labels'] = ACTIVITY_LABELS
    report['activity_summary'] = {
        bucket: {thread: {'total_samples': sum(counter.values()), 'categories': [
            {'activity': category, 'samples': count,
             'sample_share_percent': round(100 * count / sum(counter.values()), 1),
             'example_function': examples.get((bucket, thread, category))}
            for category, count in counter.most_common()]}
            for thread, counter in threads.items()} for bucket, threads in counts.items()}
    for window in report['windows'] + report.get('summary', {}).get('slowest_windows', []):
        for pc, stage in window.get('native_stages', {}).items():
            stage['label'] = NATIVE_LABELS.get(pc, 'Unmapped native geometry routine')
    note = (
        'Activity labels are stack-based operation categories, not object identities or causal proof. '
        'Sample shares are NOT percentages of frame time. A wait label identifies the waiting call, '
        'not the reason its dependency is late. Native labels refer to the verified retail specializations.')
    if note not in report['limitations']: report['limitations'].append(note)

def object_deltas(first, last):
    """Per-instance construction costs and submitted bytes; neither is GPU time."""
    result = []
    for identity, end in last.items():
        # A missing/unstable baseline is not zero: that would charge the
        # instance's whole lifetime to this interval. Wait for two observations.
        if identity not in first: continue
        begin = first[identity]
        delta = [end[i] - begin[i] for i in range(4)]
        submitted = [end[i] - begin[i] for i in (5, 6)]
        vu = [end[i] - begin[i] for i in (7, 8, 9)]
        if min(delta + submitted + vu) < 0 or not (delta[0] or submitted[0] or vu[0] or vu[1]): continue
        result.append({'id': identity, 'calls': delta[0], 'completed': delta[1],
                       'interrupted': delta[2], 'construction_ms': round(delta[3] / 1e6, 6),
                       'mean_completed_us': round(delta[3] / delta[1] / 1e3, 3) if delta[1] else None,
                       'lifetime_max_completed_us': round(end[4] / 1e3, 3),
                       'submitted_tags': submitted[0], 'submitted_payload_bytes': submitted[1],
                       'vu_callback_calls':vu[0], 'vu_callback_interrupted':vu[1],
                       'vu_callback_ms':round(vu[2]/1e6,6),
                       'vu_callback_mean_us':round(vu[2]/vu[0]/1e3,3) if vu[0] else None,
                       'lifetime_max_vu_callback_us':round(end[10]/1e3,3)})
    return sorted(result, key=lambda item: item['construction_ms'], reverse=True)

def producer_deltas(first, last):
    result=[]
    for identity,end in last.items():
        if identity not in first: continue
        pc,node,model=identity.split(':')
        delta=[end[i]-first[identity][i] for i in range(5)]
        if min(delta)<0 or not (delta[0] or delta[2] or delta[3]): continue
        result.append({'id':identity,'producer_pc':pc,'mesh_node':node,'mesh_model':model,
                       'submitted_tags':delta[0],'submitted_payload_bytes':delta[1],
                       'vu_callback_calls':delta[2],'vu_callback_interrupted':delta[3],
                       'vu_callback_ms':round(delta[4]/1e6,6),'lifetime_max_vu_callback_us':round(end[5]/1e3,3),
                       'example_source':hex(end[6])})
    return sorted(result,key=lambda r:r['vu_callback_ms'],reverse=True)

@functools.lru_cache(maxsize=512)
def producer_function(pc):
    import csv
    address=int(pc,16)
    with (ROOT/'CSV Map/retail/map.csv').open(encoding='utf-8-sig',newline='') as f:
        matches=[row for row in csv.DictReader(f) if int(row['Start'],0)<=address<int(row['End'],0)]
    if len(matches)!=1: return None
    row=matches[0]
    return {'name':row['Name'],'address':row['Start'],'basis':'retail function-map range; name may be autogenerated'}

def farm_model_catalog():
    """Names from owned assets, checked against the independent inventory.

    Keep only immutable Gmd metadata signatures, never exported model data.
    A shared signature remains ambiguous; do not choose one of its names.
    """
    import hashlib, struct
    import rumble_research as research
    inventory = json.loads((ROOT / 'analysis/assets-investigation.json').read_text(encoding='utf-8-sig'))
    build = next(b for b in inventory['Builds'] if b['Build'] == 'Retail')
    catalog = {}
    for path in ('DATA/LOCSE/SE1.TRK', 'GLBLDATA.PS2'):
        archive = next(a for a in build['Archives'] if a['Path'] == path)
        metadata = {r['Id']: r for r in archive['Resources'] if r['Type'] == 'o3d ' and not r['Error']}
        for kind, identity, _, data in research.stream_resources(research.RETAIL / path, {'o3d '}):
            row = metadata.get(identity)
            if not row or hashlib.sha256(data).hexdigest().lower() != row['SHA256'].lower():
                raise ValueError('Model differs from verified inventory: '+path)
            offset = 0
            while offset + 8 <= len(data):
                tag, size = struct.unpack_from('<4sI', data, offset)
                if size < 16 or offset + size > len(data): raise ValueError('Invalid model chunk: '+path)
                if tag == b' dmG' and size >= 0x68:
                    signature = data[offset + 0x30:offset + 0x68]
                    if any(signature):
                        item = {'archive':path, 'resource_id': identity, 'name': row['ResourceName'],
                                'sha256': row['SHA256'].lower()}
                        if item not in catalog.setdefault(signature, []): catalog[signature].append(item)
                offset += size
    return catalog

def obf_mesh_metadata(raw):
    """Bounded OBF103 tree walk; return node identity metadata, never geometry."""
    import struct
    def unpack(fmt, at):
        if at < 0 or at + struct.calcsize(fmt) > len(raw):
            raise ValueError('Truncated OBF structure')
        return struct.unpack_from(fmt, raw, at)
    if unpack('<4sI4sI', 0) != (b'OBF ', 0x103, b'HEAD', 8):
        raise ValueError('Unexpected OBF header')
    expected = unpack('<h', 16)[0]
    if not 1 <= expected <= 4096: raise ValueError('Invalid OBF node count')
    cursor, nodes = 24, []
    def walk(depth):
        nonlocal cursor
        if depth > 128 or len(nodes) >= expected: raise ValueError('OBF tree exceeds bounds')
        if unpack('<4sI', cursor) != (b'ELHE', 96): raise ValueError('Invalid OBF node header')
        cursor += 8
        children, materials, words = unpack('<hhI', cursor)
        metadata = unpack('<96s', cursor)[0]
        if children < 0 or materials < 0: raise ValueError('Negative OBF node count')
        cursor += 96
        tag, size = unpack('<4sI', cursor)
        if tag != b'ELTL' or size < materials * 4 or size % 4: raise ValueError('Invalid OBF material list')
        cursor += 8 + size
        if unpack('<4sI', cursor) != (b'ELDA', words * 4): raise ValueError('Invalid OBF geometry extent')
        cursor += 8 + words * 4
        if cursor > len(raw): raise ValueError('Truncated OBF geometry')
        nodes.append(metadata)
        for _ in range(children): walk(depth + 1)
    walk(0)
    if len(nodes) != expected or cursor != len(raw): raise ValueError('OBF count or final extent mismatch')
    return nodes

def farm_mesh_catalog():
    import hashlib
    import rumble_research as research
    inventory = json.loads((ROOT/'analysis/assets-investigation.json').read_text(encoding='utf-8-sig'))
    archive = next(a for b in inventory['Builds'] if b['Build']=='Retail'
                   for a in b['Archives'] if a['Path']=='DATA/LOCSE/SE1.TRK')
    records = {r['Id']:r for r in archive['Resources'] if r['Type']=='obf ' and not r['Error']}
    catalog = {}
    for _, identity, _, raw in research.stream_resources(research.RETAIL/'DATA/LOCSE/SE1.TRK', {'obf '}):
        record = records.get(identity)
        if not record or hashlib.sha256(raw).hexdigest().lower() != record['SHA256'].lower():
            raise ValueError('Track mesh resource differs from verified inventory')
        for node, metadata in enumerate(obf_mesh_metadata(raw)):
            catalog.setdefault(metadata, []).append({'archive':archive['Path'],'resource_id':identity,
                'name':record['ResourceName'],'resource_node':node,'sha256':record['SHA256'].lower()})
    return catalog

def draw_category_delta(first, last):
    """Sampled submitted states; NEVER is a no-op in the current backends."""
    if first is None or last is None or len(first) != 12 or len(last) != 12:
        return None
    delta = [b-a for a,b in zip(first,last)]
    count = sum(delta[:4])
    if min(delta) < 0 or not count or count != sum(delta[4:]):
        return None  # Reset or incomplete counter snapshot; never invent zeros.
    return {'samples': count,
            'depth_methods': dict(zip(('never','always','gequal','greater'), delta[:4])),
            'primitive_kinds': dict(zip(('point','line','line_strip','triangle','triangle_strip','triangle_fan','sprite','reserved'),delta[4:])),
            'never_percent': round(100*delta[0]/count, 3)}


def draw_stage_delta(first, last):
    """Sample means, not extrapolated GPU or whole-frame costs."""
    if first is None or last is None:
        return None
    delta = [b-a for a,b in zip(first,last)]
    if len(delta) != 7 or min(delta) < 0 or not delta[0]:
        return None
    names = ('build', 'display_source', 'submit', 'diagnostics')
    return {'samples': delta[0],
            'sampled_ms': {k:round(ns/1e6,6) for k,ns in zip(names,delta[1:5])},
            'mean_ns': {k:round(ns/delta[0],1) for k,ns in zip(names,delta[1:5])},
            'submit_over_100us': delta[6],
            'lifetime_max_submit_ns': last[5]}

class SlowdownTrigger:
    """Race-clock detector with a rolling window and recovery hysteresis."""
    def __init__(self, slow=90, recover=95):
        self.slow, self.recover = slow, recover
        self.rows = collections.deque(maxlen=64)
        self.low_since = self.high_since = None
        self.active = False

    @staticmethod
    def valid(row):
        return (row['phase'] == 4 and row['race_valid'] and row['consistent_phase']
                and row['clock'] > 3600)

    def update(self, row):
        previous = self.rows[-1] if self.rows else None
        changed = previous and (row['car'] != previous['car'] or row['track'] != previous['track']
                               or row['clock'] < previous['clock'])
        invalid = not self.valid(row) or changed
        gap = previous and row['t'] - previous['t'] > .5
        if invalid or gap:
            self.rows.clear()
            self.low_since = self.high_since = None
            if invalid and self.active:
                return 'scene_changed', None
        if invalid:
            return None, None
        self.rows.append(row)
        while len(self.rows) > 1 and self.rows[1]['t'] <= row['t'] - .5:
            self.rows.popleft()
        first = self.rows[0]
        elapsed = row['t'] - first['t']
        if elapsed < .5:
            return None, None
        # This game's clock advances 1200 ticks per real second at full speed.
        speed = (row['clock'] - first['clock']) / elapsed / 12
        if not self.active:
            if speed < self.slow:
                if self.low_since is None:
                    self.low_since = row['t']
                if row['t'] - self.low_since >= .25:
                    self.active = True
                    return 'slowdown', speed
            else:
                self.low_since = None
        else:
            if speed >= self.recover:
                if self.high_since is None:
                    self.high_since = row['t']
                if row['t'] - self.high_since >= 1:
                    return 'recovered', speed
            else:
                self.high_since = None
        return None, speed

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--label-existing', action='store_true',
                    help='Label the existing timing report in place without attaching to the game')
parser.add_argument('--objects', action='store_true',
                    help='Read bounded object construction/submitted-DMA counters; requires PS2_RUMBLE_OBJECT_PROFILE=1 at launch')
parser.add_argument("--trigger", choices=("now", "race-start", "slowdown"), default="race-start")
parser.add_argument("--seconds", type=float, default=25)
parser.add_argument("--wait", type=float, default=120)
parser.add_argument("--pre", type=float, default=10)
parser.add_argument("--interval-ms", type=int, default=50)
parser.add_argument("--slow-percent", type=float, default=90)
parser.add_argument("--recover-percent", type=float, default=95)
parser.add_argument("--stacks", action="store_true", help="Sample executor/renderer backtraces at a bounded, jittered interval")
parser.add_argument("--stack-lines", action="store_true", help="Resolve sampled leaf source lines after capture; requires --stacks")
parser.add_argument("--stack-interval-ms", type=int, default=250, help="Target backtrace interval, 50..1000 ms; faster sampling adds measured pause overhead")
args = parser.parse_args()
if args.stack_lines and not args.stacks:
    parser.error("--stack-lines requires --stacks")
if not (1 <= args.seconds <= 60 and 0 <= args.pre <= 15 and 1 <= args.wait <= 120
        and 25 <= args.interval_ms <= 250 and 50 <= args.stack_interval_ms <= 1000):
    parser.error("seconds 1..60; pre 0..15; wait 1..120; interval-ms 25..250; stack-interval-ms 50..1000")
if not (0 < args.slow_percent < args.recover_percent <= 110):
    parser.error("require 0 < slow-percent < recover-percent <= 110")
if args.label_existing:
    output = ROOT / 'analysis/rumble-timing.json'
    report = json.loads(output.read_text(encoding='utf-8'))
    if report.get('schema_version') not in (6, 7, 8, 9, 10, 11, 12):
        parser.error('Existing report must use schema 6 through 11; file left unchanged')
    add_activity_labels(report)
    report['schema_version'] = max(7, report['schema_version'])
    output.write_text(json.dumps(report, separators=(',', ':')), encoding='utf-8')
    print(json.dumps({'status': 'labeled_existing', 'pid': report['pid'], 'output': str(output)}))
    raise SystemExit(0)
os.chdir(ROOT)
import rumble_navigate as navigation
initial = navigation.snapshot()
if initial["age"] > 5 or initial["runtime"] != "running":
    raise SystemExit("A fresh running retail inspector is required")
import ctypes as c,struct,time,json,pathlib
handles={};stack_handles={}
k=c.WinDLL('kernel32',use_last_error=True);d=c.WinDLL('dbghelp',use_last_error=True)
k.OpenProcess.argtypes=[c.c_uint,c.c_int,c.c_uint];k.OpenProcess.restype=c.c_void_p
k.CloseHandle.argtypes=[c.c_void_p]
k.ReadProcessMemory.argtypes=[c.c_void_p,c.c_void_p,c.c_void_p,c.c_size_t,c.c_void_p]
d.SymInitializeW.argtypes=[c.c_void_p,c.c_wchar_p,c.c_int];d.SymInitializeW.restype=c.c_int
d.SymSetOptions.argtypes=[c.c_uint];d.SymFromName.argtypes=[c.c_void_p,c.c_char_p,c.c_void_p];d.SymCleanup.argtypes=[c.c_void_p]
pid=json.loads(pathlib.Path('../PS2Recomp/out/build/ps2xRuntime/inspector.json').read_text(encoding='utf-8-sig'))['process_id']
p=k.OpenProcess(0x410,False,pid)
if not p:raise c.WinError(c.get_last_error())
try:
 d.SymSetOptions(0x2|0x4|0x10|0x200|0x80000)
 if not d.SymInitializeW(p,str(pathlib.Path('../PS2Recomp/out/build/ps2xRuntime/RelWithDebInfo').resolve()),True):raise c.WinError(c.get_last_error())
 def symbol(name):
  b=c.create_string_buffer(88+2048);struct.pack_into('<I',b,0,88);struct.pack_into('<I',b,80,2048)
  if not d.SymFromName(p,name.encode(),b):raise RuntimeError(name+' '+str(c.get_last_error()))
  return struct.unpack_from('<Q',b,56)[0]
 def read(a,n):
  b=c.create_string_buffer(n);got=c.c_size_t()
  if not k.ReadProcessMemory(p,a,b,n,c.byref(got)) or got.value!=n:raise c.WinError(c.get_last_error())
  return b.raw

 d.SymGetTypeFromName.argtypes=[c.c_void_p,c.c_ulonglong,c.c_char_p,c.c_void_p]
 d.SymGetTypeInfo.argtypes=[c.c_void_p,c.c_ulonglong,c.c_uint,c.c_int,c.c_void_p]
 k.LocalFree.argtypes=[c.c_void_p]
 def sym_info(name):
  b=c.create_string_buffer(88+2048);struct.pack_into('<I',b,0,88);struct.pack_into('<I',b,80,2048)
  if not d.SymFromName(p,name.encode(),b):raise RuntimeError(name+' '+str(c.get_last_error()))
  return struct.unpack_from('<Q',b,32)[0],struct.unpack_from('<I',b,4)[0],struct.unpack_from('<Q',b,56)[0]
 mod,_,up=sym_info('upgrades')
 def ti(idx,kind,typ=c.c_uint):
  out=typ()
  if not d.SymGetTypeInfo(p,mod,idx,kind,c.byref(out)):raise RuntimeError('typeinfo '+str((idx,kind,c.get_last_error())))
  return out.value
 def field(typename,name):
  b=c.create_string_buffer(88+2048);struct.pack_into('<I',b,0,88);struct.pack_into('<I',b,80,2048)
  if not d.SymGetTypeFromName(p,mod,typename.encode(),b):raise RuntimeError('type '+typename+' '+str(c.get_last_error()))
  idx=struct.unpack_from('<I',b,4)[0]
  n=ti(idx,13); buf=(c.c_uint*(2+n))();buf[0]=n
  if not d.SymGetTypeInfo(p,mod,idx,7,buf):raise c.WinError(c.get_last_error())
  for child in list(buf)[2:]:
   try:
    ptr=ti(child,1,c.c_void_p)
   except RuntimeError:continue
   try:label=c.wstring_at(ptr)
   finally:k.LocalFree(ptr)
   if label==name:return ti(child,10)
  raise RuntimeError('field not found '+typename+'::'+name)
 state=struct.unpack('<Q',read(up,8))[0]
 runtime=struct.unpack('<Q',read(state,8))[0]
 gs=runtime+field('PS2Runtime','m_gs')
 size=struct.unpack('<I',read(gs+field('GS','m_localMemorySize'),4))[0]
 if size!=4*1024*1024:raise RuntimeError('invalid VRAM size')

 expected=ROOT.parent / 'PS2Recomp/out/build/ps2xRuntime/RelWithDebInfo/ps2EntryRunner.exe'
 k.QueryFullProcessImageNameW.argtypes=[c.c_void_p,c.c_uint,c.c_wchar_p,c.POINTER(c.c_uint)]
 pathbuf=c.create_unicode_buffer(32768);pathlen=c.c_uint(len(pathbuf))
 if not k.QueryFullProcessImageNameW(p,0,pathbuf,c.byref(pathlen)):
  raise c.WinError(c.get_last_error())
 if pathlib.Path(pathbuf.value).resolve()!=expected.resolve() or pid!=initial['pid']:
  raise RuntimeError('Unexpected runner path or changed process')
 ram=struct.unpack('<Q',read(runtime+field('PS2Runtime','m_memory')+field('PS2Memory','m_rdram'),8))[0]
 from rumble_menu_research import MenuResearch
 original=MenuResearch('Retail')
 for address in (0x100000,0x1c74d0):
  if read(ram+address,128)!=original.read(address,128):
   raise RuntimeError('Retail memory signature mismatch')
 def word(address):return struct.unpack('<I',read(ram+address,4))[0]
 backend=struct.unpack('<Q',read(gs+field('GS','m_backend'),8))[0]
 typ="`anonymous-namespace'::GSDiligentBackend"
 # MSVC x64 std::thread stores HANDLE followed by thread id. This is a
 # local Windows diagnostic, not an assumed portable runtime ABI.
 worker=struct.unpack_from('<I',read(backend+field(typ,'worker'),16),8)[0]
 scheduler=struct.unpack('<Q',read(runtime+field('PS2Runtime','m_eeScheduler'),8))[0]
 executor=struct.unpack('<I',read(scheduler+field('EeScheduler','m_executorThread'),4))[0]
 k.OpenThread.argtypes=[c.c_uint,c.c_int,c.c_uint];k.OpenThread.restype=c.c_void_p
 for label,tid in [('renderer',worker),('executor',executor)]:
  handles[label]=k.OpenThread(0x40,False,tid)
  if not handles[label]:raise c.WinError(c.get_last_error())
 for fn in (k.GetThreadTimes,k.GetProcessTimes):
  fn.argtypes=[c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p]
 def cpu_ns(handle,process=False):
  values=[c.c_ulonglong() for _ in range(4)]
  if not (k.GetProcessTimes if process else k.GetThreadTimes)(handle,*[c.byref(x) for x in values]):
   raise c.WinError(c.get_last_error())
  return (values[2].value+values[3].value)*100
 profile=symbol('ps2_vu_diagnostics::runProfile')
 # Stage counters are optional on older runner builds. Enable in new runners
 # with PS2_VU_STAGE_PROFILE=1; no guest data is stored in this table.
 try:
  native_stage_profile=symbol('ps2_vu_diagnostics::nativeStages')
  native_stage_overflow=symbol('ps2_vu_diagnostics::nativeStageOverflow')
 except RuntimeError:
  native_stage_profile=native_stage_overflow=None
 try: draw_stage_profile=symbol('ps2_gs_diagnostics::drawStages')
 except RuntimeError: draw_stage_profile=None
 try: draw_category_profile=symbol('ps2_gs_diagnostics::drawCategories')
 except RuntimeError: draw_category_profile=None
 names=('clip','reflect','lit','reflectLit','dualBasis','quad')
 native={name:symbol('rumble_vu::'+name+'NativeStats') for name in names}
 gs_names=('issued','completed','gpuDraws','cpuDraws','uploads','readbacks',
           'queuedLocalCopies','completedLocalCopies','presentationSequence')
 object_table = object_overflow = producer_table = None
 object_provenance_symbols = {}
 if args.objects:
  # Reject an older per-model-only runner instead of guessing its ABI.
  if (field('rumble_object_diagnostics::Entry','object') != 52 or
      field('rumble_object_diagnostics::Entry','submittedTags') != 56 or
      field('rumble_object_diagnostics::Entry','submittedBytes') != 64 or
      field('rumble_object_diagnostics::Entry','vuCompleted') != 72 or
      field('rumble_object_diagnostics::Entry','vuMaximumNanoseconds') != 96):
   raise RuntimeError('Unexpected object counter layout')
  object_table = symbol('rumble_object_diagnostics::entries')
  object_overflow = symbol('rumble_object_diagnostics::overflow')
  if (field('rumble_producer_diagnostics::Entry','pc') != 56 or
      field('rumble_producer_diagnostics::Entry','exampleSource') != 60 or
      field('rumble_producer_diagnostics::Entry','node') != 64 or
      field('rumble_producer_diagnostics::Entry','model') != 68):
   raise RuntimeError('Unexpected command producer counter layout')
  producer_table=symbol('rumble_producer_diagnostics::entries')
  object_provenance_symbols = {key:symbol('rumble_object_diagnostics::'+key) for key in
      ('emittedTags','matchedTags','unownedTags','mismatchedTags','tagEvictions',
       'unownedVuCompleted','unownedVuInterrupted','unownedVuNanoseconds')}
  object_provenance_symbols.update({'producer_'+key:symbol('rumble_producer_diagnostics::'+key)
                                   for key in ('overflow','emitted','matched')})
 def read_producers():
  if producer_table is None: return {}
  before=read(producer_table,512*72);after=read(producer_table,512*72);found={}
  for at in range(0,len(before),72):
   stamp,tags,byte_count,completed,interrupted,ns,maximum,pc,source,node,model=struct.unpack_from('<7Q4I',before,at)
   if not pc or stamp&1 or stamp!=struct.unpack_from('<Q',after,at)[0]: continue
   found[f'{hex(pc)}:{hex(node)}:{hex(model)}']=[tags,byte_count,completed,interrupted,ns,maximum,source]
  return found
 def read_objects():
  if object_table is None: return {}
  before = read(object_table, 512 * 104)
  after = read(object_table, 512 * 104)
  found = {}
  for at in range(0, len(before), 104):
   stamp,calls,complete,interrupted,ns,maximum,model,obj,tags,byte_count,vu_calls,vu_interrupted,vu_ns,vu_max = struct.unpack_from('<6Q2I6Q', before, at)
   if not model or not obj or stamp & 1 or stamp != struct.unpack_from('<Q', after, at)[0]: continue
   found[f'{obj:08x}:{model:08x}'] = [calls,complete,interrupted,ns,maximum,tags,byte_count,vu_calls,vu_interrupted,vu_ns,vu_max]
  return found
 gs_offsets={name:field(typ,name) for name in gs_names}
 if args.stacks:
  if c.sizeof(c.c_void_p)!=8:raise RuntimeError('Stack sampler requires Windows x64 Python')
  k.SuspendThread.argtypes=[c.c_void_p];k.SuspendThread.restype=c.c_uint
  k.ResumeThread.argtypes=[c.c_void_p];k.ResumeThread.restype=c.c_uint
  k.GetThreadContext.argtypes=[c.c_void_p,c.c_void_p];k.GetThreadContext.restype=c.c_int
  d.SymFromAddr.argtypes=[c.c_void_p,c.c_ulonglong,c.c_void_p,c.c_void_p];d.SymFromAddr.restype=c.c_int
  if args.stack_lines:
   # IMAGEHLP_LINE64 uses natural x64 alignment. Copy the borrowed file name
   # immediately; all DbgHelp calls here run on this one Python thread.
   # https://learn.microsoft.com/windows/win32/api/dbghelp/ns-dbghelp-imagehlp_line64
   class SourceLine(c.Structure):
    _fields_=[('SizeOfStruct',c.c_uint),('Key',c.c_void_p),('LineNumber',c.c_uint),
              ('FileName',c.c_char_p),('Address',c.c_ulonglong)]
   d.SymGetLineFromAddr64.argtypes=[c.c_void_p,c.c_ulonglong,c.POINTER(c.c_uint),c.POINTER(SourceLine)]
   d.SymGetLineFromAddr64.restype=c.c_int
  class Address(c.Structure):
   _fields_=[('Offset',c.c_ulonglong),('Segment',c.c_ushort),('Mode',c.c_int)]
  class Stack(c.Structure):
   _fields_=[('PC',Address),('Return',Address),('Frame',Address),('Stack',Address),('Backing',Address),('tail',c.c_byte*512)]
  d.StackWalk64.argtypes=[c.c_uint,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p,c.c_void_p]
  d.StackWalk64.restype=c.c_int
  for label,tid in [('executor',executor),('renderer',worker)]:
   stack_handles[label]=k.OpenThread(0x4a,False,tid)
   if not stack_handles[label]:raise c.WinError(c.get_last_error())
 def capture_stacks():
  results={}
  for label,thread in stack_handles.items():
   raw=c.create_string_buffer(1248);context=(c.addressof(raw)+15)&~15
   c.c_uint.from_address(context+48).value=0x100003
   started=time.monotonic()
   if k.SuspendThread(thread)==0xffffffff:raise c.WinError(c.get_last_error())
   try:
    if not k.GetThreadContext(thread,context):raise c.WinError(c.get_last_error())
    rip=c.c_ulonglong.from_address(context+248).value
    frame=Stack();frame.PC.Offset=rip
    frame.Frame.Offset=c.c_ulonglong.from_address(context+160).value
    frame.Stack.Offset=c.c_ulonglong.from_address(context+152).value
    frame.PC.Mode=frame.Frame.Mode=frame.Stack.Mode=3
    chain=[rip]
    for depth in range(12):
     if not d.StackWalk64(0x8664,p,thread,c.byref(frame),context,None,
                         c.cast(d.SymFunctionTableAccess64,c.c_void_p),c.cast(d.SymGetModuleBase64,c.c_void_p),None):break
     if not frame.PC.Offset:break
     if depth==0 and frame.PC.Offset==rip:continue
     chain.append(frame.PC.Offset)
   finally:
    if k.ResumeThread(thread)==0xffffffff:raise c.WinError(c.get_last_error())
   results[label]={'pcs':chain,'pause_ms':round((time.monotonic()-started)*1000,3)}
  return results
 began=time.monotonic();last_check=0;audio={};sequence=initial['sequence']
 object_snapshot={};object_at=None;object_dropped=0;object_global_snapshot={};producer_snapshot={}
 def sample():
  global last_check,audio,sequence,object_snapshot,object_at,object_dropped,object_global_snapshot,producer_snapshot
  at=time.monotonic()
  if at-last_check>=1:
   fresh=navigation.snapshot()
   if fresh['pid']!=pid or fresh['age']>5 or fresh['runtime']!='running':
    raise RuntimeError('Runner changed, stopped, or inspector became stale')
   audio={key:fresh['metrics'].get(key) for key in
          ('diagnostic_sound_disabled','unsupported_commands','engine_updates',
           'bank_voice_commands','music_staged_frames','speech_pcm_frames',
           'pcm_active_voice_mask','audio_paused')}
   sequence=fresh['sequence'];last_check=at
   if object_table is not None:
    object_snapshot=read_objects();object_at=round(at-began,6)
    producer_snapshot=read_producers()
    object_dropped=struct.unpack('<Q',read(object_overflow,8))[0]
    object_global_snapshot={key:struct.unpack('<Q',read(address,8))[0] for key,address in object_provenance_symbols.items()}
  phase=word(0x1f21e4);clock=word(0x1f22b8)
  car=word(0x1f22a0);track=word(0x1f2740)
  # Frontend animations reuse phase4 and advance the same clock with no
  # allocated player. Require live race owners, not just that phase value.
  owners_valid=(0<car<0x2000000-0x138 and not car&3 and
                0<track<0x2000000-4 and not track&3)
  track_count=struct.unpack('<h',read(ram+track+2,2))[0] if owners_valid else 0
  race_valid=owners_valid and 1<track_count<8192 and track+4+track_count*32<=0x2000000
  vu={}
  for index,row in enumerate(struct.iter_unpack('<5Q',read(profile,4096*40))):
   if row[0] or row[1]:
    vu[str(index)]=[row[0]+row[1],row[3]]
  row={'t':round(at-began,6),'clock':clock,'phase':phase,'car':car,'track':track,'race_valid':race_valid,
       'cpu':{name:cpu_ns(handle) for name,handle in handles.items()},
       'vu':vu,'native':{},'gs':{},
       'audio':audio,'inspector_sequence':sequence}
  row['native_stages']={}
  if object_table is not None:
   row['objects']=object_snapshot;row['objects_at']=object_at;row['object_overflow']=object_dropped
   row['object_provenance']=object_global_snapshot
   row['command_producers']=producer_snapshot
  row['draw_stages']=list(struct.unpack('<7Q',read(draw_stage_profile,56))) if draw_stage_profile else None
  row['draw_categories']=list(struct.unpack('<12Q',read(draw_category_profile,96))) if draw_category_profile else None
  if native_stage_profile:
   for key,prepare_calls,prepare_ns,apply_calls,apply_ns in struct.iter_unpack('<5Q',read(native_stage_profile,16*40)):
    if key:row['native_stages'][str(key-1)]=[prepare_calls,prepare_ns,apply_calls,apply_ns]
   row['native_stage_overflow']=struct.unpack('<Q',read(native_stage_overflow,8))[0]
  # Numeric scene identity lets a slow window be tied to a route location
  # without recording frames or exporting geometry.
  row['position']=list(struct.unpack('<3f',read(ram+car+64,12))) if race_valid else None
  row['course_step']=struct.unpack('<i',read(ram+car+0xfc,4))[0] if race_valid else None
  row['track_selection']=list(read(ram+0x2288b0,2)) if race_valid else None
  row['cpu']['process']=cpu_ns(p,True)
  for name,address in native.items():
   v=struct.unpack('<11Q',read(address,88))
   row['native'][name]=[v[0],v[1],v[4],v[5],v[7]]
  for name,offset in gs_offsets.items():
   row['gs'][name]=struct.unpack('<Q',read(backend+offset,8))[0]
  row['consistent_phase']=(phase==word(0x1f21e4) and car==word(0x1f22a0) and track==word(0x1f2740))
  row['read_ms']=round((time.monotonic()-at)*1000,3)
  return row
 interval=args.interval_ms/1000
 rows=collections.deque(maxlen=math.ceil((args.pre+args.seconds)/interval)+4)
 triggered=None;previous=None;next_at=time.monotonic();next_stack=0
 stack_rng=random.Random(0)
 detector=SlowdownTrigger(args.slow_percent,args.recover_percent)
 end_reason='wait_expired';recovered=None
 print(json.dumps({'status':'armed','pid':pid,'trigger':args.trigger,
                   'interval_ms':args.interval_ms,'max_wait_seconds':args.wait}),flush=True)
 while True:
  row=sample()
  if args.stacks and row['t']>=next_stack:
   row['stacks']=capture_stacks()
   # Jitter avoids repeatedly sampling the same phase of a 30/60-Hz frame.
   next_stack=time.monotonic()-began+stack_rng.uniform(.8,1.2)*args.stack_interval_ms/1000
  rows.append(row)
  event,speed=detector.update(row)
  row['rolling_speed_percent']=round(speed,2) if speed is not None else None
  if triggered is None:
   while len(rows)>1 and rows[0]['t']<row['t']-args.pre:rows.popleft()
   crossing=(previous is not None and previous['clock']<=3600<row['clock']
             and row['phase']==4 and row['consistent_phase'] and row['race_valid']
             and previous['phase']==4 and previous['consistent_phase'] and previous['race_valid']
             and previous['car']==row['car'] and previous['track']==row['track'])
   if args.trigger=='now' or (args.trigger=='race-start' and crossing) or (args.trigger=='slowdown' and event=='slowdown'):
    triggered=row['t']
    end_reason='time_limit'
    print(json.dumps({'status':'triggered','t':triggered,'clock':row['clock'],
                      'speed_percent':row['rolling_speed_percent']}),flush=True)
   elif row['t']>=args.wait:break
  elif args.trigger=='slowdown' and event in ('recovered','scene_changed'):
   end_reason=event
   if event=='recovered':recovered=row['t']
   break
  elif row['t']>=triggered+args.seconds:break
  previous=row;next_at+=interval
  # No catch-up burst if a read or the OS took longer than a sampling interval.
  if next_at<time.monotonic():next_at=time.monotonic()+interval
  time.sleep(max(0,next_at-time.monotonic()))
 rows=list(rows)
 # Resolve only after sampling, with every thread resumed. Persist function
 # names and counts, never raw stack contents or process addresses.
 resolved={};resolved_lines={};stack_counts={};source_counts={};pause_cost=[]
 for row in rows:
  speed=row['rolling_speed_percent']
  bucket=('unclassified' if speed is None else 'slow' if speed<args.slow_percent
          else 'recovered_speed' if speed>=args.recover_percent else 'transition')
  for label,trace in row.get('stacks',{}).items():
   names=[]
   pcs=trace.pop('pcs')
   if args.stack_lines and pcs:
    leaf=pcs[0]
    if leaf not in resolved_lines:
     line=SourceLine();line.SizeOfStruct=c.sizeof(line);displacement=c.c_uint()
     location=None
     if d.SymGetLineFromAddr64(p,leaf,c.byref(displacement),c.byref(line)) and line.FileName:
      file=Path(line.FileName.decode('mbcs',errors='replace'))
      try: file_name=file.relative_to(ROOT).as_posix()
      except ValueError: file_name=file.name
      location={'file':file_name,'line':line.LineNumber}
     resolved_lines[leaf]=location
    trace['leaf_source']=resolved_lines[leaf]
    if trace['leaf_source']:
     loc=trace['leaf_source']
     source_counts.setdefault(bucket,{}).setdefault(label,collections.Counter())[(loc['file'],loc['line'])]+=1
   for address in pcs:
    if address not in resolved:
     buf=c.create_string_buffer(88+2048);struct.pack_into('<I',buf,0,88);struct.pack_into('<I',buf,80,2048)
     displacement=c.c_ulonglong()
     resolved[address]=(c.string_at(c.addressof(buf)+84).decode(errors='replace')
                        if d.SymFromAddr(p,address,c.byref(displacement),buf) else 'unresolved')
    names.append(resolved[address])
   trace['functions']=names
   pause_cost.append(trace['pause_ms'])
   stack_counts.setdefault(bucket,{}).setdefault(label,collections.Counter())[tuple(names)]+=1
 stack_summary={bucket:{label:[{'samples':n,'functions':list(chain)} for chain,n in counts.most_common(12)]
                        for label,counts in threads.items()} for bucket,threads in stack_counts.items()}
 windows=[]
 # Half-second windows suppress the expected 20-tick quantization of the
 # game's frame clock. Raw 50-ms samples remain available in the same report.
 for index,a in enumerate(rows):
  end=next((end for end in range(index+1,len(rows)) if rows[end]['t']-a['t']>=.5),None)
  if end is None:break
  b=rows[end]
  dt=b['t']-a['t']
  segment=rows[index:end+1]
  if (any(r['phase']!=4 or not r['consistent_phase'] or not r['race_valid'] or
          r['car']!=a['car'] or r['track']!=a['track'] for r in segment)
      or any(y['clock']<x['clock'] for x,y in zip(segment,segment[1:]))):continue
  vu=[]
  for key,v in b['vu'].items():
   prev=a['vu'].get(key,[0,0]);ns=v[1]-prev[1]
   if ns<0:continue
   if ns:vu.append({'unit':int(key)//2048,'pc':hex((int(key)%2048)*8),
                    'host_ms':round(ns/1e6,3),'calls':v[0]-prev[0]})
  windows.append({'t':b['t'],'clock':b['clock'],'clock_stalled':b['clock']==a['clock'],
    'position':b['position'],'course_step':b['course_step'],'track_selection':b['track_selection'],
    'speed_percent':round((b['clock']-a['clock'])/dt/12,1),
    'cpu_percent':{key:round((b['cpu'][key]-a['cpu'][key])/dt/1e7,1) for key in a['cpu']},
    'vu':sorted(vu,key=lambda item:item['host_ms'],reverse=True)[:4],
    'gs_delta':{key:b['gs'][key]-a['gs'][key] for key in gs_names},
    'draw_stages':draw_stage_delta(a['draw_stages'],b['draw_stages']),
    'draw_categories':draw_category_delta(a['draw_categories'],b['draw_categories']),
    'native_stages':{hex(int(key)):{'prepare_calls':v[0]-a['native_stages'].get(key,[0]*4)[0],
                      'prepare_ms':round((v[1]-a['native_stages'].get(key,[0]*4)[1])/1e6,3),
                      'apply_calls':v[2]-a['native_stages'].get(key,[0]*4)[2],
                      'apply_ms':round((v[3]-a['native_stages'].get(key,[0]*4)[3])/1e6,3)}
                     for key,v in b['native_stages'].items()}})
 read_cost=sorted(row['read_ms'] for row in rows)
 summary={'samples':len(rows),'triggered':triggered,'duration':rows[-1]['t']-rows[0]['t'],
          'end_reason':end_reason,'recovered':recovered,
          'phases':sorted({row['phase'] for row in rows}),
          'valid_race_samples':sum(row['race_valid'] and row['phase']==4 for row in rows),
          'clock_range':[min(row['clock'] for row in rows),max(row['clock'] for row in rows)],
          'read_ms_median':read_cost[len(read_cost)//2],
          'read_ms_max':max(read_cost),
          'native_stage_overflow':max((row.get('native_stage_overflow',0) for row in rows),default=0),
          'native_stages_observed':any(row['native_stages'] for row in rows),
          'draw_stages':draw_stage_delta(rows[0]['draw_stages'],rows[-1]['draw_stages']),
          'draw_categories':draw_category_delta(rows[0]['draw_categories'],rows[-1]['draw_categories']),
          'stack_samples':len(pause_cost),'thread_pause_ms_total':round(sum(pause_cost),3),
          'thread_pause_ms_max':max(pause_cost,default=0),
          'stalled_windows':sum(w['clock_stalled'] for w in windows),
          'slowest_windows':sorted((w for w in windows if not w['clock_stalled']),key=lambda r:r['speed_percent'])[:3]}
 report={'schema_version':12,'pid':pid,'thread_ids':{'executor':executor,'renderer':worker},'recorded_unix':time.time(),'args':vars(args),'summary':summary,
         'limitations':['Best-effort asynchronous numeric samples; not atomic frame/GPU timestamps.',
                        'Draw categories sample submitted primitive/ZTST state, not GPU dispatches or pixels written; inconsistent category snapshots are omitted.',
                        'Per-draw submit samples can include buffered collection and full-batch flushes; end-of-tag flush work is included in native apply time, not necessarily these samples.',
                        'VU host time includes downstream GIF and renderer backpressure.',
                        'Native arithmetic time is nested inside VU time; do not sum them.',
                        'Native stage prepare includes eligibility/build/validation; apply includes playback, GIF/GS submission and waits. Both are nested inside VU host time.',
                        'Draw stages irregularly sample about 1/256 submitted primitives. Wall times include clock overhead and submission waits, not GPU execution. Means are best-effort cross-thread counter deltas; the maximum is lifetime, not window-local. Small sample counts may be unrepresentative.',
                        'Thread CPU time has OS granularity; audio counters update once per inspector publication.',
                        'A race-start trigger requires stable loaded player/track owners and clock crossing 3600 in phase4; not visual OCR of GO.',
                        'Slowdown trigger: 500-ms race-clock window below threshold for 250 ms; recovery above threshold for 1 s. Scene change ends capture separately.',
                        'A stopped clock can be a pause or a stall; the recorder cannot distinguish them conclusively.',
                        'Optional Windows x64 stack sampling briefly suspends threads; measured pause cost can perturb timing. Sampling rate is identical before/during slowdown.',
                        'Stack counts are sampled locations, not exact function duration. Wait frames identify blocking, not necessarily its upstream cause.',
                        'Optional leaf source lines are resolved after capture; optimized/inlined code can map imprecisely. Missing symbols produce null. Source locations store relative paths or external basenames only.',
                        'No stall attribution or fix is proven by this correlation trace alone.'],
         'stack_summary':stack_summary,
         'source_summary':{bucket:{label:[{'file':loc[0],'line':loc[1],'samples':n} for loc,n in counts.most_common(12)]
                          for label,counts in threads.items()} for bucket,threads in source_counts.items()},
         'windows':windows,'samples':rows}
 add_activity_labels(report)
 if args.objects:
  observations=[]
  for row in rows:
   objects=row.pop('objects',{})
   observed=row.pop('objects_at',None)
   provenance=row.pop('object_provenance',{})
   producers=row.pop('command_producers',{})
   if observed is not None and (not observations or observed != observations[-1][0]):
    observations.append((observed,objects,provenance,producers))
  report['object_costs']={'scope':'CPU construction, verified VIF1 REF submissions, and original VU callback wall time by launch-command source; not GPU or total object time',
    'capacity':512,'overflow':max((r.get('object_overflow',0) for r in rows),default=0),
    'dma_provenance':{'tag_capacity':4096,'runtime_lifetime_totals':observations[-1][2] if observations else {}},
    'intervals':[],'totals':[],'identities':{}}
  costs=report['object_costs']
  report['command_producers']={'scope':'VU callback wall time by validated game call site when object identity is unavailable; not object or GPU time',
     'capacity':512,'overflow':max((o[2].get('producer_overflow',0) for o in observations),default=0),'intervals':[],'totals':[]}
  producer_costs=report['command_producers']
  if len(observations)>1:
   first,last=observations[0],observations[-1]
   costs['dma_provenance']['observation_interval']=[first[0],last[0]]
   costs['dma_provenance']['interval_delta']={
       key:last[2][key]-value if last[2].get(key,-1)>=value else None for key,value in first[2].items()}
   totals={}
   producer_totals={}
   for a,b in zip(observations,observations[1:]):
    producer_delta=producer_deltas(a[3],b[3])
    producer_costs['intervals'].append({'start':a[0],'end':b[0],'top_vu_callback':producer_delta[:12]})
    for item in producer_delta:
     aggregate=producer_totals.setdefault(item['id'],dict(item,submitted_tags=0,submitted_payload_bytes=0,
                   vu_callback_calls=0,vu_callback_interrupted=0,vu_callback_ms=0))
     for key in ('submitted_tags','submitted_payload_bytes','vu_callback_calls','vu_callback_interrupted','vu_callback_ms'):
      aggregate[key]+=item[key]
     aggregate['lifetime_max_vu_callback_us']=max(aggregate['lifetime_max_vu_callback_us'],item['lifetime_max_vu_callback_us'])
     aggregate['example_source']=item['example_source']
    delta=object_deltas(a[1],b[1])
    costs['intervals'].append({'start':a[0],'end':b[0],'top_construction':delta[:12],
                              'top_vu_callback':sorted(delta,key=lambda r:r['vu_callback_ms'],reverse=True)[:12]})
    for item in delta:
     aggregate=totals.setdefault(item['id'],dict(item,calls=0,completed=0,interrupted=0,construction_ms=0,
                                               submitted_tags=0,submitted_payload_bytes=0,vu_callback_calls=0,
                                               vu_callback_interrupted=0,vu_callback_ms=0))
     for key in ('calls','completed','interrupted','construction_ms','submitted_tags','submitted_payload_bytes',
                 'vu_callback_calls','vu_callback_interrupted','vu_callback_ms'): aggregate[key]+=item[key]
     aggregate['lifetime_max_completed_us']=max(aggregate['lifetime_max_completed_us'],item['lifetime_max_completed_us'])
     aggregate['lifetime_max_vu_callback_us']=max(aggregate['lifetime_max_vu_callback_us'],item['lifetime_max_vu_callback_us'])
   for item in totals.values():
    item['construction_ms']=round(item['construction_ms'],6)
    item['mean_completed_us']=round(item['construction_ms']*1000/item['completed'],3) if item['completed'] else None
    item['vu_callback_ms']=round(item['vu_callback_ms'],6)
    item['vu_callback_mean_us']=round(item['vu_callback_ms']*1000/item['vu_callback_calls'],3) if item['vu_callback_calls'] else None
   costs['totals']=sorted(totals.values(),key=lambda item:item['construction_ms'],reverse=True)
   for item in producer_totals.values():
    item['vu_callback_ms']=round(item['vu_callback_ms'],6)
    item['function']=producer_function(item['producer_pc'])
   producer_costs['totals']=sorted(producer_totals.values(),key=lambda r:r['vu_callback_ms'],reverse=True)
   on_farm=all(r['track_selection']==[5,0] and r['race_valid'] for r in rows)
   catalog=farm_model_catalog() if on_farm else {}
   meshes=farm_mesh_catalog() if on_farm else {}
   for item in producer_costs['totals']:
    model=int(item['mesh_model'],16)
    if not model: continue
    before=read(ram+model,96) if 0x100000<=model<=0x2000000-96 else None
    matches=meshes.get(before,[]) if before is not None and before==read(ram+model,96) else []
    item['mesh_identity']={'resource':matches[0] if len(matches)==1 else None,
        'basis':'unique original 96-byte OBF node metadata' if len(matches)==1 else 'unresolved or ambiguous',
        'limits':'metadata identity, not full relocated geometry equality; live metadata read at capture end'}
   for item in costs['totals']:
    obj,model=(int(x,16) for x in item['id'].split(':'))
    matches=catalog.get(read(ram+model+0x30,56),[]) if 0x100000<=model<0x2000000-0x68 else []
    costs['identities'][item['id']]={'object':hex(obj),'model':hex(model),
      'resource':matches[0] if len(matches)==1 else None,
      'identity_basis':'unique farm/shared Gmd metadata signature' if len(matches)==1 else 'unresolved or ambiguous',
      'instance_lifetime':'address/model pair within this capture; allocator reuse is not independently tracked'}
  report['limitations'].append('Construction counters cover synchronous completed display-list construction only, '
    'not object update, later geometry, GPU time or visible pixels. Missing/unstable baselines and counter resets '
    'are omitted rather than treated as zero. Overflow means incomplete object coverage; lifetime maxima are not interval maxima.')
  report['limitations'].append('Submitted payload bytes are actual copied VIF1 REF payloads matched to an observed object, '
    'tag address and unchanged 128-bit tag before list-buffer reuse. They exclude TTE bytes and unattributed tags; '
    'bytes are not execution time or proof of a slowdown. Scope lost across a guest yield is left unattributed. '
    'Tag evictions, missing entries and mismatches reduce coverage; DMA provenance totals are runtime lifetime counters.')
  report['limitations'].append('VU callback time is inclusive wall time inside original MSCAL/MSCALF/MSCNT execution, '
    'attributed to the snapshotted source of the launching four-byte command, including synchronous submission/waits. '
    'It is not GPU time, exclusive CPU time, or proof that every VU input/output belongs to that object. '
    'Unknown command sources are counted separately; these commands still execute normally. Failed callbacks '
    'are counted separately and do not contribute to completed callback time. Nested timings can overlap.')
  report['limitations'].append('Command producers are validated JAL/JALR call sites, with the copy helper caller retained '
    'where available. Verified mesh nodes at retail scenery 161ab4 and track 16f944 are separate keys; '
    'zero means no mesh proved. Their times are disjoint from linked object-cookie times. A mesh node is not '
    'a full scene-object identity and allocator reuse with unchanged node/model addresses is not tracked. '
    'Example source addresses may be reused and are not persistent asset identities. Missing/unstable producer baselines '
    'are omitted; an autogenerated containing-function name is not a verified semantic label.')
 output=ROOT/'analysis/rumble-timing.json'
 output.write_text(json.dumps(report,separators=(',',':')),encoding='utf-8')
 print(json.dumps({'status':'complete' if triggered is not None else 'wait_expired',
                   'output':str(output),'summary':summary}),flush=True)
finally:
 for h in stack_handles.values():
  if h:k.CloseHandle(h)
 for h in handles.values():
  if h:k.CloseHandle(h)
 d.SymCleanup(p);k.CloseHandle(p)
