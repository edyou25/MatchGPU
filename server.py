#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json, re, os, sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data.json"
PORT = int(os.environ.get("PORT", "8000"))
UA = "version-timeline/1.0 (+local research tool)"

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]
    def handle_data(self, data):
        if data.strip(): self.parts.append(data.strip())
    def text(self): return "\n".join(self.parts)

def get_text(url, timeout=20):
    req=Request(url, headers={"User-Agent":UA})
    with urlopen(req, timeout=timeout) as r:
        raw=r.read().decode("utf-8","replace")
    p=TextExtractor(); p.feed(raw)
    return p.text(), raw

def normalize_torch(v):
    p=v.split(".")
    return ".".join(p[:2])

def update_python(db, report):
    text,_=get_text("https://www.python.org/doc/versions/")
    found={}
    for m in re.finditer(r"Python\s+(3\.\d+)\.0\s*,?\s*released on\s+([0-9]{1,2}\s+\w+\s+20\d{2})", text, re.I):
        ver, ds=m.groups()
        try: d=datetime.strptime(ds, "%d %B %Y").date().isoformat()
        except ValueError: continue
        if int(ver.split(".")[1])>=7: found[ver]=d
    if found:
        existing={x["version"]:x for x in db["tracks"]["python"]}
        for v,d in sorted(found.items(), key=lambda x: tuple(map(int,x[0].split(".")))):
            if v not in existing:
                db["tracks"]["python"].append({"id":"py"+v.replace(".",""),"version":v,"date":d,"detail":f"Python {v}.0"})
            else: existing[v]["date"]=d
        report.append(f"Python: {len(found)} feature releases checked")

def update_torch_dates_and_python(db, report):
    req=Request("https://pypi.org/pypi/torch/json", headers={"User-Agent":UA})
    with urlopen(req, timeout=25) as r: meta=json.load(r)
    releases=meta.get("releases",{})
    existing={x["version"]:x for x in db["tracks"]["torch"]}
    compat={}
    for full, files in releases.items():
        if not re.fullmatch(r"\d+\.\d+\.0", full): continue
        major_minor=normalize_torch(full)
        dates=[f.get("upload_time_iso_8601","")[:10] for f in files if f.get("upload_time_iso_8601")]
        if dates and int(dates[0][:4])>=2018:
            d=min(dates)
            if major_minor in existing: existing[major_minor]["date"]=d
            else:
                db["tracks"]["torch"].append({"id":"torch"+major_minor.replace(".","")+"0","version":major_minor,"date":d,"detail":f"PyTorch {full}"})
        pys=set()
        for f in files:
            fn=f.get("filename","")
            for m in re.finditer(r"-cp(3\d{1,2})(?:-|_)", fn):
                digits=m.group(1)
                if len(digits)==2: py=f"{digits[0]}.{digits[1]}"
                else: py=f"{digits[0]}.{digits[1:]}"
                pys.add(py)
        if pys: compat[major_minor]=sorted(pys,key=lambda s:tuple(map(int,s.split("."))))
    if compat:
        db["compat"]["torch_python"].update(compat)
    report.append(f"PyTorch/PyPI: {len(compat)} release wheel matrices checked")

def update_torch_cuda(db, report):
    text,_=get_text("https://pytorch.org/get-started/previous-versions/")
    matches=list(re.finditer(r"(?m)^v(\d+\.\d+\.\d+)\s*$", text))
    matrix={}
    for i,m in enumerate(matches):
        full=m.group(1); mm=normalize_torch(full)
        start=m.end(); end=matches[i+1].start() if i+1<len(matches) else len(text)
        sec=text[start:end]
        vals=set()
        for x in re.findall(r"CUDA\s+(\d+\.\d+)",sec): vals.add(x)
        for x in re.findall(r"/whl/cu(\d{3})",sec):
            vals.add(f"{x[:2]}.{x[2]}" if x.startswith("1") else f"{x[0]}.{x[1:]}")
        if full.endswith(".0") or mm not in matrix:
            if vals: matrix[mm]=sorted(vals,key=lambda s:tuple(map(int,s.split("."))))
    if matrix: db["compat"]["torch_cuda"].update(matrix)
    report.append(f"PyTorch↔CUDA: {len(matrix)} official binary matrices checked")

def update_cuda_versions(db, report):
    text,_=get_text("https://developer.nvidia.com/cupti/releases")
    found={}
    for m in re.finditer(r"(20\d{2})[/-](\d{2})[/-](\d{2}).{0,120}?\b(1[0-9]\.\d+)\b", text, re.S):
        y,mo,day,ver=m.groups()
        if 2018 <= int(y) <= 2100:
            found.setdefault(ver,f"{y}-{mo}-{day}")
    if found:
        existing={x["version"]:x for x in db["tracks"]["cuda"]}
        for v,d in found.items():
            if v in existing: existing[v]["date"]=d
            else:
                db["tracks"]["cuda"].append({"id":"cu"+v.replace(".",""),"version":v,"date":d,"detail":f"CUDA {v}"})
        report.append(f"CUDA/CUPTI: {len(found)} dated toolkit milestones checked")
    else:
        get_text("https://developer.nvidia.com/cuda-toolkit-archive")
        report.append("CUDA archive checked; no new dated CUPTI rows parsed")

def update_gpu_sources(db, report):
    urls=[
      "https://nvidianews.nvidia.com/news/10-years-in-the-making-nvidia-brings-real-time-ray-tracing-to-gamers-with-geforce-rtx",
      "https://nvidianews.nvidia.com/news/nvidia-delivers-greatest-ever-generational-leapwith-geforce-rtx-30-series-gpus",
      "https://nvidianews.nvidia.com/news/nvidia-delivers-quantum-leap-in-performance-introduces-new-era-of-neural-rendering-with-geforce-rtx-40-series",
      "https://nvidianews.nvidia.com/news/nvidia-blackwell-geforce-rtx-50-series-opens-new-world-of-ai-computer-graphics"
    ]
    ok=0
    for u in urls:
        try: get_text(u,timeout=12); ok+=1
        except Exception: pass
    report.append(f"NVIDIA GPU launch sources: {ok}/{len(urls)} reachable (generation dates remain curated)")

def sort_tracks(db):
    for key in db["tracks"]:
        db["tracks"][key].sort(key=lambda x:x.get("date","9999-12-31"))

def perform_update():
    db=json.loads(DATA.read_text(encoding="utf-8"))
    report=[]
    funcs=[update_python,update_torch_dates_and_python,update_torch_cuda,update_cuda_versions,update_gpu_sources]
    for fn in funcs:
        try: fn(db,report)
        except Exception as e: report.append(f"{fn.__name__}: {type(e).__name__}: {e}")
    today=datetime.now(timezone.utc).date().isoformat()
    db["meta"]["last_updated"]=today
    db["meta"]["range"][1]=today
    sort_tracks(db)
    DATA.write_text(json.dumps(db,ensure_ascii=False,indent=2),encoding="utf-8")
    return today,report

class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/api/update":
            self.send_error(404); return
        try:
            updated,report=perform_update()
            payload=json.dumps({"ok":True,"updated":updated,"report":report},ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)
        except Exception as e:
            payload=json.dumps({"ok":False,"error":str(e)},ensure_ascii=False).encode()
            self.send_response(500); self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def log_message(self, fmt, *args):
        sys.stderr.write("[timeline] "+(fmt%args)+"\n")

if __name__=="__main__":
    os.chdir(ROOT)
    print(f"Version timeline: http://127.0.0.1:{PORT}")
    print("Ctrl+C to stop.")
    ThreadingHTTPServer(("127.0.0.1",PORT),Handler).serve_forever()
