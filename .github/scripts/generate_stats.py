#!/usr/bin/env python3
"""Generate self-hosted GitHub stats cards (SVG) for the profile README.

Pulls data from the GitHub GraphQL API and writes:
  assets/stats.svg       overall stats
  assets/top-langs.svg   most used languages
  assets/streak.svg      contribution streaks
  assets/activity.svg    contributions over the last 30 days

Environment:
  GH_TOKEN   token for the GraphQL API (GITHUB_TOKEN works; a PAT with
             read:user also counts private contributions)
  GH_USER    GitHub login (default: praisino)
  MOCK_DATA  optional path to a JSON file to render without calling the API
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

USER = os.environ.get("GH_USER", "praisino")
OUT = Path(__file__).resolve().parents[2] / "assets"
API = "https://api.github.com/graphql"

# Tokyo Night palette
BG = "#1a1b27"
BORDER = "#2a2e48"
TITLE = "#70a5fd"
TEXT = "#a9b1d6"
MUTED = "#565f89"
ICON = "#bf91f3"
ACCENT = "#38bdae"
FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Sans-Serif"

# Languages that are mostly generated/markup noise and skew the chart
EXCLUDED_LANGS = {"Jupyter Notebook"}


# --------------------------------------------------------------------------- data

def gql(query: str, variables: dict | None = None) -> dict:
    token = os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("GH_TOKEN is not set")
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-stats-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {json.dumps(payload['errors'], indent=2)}")
    return payload["data"]


PROFILE_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    name
    login
    createdAt
    followers { totalCount }
    pullRequests(first: 1) { totalCount }
    issues(first: 1) { totalCount }
    repositoriesContributedTo(first: 1, contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]) { totalCount }
    contributionsCollection { contributionYears }
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        forkCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""


def fetch() -> dict:
    repos, cursor, user = [], None, None
    while True:
        user = gql(PROFILE_QUERY, {"login": USER, "cursor": cursor})["user"]
        page = user["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    now = dt.datetime.now(dt.timezone.utc)
    parts = []
    for year in sorted(user["contributionsCollection"]["contributionYears"]):
        start = f"{year}-01-01T00:00:00Z"
        end = min(dt.datetime(year, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc), now)
        parts.append(
            f'y{year}: contributionsCollection(from: "{start}", to: "{end.strftime("%Y-%m-%dT%H:%M:%SZ")}") {{'
            " totalCommitContributions restrictedContributionsCount"
            " contributionCalendar { weeks { contributionDays { date contributionCount } } } }"
        )
    years = gql(
        "query($login: String!) { user(login: $login) { " + " ".join(parts) + " } }",
        {"login": USER},
    )["user"]

    commits, days = 0, {}
    for coll in years.values():
        commits += coll["totalCommitContributions"] + coll["restrictedContributionsCount"]
        for week in coll["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                days[d["date"]] = d["contributionCount"]

    langs: dict[str, dict] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            entry = langs.setdefault(name, {"size": 0, "count": 0, "color": edge["node"]["color"] or MUTED})
            entry["size"] += edge["size"]
            entry["count"] += 1

    return {
        "name": user["name"] or user["login"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "forks": sum(r["forkCount"] for r in repos),
        "repos": user["repositories"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "prs": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "contributed_to": user["repositoriesContributedTo"]["totalCount"],
        "commits": commits,
        "days": days,
        "langs": langs,
        "generated": now.strftime("%Y-%m-%d"),
    }


# --------------------------------------------------------------------------- helpers

def esc(s) -> str:
    return html.escape(str(s), quote=True)


def fmt(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 10_000 else f"{n:,}"


def fmt_date(d: dt.date, with_year: bool = True) -> str:
    return d.strftime("%b %-d, %Y" if with_year else "%b %-d")


def card(width: int, height: int, title: str, body: str, label: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="t">
<title id="t">{esc(label)}</title>
<style>
  .title {{ font: 600 18px {FONT}; fill: {TITLE}; }}
  .label {{ font: 600 14px {FONT}; fill: {TEXT}; }}
  .value {{ font: 700 14px {FONT}; fill: {TEXT}; }}
  .muted {{ font: 400 12px {FONT}; fill: {MUTED}; }}
  .big   {{ font: 700 28px {FONT}; fill: {TEXT}; }}
  .fade  {{ opacity: 0; animation: fade .6s ease-in-out forwards; }}
  @keyframes fade {{ to {{ opacity: 1; }} }}
  @media (prefers-reduced-motion: reduce) {{ .fade {{ animation: none; opacity: 1; }} }}
</style>
<rect x="0.5" y="0.5" rx="6" width="{width - 1}" height="{height - 1}" fill="{BG}" stroke="{BORDER}"/>
<text x="25" y="35" class="title">{esc(title)}</text>
{body}
</svg>
"""


# Small 16px icons (Octicons, MIT)
ICONS = {
    "star": "M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25Z",
    "commit": "M11.93 8.5a4.002 4.002 0 0 1-7.86 0H.75a.75.75 0 0 1 0-1.5h3.32a4.002 4.002 0 0 1 7.86 0h3.32a.75.75 0 0 1 0 1.5Zm-1.43-.75a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z",
    "pr": "M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354Z",
    "issue": "M8 9.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0ZM1.5 8a6.5 6.5 0 1 0 13 0 6.5 6.5 0 0 0-13 0Z",
    "repo": "M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8ZM5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z",
    "people": "M2 5.5a3.5 3.5 0 1 1 5.898 2.549 5.508 5.508 0 0 1 3.034 4.084.75.75 0 1 1-1.482.235 4 4 0 0 0-7.9 0 .75.75 0 0 1-1.482-.236A5.507 5.507 0 0 1 3.102 8.05 3.493 3.493 0 0 1 2 5.5ZM11 4a3.001 3.001 0 0 1 2.22 5.018 5.01 5.01 0 0 1 2.56 3.012.749.749 0 0 1-.885.954.752.752 0 0 1-.549-.514 3.507 3.507 0 0 0-2.522-2.372.75.75 0 0 1-.574-.73v-.352a.75.75 0 0 1 .416-.672A1.5 1.5 0 0 0 11 5.5.75.75 0 0 1 11 4Zm-5.5-.5a2 2 0 1 0-.001 3.999A2 2 0 0 0 5.5 3.5Z",
}


# --------------------------------------------------------------------------- streaks

def streaks(days: dict[str, int]) -> dict:
    if not days:
        today = dt.date.today()
        return {"total": 0, "first": today, "cur": (0, None, None), "best": (0, None, None)}
    dates = sorted(dt.date.fromisoformat(d) for d in days)
    today = dates[-1]
    counts = {dt.date.fromisoformat(k): v for k, v in days.items()}
    total = sum(counts.values())
    first = next((d for d in dates if counts[d] > 0), dates[0])

    best, run, run_start = (0, None, None), 0, None
    for d in dates:
        if counts[d] > 0:
            run_start = d if run == 0 else run_start
            run += 1
            if run > best[0]:
                best = (run, run_start, d)
        else:
            run = 0

    # Current streak: today may still be empty without breaking it
    d = today if counts.get(today, 0) > 0 else today - dt.timedelta(days=1)
    cur_end, cur = d, 0
    while counts.get(d, 0) > 0:
        cur += 1
        d -= dt.timedelta(days=1)
    cur_t = (cur, d + dt.timedelta(days=1), cur_end) if cur else (0, None, None)
    return {"total": total, "first": first, "cur": cur_t, "best": best}


# --------------------------------------------------------------------------- cards

def stats_card(data: dict) -> str:
    year = str(dt.date.today().year)
    rows = [
        ("commit", "Total Commits", data["commits"]),
        ("star", "Total Stars Earned", data["stars"]),
        ("pr", "Total PRs", data["prs"]),
        ("issue", "Total Issues", data["issues"]),
        ("people", "Contributed to (last year)", data["contributed_to"]),
        ("commit", f"Contributions in {year}", sum(v for k, v in data["days"].items() if k.startswith(year))),
        ("star", "Active Days", sum(1 for v in data["days"].values() if v > 0)),
        ("repo", "Original Repos", data["repos"]),
        ("issue", "Languages Used", len(data["langs"])),
    ]
    # Show the six strongest non-zero metrics, keeping their listed order
    keep = [r for r in rows if r[2] > 0][:6]
    rows = [r for r in rows if r in keep]
    body = []
    for i, (icon, label, value) in enumerate(rows):
        y = 62 + i * 25
        body.append(
            f'<g class="fade" style="animation-delay:{150 + i * 100}ms" transform="translate(25,{y})">'
            f'<path transform="translate(0,-12)" fill="{ICON}" d="{ICONS[icon]}"/>'
            f'<text x="25" y="0" class="label">{esc(label)}:</text>'
            f'<text x="230" y="0" class="value">{fmt(value)}</text></g>'
        )
    # Contribution ring
    total = streaks(data["days"])["total"]
    r, cx, cy = 45, 400, 108
    circ = 2 * math.pi * r
    fill = min(1.0, math.log10(total + 1) / 4)  # 10k contributions == full ring
    body.append(
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{BORDER}" stroke-width="6"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{TITLE}" stroke-width="6" stroke-linecap="round" '
        f'transform="rotate(-90 {cx} {cy})" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{circ:.1f}">'
        f'<animate attributeName="stroke-dashoffset" from="{circ:.1f}" to="{circ * (1 - fill):.1f}" dur="1.2s" fill="freeze"/></circle>'
        f'<text x="{cx}" y="{cy + 3}" text-anchor="middle" class="big" style="font-size:{24 if total < 10_000 else 22}px">{fmt(total)}</text>'
        f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" class="muted" style="font-size:10px">contributions</text>'
    )
    return card(495, 215, f"{data['name']}'s GitHub Stats", "\n".join(body), "GitHub stats")


def langs_card(data: dict, limit: int = 8) -> str:
    langs = {k: v for k, v in data["langs"].items() if k not in EXCLUDED_LANGS}
    # Blend bytes and repo count so one huge repo doesn't dominate
    scored = sorted(
        ((name, math.sqrt(v["size"]) * math.sqrt(v["count"]), v["color"]) for name, v in langs.items()),
        key=lambda t: t[1],
        reverse=True,
    )[:limit]
    total = sum(s for _, s, _ in scored) or 1
    width, bar_w = 350, 300
    body = [f'<clipPath id="bar"><rect x="25" y="52" width="{bar_w}" height="8" rx="4"/></clipPath><g clip-path="url(#bar)">']
    x = 25.0
    for name, score, color in scored:
        w = bar_w * score / total
        body.append(f'<rect x="{x:.2f}" y="52" width="{w + 0.5:.2f}" height="8" fill="{color}"/>')
        x += w
    body.append("</g>")
    for i, (name, score, color) in enumerate(scored):
        col, row = i % 2, i // 2
        gx, gy = 25 + col * 155, 88 + row * 27
        body.append(
            f'<g class="fade" style="animation-delay:{150 + i * 80}ms" transform="translate({gx},{gy})">'
            f'<circle cx="5" cy="-5" r="5" fill="{color}"/>'
            f'<text x="16" y="0" class="label">{esc(name)}</text>'
            f'<text x="140" y="0" text-anchor="end" class="muted">{100 * score / total:.1f}%</text></g>'
        )
    return card(width, 215, "Most Used Languages", "\n".join(body), "Top languages")


def streak_card(data: dict) -> str:
    s = streaks(data["days"])
    cur, cs, ce = s["cur"]
    best, bs, be = s["best"]

    def rng(a, b):
        if not a:
            return "No active streak"
        return fmt_date(a) if a == b else f"{fmt_date(a, a.year != b.year)} - {fmt_date(b)}"

    cols = [
        (82, fmt(s["total"]), "Total Contributions", f"{fmt_date(s['first'])} - Present"),
        (247, fmt(cur), "Current Streak", rng(cs, ce)),
        (412, fmt(best), "Longest Streak", rng(bs, be)),
    ]
    body = [
        f'<line x1="165" y1="60" x2="165" y2="185" stroke="{BORDER}"/>',
        f'<line x1="330" y1="60" x2="330" y2="185" stroke="{BORDER}"/>',
    ]
    for i, (cx, value, label, sub) in enumerate(cols):
        hl = i == 1
        if hl:
            body.append(
                f'<circle cx="{cx}" cy="105" r="38" fill="none" stroke="{TITLE}" stroke-width="5"/>'
                f'<path transform="translate({cx - 9},55) scale(1.1)" fill="{ACCENT}" d="M9 0c1 4 7 6 7 12a7 7 0 1 1-14 0c0-3 2-5 3-6 0 2 1 3 2 3 0-4 1-7 2-9z"/>'
            )
        body.append(
            f'<g class="fade" style="animation-delay:{200 + i * 150}ms">'
            f'<text x="{cx}" y="{113 if hl else 108}" text-anchor="middle" class="big" style="fill:{TITLE if hl else TEXT}">{value}</text>'
            f'<text x="{cx}" y="{168 if hl else 140}" text-anchor="middle" class="label" style="fill:{ACCENT if hl else TEXT}">{label}</text>'
            f'<text x="{cx}" y="{188 if hl else 162}" text-anchor="middle" class="muted">{esc(sub)}</text></g>'
        )
    return card(495, 215, "Contribution Streak", "\n".join(body), "GitHub streak")


def activity_card(data: dict, span: int = 31) -> str:
    days = data["days"]
    last = max((dt.date.fromisoformat(d) for d in days), default=dt.date.today())
    series = [(last - dt.timedelta(days=span - 1 - i)) for i in range(span)]
    values = [days.get(d.isoformat(), 0) for d in series]
    width, height = 850, 300
    left, right, top, bottom = 55, 25, 60, 50
    pw, ph = width - left - right, height - top - bottom
    vmax = max(4, max(values))
    step = max(1, math.ceil(vmax / 4))
    vmax = step * 4

    def pt(i, v):
        return left + pw * i / (span - 1), top + ph * (1 - v / vmax)

    body = []
    for k in range(5):
        y = top + ph * k / 4
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="{BORDER}" stroke-dasharray="3 4"/>')
        body.append(f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" class="muted">{vmax - k * step}</text>')
    for i, d in enumerate(series):
        if i % 5 == 0 or i == span - 1:
            x, _ = pt(i, 0)
            body.append(f'<text x="{x:.1f}" y="{height - bottom + 22}" text-anchor="middle" class="muted">{fmt_date(d, False)}</text>')
    pts = [pt(i, v) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{left},{top + ph} " + line + f" {width - right},{top + ph}"
    body.append(
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{TITLE}" stop-opacity=".45"/><stop offset="100%" stop-color="{TITLE}" stop-opacity="0"/></linearGradient>'
        f'<clipPath id="reveal"><rect x="0" y="0" width="0" height="{height}"><animate attributeName="width" from="0" to="{width}" dur="1.5s" fill="freeze"/></rect></clipPath></defs>'
        f'<g clip-path="url(#reveal)"><polygon points="{area}" fill="url(#g)"/>'
        f'<polyline points="{line}" fill="none" stroke="{TITLE}" stroke-width="2.5" stroke-linejoin="round"/>'
    )
    for (x, y), v in zip(pts, values):
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{BG}" stroke="{ICON}" stroke-width="2"><title>{v}</title></circle>')
    body.append("</g>")
    body.append(
        f'<text x="{width - right}" y="35" text-anchor="end" class="muted">{sum(values)} contributions in the last {span} days</text>'
    )
    return card(width, height, "Contribution Activity", "\n".join(body), "Contribution activity graph")


# --------------------------------------------------------------------------- main

def main() -> None:
    mock = os.environ.get("MOCK_DATA")
    data = json.loads(Path(mock).read_text()) if mock else fetch()
    OUT.mkdir(exist_ok=True)
    cards = {
        "stats.svg": stats_card(data),
        "top-langs.svg": langs_card(data),
        "streak.svg": streak_card(data),
        "activity.svg": activity_card(data),
    }
    for name, svg in cards.items():
        (OUT / name).write_text(svg)
        print(f"wrote assets/{name}")
    s = streaks(data["days"])
    print(f"commits={data['commits']} contributions={s['total']} current={s['cur'][0]} longest={s['best'][0]}")


if __name__ == "__main__":
    main()
