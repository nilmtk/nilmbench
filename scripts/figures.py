#!/usr/bin/env python3
"""
Generate the deck's data figures from REAL UK-DALE house 1 (streamed from CEDA,
no full download) plus the Hart edge-detection and efficiency plots.

Outputs (to slides/figs/):
    decomposition.png      mains -> fridge / washer / dishwasher
    decomposition_agg.png  same frame, appliances greyed (slide-2 build step 1)
    sig_fridge2.png        per-appliance signature (periodic)
    sig_washer2.png        per-appliance signature (multi-stage)
    sig_dishwasher2.png    per-appliance signature (high-power bursts)
    hart_edge.png          edge detection (ΔP) — illustrative
    efficiency.png         cross-building MAE vs parameters (paper numbers)
    generalization_trend.png

Deps:  pip install pandas matplotlib remotezip
Appliance icons (slides/figs/ic-*.png) are overlaid if present
(run diagrams.py + render.mjs first); otherwise they are skipped.

UK-DALE is CC-BY (Kelly & Knottenbelt, 2015). To use a different house/day,
edit HOUSE / WINDOW_START / pick_day below, or point read_local() at a local
ukdale.h5 instead of streaming.
"""
import os, io, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt, matplotlib.dates as mdates
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
import matplotlib.image as mpimg

plt.rcParams.update({"font.family": "sans-serif",
                     "font.sans-serif": ["Helvetica Neue", "Arial", "DejaVu Sans"],
                     "axes.edgecolor": "#c7ccd3"})
INK="#1b2330"; ACC="#c44536"; NAVY="#1b3b6f"; BLUE="#3b6ea5"; TEAL="#2a9d8f"; AMBER="#c98a2b"; PURP="#8a6fae"
FIGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "slides", "figs")
UKDALE = "https://data.ceda.ac.uk/edc/d1/7d78f943-f9fe-413b-af52-1816f9d968b0/data/version_0/ukdale.zip"
HOUSE = 1
# house-1 channel map (see house_1/labels.dat); these are the paper's appliances
CH = {"mains": 1, "fridge": 12, "washer": 5, "dishwasher": 6, "kettle": 10, "microwave": 13, "tv": 7}
WINDOW_START = 1355529600          # 2012-12-15 (after fridge/microwave channels came online)
WINDOW_DAYS = 24

def _icon(name):
    p = os.path.join(FIGS, f"ic-{name}.png")
    return mpimg.imread(p) if os.path.exists(p) else None

def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(True, color="#eef0f3", lw=0.8, zorder=0)
    ax.tick_params(colors="#5a626e", labelsize=10); ax.margins(x=0)

def stream_ukdale():
    """Stream the needed channels for a ~24-day window (downloads only that prefix)."""
    from remotezip import RemoteZip
    end = WINDOW_START + WINDOW_DAYS * 86400
    def read(z, ch):
        ts, pw = [], []
        with z.open(f"house_{HOUSE}/channel_{ch}.dat") as f:
            for line in io.TextIOWrapper(f):
                a = line.split()
                if len(a) < 2: continue
                t = int(a[0])
                if t < WINDOW_START: continue
                if t > end: break
                ts.append(t); pw.append(float(a[1]))
        return pd.Series(pw, index=pd.to_datetime(ts, unit="s")).resample("1min").mean().fillna(0)
    with RemoteZip(UKDALE) as z:
        return pd.DataFrame({k: read(z, c) for k, c in CH.items()}).fillna(0)

def pick_day(df):
    df = df.copy(); df["day"] = df.index.date; g = df.groupby("day")
    ok = (g["fridge"].apply(lambda s: (s > 40).sum()) > 200) & \
         (g["washer"].apply(lambda s: (s > 20).sum()) > 8) & \
         (g["dishwasher"].apply(lambda s: (s > 50).sum()) > 5)
    score = (g["washer"].apply(lambda s: (s > 20).sum()) +
             g["dishwasher"].apply(lambda s: (s > 50).sum()))[ok]
    return str(score.sort_values(ascending=False).index[0])

def _decomp_fig(d, series, fname, dim_appliances=False):
    """Render the 4-panel disaggregation figure. With dim_appliances=True the
    three appliance panels are greyed (used as the 'aggregate-first' build step
    on slide 2); the layout is identical so the two frames swap without jumping."""
    FADE = "#c3c8d0"
    fig, axes = plt.subplots(4, 1, figsize=(11, 5.8), dpi=200, sharex=True,
                             gridspec_kw={"height_ratios": [2.2,1,1,1], "hspace": 0.34})
    for i,(ax,(c,name,col,ic)) in enumerate(zip(axes, series)):
        faded = dim_appliances and i > 0
        pcol = FADE if faded else col
        ax.fill_between(d.index, d[c], color=pcol, alpha=0.07 if faded else 0.14, zorder=2)
        ax.plot(d.index, d[c], color=pcol, lw=1.0 if faded else 1.3, zorder=3)
        img = _icon(ic)
        if img is not None:
            ax.add_artist(AnnotationBbox(OffsetImage(img, zoom=0.05, alpha=0.35 if faded else 1.0),
                          (0.012, 0.84), xycoords="axes fraction", frameon=False, box_alignment=(0, 0.5)))
        ax.text(0.042 if img is not None else 0.012, 0.84, name, transform=ax.transAxes,
                color=pcol, fontsize=12, fontweight="bold", va="center", alpha=0.55 if faded else 1.0)
        _style(ax); ax.set_ylim(0, max(d[c].max()*1.3, 30))
    axes[0].set_ylabel("power (W)", color=INK, fontsize=11)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[-1].xaxis.set_major_locator(mdates.HourLocator(interval=3))
    fig.savefig(os.path.join(FIGS, fname), bbox_inches="tight", facecolor="white"); plt.close(fig)

def decomposition(df, day):
    d = df.loc[day]
    series = [("mains","Aggregate mains",INK,"meter"),("fridge","Fridge",BLUE,"fridge"),
              ("washer","Washing machine",ACC,"washer"),("dishwasher","Dishwasher",TEAL,"dishwasher")]
    _decomp_fig(d, series, "decomposition_agg.png", dim_appliances=True)   # build step 1: aggregate only
    _decomp_fig(d, series, "decomposition.png",     dim_appliances=False)  # build step 2: full disaggregation

def signature(df, col, color, icon, fname, win, desc, headroom=1.7, annotate=None):
    d = df.loc[win[0]:win[1], col]
    fig, ax = plt.subplots(figsize=(8.8, 3.3), dpi=200)
    ax.fill_between(d.index, d.values, color=color, alpha=0.15, zorder=2)
    ax.plot(d.index, d.values, color=color, lw=1.6, zorder=3)
    _style(ax); ax.set_ylim(0, max(d.max()*headroom, 10)); ax.set_ylabel("power (W)", color=INK, fontsize=11)
    ax.set_xlabel("time", color=INK, fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    if annotate:
        for x, y, txt, c in annotate:
            ax.annotate(txt, xy=(x, y), xytext=(8, 14), textcoords="offset points",
                        fontsize=10, color=c, fontweight="bold",
                        arrowprops=dict(arrowstyle="->", color=c, lw=1.1))
    img = _icon(icon)
    x0 = 0.018
    if img is not None:
        ax.add_artist(AnnotationBbox(OffsetImage(img, zoom=0.07), (x0, 0.9),
                      xycoords="axes fraction", frameon=False, box_alignment=(0, 0.5))); x0 = 0.058
    ax.text(x0, 0.9, desc, transform=ax.transAxes, fontsize=12.5, color=INK, fontweight="bold", va="center")
    fig.savefig(os.path.join(FIGS, fname), bbox_inches="tight", facecolor="white"); plt.close(fig)

def _fridge_annotations(df, win):
    d = df.loc[win[0]:win[1], "fridge"]
    if d.empty:
        return []
    # Label a typical compressor plateau and the largest spike as defrost.
    base = d.quantile(0.25)
    comp = d[(d > base + 15) & (d < base + 130)]
    ann = []
    if len(comp) > 5:
        cx = comp.index[len(comp) // 3]
        ann.append((cx, float(d.asof(cx)), "compressor", BLUE))
    peak = d.idxmax()
    if float(d.max()) > base + 80:
        ann.append((peak, float(d.loc[peak]), "defrost", ACC))
    return ann

def _at_frac(d, t0, span, frac):
    ts = t0 + span * frac
    idx = d.index.get_indexer([ts], method="nearest")[0]
    t = d.index[idx]
    return t, float(d.iloc[idx])

def _washer_annotations(df, win):
    d = df.loc[win[0]:win[1], "washer"]
    if d.empty:
        return []
    on = d[d > 20]
    if len(on) < 10:
        return []
    t0, t1 = on.index.min(), on.index.max()
    span = t1 - t0
    out = []
    for frac, label, col in [(0.12, "heat", ACC), (0.45, "agitate", NAVY), (0.82, "spin", TEAL)]:
        t, v = _at_frac(d, t0, span, frac)
        out.append((t, v, label, col))
    return out

def hart():
    t = np.linspace(0, 100, 2000); p = np.full_like(t, 60.0)
    for a, b, v in [(12,34,120),(42,47,1900),(58,66,1000),(74,96,120)]:
        p[(t >= a) & (t < b)] += v
    p += np.random.RandomState(1).randn(len(t)) * 4
    fig, ax = plt.subplots(figsize=(9.4, 3.5), dpi=200); ax.plot(t, p, color=INK, lw=1.4); ax.set_ylim(-150, 2450)
    for x, dy, txt, col in [(12,120,"+120 W",BLUE),(42,1900,"+1900 W",ACC),(58,1000,"+1000 W",TEAL)]:
        ax.annotate("", xy=(x,60+dy), xytext=(x,60), arrowprops=dict(arrowstyle="->", color=col, lw=1.5))
        ax.text(x+1.4, 60+dy*0.5, txt, color=col, fontsize=10.5, va="center", fontweight="bold")
    for ic, x, y in [("fridge",20,360),("kettle",44,2200),("microwave",62,1280)]:
        img = _icon(ic)
        if img is not None: ax.add_artist(AnnotationBbox(OffsetImage(img, zoom=0.085), (x, y), frameon=False))
    ax.set_xlabel("time", color=INK, fontsize=12); ax.set_ylabel("aggregate power (W)", color=INK, fontsize=12)
    ax.tick_params(colors="#5a626e"); ax.grid(True, color="#eef0f3", lw=0.8); ax.spines[["top","right"]].set_visible(False)
    ax.text(0.5, 1.05, "detect ON/OFF edges ($\\Delta$P), then attribute each step to an appliance",
            transform=ax.transAxes, ha="center", fontsize=11.5, color="#5a626e")
    fig.savefig(os.path.join(FIGS, "hart_edge.png"), bbox_inches="tight", facecolor="white"); plt.close(fig)

def efficiency():
    # cross-building (T2, UK-DALE) mean MAE vs parameters (K) — from the paper
    d = [("NILMFormer",15.42,383),("Seq2Point",18.43,3620),("ResNet",20.16,669),
         ("TCN",20.84,69),("RNN Att. Cl.",21.81,4940),("DAE",22.43,832),("Seq2Seq",22.53,447),
         ("ResNet Cl.",22.61,4200),("BERT",23.74,803),("WindowGRU",23.83,427),("Reformer",26.82,943),
         ("RNN Att.",27.20,1330),("ConvLSTM",27.44,483),("RNN",33.78,1270)]
    hi = {"TCN","NILMFormer"}; GRY="#8b929c"
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=200)
    for n,m,p in d:
        if n not in hi: ax.scatter(p,m,s=70,color=GRY,alpha=0.85,zorder=3,edgecolor="white",linewidth=0.8)
    for n,m,p in d:
        if n in hi: ax.scatter(p,m,s=160,color=ACC,zorder=5,edgecolor="white",linewidth=1.2)
    lab={"NILMFormer":(10,8),"TCN":(12,-4),"Seq2Point":(-6,12),"RNN":(8,4),"RNN Att. Cl.":(8,6),"ConvLSTM":(-8,-16)}
    for n,m,p in d:
        if n in lab:
            dx,dy=lab[n]; c=ACC if n in hi else INK; w="bold" if n in hi else "normal"
            ax.annotate(n,(p,m),textcoords="offset points",xytext=(dx,dy),fontsize=11.5,color=c,fontweight=w,ha="left" if dx>0 else "right")
    ax.set_xscale("log"); ax.set_xlabel("Parameters  (log scale, lower is lighter)", fontsize=13, color=INK)
    ax.set_ylabel("Cross-building MAE  (lower is better)", fontsize=13, color=INK)
    ax.set_xticks([1e2,1e3,1e4]); ax.set_xticklabels(["100K","1M","10M"]); ax.tick_params(colors="#5a626e")
    ax.grid(True, which="major", color="#e9ebef", lw=0.9, zorder=0); ax.spines[["top","right"]].set_visible(False)
    ax.annotate("better:\naccurate & light",(95,15.2),fontsize=11,color=GRY,style="italic",ha="left",va="center")
    fig.tight_layout(); fig.savefig(os.path.join(FIGS, "efficiency.png"), bbox_inches="tight", facecolor="white"); plt.close(fig)

def generalization_trend():
    # Reported table means from the project site / paper result tables.
    tasks = ["T1\nsame building", "T2\nnew building", "T3\nnew dataset"]
    values = [32.86, 34.08, 55.46]
    colors = [BLUE, NAVY, ACC]
    fig, ax = plt.subplots(figsize=(9.2, 4.4), dpi=200)
    x = np.arange(len(tasks))
    bars = ax.bar(x, values, width=0.58, color=colors, alpha=0.95, zorder=3)
    ax.plot(x, values, color=INK, lw=1.6, marker="o", ms=5, zorder=4)
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.2, f"{v:.1f} W",
                ha="center", va="bottom", fontsize=13, color=INK, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(tasks, fontsize=12, color=INK)
    ax.set_ylabel("Mean MAE (W, lower is better)", fontsize=12.5, color=INK)
    ax.set_ylim(0, 66)
    ax.grid(True, axis="y", color="#e9ebef", lw=0.9, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="y", colors="#5a626e")
    ax.text(0.02, 0.96, "Mean across reported task table entries",
            transform=ax.transAxes, ha="left", va="top", fontsize=11, color="#8b929c")
    ax.annotate("domain shift hurts most", xy=(2, values[2]), xytext=(1.25, 61),
                arrowprops=dict(arrowstyle="->", color=ACC, lw=1.5),
                fontsize=12, color=ACC, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "generalization_trend.png"), bbox_inches="tight", facecolor="white")
    plt.close(fig)

def main():
    print("streaming UK-DALE house", HOUSE, "(only the chosen window is fetched)…")
    df = stream_ukdale()
    day = pick_day(df); print("using day", day)
    decomposition(df, day)
    fwin = (day+" 06:00", day+" 10:00")
    signature(df,"fridge",BLUE,"fridge","sig_fridge2.png",fwin,"Fridge · UK-DALE house 1",1.85)
    w = df.loc[day,"washer"]; on = w[w>20]
    wwin = (on.index.min()-pd.Timedelta("25min"), on.index.max()+pd.Timedelta("25min"))
    signature(df,"washer",ACC,"washer","sig_washer2.png",wwin,"Washing machine · UK-DALE house 1",1.5)
    dw = df.loc[day,"dishwasher"]; on = dw[dw>50]
    signature(df,"dishwasher",TEAL,"dishwasher","sig_dishwasher2.png",(on.index.min()-pd.Timedelta("15min"),on.index.max()+pd.Timedelta("15min")),"Heating elements — high-power bursts",1.45)
    hart(); efficiency(); generalization_trend()
    print("done ->", os.path.normpath(FIGS))

if __name__ == "__main__":
    main()
