import baostock as bs
import orjson, os, time, subprocess, glob, traceback
from datetime import datetime, timedelta

DATA_DIR="data/stocks"
os.makedirs(DATA_DIR, exist_ok=True)

MAX_KEEP=350
DEEP_DAYS=350
TOPUP_DAYS=25
CHECKPOINT_EVERY=120
BUDGET=14*60
RECONNECT=180

def login():
    for _ in range(3):
        lg=bs.login()
        if lg.error_code=='0': return True
        time.sleep(5)
    return False

def git_push(msg):
    try:
        subprocess.run(['git','add','data/'],check=True)
        if subprocess.run(['git','diff','--staged','--quiet']).returncode==0: return
        subprocess.run(['git','commit','-m',msg],check=True)
        subprocess.run(['git','fetch','origin','main'],check=True)
        subprocess.run(['git','merge','origin/main','-X','ours','--no-edit','-m','auto merge'],check=True)
        subprocess.run(['git','push'],check=True)
    except Exception as e: print("git push fail",e)

def build_latest():
    out=[]
    last_dates=[]
    for fp in glob.glob(f"{DATA_DIR}/*.json"):
        try:
            with open(fp,'rb') as f: bars=orjson.loads(f.read())
            if len(bars)<5: continue
            last=bars[-1]
            closes=sorted([b['close'] for b in bars[-200:]])
            def q(p): return closes[int(len(closes)*p)] if closes else 0
            code=os.path.basename(fp).replace('.json','')
            out.append({"c":code,"p":last['close'],"d":last['date'],"chg": round((last['close']-bars[-2]['close'])/bars[-2]['close']*100,2) if len(bars)>1 else 0,"cost50":q(0.5),"cost75":q(0.75),"cost90":q(0.9)})
            last_dates.append(last['date'])
        except: pass
    os.makedirs('data',exist_ok=True)
    with open('data/latest.json','wb') as f: f.write(orjson.dumps(out))
    real_last = max(last_dates) if last_dates else datetime.now().strftime('%Y-%m-%d')
    meta={"total":len(out),"updated":len(out),"lastUpdateDate": real_last,"ranAt": datetime.utcnow().isoformat()+"Z","note": f"short-term 350d incl BSE | 库内 {len(out)} 支"}
    with open('data/meta.json','wb') as f: f.write(orjson.dumps(meta))
    print(f"built latest {len(out)} lastDate {real_last}")
    return out, real_last

def write_status(status):
    try:
        os.makedirs('data',exist_ok=True)
        with open('data/status.json','wb') as f:
            f.write(orjson.dumps(status))
    except Exception as e:
        print("status write fail",e)

def main():
    status = {
        "runAt": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "startAt": datetime.utcnow().isoformat()+"Z",
        "stage": "初始化",
        "marketTotal": 0,
        "cursorFrom": 0,
        "batchProcessed": 0,
        "batchTried": 0,
        "dbCount": 0,
        "dbLastDate": "",
        "cycleState": "进行中",
        "messages": [],
        "ok": False
    }
    try:
        if not login():
            status["stage"]="登录失败"
            status["messages"].append("baostock 登录失败")
            status["cycleState"]="异常"
            write_status(status)
            return
        status["stage"]="获取全市场代码表"
        start=time.time()
        last_login=start
        codes=[]
        for d in range(10):
            day=(datetime.now()-timedelta(days=d)).strftime('%Y-%m-%d')
            rs=bs.query_all_stock(day=day)
            tmp=[]
            while rs.error_code=='0' and rs.next():
                row=rs.get_row_data()
                c=row[0]
                if c.startswith(('sh.60','sh.68','sz.00','sz.30','bj.92','bj.43','bj.83','bj.87','sz.92','sh.92')):
                    tmp.append(c)
                if c.split('.')[1].startswith(('92','43','83','87')):
                    if c not in tmp: tmp.append(c)
            if len(tmp)>5000: codes=tmp; break
            time.sleep(0.3)
        print(f"codes {len(codes)} 含北交所")
        status["marketTotal"]=len(codes)
        with open('data/codes.json','wb') as f: f.write(orjson.dumps([{"code":c.split('.')[1]} for c in codes]))
        cursor_file='data/collect-cursor.json'
        cursor=0
        if os.path.exists(cursor_file):
            try: cursor=orjson.loads(open(cursor_file,'rb').read()).get('nextIndex',0)
            except: pass
        status["cursorFrom"]=cursor
        print(f"from {cursor}")
        processed=0
        tried=0
        for offset in range(len(codes)):
            if time.time()-start>BUDGET: 
                status["stage"]=f"本轮时限 {int(BUDGET/60)}分 已到"
                break
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
            need_deep=len(existing)<50
            start_date=(datetime.now()-timedelta(days=DEEP_DAYS if need_deep else TOPUP_DAYS)).strftime('%Y-%m-%d')
            today=datetime.now().strftime('%Y-%m-%d')
            try:
                rs=bs.query_history_k_data_plus(code,"date,open,high,low,close,volume,amount,turn",start_date=start_date,end_date=today,frequency="d",adjustflag="2")
                rows=[]
                while rs.error_code=='0' and rs.next(): rows.append(rs.get_row_data())
                bars=[{"date":r[0],"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),"close":float(r[4]),"vol":float(r[5])/100,"amount":float(r[6]),"turnover":float(r[7] or 0)} for r in rows if r[4]]
            except Exception as e:
                status["messages"].append(f"{raw} 读取异常")
                if len(status["messages"])>20: status["messages"]=status["messages"][-20:]
                continue
            tried+=1
            if not bars: continue
            if need_deep: merged=bars
            else:
                d={b["date"]:b for b in existing}
                for b in bars: d[b["date"]]=b
                merged=[d[k] for k in sorted(d.keys())]
            if len(merged)>MAX_KEEP: merged=merged[-MAX_KEEP:]
            with open(fp,'wb') as f: f.write(orjson.dumps(merged))
            processed+=1
            status["batchProcessed"]=processed
            status["batchTried"]=tried
            status["stage"]=f"更新中 {raw} 第{i+1}位"
            if processed%CHECKPOINT_EVERY==0:
                out, real_last=build_latest()
                status["dbCount"]=len(out)
                status["dbLastDate"]=real_last
                write_status(status)
                with open(cursor_file,'wb') as f: f.write(orjson.dumps({"nextIndex":(i+1)%len(codes)}))
                git_push(f"checkpoint {processed} pos {i} count {len(out)}")
        with open(cursor_file,'wb') as f: f.write(orjson.dumps({"nextIndex":(cursor+processed)%len(codes)}))
        out, real_last=build_latest()
        status["dbCount"]=len(out)
        status["dbLastDate"]=real_last
        status["batchProcessed"]=processed
        status["batchTried"]=tried
        status["stage"]="本轮完成"
        status["cycleState"]="正常"
        status["ok"]=True
        status["endAt"]=datetime.utcnow().isoformat()+"Z"
        write_status(status)
        git_push(f"sync {datetime.now().isoformat()} count {len(out)}")
        bs.logout()
        print(f"done processed {processed} count {len(out)}")
    except Exception as e:
        status["stage"]="运行异常"
        status["messages"].append(traceback.format_exc()[-500:])
        status["cycleState"]="异常"
        status["ok"]=False
        status["endAt"]=datetime.utcnow().isoformat()+"Z"
        write_status(status)
        try: git_push(f"sync error {datetime.now().isoformat()}")
        except: pass
        print("exception",e)
        raise

if __name__=="__main__": main()
