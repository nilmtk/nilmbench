#!/usr/bin/env python3
"""
Generate the deck's vector figures (appliance icons, model-architecture
diagrams, the model taxonomy tree, and the T1/T2/T3 task diagrams) as a single
HTML file. Each figure lives in a <div data-fig id="..."> so that render.mjs can
screenshot it to slides/figs/<id>.png.

Usage:
    python scripts/diagrams.py            # writes scripts/_diagrams.html
    node   scripts/render.mjs             # -> slides/figs/<id>.png  (needs a browser)

Colours follow the deck's design system (ink / navy / warm-red accent).
"""
import os

INK = "#1b2330"; NAVY = "#1b3b6f"; ACC = "#c44536"
BLUE = "#3b6ea5"; TEAL = "#2a9d8f"; AMBER = "#c98a2b"; PURP = "#8a6fae"
GRY = "#8b929c"; LINE = "#d8dbe0"; LF = "#eef2f8"; MUT = "#5a626e"

# ---- appliance + meter icons (stroke = currentColor so they recolour) --------
ICONS = {
 "meter": '<g fill="none" stroke="C" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="2.5" width="14" height="19" rx="2.2"/><rect x="8" y="6" width="8" height="4.5" rx="1"/><circle cx="9.5" cy="15.5" r="1.2"/><circle cx="14.5" cy="15.5" r="1.2"/></g>',
 "fridge": '<g fill="none" stroke="C" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2.5" width="12" height="19" rx="2.2"/><line x1="6" y1="9.5" x2="18" y2="9.5"/><line x1="9" y1="5" x2="9" y2="7"/><line x1="9" y1="12.5" x2="9" y2="15"/></g>',
 "washer": '<g fill="none" stroke="C" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="4.5" y="3" width="15" height="18" rx="2.2"/><circle cx="12" cy="13.5" r="4.8"/><circle cx="12" cy="13.5" r="1.4"/><line x1="7" y1="6.2" x2="9" y2="6.2"/></g>',
 "dishwasher": '<g fill="none" stroke="C" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="3" width="14" height="18" rx="2"/><line x1="5" y1="7.5" x2="19" y2="7.5"/><line x1="8" y1="5.2" x2="11" y2="5.2"/><rect x="8.5" y="10" width="7" height="8" rx="1"/></g>',
 "kettle": '<g fill="none" stroke="C" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M7 11 h9 v6.5 a2.5 2.5 0 0 1 -2.5 2.5 h-4 a2.5 2.5 0 0 1 -2.5 -2.5 z"/><path d="M16 12 l3 -2.5"/><path d="M8.5 11 a3.2 3.2 0 0 1 6 0"/><line x1="11.5" y1="7.6" x2="11.5" y2="8.6"/></g>',
 "microwave": '<g fill="none" stroke="C" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="6" width="19" height="12" rx="2"/><rect x="5" y="8.5" width="9.5" height="7" rx="1"/><circle cx="18" cy="10" r="0.9" fill="C" stroke="none"/><line x1="17" y1="13" x2="19" y2="13"/></g>',
 "tv": '<g fill="none" stroke="C" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="5" width="19" height="12.5" rx="2"/><line x1="8.5" y1="20.5" x2="15.5" y2="20.5"/><line x1="12" y1="17.5" x2="12" y2="20.5"/></g>',
}
# Icon colours used in the deck. (Used both as standalone PNGs and inside diagrams.)
ICON_COLOR = {"meter": INK, "fridge": BLUE, "washer": ACC, "dishwasher": TEAL,
              "kettle": ACC, "kettle-amber": AMBER, "microwave": TEAL, "tv": PURP}

def icon_symbol(name):
    base = name.replace("-amber", "")
    return f'<symbol id="ic-{name}" viewBox="0 0 24 24">{ICONS[base].replace("C","currentColor")}</symbol>'

SYMBOLS = "".join(icon_symbol(n) for n in
                  ["meter","fridge","washer","dishwasher","kettle","microwave","tv"])
ARROW = ('<marker id="ah" markerWidth="9" markerHeight="9" refX="6.5" refY="3" orient="auto">'
         '<path d="M0,0 L6.5,3 L0,6 Z" fill="#8b929c"/></marker>'
         '<linearGradient id="gC" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5b86b8"/><stop offset="1" stop-color="#36567f"/></linearGradient>'
         '<linearGradient id="gD" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e7ecf3"/><stop offset="1" stop-color="#cfd8e6"/></linearGradient>')

def use(name, x, y, s, color):
    return f'<use href="#ic-{name.replace("-amber","")}" x="{x}" y="{y}" width="{s}" height="{s}" color="{color}"/>'

def block3d(x, y, w, h, d, grad, stroke):
    return (f'<polygon points="{x},{y} {x+w},{y} {x+w+d},{y-d} {x+d},{y-d}" fill="{grad}" opacity="0.85" stroke="{stroke}" stroke-width="1"/>'
            f'<polygon points="{x+w},{y} {x+w+d},{y-d} {x+w+d},{y+h-d} {x+w},{y+h}" fill="{grad}" opacity="0.6" stroke="{stroke}" stroke-width="1"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{grad}" stroke="{stroke}" stroke-width="1.1"/>')

def arr(a, b, y):
    return f'<line x1="{a}" y1="{y}" x2="{b}" y2="{y}" stroke="{GRY}" stroke-width="1.7" marker-end="url(#ah)"/>'

# ---- Seq2Point (modern CNN) --------------------------------------------------
def seq2point():
    s=f'<svg width="780" height="300" viewBox="0 0 780 300" font-family="Work Sans,sans-serif"><defs>{ARROW}{SYMBOLS}</defs>'
    s+=f'<text x="390" y="22" text-anchor="middle" font-size="14" fill="{MUT}">aggregate <tspan fill="{INK}" font-weight="700">window</tspan> (L) &#8594; one <tspan fill="{INK}" font-weight="700">appliance</tspan> at the centre</text>'
    # --- input: a window of the aggregate signal ---
    s+=use("meter",6,130,26,INK)
    ix,iy,iw,ih=44,98,100,92
    s+=f'<rect x="{ix}" y="{iy}" width="{iw}" height="{ih}" rx="6" fill="#f6f7f9" stroke="{LINE}"/>'
    wf="52,176 63,172 74,142 85,176 95,156 105,124 116,170 127,166 136,168"
    s+=f'<polygon points="52,184 {wf} 136,184" fill="{NAVY}" opacity="0.07"/>'
    s+=f'<polyline points="{wf}" fill="none" stroke="{INK}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>'
    s+=f'<line x1="{ix+iw/2}" y1="103" x2="{ix+iw/2}" y2="184" stroke="{GRY}" stroke-width="1" stroke-dasharray="3 3" opacity="0.7"/>'
    s+=f'<text x="{ix+iw/2}" y="208" text-anchor="middle" font-size="11.5" fill="{MUT}">L samples</text>'
    # --- conv stack ---
    for (bx,ch),bw,bh in zip([(178,"30"),(258,"40"),(340,"50")],[26,24,22],[96,84,72]):
        by=100+(96-bh)//2+10
        s+=block3d(bx,by,bw,bh,12,"url(#gC)",NAVY)
        s+=f'<text x="{bx+bw/2+6}" y="{by+bh+18}" text-anchor="middle" font-size="11" fill="{NAVY}">conv</text><text x="{bx+bw/2+6}" y="{by+bh+31}" text-anchor="middle" font-size="11" fill="{MUT}">{ch}</text>'
    s+=block3d(420,108,12,84,10,"url(#gD)",NAVY)+f'<text x="430" y="212" text-anchor="middle" font-size="11" fill="{MUT}">flatten</text>'
    s+=f'<rect x="470" y="120" width="48" height="60" rx="5" fill="url(#gD)" stroke="{NAVY}" stroke-width="1.1"/><text x="494" y="155" text-anchor="middle" font-size="11.5" fill="{NAVY}">dense</text>'
    # --- output: a single appliance value at the window centre ---
    ox,oy,ow,oh=560,98,172,92
    s+=f'<rect x="{ox}" y="{oy}" width="{ow}" height="{oh}" rx="6" fill="#f6f7f9" stroke="{LINE}"/>'
    s+=use("fridge",ox+9,oy+9,21,BLUE)
    owf="572,174 588,172 604,150 620,176 636,160 645,128 660,172 686,170 720,170"
    s+=f'<polyline points="{owf}" fill="none" stroke="{GRY}" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round" opacity="0.5"/>'
    s+=f'<line x1="645" y1="106" x2="645" y2="126" stroke="{ACC}" stroke-width="1.1" stroke-dasharray="3 3"/>'
    s+=f'<rect x="642" y="128" width="6" height="54" rx="2.5" fill="{ACC}"/>'
    s+=f'<circle cx="645" cy="128" r="6.5" fill="{ACC}"/>'
    s+=f'<text x="{ox+ow/2}" y="208" text-anchor="middle" font-size="11.5" fill="{BLUE}">&#375; · power at centre</text>'
    for a,b in [(146,174),(376,416),(520,556)]: s+=arr(a,b,150)
    return s+"</svg>"

# ---- Transformer encoder -----------------------------------------------------
def transformer():
    s=f'<svg width="780" height="330" viewBox="0 0 780 330" font-family="Work Sans,sans-serif"><defs>{ARROW}{SYMBOLS}</defs>'
    s+=f'<text x="390" y="20" text-anchor="middle" font-size="14" fill="{MUT}">aggregate <tspan fill="{INK}" font-weight="700">sequence</tspan> &#8594; appliance <tspan fill="{INK}" font-weight="700">sequence</tspan></text>'
    # --- input: aggregate signal sampled into a token sequence ---
    s+=use("meter",20,150,28,INK)
    in_h=[11,17,14,22,15,19]
    for i,h in enumerate(in_h):
        tx=58+i*16
        s+=f'<rect x="{tx}" y="150" width="13" height="30" rx="3" fill="#eef2f8" stroke="{NAVY}" stroke-width="1.1"/>'
        s+=f'<rect x="{tx+3.5}" y="{179-h}" width="6" height="{h}" rx="1.5" fill="{BLUE}"/>'
    s+=f'<text x="108" y="198" text-anchor="middle" font-size="11" fill="{MUT}">aggregate</text>'
    s+=f'<circle cx="178" cy="165" r="9" fill="#fff" stroke="{NAVY}" stroke-width="1.3"/><text x="178" y="169" text-anchor="middle" font-size="13" fill="{NAVY}">+</text><text x="178" y="192" text-anchor="middle" font-size="10" fill="{MUT}">pos.</text>'
    bx,by,bw,bh=230,58,330,210
    s+=f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="10" fill="none" stroke="{NAVY}" stroke-width="1.4" stroke-dasharray="5 4"/><text x="{bx+bw-8}" y="{by+18}" text-anchor="end" font-size="11.5" fill="{MUT}">encoder &#215; N</text>'
    def lyr(y,h,label,fill,tcol):
        return f'<rect x="{bx+30}" y="{y}" width="{bw-60}" height="{h}" rx="6" fill="{fill}" stroke="{NAVY}" stroke-width="1.1"/><text x="{bx+bw/2}" y="{y+h/2+5}" text-anchor="middle" font-size="13" font-weight="600" fill="{tcol}">{label}</text>'
    s+=lyr(76,46,"Multi-Head Self-Attention","url(#gC)","#fff")
    s+=lyr(132,30,"Add &amp; Norm","url(#gD)",NAVY)
    s+=lyr(174,42,"Feed-Forward","url(#gD)",NAVY)
    s+=lyr(226,30,"Add &amp; Norm","url(#gD)",NAVY)
    s+=f'<path d="M{bx+22},150 C{bx+8},150 {bx+8},99 {bx+28},99" fill="none" stroke="{ACC}" stroke-width="1.4" marker-end="url(#ah)"/>'
    s+=f'<path d="M{bx+22},241 C{bx+8},241 {bx+8},197 {bx+28},197" fill="none" stroke="{ACC}" stroke-width="1.4" marker-end="url(#ah)"/>'
    s+=f'<text x="{bx+6}" y="175" font-size="10" fill="{ACC}" transform="rotate(-90 {bx+6} 175)">residual</text>'
    # --- output: predicted appliance power sequence (an activation event) ---
    s+=use("fridge",600,150,26,BLUE)
    out_h=[4,5,8,24,21,6]
    for i,h in enumerate(out_h):
        tx=636+i*16
        s+=f'<rect x="{tx}" y="150" width="13" height="30" rx="3" fill="#fbeeec" stroke="{ACC}" stroke-width="1.1"/>'
        s+=f'<rect x="{tx+3.5}" y="{179-h}" width="6" height="{h}" rx="1.5" fill="{ACC}"/>'
    s+=f'<text x="684" y="198" text-anchor="middle" font-size="11" fill="{BLUE}">appliance</text>'
    s+=f'<line x1="190" y1="165" x2="226" y2="165" stroke="{GRY}" stroke-width="1.6" marker-end="url(#ah)"/><line x1="564" y1="165" x2="598" y2="165" stroke="{GRY}" stroke-width="1.6" marker-end="url(#ah)"/>'
    return s+"</svg>"

# ---- FHMM (graphical model) --------------------------------------------------
def fhmm():
    cols=[230,380,530]; rows=[64,130,196]; oy=270
    icons=["fridge","washer","dishwasher"]; cl=[BLUE,ACC,TEAL]
    s=f'<svg width="640" height="320" viewBox="0 0 640 320" font-family="Work Sans,sans-serif"><defs>{ARROW}{SYMBOLS}</defs>'
    s+=f'<rect x="196" y="40" width="380" height="186" rx="10" fill="none" stroke="{LINE}" stroke-width="1.2" stroke-dasharray="5 4"/>'
    s+=f'<text x="386" y="33" text-anchor="middle" font-size="12" fill="{MUT}">hidden appliance states</text>'
    for ri,ry in enumerate(rows):
        s+=use(icons[ri],150,ry-13,26,cl[ri])
        for ci in range(2):
            s+=f'<line x1="{cols[ci]+19}" y1="{ry}" x2="{cols[ci+1]-19}" y2="{ry}" stroke="{NAVY}" stroke-width="1.4" marker-end="url(#ah)"/>'
        for cx in cols:
            s+=f'<circle cx="{cx}" cy="{ry}" r="18" fill="#eef2f8" stroke="{NAVY}" stroke-width="1.6"/><text x="{cx}" y="{ry+4}" text-anchor="middle" font-size="12.5" fill="{NAVY}">z</text>'
    for cx in cols:
        for ry in rows: s+=f'<line x1="{cx}" y1="{ry+18}" x2="{cx}" y2="{oy-21}" stroke="{LINE}" stroke-width="1.2" marker-end="url(#ah)"/>'
        s+=f'<circle cx="{cx}" cy="{oy}" r="21" fill="#fbeeec" stroke="{ACC}" stroke-width="1.7"/><text x="{cx}" y="{oy+5}" text-anchor="middle" font-size="14" fill="{ACC}">y</text>'
    s+=use("meter",150,oy-15,28,INK)+f'<text x="40" y="{oy+5}" font-size="13" font-weight="600" fill="{ACC}">aggregate</text>'
    for cx,lab in zip(cols,["t","t+1","t+2"]): s+=f'<text x="{cx}" y="312" text-anchor="middle" font-size="11" fill="{GRY}">{lab}</text>'
    return s+"</svg>"

# ---- model taxonomy tree -----------------------------------------------------
def model_tree():
    fams=[("Recurrent & Hybrid",140,["RNN","WindowGRU","ConvLSTM*","RNN-Attn","RNN-Attn-Cl"]),
          ("Convolutional",380,["Seq2Point","Seq2Seq","TCN*","ResNet","ResNet-Cl"]),
          ("Transformer",620,["BERT","Reformer*","NILMFormer*"]),
          ("Specialized",858,["DAE","MSDC*"])]
    W,H=998,348
    s=f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Work Sans,sans-serif">'
    s+=f'<rect x="409" y="14" width="180" height="40" rx="20" fill="{INK}"/><text x="499" y="39" text-anchor="middle" font-size="16" font-weight="700" fill="#fff">16 NILM models</text>'
    for name,fx,models in fams:
        s+=f'<path d="M499,54 C499,70 {fx},66 {fx},84" fill="none" stroke="{LINE}" stroke-width="1.4"/>'
        s+=f'<rect x="{fx-92}" y="84" width="184" height="36" rx="8" fill="#eef2f8" stroke="{NAVY}" stroke-width="1.6"/><text x="{fx}" y="107" text-anchor="middle" font-size="14.5" font-weight="700" fill="{NAVY}">{name}</text>'
        last_y=146+(len(models)-1)*42
        s+=f'<line x1="{fx}" y1="120" x2="{fx}" y2="{last_y}" stroke="{LINE}" stroke-width="1.2"/>'
        for i,m in enumerate(models):
            y=146+i*42; new=m.endswith("*"); label=(m[:-1]+" ★") if new else m
            fill="#fbeeec" if new else "#f6f7f9"; stroke=ACC if new else LINE; tcol=ACC if new else INK
            s+=f'<rect x="{fx-85}" y="{y}" width="170" height="30" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="{1.6 if new else 1.2}"/>'
            s+=f'<text x="{fx}" y="{y+20}" text-anchor="middle" font-size="13" font-weight="{700 if new else 500}" fill="{tcol}">{label}</text>'
    s+=f'<text x="{W-12}" y="{H-10}" text-anchor="end" font-size="12.5" fill="{MUT}">★ = added in NILMBench2026</text>'
    return s+"</svg>"

# ---- T1 / T2 / T3 task diagrams (with country flags) -------------------------
USA=('<rect width="20" height="13" fill="#b22234"/><rect y="1.85" width="20" height="1.85" fill="#fff"/><rect y="5.55" width="20" height="1.85" fill="#fff"/><rect y="9.25" width="20" height="1.85" fill="#fff"/><rect width="9" height="7" fill="#3c3b6e"/>'
     '<g fill="#fff"><circle cx="2" cy="1.6" r="0.5"/><circle cx="4.5" cy="1.6" r="0.5"/><circle cx="7" cy="1.6" r="0.5"/><circle cx="3.25" cy="3.5" r="0.5"/><circle cx="5.75" cy="3.5" r="0.5"/><circle cx="2" cy="5.4" r="0.5"/><circle cx="4.5" cy="5.4" r="0.5"/><circle cx="7" cy="5.4" r="0.5"/></g>')
UK=('<rect width="20" height="13" fill="#012169"/><path d="M0,0 L20,13 M20,0 L0,13" stroke="#fff" stroke-width="2.6"/><path d="M0,0 L20,13 M20,0 L0,13" stroke="#C8102E" stroke-width="1.1"/>'
    '<path d="M10,0 V13 M0,6.5 H20" stroke="#fff" stroke-width="3.4"/><path d="M10,0 V13 M0,6.5 H20" stroke="#C8102E" stroke-width="1.9"/>')
HOUSE='M2,13 L14,3 L26,13 M4,13 V27 H24 V13 M11,27 V20 H17 V27'; BODY='M4,13 V27 H24 V13 Z'
def flag(k,x,y): return f'<svg x="{x}" y="{y}" width="20" height="13" viewBox="0 0 20 13" overflow="hidden">{USA if k=="usa" else UK}</svg>'
def th(x,y,s=1.1): return f'<g class="hg" transform="translate({x},{y}) scale({s})"><path class="fillbody" d="{BODY}"/><path d="{HOUSE}"/></g>'
def xh(x,y,s=1.25): return f'<g class="hg hg-test" transform="translate({x},{y}) scale({s})" stroke-dasharray="3 3"><path d="{HOUSE}"/></g>'
TASK_CSS='<style>.hg{fill:none;stroke-width:1.8;stroke-linejoin:round;stroke-linecap:round;stroke:#2a3340}.hg .fillbody{fill:rgba(20,28,40,0.05);stroke:none}.hg-test{stroke:#c44536}.lab{font-family:Work Sans,sans-serif;font-size:10.5px;fill:#6a7280}.sub{font-family:Work Sans,sans-serif;font-size:10.5px;font-weight:700}.dash{stroke:#c9ccd3;stroke-width:1.3;stroke-dasharray:3 4}.arr{stroke:#8b929c;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}.q{font-family:Work Sans,sans-serif;font-size:11px;font-weight:800;fill:#fff}</style>'
def task_t1():
    return f'''{TASK_CSS}<svg viewBox="0 0 300 118" width="600"><text x="150" y="13" text-anchor="middle" class="lab">Building 1 · same home</text>{th(136,18,1.2)}
 <line x1="150" y1="54" x2="150" y2="66" class="dash"/><rect x="30" y="68" width="240" height="16" rx="8" fill="#f3f4f6" stroke="#d8dbe0"/>
 <rect x="34" y="71" width="158" height="10" rx="5" fill="#1b3b6f"/><rect x="220" y="71" width="46" height="10" rx="5" fill="#c44536"/><line x1="206" y1="66" x2="206" y2="86" class="dash"/>
 <text x="113" y="103" text-anchor="middle" class="sub" fill="#1b3b6f">TRAIN · 30 d</text><text x="243" y="103" text-anchor="middle" class="sub" fill="#c44536">TEST · 1 wk</text></svg>'''
def task_t2():
    return f'''{TASK_CSS}<svg viewBox="0 0 300 118" width="600"><text x="150" y="12" text-anchor="middle" class="lab">same dataset &amp; country · unseen home</text>{flag('uk',34,20)}{th(22,40,1.1)}{th(56,40,1.1)}
 <text x="46" y="104" text-anchor="middle" class="sub" fill="#1b3b6f">TRAIN · B1, B2</text><path d="M100,60 H162" class="arr"/><path d="M156,55 L162,60 L156,65" class="arr"/><text x="131" y="52" text-anchor="middle" class="lab">generalize</text>
 {flag('uk',196,20)}{xh(188,38,1.45)}<circle cx="228" cy="44" r="9" fill="#c44536"/><text x="228" y="48" text-anchor="middle" class="q">?</text><text x="214" y="104" text-anchor="middle" class="sub" fill="#c44536">TEST · B4</text></svg>'''
def task_t3():
    return f'''{TASK_CSS}<svg viewBox="0 0 300 118" width="600"><text x="150" y="12" text-anchor="middle" class="lab">different countries &amp; grids</text>{flag('usa',44,20)}{th(24,40,1.05)}{th(58,40,1.05)}
 <text x="54" y="104" text-anchor="middle" class="sub" fill="#1b3b6f">REDD · USA · 110V</text><line x1="150" y1="30" x2="150" y2="94" class="dash"/>{flag('uk',226,20)}{xh(206,40,1.05)}{xh(240,40,1.05)}
 <text x="244" y="104" text-anchor="middle" class="sub" fill="#c44536">REFIT · UK · 230V</text><path d="M112,62 H188" class="arr"/><path d="M118,57 L112,62 L118,67" class="arr"/><path d="M182,57 L188,62 L182,67" class="arr"/><text x="150" y="54" text-anchor="middle" class="lab">zero-shot</text></svg>'''

FIGS = {
    "seq2point": seq2point(), "transformer": transformer(), "fhmm": fhmm(),
    "model_tree": model_tree(), "task_t1": task_t1(), "task_t2": task_t2(), "task_t3": task_t3(),
}
# standalone icon PNGs (used by figures.py to overlay on matplotlib plots)
ICON_FIGS = {f"ic-{n}": f'<svg width="120" height="120" viewBox="0 0 24 24">{ICONS[n.split("-")[0] if "-" not in n else n.replace("-amber","")].replace("C", ICON_COLOR[n])}</svg>'
             for n in ["meter","fridge","washer","dishwasher","kettle","kettle-amber","microwave","tv"]}

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "_diagrams.html")
    parts = ['<!DOCTYPE html><html><head><meta charset="utf-8">',
             '<link href="https://fonts.googleapis.com/css2?family=Work+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">',
             '<style>body{margin:0;background:#fff}[data-fig]{display:inline-block;padding:14px;background:#fff}svg{display:block}</style></head><body>']
    for fid, svg in {**FIGS, **ICON_FIGS}.items():
        parts.append(f'<div data-fig id="{fid}">{svg}</div>')
    parts.append("</body></html>")
    open(out, "w").write("".join(parts))
    print("wrote", out, "with", len(FIGS) + len(ICON_FIGS), "figures")
    print("now run:  node scripts/render.mjs")

if __name__ == "__main__":
    main()
