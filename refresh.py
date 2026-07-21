#!/usr/bin/env python3
"""
Draft War Room — data refresher.
Pulls fresh ADP + projections + injuries from every free source, rebuilds data.js,
and pushes it to your GitHub repo (which auto-updates your live site).

Run it and leave it: it refreshes every REFRESH_HOURS and pushes only when data changed.
    python refresh.py

First run downloads a bit; after that each cycle is ~30-60s.
Requires: pip install pandas numpy   (git must be installed and the repo already set up)
"""
import urllib.request, urllib.error, json, re, time, subprocess, os, sys, traceback

# ---------- config ----------
HERE      = os.path.dirname(os.path.abspath(__file__))
SEASON    = "2026"
REFRESH_HOURS = 6            # how often to re-pull + push
GIT_PUSH  = True            # set False to just write data.js locally without pushing
# ----------------------------

def log(*a):
    msg=time.strftime("[%H:%M:%S] ")+" ".join(str(x) for x in a)
    try: print(msg, flush=True)
    except UnicodeEncodeError: print(msg.encode("ascii","replace").decode("ascii"), flush=True)
def norm(n):
    n=str(n).lower(); n=re.sub(r"[.'`]","",n); n=re.sub(r"\b(jr|sr|ii|iii|iv|v)\b","",n)
    n=re.sub(r"[^a-z ]"," ",n); return re.sub(r"\s+"," ",n).strip()
def get(url, headers=None, timeout=40):
    req=urllib.request.Request(url, headers=headers or {"User-Agent":"Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()
def getj(url, headers=None, timeout=40): return json.loads(get(url,headers,timeout))

SCORING={"py":0.04,"ptd":4,"int":-2,"ry":0.1,"rtd":6,"rec":1,"recy":0.1,"rectd":6,"fl":-2}
EPOS={1:"QB",2:"RB",3:"WR",4:"TE",5:"K",16:"DST"}
NICK={"Cardinals":"ARI","Falcons":"ATL","Ravens":"BAL","Bills":"BUF","Panthers":"CAR","Bears":"CHI","Bengals":"CIN","Browns":"CLE","Cowboys":"DAL","Broncos":"DEN","Lions":"DET","Packers":"GB","Texans":"HOU","Colts":"IND","Jaguars":"JAX","Chiefs":"KC","Raiders":"LV","Chargers":"LAC","Rams":"LAR","Dolphins":"MIA","Vikings":"MIN","Patriots":"NE","Saints":"NO","Giants":"NYG","Jets":"NYJ","Eagles":"PHI","Steelers":"PIT","49ers":"SF","Seahawks":"SEA","Buccaneers":"TB","Titans":"TEN","Commanders":"WAS"}
ALIAS={"LA":"LAR","STL":"LAR","SD":"LAC","OAK":"LV","WSH":"WAS","JAC":"JAX"}
def std(t): return ALIAS.get(t,t)

# ---------- live pulls ----------
def pull_ffc():
    out={}
    for fmt in ("ppr","2qb"):
        try:
            d=getj(f"https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams=12&year={SEASON}")
            for p in d.get("players",[]):
                k=norm(p["name"]); e=out.setdefault(k,{"name":p["name"],"pos":p["position"],"team":std(p.get("team",""))})
                e["adp" if fmt=="ppr" else "adpSf"]=round(float(p["adp"]),1)
        except Exception as ex: log("  FFC",fmt,"fail:",ex)
    for v in out.values(): v.setdefault("adp",v.get("adpSf",999)); v.setdefault("adpSf",v.get("adp",999))
    log(f"  FFC: {len(out)} players")
    return out

def pull_sleeper_players():
    out={}
    try:
        d=getj("https://api.sleeper.app/v1/players/nfl", timeout=90)
        for p in d.values():
            if p.get("position") not in ("QB","RB","WR","TE","K"): continue
            nm=p.get("full_name") or ""
            if not nm: continue
            out[norm(nm)]={"age":p.get("age"),"team":p.get("team"),"inj":p.get("injury_status"),"depth":p.get("depth_chart_order")}
        log(f"  Sleeper players: {len(out)}")
    except Exception as ex: log("  Sleeper players fail:",ex)
    return out

def pull_sleeper_weekly():
    out={}
    posq="&".join(f"position[]={p}" for p in ["QB","RB","WR","TE","K","DEF"])
    for w in range(1,19):
        try:
            d=getj(f"https://api.sleeper.com/projections/nfl/{SEASON}/{w}?season_type=regular&{posq}&order_by=pts_ppr")
            for it in d:
                pl=it.get("player",{}); pos=pl.get("position"); pos="DST" if pos=="DEF" else pos
                if pos not in ("QB","RB","WR","TE","K","DST"): continue
                key=(it.get("player_id") if pos=="DST" else norm((pl.get("first_name","")+" "+pl.get("last_name","")).strip()))
                if not key: continue
                pts=(it.get("stats") or {}).get("pts_ppr")
                e=out.setdefault((pos,key),{"wk":[0.0]*19})
                if pts is not None: e["wk"][w]=round(float(pts),2)
            time.sleep(0.2)
        except Exception as ex: log("  Sleeper wk",w,"fail:",ex)
    for e in out.values(): e["season"]=round(sum(e["wk"][1:19]),1); e["wk"]=e["wk"][1:19]
    log(f"  Sleeper weekly: {len(out)}")
    return out

def _espn_call(filt):
    hdr={"X-Fantasy-Filter":json.dumps({"players":filt}),"User-Agent":"Mozilla/5.0","Accept":"application/json"}
    return getj(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leaguedefaults/3?view=kona_player_info", hdr)

def pull_espn():
    proj={}; adp={}
    calls=[{"limit":350,"sortDraftRanks":{"sortPriority":100,"sortAsc":True,"value":"PPR"}},
           {"limit":80,"filterSlotIds":{"value":[17,16]},"sortPercOwned":{"sortPriority":1,"sortAsc":False}}]
    for filt in calls:
        try:
            d=_espn_call(filt)
            for x in d.get("players",[]):
                p=x["player"]; pos=EPOS.get(p.get("defaultPositionId"))
                if not pos: continue
                key=NICK.get(p["fullName"].split(" D/ST")[0]) if pos=="DST" else norm(p["fullName"])
                if not key: continue
                st=[s for s in p.get("stats",[]) if s.get("seasonId")==int(SEASON) and s.get("statSourceId")==1]
                seas=next((s["appliedTotal"] for s in st if s.get("scoringPeriodId")==0),None)
                wk=[0.0]*19
                for s in st:
                    sp=s.get("scoringPeriodId")
                    if isinstance(sp,int) and 1<=sp<=18: wk[sp]=round(s.get("appliedTotal",0),2)
                if seas is not None: proj[(pos,key)]={"season":round(seas,1),"wk":wk[1:19]}
                o=(p.get("ownership") or {}).get("averageDraftPosition")
                sfr=(p.get("draftRanksByRankType") or {}).get("SUPERFLEX",{}).get("rank")
                adp[(pos,key)]={"s":round(o,1) if o and o>0 else None,"sf":sfr}
        except Exception as ex: log("  ESPN fail:",ex)
    log(f"  ESPN: {len(proj)} proj / {len(adp)} adp")
    return proj, adp

def pull_yahoo():
    out={}
    for start in range(0,340,25):
        try:
            d=getj(f"https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2/game/nfl/players;start={start};count=25;sort=AR;out=draft_analysis?format=json")
            pl=d["fantasy_content"]["game"][1]["players"]; n=pl.get("count",0)
            for i in range(n):
                arr=pl[str(i)]["player"]; attrs=arr[0]
                name=next((a["name"]["full"] for a in attrs if isinstance(a,dict) and "name" in a),None)
                da=next((x["draft_analysis"] for x in arr[1:] if isinstance(x,dict) and "draft_analysis" in x),None)
                ap=None
                if da: ap=next((float(z["average_pick"]) for z in da if "average_pick" in z and z["average_pick"] not in ("-","0.0")),None)
                if name and ap: out[norm(name)]=round(ap,1)
            time.sleep(0.2)
            if n<25: break
        except Exception as ex: log("  Yahoo",start,"fail:",ex); break
    log(f"  Yahoo: {len(out)}")
    return out

def pull_sleeper_adp():
    # Sleeper ADP lives in the PUBLIC season-projections payload (no auth).
    # stats.adp_ppr (1QB), adp_2qb (superflex), adp_dynasty_ppr (dynasty). 999 = undrafted.
    out={}
    try:
        d=getj(f"https://api.sleeper.com/projections/nfl/{SEASON}?season_type=regular", timeout=60)
        for it in d:
            st=it.get("stats") or {}
            ppr=st.get("adp_ppr")
            if ppr is None or ppr>=900: continue
            pl=it.get("player") or {}
            nm=((pl.get("first_name") or "")+" "+(pl.get("last_name") or "")).strip()
            if not nm: continue
            e={"ppr":round(float(ppr),1)}
            for src,dst in (("adp_2qb","sf"),("adp_dynasty_ppr","dyn"),("adp_dynasty_2qb","dynSf")):
                v=st.get(src)
                if v is not None and v<900: e[dst]=round(float(v),1)
            out[norm(nm)]=e
        log(f"  Sleeper ADP: {len(out)}")
    except Exception as ex: log("  Sleeper ADP fail:",ex)
    return out

# ---------- build ----------
def build_stats(pos, C, templ):
    if pos in ("K","DST"): return {"p": round(C,1)}
    t=templ.get(pos,{}); intf=t.get("int_ppr",0.0)
    posP = C/(1+intf) if (1+intf)>0 else C
    stats={}
    for k,frac in t.items():
        if k=="int_ppr" or frac<=0: continue
        stats[k]=round(posP*frac/SCORING[k],1)
    if intf<0: stats["int"]=round((intf*posP)/SCORING["int"],1)
    stats["fl"]= 2 if pos=="QB" else 1
    return stats

def dist_for(pos, rank, FACT, BANDW):
    d=FACT.get(pos)
    if not d: return None
    b=max(1,int(-(-rank//BANDW)))  # ceil
    while b>0 and str(b) not in d: b-=1
    return d.get(str(b)) or d.get(min(d,key=lambda x:int(x)))

def byes_from_sched(SCHED):
    out={}
    for t,arr in SCHED.items():
        for w,opp in enumerate(arr):
            if opp is None: out[t]=w+1; break
    return out

def build_data():
    base=json.load(open(os.path.join(HERE,"base.json"),encoding="utf-8"))
    FACT=base["DIST_FACTORS"]; BANDW=base.get("BANDW",8); TEMPL=base["STAT_TEMPLATE"]
    BYE=byes_from_sched(base["SCHED"])
    log("Pulling live sources…")
    ffc=pull_ffc(); slp=pull_sleeper_players(); slw=pull_sleeper_weekly()
    espn_proj,espn_adp=pull_espn(); yah=pull_yahoo(); sadp=pull_sleeper_adp()
    if not ffc: raise RuntimeError("FFC returned nothing — aborting this cycle")

    # consensus per (pos,key)
    def cons(pos,key):
        e=espn_proj.get((pos,key)); s=slw.get((pos,key))
        seas=[v["season"] for v in (e,s) if v and v.get("season")]
        if not seas: return None
        wk=[]
        for i in range(18):
            vs=[v["wk"][i] for v in (e,s) if v and v.get("wk") and v["wk"][i]>0]
            wk.append(round(sum(vs)/len(vs),2) if vs else 0.0)
        return {"s":round(sum(seas)/len(seas),1),"e":(e["season"] if e else None),"k":(s["season"] if s else None),"wk":wk}

    # positional ranks by FFC ppr adp
    bypos={}
    for k,v in ffc.items(): bypos.setdefault(v["pos"],[]).append((v["adp"],k))
    rank={}
    for pos,lst in bypos.items():
        for i,(_,k) in enumerate(sorted(lst)): rank[(pos,k)]=i+1

    players=[]; pid=0
    for k,v in sorted(ffc.items(), key=lambda kv: kv[1]["adp"]):
        pos=v["pos"]; team=std(v.get("team") or (slp.get(k,{}) or {}).get("team") or "FA")
        c = cons(pos,k) if pos in ("QB","RB","WR","TE") else cons(pos, team if pos=="DST" else k)
        p={"id":pid,"name":v["name"],"pos":pos,"team":team,"bye":BYE.get(team,0),
           "adp":v["adp"],"adpSf":v["adpSf"],"override":None,
           "age":(slp.get(k,{}) or {}).get("age") or 27}
        if pos in ("QB","RB","WR","TE"):
            season = c["s"] if c else None
            if season: p["stats"]=build_stats(pos,season,TEMPL); p["cons"]=({"s":c["s"],"e":c["e"],"k":c["k"]}); p["wk"]=c["wk"]
            else: p["stats"]={"p":80}   # fallback for a skill player with no consensus
            fa=dist_for(pos, rank.get((pos,k),99), FACT, BANDW)
            if fa: p["dist"]={"f":fa["f"],"c":fa["c"],"bust":fa["bust"],"boom":fa["boom"]}
        else:  # K / DST
            season = c["s"] if c else None
            p["stats"]={"p": round(season,1) if season else (140 if pos=="DST" else 130)}
            if c: p["cons"]={"s":c["s"],"e":c["e"],"k":c["k"]}; p["wk"]=c["wk"]
        # injuries / depth
        io=slp.get(k)
        if io:
            if io.get("inj"): p["inj"]=io["inj"]
            if io.get("depth") is not None: p["depth"]=io["depth"]
        # extra ADP sources
        ea=espn_adp.get((pos, team if pos=="DST" else k))
        if ea:
            if ea.get("s"): p["adpE"]=ea["s"]
            if ea.get("sf"): p["adpEsf"]=ea["sf"]
        ya=yah.get(k)
        if ya is not None: p["adpY"]=ya
        sa=sadp.get(k)
        if sa:
            p["adpS"]=sa["ppr"]
            if sa.get("sf"): p["adpSsf"]=sa["sf"]
            if sa.get("dyn"): p["adpSdyn"]=sa["dyn"]
        players.append(p); pid+=1

    out={"PLAYERS":players,"BACKTEST":base["BACKTEST"],"SLOTVAL":base["SLOTVAL"],"OPENING":base["OPENING"],
         "DVP":base["DVP"],"SCHED":base["SCHED"],"CALIB":base["CALIB"],
         "META":{"updated":time.strftime("%Y-%m-%d %H:%M"),"sources":"FFC+ESPN+Sleeper+Yahoo (live) · nflverse (historical)"}}
    log(f"Built {len(players)} players "
        f"({sum(1 for p in players if p.get('cons'))} w/ consensus, "
        f"{sum(1 for p in players if p.get('inj'))} injuries)")
    return out

def write_data_js(data):
    path=os.path.join(HERE,"data.js")
    open(path,"w",encoding="utf-8").write("window.__FFDATA__="+json.dumps(data,separators=(",",":"))+";")
    return path

def git_push():
    if not GIT_PUSH: return
    try:
        st=subprocess.run(["git","-C",HERE,"status","--porcelain","data.js"],capture_output=True,text=True)
        if not st.stdout.strip():
            log("git: no change, nothing to push"); return
        subprocess.run(["git","-C",HERE,"add","data.js"],check=True)
        subprocess.run(["git","-C",HERE,"commit","-m",f"data update {time.strftime('%Y-%m-%d %H:%M')}"],check=True)
        subprocess.run(["git","-C",HERE,"push"],check=True)
        log("git: pushed OK - your live site will update in ~1 min")
    except Exception as ex:
        log("git push failed (is the repo set up? see README):",ex)

def cycle():
    try:
        data=build_data(); write_data_js(data); git_push()
    except Exception:
        log("cycle error:\n"+traceback.format_exc())

if __name__=="__main__":
    once = "--once" in sys.argv
    log(f"Draft War Room refresher starting (every {REFRESH_HOURS}h). Ctrl+C to stop.")
    while True:
        cycle()
        if once: break
        log(f"Sleeping {REFRESH_HOURS}h…")
        try: time.sleep(REFRESH_HOURS*3600)
        except KeyboardInterrupt: log("stopped."); break
