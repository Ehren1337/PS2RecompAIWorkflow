"""Bounded Windows CPU/wait trace alongside the existing farm-route probe.

Run with administrator rights. Reuses analysis/rumble-cpu.etl and
analysis/rumble-etw.json; does not rebuild or change runtime settings.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'analysis/rumble-etw.json'
TRACE = ROOT / 'analysis/rumble-cpu.etl'


def main(gpu=False):
    os.chdir(ROOT)
    report = {'status': 'starting', 'started_unix': time.time()}
    if REPORT.exists():
        previous = json.loads(REPORT.read_text(encoding='utf-8'))
        if 'backend_comparison' in previous:
            report['backend_comparison'] = previous['backend_comparison']
    report['profiles'] = ['CPU', 'GPU'] if gpu else ['CPU']
    def save():
        REPORT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    save()
    recording = False
    profile = driver = None
    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError('Windows administrator approval is required for WPR CPU profiling')
        import rumble_navigate as n
        nav = n.Navigator(30)
        with n.RaceProbe(nav) as probe:
            state = probe.state()
        if state['track_id'] != 10:
            raise RuntimeError('Expected active True Grits race; leaving current game untouched')
        report['pid'] = nav.pid
        report['initial_race'] = state
        report['wpr_start_request_unix'] = time.time()
        command = ['wpr', '-start', 'CPU']
        if gpu:
            command += ['-start', 'GPU']
        result = subprocess.run(command + ['-filemode'], capture_output=True, text=True)
        report['start_output'] = result.stdout + result.stderr
        if result.returncode:
            raise RuntimeError('WPR start failed; no existing recording will be stopped')
        recording = True
        report['wpr_start_return_unix'] = time.time()
        report['status'] = 'recording'
        save()
        profile = subprocess.Popen([sys.executable, '-B', 'tools/rumble_profile.py',
                                    '--trigger', 'now', '--seconds', '30', '--pre', '0', '--objects'],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        report['profile_launch_unix'] = time.time()
        time.sleep(3)
        code = """
import sys,json,contextlib,io
from pathlib import Path
sys.path.insert(0,'tools')
import rumble_navigate as n
n.research.configure_navigation_profile(Path.home() / 'Documents/PCSX2/inputprofiles/Keyboard.ini')
nav=n.Navigator(30)
with n.RaceProbe(nav) as p:
 s=p.state()
 hint=min(range(p.count),key=lambda i:sum((p.point(i)[j]-s['position'][j])**2 for j in range(3)))
# Navigation progress can fill a pipe before the parent finishes recording.
# Keep it local and emit only the final bounded result to avoid blocking input.
log=io.StringIO()
try:
 with contextlib.redirect_stdout(log): result=n.drive(nav,hint,20,24000)
 print(json.dumps(result))
except BaseException:
 print(log.getvalue()[-2000:])
 raise
"""
        # Use the existing local keyboard profile, without changing the runtime mapping.
        driver = subprocess.Popen([sys.executable, '-B', '-c', code], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        report['driver_launch_unix'] = time.time()
        report['profile_output'] = profile.communicate(timeout=50)[0][-3000:]
        report['profile_exit'] = profile.returncode
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if recording:
            report['status'] = 'saving'
            report['stop_requested_unix'] = time.time()
            save()
            result = subprocess.run(['wpr', '-stop', str(TRACE), 'Farm CPU and thread waits'],
                                    capture_output=True, text=True)
            report['stop_exit'] = result.returncode
            report['stop_output'] = result.stdout + result.stderr
        for name, child in [('driver', driver), ('profile', profile)]:
            if child is not None:
                try:
                    report[name + '_output'] = child.communicate(timeout=50)[0][-3000:]
                    report[name + '_exit'] = child.returncode
                except subprocess.TimeoutExpired:
                    # Do not terminate the driver while it may be holding input.
                    report[name + '_still_running_pid'] = child.pid
        report['status'] = ('failed' if report.get('error') or report.get('stop_exit', 1)
                            or report.get('profile_exit', 1) else
                            'captured_driver_failed' if report.get('driver_exit', 1) else 'captured')
        report['finished_unix'] = time.time()
        report['alignment_note'] = 'Wall-clock launch bounds; inspect ETL session origin before aligning narrow intervals.'
        save()


def analyze_waits(executor_tid, start_us, end_us):
    import csv, collections
    x=r'C:\Program Files (x86)\Windows Kits\10\Windows Performance Toolkit\xperf.exe'
    e=os.environ.copy();e['_NT_SYMBOL_PATH']=str(ROOT/'PS2Recomp/out/build/ps2xRuntime/RelWithDebInfo');e['_NT_SYMCACHE_PATH']=str(Path(os.environ['TEMP'])/'rumble-etw-symbols')
    p=subprocess.Popen([x,'-i',str(TRACE),'-symbols','-a','dumper','-range',str(start_us),str(end_us),'-stacktimeshifting','-provider','{3d6fa8d1-fe05-11d0-9dda-00c04fd7ba7c}'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace',env=e)
    pending=None;completed_wait=None;stack_target=None;by=collections.defaultdict(collections.Counter)
    examples={};longest=[]
    def finish_wait():
     nonlocal completed_wait
     if completed_wait is None:return
     item=completed_wait;st=item['stack'];duration=item['end']-item['t'];state=item['state']
     device_waits = ['ReadPrefetchedVramPages', 'DownloadVramPages', 'DownloadVram', 'ReadLocalToHost', 'WaitForCompletion', 'PollPresentation', 'Present']
     label=next(('device::'+name for name in device_waits if any('GSDiligentDevice::'+name in f for f in st)), None)
     if label is None:
      label=next((name for name in ['Enqueue','BeginTransfer','ConsumeLocalToHostBytes','Present','GetTransferSnapshot','TextureFlush','Transfer','Draw','Run'] if any('GSDiligentBackend::'+name in f for f in st)),st[0] if st else 'missing_stack')
     b=by[label];b['count']+=1;b['off_cpu_us']+=duration
     if label not in examples:examples[label]=st[:32]
     longest.append({'start':item['t'],'end':item['end'],'ready':item.get('ready'),'state':state,'group':label,'off_cpu_us':duration,'stack':st[:24]})
     longest.sort(key=lambda row:row['off_cpu_us'],reverse=True)
     del longest[16:]
     if state in ('Ready','DeferredReady'):b['ready_us']+=duration
     elif 'ready' in item and item['t']<=item['ready']<=item['end']:
      b['blocked_us']+=item['ready']-item['t'];b['ready_us']+=item['end']-item['ready']
     else:b['unclassified_us']+=duration
     completed_wait=None
    for raw in p.stdout:
     if raw.lstrip().startswith('Stack,'):
      if stack_target is not None:
       r=next(csv.reader([raw]));fn=','.join(r[5:]).strip()
       if r[2].strip()==str(executor_tid) and fn.startswith('ps2EntryRunner.exe!'):stack_target.append(fn)
      continue
     finish_wait();stack_target=None
     r=[v.strip() for v in next(csv.reader([raw]))]
     if len(r)<6 or not r[1].isdigit():continue
     at=int(r[1])
     if r[0]=='ReadyThread' and int(r[5])==executor_tid and pending is not None:
      pending['ready']=at
     elif r[0]=='CSwitch':
      if int(r[3])==executor_tid and pending is not None:
       completed_wait=pending|{'end':at,'stack':[]};stack_target=completed_wait['stack'];pending=None
      if int(r[9])==executor_tid:
       pending={'t':at,'state':r[12]}
    finish_wait()
    err=p.stderr.read();rc=p.wait()
    result={'range_us':[start_us,end_us],'exit':rc,'errors':err[:500],'groups':dict(sorted(by.items(),key=lambda kv:kv[1]['blocked_us'],reverse=True))}
    result['stack_examples']={label:examples[label] for label in list(result['groups'])[:12]}
    result['longest_intervals_us']=longest
    result['limits'] = 'Completed off-CPU intervals only. Resume stacks identify the blocking call; ReadyThread splits blocked from runnable time. Boundary intervals excluded. No claim that every wait is removable.'
    return result

def analyze_gpu_queue(pid, start_us, end_us):
    import subprocess,csv,collections,json,statistics
    from pathlib import Path
    x=r'C:\Program Files (x86)\Windows Kits\10\Windows Performance Toolkit\xperf.exe'
    p=subprocess.Popen([x,'-i',str(TRACE),'-a','dumper','-range',str(start_us),str(end_us),'-provider','{802ec45a-1e99-4b83-9920-87c98277ba9d}','-add_fieldnames'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,errors='replace')
    pending={};packets=[];qpending={};queues=[];events=collections.Counter()
    for line in p.stdout:
     if not line.startswith(('Microsoft-Windows-DxgKrnl/DmaPacket/','Microsoft-Windows-DxgKrnl/QueuePacket/')):continue
     r=[v.strip() for v in next(csv.reader([line],skipinitialspace=True))]
     if len(r)<10 or not r[1].isdigit():continue
     at=int(r[1]);f=dict(v.split(' : ',1) for v in r[9:] if ' : ' in v);ours=r[2].endswith(f'({pid})')
     if '/DmaPacket/' in r[0] and 'hHwQueue' in f and 'ProgressFenceValue' in f:
      key=(f['hHwQueue'],f['ProgressFenceValue'])
      if 'pDmaBuffer' in f:
       if ours:
        pending[key]=at;events['release']+=1
      elif key in pending:
       packets.append({'start':pending.pop(key),'end':at,'queue':key[0],'fence':key[1]});events['complete']+=1
     elif '/QueuePacket/' in r[0] and 'SubmitSequence' in f:
      key=(f.get('hContext'),f['SubmitSequence'])
      if r[0].endswith('win:Start') and ours:
       qpending[key]=(at,f.get('PacketType','sync'));events['submit']+=1
      elif r[0].endswith('win:Stop') and key in qpending:
       begin,kind=qpending.pop(key);queues.append({'start':begin,'end':at,'type':kind})
    def summary(rows):
     spans=sorted((r['start'],r['end']) for r in rows);merged=[]
     for a,b in spans:
      if merged and a<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],b)
      else:merged.append([a,b])
     times=sorted(b-a for a,b in spans)
     return {'count':len(times),'median_us':statistics.median(times) if times else None,'p95_us':times[int(.95*(len(times)-1))] if times else None,'max_us':max(times,default=0),'pending_union_us':sum(b-a for a,b in merged),'longest':sorted(rows,key=lambda r:r['end']-r['start'],reverse=True)[:12]}
    result={'range_us':[start_us,end_us],'pid':pid,'events':dict(events),'release_to_completion':summary(packets),'submit_to_completion':summary(queues),'unfinished_releases':len(pending),'unfinished_submits':len(qpending),'error':p.stderr.read(),'exit':p.wait(),'limits':'Completed intervals only; driver release-to-completion includes scheduling and completion reporting, NOT precise GPU shader execution. HAGS events paired by hardware queue and fence, not ambiguous event-name position. No primitive/object attribution.'}
    return result

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analyze-window', nargs=2, type=float, metavar=('START_SECONDS', 'END_SECONDS'))
    parser.add_argument('--executor-tid', type=int)
    parser.add_argument('--gpu-pid', type=int, help='Analyze hardware queue completion events for this captured PID')
    parser.add_argument('--gpu', action='store_true', help='Record GPU activity alongside CPU and thread waits')
    args = parser.parse_args()
    if args.analyze_window:
        start, end = args.analyze_window
        if (args.executor_tid is None) == (args.gpu_pid is None) or not 0 <= start < end or end - start > 60:
            parser.error('Analysis requires exactly one verified TID or GPU PID and a window of at most 60 seconds')
        report = json.loads(REPORT.read_text(encoding='utf-8'))
        key = 'gpu_queue_analysis' if args.gpu_pid is not None else 'weighted_executor_waits'
        result = (analyze_gpu_queue(args.gpu_pid, int(start * 1e6), int(end * 1e6)) if args.gpu_pid is not None
                  else analyze_waits(args.executor_tid, int(start * 1e6), int(end * 1e6)))
        report[key] = result
        REPORT.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    else:
        main(gpu=args.gpu)
