#!/usr/bin/env python3
"""Render 31 complete UTC days from GitHub's contribution calendar to SVG.

Uses only the Python standard library. GITHUB_TOKEN is read from the environment.
The destination is replaced only after the response and SVG have been validated.
"""

import argparse
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

QUERY = '''
query ProfileActivity($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
'''


def read_days(payload, first_day, number_of_days=31):
    if payload.get('errors'):
        raise ValueError('GitHub GraphQL returned errors; check Actions permissions and API limits.')
    user = payload.get('data', {}).get('user')
    if not user:
        raise ValueError('GitHub did not return the requested user.')
    weeks = user['contributionsCollection']['contributionCalendar']['weeks']
    expected = [first_day + dt.timedelta(days=i) for i in range(number_of_days)]
    wanted = set(expected)
    counts = {}
    for week in weeks:
        for entry in week['contributionDays']:
            day = dt.date.fromisoformat(entry['date'])
            if day not in wanted:
                continue
            count = entry['contributionCount']
            if type(count) is not int or count < 0 or day in counts:
                raise ValueError('Invalid or duplicate contribution data.')
            counts[day] = count
    if set(counts) != wanted:
        raise ValueError('The contribution calendar is incomplete; the previous SVG is retained.')
    return [(day, counts[day]) for day in expected]


def render_svg(username, days):
    if len(days) < 2:
        raise ValueError('At least two days are required.')
    width, height = 900, 300
    left, right, top, bottom = 58, 864, 96, 238
    peak = max(count for _, count in days)
    # Integer grid labels, including a usable scale for zero-activity periods.
    tick = max(1, math.ceil(peak / 4))
    maximum = tick * 4
    points = [(left + i * (right - left) / (len(days) - 1),
               bottom - count * (bottom - top) / maximum)
              for i, (_, count) in enumerate(days)]
    line = 'M ' + ' L '.join(f'{x:.2f},{y:.2f}' for x, y in points)
    area = f'{line} L {right},{bottom} L {left},{bottom} Z'
    title = f'{username} · Contribution activity'
    date_range = f'{days[0][0].isoformat()} to {days[-1][0].isoformat()} (UTC)'
    summary = f'{sum(count for _, count in days)} contributions · {date_range}'
    description = '; '.join(f'{day.isoformat()}: {count}' for day, count in days)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{html.escape(title)}</title>',
        f'<desc id="desc">{html.escape(summary + "; " + description)}</desc>',
        '<rect width="900" height="300" rx="10" fill="#1a1b27"/>',
        '<g font-family="DejaVu Sans,Arial,sans-serif">',
        f'<text x="30" y="36" fill="#70a5fd" font-size="20" font-weight="600">{html.escape(title)}</text>',
        f'<text x="30" y="62" fill="#a9b1d6" font-size="13">{html.escape(summary)}</text>',
    ]
    for i in range(5):
        y = bottom - i * (bottom - top) / 4
        parts.append(f'<path d="M {left},{y:.2f} H {right}" stroke="#2f334d"/>')
        parts.append(f'<text x="{left - 12}" y="{y + 4:.2f}" text-anchor="end" fill="#a9b1d6" font-size="12">{i * tick}</text>')
    parts.extend([
        f'<path d="{area}" fill="#7aa2f7" opacity="0.12"/>',
        f'<path d="{line}" fill="none" stroke="#7aa2f7" stroke-width="2.5" stroke-linejoin="round"/>',
    ])
    for (day, count), (x, y) in zip(days, points):
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#bb9af7"><title>{day.isoformat()}: {count}</title></circle>')
    for i in sorted(set(range(0, len(days), 5)) | {len(days) - 1}):
        x = points[i][0]
        label = days[i][0].strftime('%m/%d')
        parts.append(f'<text x="{x:.2f}" y="263" text-anchor="middle" fill="#a9b1d6" font-size="12">{label}</text>')
    parts.append('<text x="864" y="286" text-anchor="end" fill="#a9b1d6" font-size="11">Last 31 complete UTC days · GitHub contribution calendar</text>')
    parts.extend(['</g>', '</svg>'])
    svg = '\n'.join(parts) + '\n'
    ET.fromstring(svg)
    return svg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', args.username):
        parser.error('Invalid GitHub username.')
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        parser.error('GITHUB_TOKEN must be set in the environment.')
    end = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=31)
    variables = {
        'login': args.username,
        'from': start.isoformat().replace('+00:00', 'Z'),
        'to': (end - dt.timedelta(seconds=1)).isoformat().replace('+00:00', 'Z'),
    }
    request = urllib.request.Request(
        'https://api.github.com/graphql',
        data=json.dumps({'query': QUERY, 'variables': variables}).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
                 'User-Agent': 'profile-svg-workflow'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
        days = read_days(payload, start.date())
        svg = render_svg(args.username, days)
    except (urllib.error.URLError, ValueError, KeyError, TypeError, TimeoutError) as error:
        raise SystemExit(f'Could not generate activity SVG: {error}') from None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.svg.tmp')
    temporary.write_text(svg, encoding='utf-8')
    temporary.replace(args.output)
    print(f'Generated {args.output} from {len(days)} complete UTC days.')


if __name__ == '__main__':
    main()
