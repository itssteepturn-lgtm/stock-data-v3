import baostock as bs
import orjson, os, time, subprocess, glob
from datetime import datetime, timedelta

DATA_DIR="data/stocks"
os.makedirs(DATA_DIR, exist_ok=True)

MAX_KEEP=350
DEEP_DAYS=350
TOPUP_DAYS=20
CHECKPOINT_EVERY=120
BUDGET=13*60
RECONNECT=180

def login():
    for _ in range(3):
        lg=bs.login()
        if lg.error_code=='0': return True
        time.sleep(5)
    return False

def git_push(msg):
    try:
        subprocess.run(['git','config','user.name','stock-bot'],check=True)
        subprocess.run(['git','config','user.email','bot@users.noreply.github.com'],check=True)
        subprocess.run(['git','add','data/'],check=True)
        if subprocess.run(['git','diff','--staged','--quiet']).returncode==0: return
        subprocess.run(['git','commit','-m',msg],check=True)
        subprocess.run(['git','fetch','origin','main'],check=True)
        subprocess.run(['git','merge','origin/main','-X','ours','--no-edit','-m','auto merge'],check=True)
        subprocess.run(['git','push'],check=True)
    except Exception as e: print("git fail",e)

def build_latest():
    out=[]
    for fp in glob.glob(f"{DATA_DIR}/*.json"):
        try:
            with open(fp,'rb') as f: bars=orjson.loads(f.read())
            if len(bars)<10: continue
            last=bars[-1]
            closes=sorted([b['close'] for b in bars[-200:]])
            def q(p): return closes[int(len(closes)*p)] if closes else 0
            code=os.path.basename(fp).replace('.json','')
            out.append({"c":code,"p":last['close'],"d":last['date'],"chg": round((last['close']-bars[-2]['close'])/bars[-2]['close']*100,2) if len(bars)>1 else 0,"cost50":q(0.5),"cost75":q(0.75),"cost90":q(0.9)})
        except: pass
    os.makedirs('data',exist_ok=True)
    with open('data/latest.json','wb') as f: f.write(orjson.dumps(out))
    meta={"total":len(out),"updated":len(out),"lastUpdateDate": max([x['d'] for x in out], default=""),"ranAt": datetime.utcnow().isoformat()+"Z","note": f"short-term 350d keep display 60"}
    with open('data/meta.json','wb') as f: f.write(orjson.dumps(meta))
    print(f"built latest {len(out)}")
    return out

def main():
    if not login(): print("login fail"); return
    start=time.time()
    last_login=start
    codes=[]
    for d in [0,1,2,3,4,5]:
        day=(datetime.now()-timedelta(days=d)).strftime('%Y-%m-%d')
        rs=bs.query_all_stock(day=day)
        tmp=[]
        while rs.error_code=='0' and rs.next():
            row=rs.get_row_data()
            if row[0].startswith(('sh.60','sh.68','sz.00','sz.30')): tmp.append(row[0])
        if len(tmp)>4000: codes=tmp; break
    if len(codes)<4000: print("codes fail"); bs.logout(); return
    print(f"codes {len(codes)}")
    with open('data/codes.json','wb') as f: f.write(orjson.dumps([{"code":c.split('.')[1]} for c in codes]))
    cursor_file='data/collect-cursor.json'
    cursor=0
    if os.path.exists(cursor_file):
        try: cursor=orjson.loads(open(cursor_file,'rb').read()).get('nextIndex',0)
        except: pass
    print(f"from {cursor}")
    processed=0
    for offset in range(len(codes)):
        if time.time()-start>BUDGET: break
        if time.time()-last_login>RECONNECT:
            try: bs.logout()
            except: pass
            login(); last_login=time.time()
        i=(cursor+offset)%len(codes)
        code=codes[i]
        raw=code.split('.')[1]
        fp=os.path.join(DATA_DIR,f"{raw}.json")
        existing=[]
        if os.path.exists(fp):
            try: existing=orjson.loads(open(fp,'rb').read())
            except: existing=[]
        need_deep=len(existing)<100
        start_date=(datetime.now()-timedelta(days=DEEP_DAYS if need_deep else TOPUP_DAYS)).strftime('%Y-%m-%d')
        today=datetime.now().strftime('%Y-%m-%d')
        try:
            rs=bs.query_history_k_data_plus(code,"date,open,high,low,close,volume,amount,turn",start_date=start_date,end_date=today,frequency="d",adjustflag="2")
            rows=[]
            while rs.error_code=='0' and rs.next(): rows.append(rs.get_row_data())
            bars=[{"date":r[0],"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4]),"vol":float(r[5])/100,"amount":float(r[6]),"turnover":float(r[7] or 0)} for r in rows if r[4]]
        except: continue
        if not bars: continue
        if need_deep: merged=bars
        else:
            d={b["date"]:b for b in existing}
            for b in bars: d[b["date"]]=b
            merged=[d[k] for k in sorted(d.keys())]
        if len(merged)>MAX_KEEP: merged=merged[-MAX_KEEP:]
        with open(fp,'wb') as f: f.write(orjson.dumps(merged))
        processed+=1
        if processed%CHECKPOINT_EVERY==0:
            build_latest()
            with open(cursor_file,'wb') as f: f.write(orjson.dumps({"nextIndex":(i+1)%len(codes)}))
            git_push(f"checkpoint {processed} pos {i}")
    with open(cursor_file,'wb') as f: f.write(orjson.dumps({"nextIndex":(cursor+processed)%len(codes)}))
    build_latest()
    git_push("final "+datetime.now().isoformat())
    bs.logout()
    print("done",processed)

if __name__=="__main__": main()
