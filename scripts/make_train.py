"""Commit Express: renders assets/train.svg, a steam train that lights up your real contribution grid.
Usage:  python scripts/make_train.py            (needs GH_TOKEN, USERNAME)
        python scripts/make_train.py --demo     (sample data, no network)
        python scripts/make_train.py --preview  (static frame for checking layout)"""
import os, sys, json, math, html, urllib.request, pathlib
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
USER = os.environ.get("USERNAME", "Xunaeid")
TOKEN = os.environ.get("GH_TOKEN", "")
TZ = float(os.environ.get("TZ_OFFSET", "6"))
FONT = "-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Inter,Roboto,Helvetica,Arial,sans-serif"
BLUE, INDIGO, PURPLE, PINK, ORANGE, GREEN, CYAN = "#0A84FF", "#5E5CE6", "#BF5AF2", "#FF375F", "#FF9F0A", "#30D158", "#64D2FF"
EMPTY = "#2b2b5c"
W, H = 1200, 370
RAIL_Y = 128
GX0, GY0, PITCH, CELL = 46, 160, 21, 16
T, RUN = 18.0, 14.0

QUERY = """query($login:String!){user(login:$login){contributionsCollection{totalCommitContributions contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}}}}"""

def now_local():
    return (datetime.now(timezone.utc) + timedelta(hours=TZ)).date()

def fetch():
    req = urllib.request.Request("https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": USER}}).encode(),
        headers={"Authorization": "bearer " + TOKEN, "Content-Type": "application/json", "User-Agent": "profile-train"})
    data = json.load(urllib.request.urlopen(req, timeout=60))
    if "errors" in data: raise SystemExit(json.dumps(data["errors"]))
    cal = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    return cal["weeks"], data["data"]["user"]["contributionsCollection"]["totalCommitContributions"]

def demo():
    today = now_local()
    known = {0: 31, 1: 5, 2: 16, 6: 1, 7: 6}      # only the real recent days, nothing invented
    days = [{"date": (today - timedelta(days=k)).isoformat(), "contributionCount": known.get(k, 0)} for k in range(370, -1, -1)]
    weeks, cur = [], []
    for d in days:
        wd = (datetime.fromisoformat(d["date"]).weekday() + 1) % 7
        if wd == 0 and cur: weeks.append({"contributionDays": cur}); cur = []
        cur.append(d)
    if cur: weeks.append({"contributionDays": cur})
    return weeks, sum(known.values())

def col(c): return CYAN if c <= 1 else BLUE if c <= 3 else PURPLE if c <= 7 else PINK if c <= 14 else ORANGE

# ---------------- train parts (local coords: x right, y up is negative, ground y=0) ----------------
V = None  # px/s, set in build()

def wheel(cx, cy, r, drive=False):
    d = 2 * math.pi * r / V
    sp = "".join(f'<line x1="0" y1="0" x2="{r*.9*math.cos(math.radians(a)):.1f}" y2="{r*.9*math.sin(math.radians(a)):.1f}" stroke="#e4e7ff" stroke-width="1.6" stroke-linecap="round"/>' for a in range(0, 360, 60))
    extra = f'<circle cx="{-r*.55:.1f}" cy="0" r="{r*.24:.1f}" fill="{ORANGE}"/><circle cx="{r*.5:.1f}" cy="0" r="3.6" fill="{ORANGE}" stroke="#14142b" stroke-width="1"/>' if drive else ""
    return (f'<g transform="translate({cx} {cy})"><circle r="{r}" fill="#12122b" stroke="url(#rim)" stroke-width="2.6"/><circle r="{r*.6:.1f}" fill="#23235a"/>'
            f'<g><animateTransform attributeName="transform" type="rotate" from="0" to="360" dur="{d:.3f}s" repeatCount="indefinite"/>{sp}{extra}<circle r="{max(2.6,r*.16):.1f}" fill="#fff"/></g></g>')

def engine(x):
    d17 = 2 * math.pi * 17 / V
    rod = (f'<g transform="translate(62 -17)"><g><animateMotion dur="{d17:.3f}s" repeatCount="indefinite" path="M8.5,0 a8.5,8.5 0 1,1 -17,0 a8.5,8.5 0 1,1 17,0"/>'
           f'<rect x="-4" y="-2.6" width="84" height="5.2" rx="2.6" fill="#d7dbff"/><circle cx="0" r="3.4" fill="{ORANGE}"/><circle cx="38" r="3.4" fill="{ORANGE}"/><circle cx="76" r="3.4" fill="{ORANGE}"/></g></g>')
    puffs = "".join(f'<circle class="puff" style="animation-delay:-{i*0.52:.2f}s" cx="148" cy="-100" r="9" fill="url(#steam)"/>' for i in range(5))
    jets = "".join(f'<circle class="jet" style="animation-delay:-{i*0.45:.2f}s" cx="160" cy="-12" r="5" fill="url(#steam)"/>' for i in range(2))
    return f'''<g transform="translate({x} 0)">
<polygon class="beam" points="182,-54 182,-42 340,-8 340,-92" fill="url(#beam)"/>
<rect x="6" y="-26" width="184" height="9" rx="3" fill="#1b1b3d"/>
<rect x="52" y="-68" width="116" height="38" rx="19" fill="url(#boiler)"/>
<rect x="60" y="-64" width="100" height="6" rx="3" fill="#fff" opacity=".28"/>
<rect x="76" y="-68" width="5" height="38" fill="{ORANGE}" opacity=".95"/><rect x="112" y="-68" width="5" height="38" fill="{ORANGE}" opacity=".95"/><rect x="144" y="-68" width="5" height="38" fill="{ORANGE}" opacity=".95"/>
<rect x="166" y="-70" width="12" height="42" rx="5" fill="#23235a" stroke="#fff" stroke-opacity=".2"/>
<circle cx="180" cy="-50" r="7.5" fill="#fff"/><circle cx="180" cy="-50" r="11" fill="#fff" opacity=".35" class="lamp"/>
<polygon points="140,-68 143,-92 136,-100 160,-100 153,-92 156,-68" fill="#2a2a5e" stroke="#fff" stroke-opacity=".4"/><rect x="138" y="-97" width="20" height="3.5" rx="1.7" fill="{PINK}"/>
<path d="M92,-68 q0,-22 17,-22 q17,0 17,22 z" fill="url(#brass)"/><path d="M68,-68 q0,-13 10,-13 q10,0 10,13 z" fill="url(#brass)" opacity=".9"/>
<line x1="60" y1="-74" x2="164" y2="-74" stroke="#fff" stroke-opacity=".5" stroke-width="2"/>
<rect x="2" y="-88" width="62" height="9" rx="3.5" fill="url(#roof)"/>
<rect x="6" y="-79" width="50" height="55" rx="4" fill="#2d2170" stroke="#fff" stroke-opacity=".22"/>
<rect x="15" y="-70" width="30" height="22" rx="6" fill="#0b0b22"/><rect x="15" y="-70" width="30" height="22" rx="6" fill="url(#win)" opacity=".55"/>
<line x1="30" y1="-70" x2="30" y2="-48" stroke="#2d2170" stroke-width="2"/>
<polygon points="178,-26 178,0 202,0" fill="url(#pilot)"/>
<rect x="146" y="-36" width="24" height="15" rx="4" fill="#23235a" stroke="{ORANGE}" stroke-width="1.4"/>
<rect class="piston" x="124" y="-30" width="30" height="3.6" rx="1.8" fill="#d7dbff"/>
{rod}
{wheel(62,-17,17,True)}{wheel(100,-17,17,True)}{wheel(138,-17,17,True)}{wheel(24,-9,9)}{wheel(178,-9,9)}
<g transform="translate(0 0)">{puffs}{jets}</g>
</g>'''

def tender(x):
    gems = [(14,-62,CYAN),(30,-68,BLUE),(46,-63,PURPLE),(62,-70,PINK),(78,-64,ORANGE),(90,-60,CYAN)]
    gm = "".join(f'<circle class="gem" style="animation-delay:-{i*.35:.2f}s" cx="{gx}" cy="{gy}" r="3.2" fill="{gc}"/>' for i,(gx,gy,gc) in enumerate(gems))
    return f'''<g transform="translate({x} 0)">
<rect x="0" y="-28" width="100" height="8" rx="3" fill="#1b1b3d"/>
<rect x="0" y="-52" width="100" height="28" rx="6" fill="url(#car)" stroke="{PURPLE}" stroke-opacity=".7"/>
<rect x="0" y="-52" width="100" height="5" rx="2.5" fill="{PURPLE}"/>
<path d="M6,-52 q10,-20 26,-13 q10,-11 26,-5 q16,-8 32,0 q8,4 8,18 z" fill="#0e0e24" stroke="#fff" stroke-opacity=".15"/>{gm}
<text x="50" y="-33" text-anchor="middle" font-size="12" font-weight="800" letter-spacing="2.2" fill="#fff">XUNAEID</text>
{wheel(24,-11,11)}{wheel(76,-11,11)}
</g>'''

def wagon(x, label, accent, cargo):
    cubes = "".join(f'<rect class="gem" style="animation-delay:-{(i*.4)%1.6:.2f}s" x="{cx}" y="{cy}" width="14" height="14" rx="3.5" fill="{c}"/>' for i,(cx,cy,c) in enumerate(cargo))
    return f'''<g transform="translate({x} 0)">
<rect x="0" y="-26" width="98" height="7" rx="3" fill="#1b1b3d"/>
<rect x="0" y="-46" width="98" height="24" rx="5" fill="url(#car)" stroke="{accent}" stroke-opacity=".75"/>
<rect x="0" y="-46" width="98" height="4.5" rx="2.2" fill="{accent}"/>
<line x1="24" x2="24" y1="-40" y2="-24" stroke="#fff" stroke-opacity=".12"/><line x1="49" x2="49" y1="-40" y2="-24" stroke="#fff" stroke-opacity=".12"/><line x1="74" x2="74" y1="-40" y2="-24" stroke="#fff" stroke-opacity=".12"/>
<text x="49" y="-28" text-anchor="middle" font-size="11.5" font-weight="800" letter-spacing="2" fill="#fff">{label}</text>
{cubes}
{wheel(20,-10,10)}{wheel(78,-10,10)}
</g>'''

def build(weeks, total, preview=False):
    global V
    L = 426 + 202
    F = W + 14 + L
    V = F / RUN
    today = now_local().isoformat()
    weeks = weeks[-53:]
    base, over, labels, css_cols = "", "", "", ""
    last_lbl, lastc = -9, None
    for c, wk in enumerate(weeks):
        x = GX0 + c * PITCH
        days = [d for d in wk["contributionDays"] if d["date"] <= today]
        if not days: continue
        d0 = datetime.fromisoformat(wk["contributionDays"][0]["date"])
        if (d0.month != lastc) and c - last_lbl >= 3:
            labels += f'<text x="{x}" y="324" font-size="12" font-weight="600" fill="#9ea4d6">{d0.strftime("%b")}</text>'; last_lbl = c; lastc = d0.month
        t_c = max(0.2, RUN * (x - 6) / F)
        P = t_c / T * 100
        has = False
        for d in days:
            row = (datetime.fromisoformat(d["date"]).weekday() + 1) % 7
            y = GY0 + row * PITCH; n = d["contributionCount"]
            base += f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="4.5" fill="{EMPTY}"/>'
            if n > 0:
                has = True; over += f'<rect class="k c{c}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="4.5" fill="{col(n)}"/>'
            if d["date"] == today:
                over += f'<rect x="{x-2}" y="{y-2}" width="{CELL+4}" height="{CELL+4}" rx="6.5" fill="none" stroke="#fff" stroke-width="1.6" class="now"/>'
        if has:
            css_cols += (f".c{c}{{animation:c{c} {T}s ease-out infinite}}@keyframes c{c}{{0%,{P:.2f}%{{opacity:0;transform:scale(.3)}}{P+1.1:.2f}%{{opacity:1;transform:scale(1.5)}}{P+2.8:.2f}%,92%{{opacity:1;transform:scale(1)}}96%,100%{{opacity:0;transform:scale(.3)}}}}\n")
    # parallax hills
    def hills(baseline, amp, ph, op, color, dur):
        pts = []
        for x in range(0, 2401, 30):
            y = baseline - amp * (math.sin(2*math.pi*x/1200*1+ph) * .6 + math.sin(2*math.pi*x/1200*3+ph*2) * .3 + math.sin(2*math.pi*x/1200*5+ph*3) * .1)
            pts.append(f"{x},{y:.1f}")
        return (f'<g opacity="{op}"><polygon fill="{color}" points="0,{RAIL_Y} {" ".join(pts)} 2400,{RAIL_Y}">'
                f'<animateTransform attributeName="transform" type="translate" from="0 0" to="-1200 0" dur="{dur}s" repeatCount="indefinite"/></polygon></g>')
    stars = "".join(f'<circle cx="{(i*167)%1180+10}" cy="{(i*53)%70+12}" r="{1+(i%3)*.5}" fill="#fff"><animate attributeName="opacity" values=".1;.9;.1" dur="{2.5+(i%4)*.7}s" begin="{(i%5)*.6}s" repeatCount="indefinite"/></circle>' for i in range(22))
    sleepers = "".join(f'<rect x="{x}" y="132" width="9" height="5" rx="1.5" fill="#6b4bd1"/>' for x in range(2, W, 18))
    cars = (wagon(0, "DESIGN", PINK, [(12,-60,CYAN),(32,-60,BLUE),(52,-60,PURPLE),(72,-60,PINK),(22,-74,ORANGE),(42,-74,CYAN),(62,-74,BLUE)]) +
            wagon(106, "AI", PURPLE, [(12,-60,PINK),(32,-60,ORANGE),(52,-60,CYAN),(72,-60,BLUE),(22,-74,PURPLE),(42,-74,PINK),(62,-74,ORANGE)]) +
            wagon(212, "REACT", BLUE, [(12,-60,BLUE),(32,-60,CYAN),(52,-60,PURPLE),(72,-60,ORANGE),(22,-74,PINK),(42,-74,BLUE),(62,-74,CYAN)]))
    couplers = "".join(f'<rect x="{x}" y="-20" width="10" height="4" fill="#8a8fc2"/>' for x in (98, 204, 310, 418))
    train_inner = f'{couplers}{cars}{tender(318)}{engine(426)}'
    if preview:
        train = f'<g transform="translate(0 {RAIL_Y})"><g transform="translate(330 0)" filter="url(#ds)">{train_inner}</g></g>'
    else:
        train = (f'<g transform="translate(0 {RAIL_Y})"><g filter="url(#ds)"><animateTransform attributeName="transform" type="translate" calcMode="linear" '
                 f'values="-{L} 0;{W+14} 0;{W+14} 0" keyTimes="0;{RUN/T:.4f};1" dur="{T}s" repeatCount="indefinite"/>{train_inner}</g></g>')
    legend = "".join(f'<rect x="{992+i*20}" y="338" width="14" height="14" rx="4" fill="{c}"/>' for i, c in enumerate([EMPTY, CYAN, BLUE, PURPLE, PINK, ORANGE]))
    upd = datetime.fromisoformat(today).strftime("%b ") + str(datetime.fromisoformat(today).day) + ", " + today[:4]
    css = f"""text{{font-family:{FONT}}}
.k{{transform-box:fill-box;transform-origin:center}}
{css_cols}.now{{animation:now 1.6s ease-in-out infinite}}@keyframes now{{0%,100%{{opacity:.2}}50%{{opacity:1}}}}
.puff{{transform-box:fill-box;transform-origin:center;opacity:0;animation:puff 2.6s ease-out infinite}}
@keyframes puff{{0%{{transform:translate(0,0) scale(.4);opacity:0}}12%{{opacity:.9}}100%{{transform:translate(-95px,-62px) scale(2.9);opacity:0}}}}
.jet{{transform-box:fill-box;transform-origin:center;opacity:0;animation:jet .9s ease-out infinite}}
@keyframes jet{{0%{{transform:translate(0,0) scale(.5);opacity:.8}}100%{{transform:translate(-16px,14px) scale(1.8);opacity:0}}}}
.piston{{animation:pi .41s ease-in-out infinite alternate}}@keyframes pi{{from{{transform:translateX(-5px)}}to{{transform:translateX(5px)}}}}
.gem{{animation:gm 1.8s ease-in-out infinite}}@keyframes gm{{0%,100%{{opacity:.75}}50%{{opacity:1;filter:brightness(1.35)}}}}
.beam{{animation:bm 1.2s ease-in-out infinite alternate}}@keyframes bm{{from{{opacity:.7}}to{{opacity:1}}}}
.lamp{{animation:lp 1.6s ease-in-out infinite}}@keyframes lp{{0%,100%{{opacity:.25}}50%{{opacity:.6}}}}"""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Commit Express: a steam train lighting up your grid, {total} commits in the last 12 months">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#16163a"/><stop offset="1" stop-color="#090a1c"/></linearGradient>
<linearGradient id="bd" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BLUE}"/><stop offset=".5" stop-color="#fff" stop-opacity=".14"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
<linearGradient id="rim" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{CYAN}"/><stop offset="1" stop-color="{BLUE}"/></linearGradient>
<linearGradient id="boiler" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#58b2ff"/><stop offset=".45" stop-color="{BLUE}"/><stop offset="1" stop-color="#3a37c4"/></linearGradient>
<linearGradient id="brass" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffd27a"/><stop offset="1" stop-color="{ORANGE}"/></linearGradient>
<linearGradient id="roof" x1="0" x2="1"><stop offset="0" stop-color="{PURPLE}"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
<linearGradient id="win" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{CYAN}"/><stop offset="1" stop-color="{PURPLE}"/></linearGradient>
<linearGradient id="pilot" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{ORANGE}"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
<linearGradient id="car" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3a2a85"/><stop offset="1" stop-color="#1b1456"/></linearGradient>
<linearGradient id="beam" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity=".5"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<linearGradient id="rail" x1="0" x2="1"><stop offset="0" stop-color="{BLUE}"/><stop offset=".5" stop-color="{PURPLE}"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
<radialGradient id="steam"><stop offset="0" stop-color="#fff" stop-opacity=".95"/><stop offset="1" stop-color="#cfd6ff" stop-opacity=".1"/></radialGradient>
<filter id="bl" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="34"/></filter>
<filter id="ds" x="-5%" y="-30%" width="110%" height="170%"><feDropShadow dx="0" dy="8" stdDeviation="7" flood-color="#000" flood-opacity=".5"/></filter>
<clipPath id="panel"><rect width="{W}" height="{H}" rx="34"/></clipPath>
</defs>
<style>{css}</style>
<rect width="{W}" height="{H}" rx="34" fill="url(#bg)"/>
<g clip-path="url(#panel)">
<circle cx="180" cy="60" r="190" fill="{BLUE}" opacity=".22" filter="url(#bl)"/><circle cx="1020" cy="70" r="200" fill="{PURPLE}" opacity=".26" filter="url(#bl)"/><circle cx="640" cy="330" r="190" fill="{PINK}" opacity=".14" filter="url(#bl)"/>
{stars}
{hills(112,22,0.6,.35,"#3a2a85",70)}{hills(122,16,2.1,.55,"#2a1f78",42)}
<rect x="0" y="{RAIL_Y+9}" width="{W}" height="6" fill="#0d0d26"/>
{sleepers}
<rect x="0" y="{RAIL_Y}" width="{W}" height="4.5" fill="url(#rail)"/><rect x="0" y="{RAIL_Y}" width="{W}" height="1.6" fill="#fff" opacity=".55"/>
{base}
{over}
{train}
</g>
{labels}
<text x="{GX0}" y="352" font-size="12" font-weight="800" letter-spacing="1.6" fill="{CYAN}">COMMIT EXPRESS</text>
<text x="{GX0+150}" y="352" font-size="13" fill="#9ea4d6">{total} commits · last 12 months · updated {upd}</text>
<text x="982" y="350" text-anchor="end" font-size="12" fill="#9ea4d6">Less</text>{legend}<text x="1114" y="350" font-size="12" fill="#9ea4d6">More</text>
<rect x=".75" y=".75" width="{W-1.5}" height="{H-1.5}" rx="34" fill="none" stroke="url(#bd)" stroke-width="1.5"/>
</svg>'''

if __name__ == "__main__":
    weeks, total = demo() if ("--demo" in sys.argv or "--preview" in sys.argv) else fetch()
    out = ROOT / "assets" / ("train_preview.svg" if "--preview" in sys.argv else "train.svg")
    out.write_text(build(weeks, total, preview="--preview" in sys.argv), encoding="utf-8")
    print("wrote", out.name, "| commits (12 mo):", total)
