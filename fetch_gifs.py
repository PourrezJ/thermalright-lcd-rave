#!/usr/bin/env python3
"""Télécharge des GIFs d'anime (SFW) depuis l'API publique nekos.best.

Usage : python3 fetch_gifs.py [--per 20] [cat ...]
Les GIFs vont dans ./gifs/<categorie>-<id>.gif. Ceux déjà présents sont gardés.
Tu peux aussi déposer tes propres GIFs/MP4 dans ./gifs/ : ils sont pris
automatiquement par rave.py (l'énergie de la catégorie est devinée par le nom :
"dance" = groove, punch/kick/shoot/yeet = frappe sur les gros beats).
"""
from __future__ import annotations
import argparse, json, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / 'gifs'
DEFAULT = {'dance': 40, 'punch': 12, 'kick': 12, 'shoot': 12, 'yeet': 8, 'smug': 6, 'run': 6}
UA = {'User-Agent': 'trcc-rave-v7/1.0 (personal LCD visualizer)'}

def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--per', type=int, default=0, help='nombre par catégorie (0 = défaut)')
    p.add_argument('cats', nargs='*')
    a = p.parse_args()
    cats = {c: (a.per or DEFAULT.get(c, 10)) for c in (a.cats or DEFAULT)}
    OUT.mkdir(exist_ok=True)
    total = 0
    for cat, want in cats.items():
        have = {f.stem for f in OUT.glob(f'{cat}-*.gif')}
        tries = 0
        while len(have) < want and tries < 6:
            tries += 1
            n = min(20, want - len(have) + 4)
            try:
                res = json.loads(get(f'https://nekos.best/api/v2/{cat}?amount={n}'))['results']
            except Exception as e:
                print(f'[{cat}] API erreur: {e}', file=sys.stderr); time.sleep(2); continue
            for item in res:
                gid = item['url'].rsplit('/', 1)[-1].removesuffix('.gif')[:8]
                name = f'{cat}-{gid}'
                if name in have or len(have) >= want:
                    continue
                try:
                    data = get(item['url'])
                except Exception as e:
                    print(f'  ! {name}: {e}', file=sys.stderr); continue
                if not data.startswith(b'GIF8'):
                    continue
                (OUT / f'{name}.gif').write_bytes(data)
                (OUT / f'{name}.txt').write_text(item.get('anime_name', '') + '\n')
                have.add(name); total += 1
                print(f'  + {name}.gif  ({item.get("anime_name","?")}, {len(data)//1024} Ko)')
            time.sleep(0.5)
        print(f'[{cat}] {len(have)}/{want}')
    print(f'{total} nouveaux GIFs -> {OUT}')

if __name__ == '__main__':
    main()
