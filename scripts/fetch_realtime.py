import requests, orjson, os, time
from datetime import datetime

def fetch_batch(secids):
    url=f"https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={secids}&fields=f2,f3,f12,f14,f15,f16,f17,f18,f19,f20,f21,f8,f10"
    try:
        j=requests.get(url, timeout=10, headers={"Referer":"https://quote.eastmoney.com/"}).json()
        return j.get('data',{}).get('diff',[])
    except: return []

def main():
    codes=[]
    if os.path.exists('data/codes.json'):
        try: codes=[x['code'] for x in orjson.loads(open('data/codes.json','rb').read())]
        except: pass
    if not codes: print("no codes"); return
    all_data=[]
    for i in range(0,len(codes),900):
        batch=codes[i:i+900]
        secids=",".join([('1.'+c if c[0] in '6895' else '0.'+c) for c in batch])
        diff=fetch_batch(secids)
        all_data.extend(diff)
        print(f"batch {i} got {len(diff)}")
        time.sleep(0.3)
    out=[{"c":d.get('f12'),"p":d.get('f2'),"pct":d.get('f3'),"high":d.get('f15'),"low":d.get('f16'),"open":d.get('f17'),"prev":d.get('f18')} for d in all_data]
    os.makedirs('data',exist_ok=True)
    with open('data/realtime.json','wb') as f:
        f.write(orjson.dumps({"time":datetime.now().isoformat(),"data":out}))
    print(f"realtime {len(out)}")

if __name__=="__main__": main()
