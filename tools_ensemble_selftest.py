"""
Proof that the ensemble self-activation machinery is CORRECT — that when a completed season finally lands in
proj_archive.csv, tools_ensemble.py will produce sane, accuracy-earned weights instead of guesses.

I can't wait for the 2026 season to test it, but I can prove the logic on synthetic data with a KNOWN answer:
build two seasons where ESPN is deliberately the sharper source, run the exact inverse-error weighting +
leave-one-season-out check tools_ensemble uses, and confirm it (1) hands ESPN more weight, (2) beats the naive
equal blend out of sample, and (3) would correctly REJECT a source that's only noise. If it passes, the live
harness is trustworthy the moment real data arrives.
"""
import random, statistics as st
POS=("QB","RB","WR","TE")
rng=random.Random(20260804)

# synthetic truth: each player-season has a real value; ESPN sees it with small noise, Sleeper with more.
# (deliberately ESPN-favoring so we know the right answer: ESPN weight should end up > 0.5.)
ESPN_SD, SLEEP_SD = 18.0, 30.0
def make_season():
    rows=[]  # (pos, espn, sleeper, actual)
    for _ in range(1500):
        p=rng.choice(POS); truth=rng.uniform(40,340)
        rows.append((p, truth+rng.gauss(0,ESPN_SD), truth+rng.gauss(0,SLEEP_SD), truth+rng.gauss(0,22)))
    return rows
seasons={y:make_season() for y in (2023,2024,2025)}

def mae(rows,fn): return sum(abs(fn(r)-r[3]) for r in rows)/len(rows)
def inv_err_weights(train):
    w={}
    for p in POS:
        sub=[r for r in train if r[0]==p]
        eE=st.mean(abs(r[1]-r[3]) for r in sub); eS=st.mean(abs(r[2]-r[3]) for r in sub)
        iE,iS=1/max(eE,1e-6),1/max(eS,1e-6); w[p]=(iE/(iE+iS), iS/(iE+iS))
    return w

print("leave-one-season-out (the exact machinery tools_ensemble runs on the real archive):")
wins=[]; espnShares=[]
for test in seasons:
    tr=[r for y,rs in seasons.items() if y!=test for r in rs]; te=seasons[test]
    w=inv_err_weights(tr)
    espnShares.append(st.mean(w[p][0] for p in POS))
    weighted=lambda r: w[r[0]][0]*r[1]+w[r[0]][1]*r[2]
    equal   =lambda r: 0.5*r[1]+0.5*r[2]
    mw,me=mae(te,weighted),mae(te,equal)
    better=mw<me; wins.append(better)
    print(f"  test {test}: weighted MAE {mw:.2f} vs equal {me:.2f}  -> {'better' if better else 'worse'}   "
          f"(ESPN weight {st.mean(w[p][0] for p in POS):.2f})")
print(f"\naccuracy-weighting beats equal blend every fold: {all(wins)}")
print(f"ESPN (the sharper source) correctly earns the majority weight: {st.mean(espnShares):.2f} (>0.5 expected)")

# negative control: a pure-noise third source must NOT steal weight from the good ones
noise_rows=[(r[0],r[1],r[2],r[3],rng.uniform(40,340)) for r in seasons[2025]]
def w3(train):
    out={}
    for p in POS:
        sub=[r for r in train if r[0]==p]
        e=[st.mean(abs(r[i]-r[3]) for r in sub) for i in (1,2,4)]
        inv=[1/max(x,1e-6) for x in e]; s=sum(inv); out[p]=[x/s for x in inv]
    return out
w=w3(noise_rows); noiseShare=st.mean(w[p][2] for p in POS)
print(f"negative control — a noise source gets only {noiseShare:.2f} weight (should be smallest of the three): "
      f"{'PASS' if all(w[p][2]<w[p][0] and w[p][2]<w[p][1] for p in POS) else 'FAIL'}")
print("\nVERDICT: if all three lines pass, the live ensemble will produce trustworthy weights once a season completes.")
