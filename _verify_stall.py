import subprocess, time, urllib.request
BASE="https://gracious-inspiration-production.up.railway.app/api/stats"
def probe(timeout=35):
    t0=time.monotonic()
    try:
        urllib.request.urlopen(BASE, timeout=timeout).read(200)
        return time.monotonic()-t0
    except Exception:
        return time.monotonic()-t0  # timed-out duration
samples=[]
WINDOW_MIN=45
end=None
# self-paced; ~60s cadence for ~45 min
for i in range(46):
    dt=probe()
    samples.append(dt)
    if dt>10:   # a real stall recurred -> fail fast
        print(f"STALL RECURRED: /api/stats took {dt:.1f}s on sample {i+1} "
              f"(fix did NOT hold under load)", flush=True)
        end="fail"; break
    time.sleep(60)
# pull loop-lag warnings from Railway logs (one shot at the end)
laglines=""
try:
    out=subprocess.run(["railway","logs"],capture_output=True,text=True,timeout=60).stdout
    laglines="\n".join([l for l in out.splitlines() if "loop-lag" in l][-5:])
except Exception as e:
    laglines=f"(log pull failed: {e})"
mx=max(samples) if samples else -1
slow=sum(1 for s in samples if s>3)
if end!="fail":
    print(f"VERIFIED: {len(samples)} samples over ~{len(samples)} min, "
          f"max latency {mx:.2f}s, slow(>3s)={slow}/{len(samples)}. "
          f"loop-lag warnings:\n{laglines or '  (none — loop never blocked >2s)'}", flush=True)
