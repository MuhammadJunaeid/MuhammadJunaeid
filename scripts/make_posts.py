"""Builds LinkedIn post cards from posts.json and refreshes the README block.
Edit posts.json (newest first, max 3), commit, and the workflow does the rest."""
import json, re, textwrap, html, pathlib

FONT = "-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Inter,Roboto,Helvetica,Arial,sans-serif"
PAIRS = [("#0A84FF", "#64D2FF"), ("#BF5AF2", "#FF375F"), ("#FF9F0A", "#FF375F")]
root = pathlib.Path(__file__).resolve().parent.parent
posts = json.loads((root / "posts.json").read_text(encoding="utf-8"))[:3]

def card(i, p):
    c1, c2 = PAIRS[i % 3]
    lines = textwrap.wrap(p["title"], width=27)[:3]
    if len(textwrap.wrap(p["title"], width=27)) > 3:
        lines[-1] = lines[-1].rstrip(" .,") + "…"
    t = "".join(f'<text x="32" y="{112+k*26}" font-size="21" font-weight="800" fill="#fff">{html.escape(l)}</text>' for k, l in enumerate(lines))
    date = html.escape(p.get("date", ""))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="262" viewBox="0 0 400 262" role="img" aria-label="LinkedIn post: {html.escape(p["title"])}">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#20203f"/><stop offset="1" stop-color="#0e0e1f"/></linearGradient>
<linearGradient id="bd" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}" stop-opacity=".95"/><stop offset=".5" stop-color="#fff" stop-opacity=".10"/><stop offset="1" stop-color="{c2}" stop-opacity=".85"/></linearGradient>
<linearGradient id="ic" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>
<linearGradient id="sh" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".1"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<filter id="bl" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="30"/></filter>
<clipPath id="c"><rect width="392" height="250" rx="30"/></clipPath>
</defs>
<style>text{{font-family:{FONT}}}.b{{transform-box:fill-box;transform-origin:center;animation:b 3.2s ease-in-out infinite}}@keyframes b{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.12)}}}}.arr{{animation:ar 1.4s ease-in-out infinite}}@keyframes ar{{0%,100%{{transform:translateX(0)}}50%{{transform:translateX(6px)}}}}</style>
<rect x="4" y="4" width="392" height="250" rx="30" fill="url(#bg)"/>
<g clip-path="url(#c)" transform="translate(4 4)"><circle cx="340" cy="30" r="100" fill="{c1}" opacity=".5" filter="url(#bl)" class="b"/><circle cx="30" cy="240" r="80" fill="{c2}" opacity=".3" filter="url(#bl)"/>
<rect width="90" height="250" fill="url(#sh)" transform="skewX(-20)"><animate attributeName="x" values="-200;560" dur="6s" repeatCount="indefinite"/></rect></g>
<rect x="4.75" y="4.75" width="390.5" height="248.5" rx="30" fill="none" stroke="url(#bd)" stroke-width="1.5"/>
<rect x="32" y="30" width="44" height="44" rx="13" fill="url(#ic)"/><text x="54" y="61" text-anchor="middle" font-size="24" font-weight="800" fill="#fff">in</text>
<text x="88" y="49" font-size="12.5" font-weight="800" letter-spacing="1.6" fill="#fff">LINKEDIN</text>
<text x="88" y="66" font-size="13" fill="#9ea4d6">{date}</text>
{t}
<rect x="32" y="206" width="132" height="34" rx="17" fill="url(#ic)"/><text x="50" y="228" font-size="14.5" font-weight="700" fill="#fff">Read post</text><text class="arr" x="138" y="229" font-size="16" font-weight="800" fill="#fff">→</text>
</svg>'''

blocks = []
for i, p in enumerate(posts):
    (root / "assets" / f"post{i+1}.svg").write_text(card(i, p), encoding="utf-8")
    blocks.append(f'<a href="{html.escape(p["url"])}"><img src="assets/post{i+1}.svg" alt="LinkedIn post: {html.escape(p["title"])}" width="32%"/></a>')

readme = root / "README.md"
txt = readme.read_text(encoding="utf-8")
new = "<!--POSTS_START-->\n" + "\n".join(blocks) + "\n<!--POSTS_END-->"
txt = re.sub(r"<!--POSTS_START-->.*?<!--POSTS_END-->", new, txt, flags=re.S)
readme.write_text(txt, encoding="utf-8")
print(f"Updated {len(posts)} post cards")
