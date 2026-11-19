"""Generates assets/dashboard.svg from your real GitHub data (GraphQL API).
Usage:  python scripts/make_dashboard.py          (needs GH_TOKEN, USERNAME)
        python scripts/make_dashboard.py --demo   (sample data, no network)"""
import os, sys, json, math, html, urllib.request, pathlib
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
USER = os.environ.get("USERNAME", "Xunaeid")
TOKEN = os.environ.get("GH_TOKEN", "")
TZ = float(os.environ.get("TZ_OFFSET", "6"))          # Dhaka = UTC+6
FONT = "-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Inter,Roboto,Helvetica,Arial,sans-serif"
BLUE, PURPLE, PINK, ORANGE, GREEN, CYAN = "#0A84FF", "#BF5AF2", "#FF375F", "#FF9F0A", "#30D158", "#64D2FF"
LANG_COLORS = [BLUE, PURPLE, PINK, ORANGE, CYAN]

QUERY = """query($login:String!){user(login:$login){
 followers{totalCount}
 repositories(ownerAffiliations:OWNER,isFork:false,first:100){totalCount nodes{stargazerCount
  languages(first:8,orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}}}}
 contributionsCollection{contributionCalendar{totalContributions weeks{contributionDays{date contributionCount}}}}}}"""

def fetch():
    req = urllib.request.Request("https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": USER}}).encode(),
        headers={"Authorization": "bearer " + TOKEN, "Content-Type": "application/json", "User-Agent": "profile-dashboard"})
    data = json.load(urllib.request.urlopen(req, timeout=60))
    if "errors" in data: raise SystemExit(json.dumps(data["errors"]))
    return data["data"]["user"]

def demo():
    today = (datetime.now(timezone.utc) + timedelta(hours=TZ)).date()
    recent = {0: 31, 1: 5, 2: 16, 6: 1, 7: 6}
    days = []
    for k in range(364, -1, -1):
        d = today - timedelta(days=k)
        c = recent.get(k, 0)
        days.append({"date": d.isoformat(), "contributionCount": c})
    return {"followers": {"totalCount": 12},
            "repositories": {"totalCount": 9, "nodes": [{"stargazerCount": 4, "languages": {"edges": [
                {"size": 5200, "node": {"name": "JavaScript"}}, {"size": 3100, "node": {"name": "TypeScript"}},
                {"size": 2400, "node": {"name": "CSS"}}, {"size": 1500, "node": {"name": "HTML"}}, {"size": 600, "node": {"name": "Python"}}]}}]},
            "contributionsCollection": {"contributionCalendar": {"totalContributions": sum(x["contributionCount"] for x in days),
                "weeks": [{"contributionDays": days}]}}}

def analyse(u):
    cal = u["contributionsCollection"]["contributionCalendar"]
    today = (datetime.now(timezone.utc) + timedelta(hours=TZ)).date().isoformat()
    days = [{"date": d["date"], "count": d["contributionCount"]} for w in cal["weeks"] for d in w["contributionDays"] if d["date"] <= today]
    days.sort(key=lambda x: x["date"])
    cur, i = 0, len(days) - 1
    if i >= 0 and days[i]["count"] == 0: i -= 1          # today not committed yet: streak survives
    while i >= 0 and days[i]["count"] > 0: cur += 1; i -= 1
    best = run = 0
    for d in days:
        run = run + 1 if d["count"] > 0 else 0; best = max(best, run)
    top = max(days, key=lambda x: x["count"]) if days else {"count": 0, "date": today}
    langs = {}
    stars = 0
    for r in u["repositories"]["nodes"]:
        stars += r["stargazerCount"]
        for e in r["languages"]["edges"]: langs[e["node"]["name"]] = langs.get(e["node"]["name"], 0) + e["size"]
    return dict(days=days, cur=cur, best=best, total=cal["totalContributions"], top=top, today=today,
                langs=sorted(langs.items(), key=lambda x: -x[1]), repos=u["repositories"]["totalCount"],
                stars=stars, followers=u["followers"]["totalCount"])

def col(c):
    return CYAN if c <= 1 else BLUE if c <= 3 else PURPLE if c <= 7 else PINK if c <= 14 else ORANGE

def short(iso):
    d = datetime.fromisoformat(iso); return d.strftime("%b ") + str(d.day)

def build(s):
    days = s["days"]; N = 91
    last = days[-N:]
    last = [{"date": "", "count": 0}] * (N - len(last)) + last
    cx, cy, r0 = 232, 196, 62
    bars = ""
    for i, d in enumerate(last):
        th = math.radians(-90 + i * 360 / N); c = d["count"]
        x1, y1 = cx + r0 * math.cos(th), cy + r0 * math.sin(th)
        if c == 0:
            bars += f'<circle cx="{cx+(r0+3)*math.cos(th):.1f}" cy="{cy+(r0+3)*math.sin(th):.1f}" r="1.7" fill="#4a4a8c" opacity=".7"/>'
            continue
        L = 8 + math.sqrt(min(c, 40) / 40) * 104
        x2, y2 = cx + (r0 + L) * math.cos(th), cy + (r0 + L) * math.sin(th)
        bars += f'<line class="g" style="animation-delay:{i*0.03:.2f}s" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{col(c)}" stroke-width="5" stroke-linecap="round" pathLength="1"/>'
        if i == N - 1:
            bars += f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="6" fill="{col(c)}"/><circle cx="{x2:.1f}" cy="{y2:.1f}" r="6" fill="{col(c)}" class="pulse"/>'
    # last 7 days
    w7 = days[-7:]; prev = sum(d["count"] for d in days[-14:-7]); cur7 = sum(d["count"] for d in w7)
    mx = max([d["count"] for d in w7] + [1]); cols7 = ""
    for k, d in enumerate(w7):
        x = 27 + k * 38; h = 4 if d["count"] == 0 else 10 + d["count"] / mx * 100
        lab = datetime.fromisoformat(d["date"]).strftime("%a")
        cols7 += (f'<rect class="r" style="animation-delay:{k*0.12:.2f}s" x="{x}" y="{190-h:.1f}" width="28" height="{h:.1f}" rx="9" fill="url(#bar)"/>'
                  f'<text x="{x+14}" y="{182-h:.1f}" text-anchor="middle" font-size="13" font-weight="800" fill="#fff">{d["count"]}</text>'
                  f'<text x="{x+14}" y="212" text-anchor="middle" font-size="12" fill="#9ea4d6">{lab}</text>')
    if prev == 0: mom, mc = ("▲ new activity" if cur7 else "no activity yet"), (GREEN if cur7 else "#9ea4d6")
    else:
        p = round((cur7 - prev) / prev * 100); mom, mc = (f"▲ +{p}%" if p >= 0 else f"▼ {p}%"), (GREEN if p >= 0 else ORANGE)
    tiles = [("CURRENT STREAK", s["cur"], "days in a row", CYAN), ("LONGEST STREAK", s["best"], "days, past year", BLUE),
             ("THIS YEAR", s["total"], "contributions", PURPLE), ("BEST DAY", s["top"]["count"], short(s["top"]["date"]), PINK)]
    tl = ""; defs_t = ""
    for i, (lab, val, sub, c) in enumerate(tiles):
        x = 450 + (i % 2) * 195; y = 30 + (i // 2) * 122
        defs_t += f'<linearGradient id="t{i}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="{c}"/></linearGradient><clipPath id="tc{i}"><rect width="181" height="108" rx="24"/></clipPath>'
        tl += (f'<g transform="translate({x} {y})"><rect width="181" height="108" rx="24" fill="#fff" fill-opacity=".05" stroke="#fff" stroke-opacity=".14"/>'
               f'<g clip-path="url(#tc{i})"><circle cx="160" cy="14" r="46" fill="{c}" opacity=".35" filter="url(#bl)" class="breathe"/></g>'
               f'<text x="20" y="30" font-size="11.5" font-weight="800" letter-spacing="1.4" fill="{c}">{lab}</text>'
               f'<text x="20" y="76" font-size="46" font-weight="800" fill="url(#t{i})">{val}</text>'
               f'<text x="20" y="97" font-size="13" fill="#9ea4d6">{html.escape(str(sub))}</text></g>')
    # languages
    tot = sum(v for _, v in s["langs"][:5]) or 1; seg = ""; leg = ""; x = 0
    for i, (n, v) in enumerate(s["langs"][:5]):
        w = v / tot * 710; seg += f'<rect x="{x:.1f}" y="0" width="{w+1:.1f}" height="14" fill="{LANG_COLORS[i]}"/>'; x += w
        leg += (f'<g transform="translate({i*142} 0)"><circle cx="6" cy="-4" r="5.5" fill="{LANG_COLORS[i]}"/>'
                f'<text x="18" y="0" font-size="13.5" font-weight="700" fill="#fff">{html.escape(n)}</text>'
                f'<text x="18" y="18" font-size="12" fill="#9ea4d6">{v/tot*100:.0f}%</text></g>')
    if not s["langs"]: leg = '<text y="0" font-size="13.5" fill="#9ea4d6">Add public repositories to see your languages here.</text>'
    legend = "".join(f'<circle cx="{232-58+i*29}" cy="412" r="5.5" fill="{c}"/>' for i, c in enumerate(["#4a4a8c", CYAN, BLUE, PURPLE, PINK]))
    lc = [d for d in days if d["count"] > 0]
    lastc = short(lc[-1]["date"]) if lc else "none yet"
    css = f"""text{{font-family:{FONT}}}
.g{{stroke-dasharray:1;stroke-dashoffset:1;animation:g 9s cubic-bezier(.2,.8,.2,1) infinite}}
@keyframes g{{0%{{stroke-dashoffset:1}}14%,88%{{stroke-dashoffset:0}}100%{{stroke-dashoffset:1}}}}
.r{{transform-box:fill-box;transform-origin:50% 100%;animation:r 9s cubic-bezier(.2,.8,.2,1) infinite}}
@keyframes r{{0%{{transform:scaleY(0)}}12%,88%{{transform:scaleY(1)}}100%{{transform:scaleY(0)}}}}
.pulse{{transform-box:fill-box;transform-origin:center;animation:p 1.8s ease-out infinite}}
@keyframes p{{0%{{transform:scale(1);opacity:.9}}100%{{transform:scale(3.4);opacity:0}}}}
.breathe{{transform-box:fill-box;transform-origin:center;animation:br 4s ease-in-out infinite}}
@keyframes br{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.15)}}}}"""
    upd = datetime.fromisoformat(s["today"]).strftime("%b ") + str(datetime.fromisoformat(s["today"]).day) + ", " + s["today"][:4]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="440" viewBox="0 0 1200 440" role="img" aria-label="Contribution dashboard: {s["cur"]} day streak, {s["total"]} contributions this year">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#16163a"/><stop offset="1" stop-color="#0a0a1c"/></linearGradient>
<linearGradient id="bd" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{BLUE}"/><stop offset=".5" stop-color="#fff" stop-opacity=".14"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
<linearGradient id="bar" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="{BLUE}"/><stop offset=".55" stop-color="{PURPLE}"/><stop offset="1" stop-color="{PINK}"/></linearGradient>
<linearGradient id="cn" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="{CYAN}"/></linearGradient>
<radialGradient id="core"><stop offset=".5" stop-color="{PURPLE}" stop-opacity=".45"/><stop offset="1" stop-color="{PURPLE}" stop-opacity="0"/></radialGradient>
<filter id="bl" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="26"/></filter>
<clipPath id="panel"><rect width="1200" height="440" rx="34"/></clipPath>
<clipPath id="lb"><rect width="710" height="14" rx="7"/></clipPath>
{defs_t}
</defs>
<style>{css}</style>
<rect width="1200" height="440" rx="34" fill="url(#bg)"/>
<g clip-path="url(#panel)"><circle cx="232" cy="196" r="190" fill="url(#core)" class="breathe"/><circle cx="1130" cy="40" r="150" fill="{BLUE}" opacity=".25" filter="url(#bl)"/><circle cx="780" cy="430" r="150" fill="{PINK}" opacity=".18" filter="url(#bl)"/></g>
<rect x=".75" y=".75" width="1198.5" height="438.5" rx="34" fill="none" stroke="url(#bd)" stroke-width="1.5"/>
<text x="44" y="46" font-size="12" font-weight="800" letter-spacing="1.6" fill="{CYAN}">LAST 13 WEEKS</text>
<circle cx="{cx}" cy="{cy}" r="{r0-10}" fill="none" stroke="#fff" stroke-opacity=".08"/><circle cx="{cx}" cy="{cy}" r="{r0+112}" fill="none" stroke="#fff" stroke-opacity=".06" stroke-dasharray="3 7"/>
{bars}
<text x="{cx}" y="{cy+14}" text-anchor="middle" font-size="52" font-weight="800" fill="url(#cn)">{s["cur"]}</text>
<text x="{cx}" y="{cy+36}" text-anchor="middle" font-size="11.5" font-weight="800" letter-spacing="1.6" fill="{CYAN}">DAY STREAK</text>
<text x="{cx-98}" y="418" text-anchor="end" font-size="11.5" fill="#9ea4d6">Less</text>{legend}<text x="{cx+80}" y="418" font-size="11.5" fill="#9ea4d6">More</text>
{tl}
<g transform="translate(850 30)"><rect width="310" height="230" rx="24" fill="#fff" fill-opacity=".05" stroke="#fff" stroke-opacity=".14"/>
<text x="22" y="34" font-size="11.5" font-weight="800" letter-spacing="1.4" fill="{PINK}">LAST 7 DAYS</text>
<text x="288" y="34" text-anchor="end" font-size="13" font-weight="800" fill="{mc}">{mom}</text>
<line x1="22" x2="288" y1="192" y2="192" stroke="#fff" stroke-opacity=".12"/>{cols7}</g>
<g transform="translate(450 290)"><text y="0" font-size="11.5" font-weight="800" letter-spacing="1.4" fill="{ORANGE}">TOP LANGUAGES</text>
<text x="710" text-anchor="end" font-size="13" fill="#b9bff0">{s["repos"]} repos · {s["stars"]} stars · {s["followers"]} followers</text>
<g transform="translate(0 14)" clip-path="url(#lb)"><rect width="710" height="14" fill="#fff" fill-opacity=".08"/>{seg}</g>
<g transform="translate(0 66)">{leg}</g></g>
<text x="1160" y="424" text-anchor="end" font-size="12" fill="#7f85b8">Last commit {lastc} · Updated {upd}</text>
</svg>'''

if __name__ == "__main__":
    user = demo() if "--demo" in sys.argv else fetch()
    s = analyse(user)
    (ROOT / "assets" / "dashboard.svg").write_text(build(s), encoding="utf-8")
    print(f"streak {s['cur']} | best {s['best']} | total {s['total']} | top {s['top']['count']}")
