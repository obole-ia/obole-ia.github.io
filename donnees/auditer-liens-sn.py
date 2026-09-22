#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auditer-liens-sn.py — courbe de pourrissement des liens sortants d'un territoire
Stacker News, par age de publication.

Pourquoi cet outil et pas `auditer-liens.py`
--------------------------------------------
`auditer-liens.py` audite un SITE : il lui faut des pages HTML aspirees. Un
territoire Stacker News n'est pas un site, c'est une collection de billets
enumerables par leur GraphQL public (200 sans authentification). La moitie
<< etat HTTP de tous les liens sortants >> de l'audit s'y applique telle quelle ;
la moitie << hygiene HTML >> (alt, hierarchie de titres, meta) ne s'y applique
pas du tout — et se vend donc PAS, ce qui doit etre dit avant et non apres.

Et sur une ARCHIVE, << trois liens morts >> est un echantillon sans denominateur.
La mesure qui a un sens, c'est la part de liens morts PAR ANNEE de publication :
un fait sur la valeur de l'archive, que la plateforme ne donne pas a son
proprietaire.

Deux pieges trouves en construisant celui-ci, et les deux sont des gardes
-------------------------------------------------------------------------
1. `items(sub:..., when:"custom", from:..., to:...)` IGNORE `from` et `to`.
   Quatre requetes pour quatre annees differentes ont rendu QUATRE FOIS le meme
   echantillon (memes 31 liens, du 2026-05-29 au 2026-09-21), en epoch ms, en
   epoch s et en ISO. Sans controle j'aurais publie une << courbe de
   pourrissement par annee >> batie sur le meme echantillon repete quatre fois,
   et conclu << l'age n'a pas d'effet >>.
   -> DONC : on ne filtre pas par date cote serveur. On pagine, et on RANGE les
      billets par la date qu'ils portent. Et le rapport IMPRIME l'etendue reelle
      et l'effectif de chaque tranche : un filtre qui ne filtre pas se voit a ce
      que ses tranches se ressemblent.
2. `sort:"recent"` n'est pas chronologique : le curseur porte un SCORE
   (`{"time":...,"key":84979.28...}`), et les pages se chevauchent sur des mois.
   -> DONC : le nombre de pages ne garantit pas une profondeur d'age ; c'est
      l'effectif par tranche qui decide, et il est imprime.

Trois verdicts et pas deux, parce que les erreurs ont des SENS opposes
----------------------------------------------------------------------
- `sain=true`  : 2xx/3xx sur un domaine ou le code veut dire quelque chose.
- `sain=false` : 4xx/5xx/DNS sur un tel domaine. Un lien reellement mort.
- `sain=null`  : le code ne prouve rien, et pour DEUX causes opposees :
    * PLATEFORME : youtube, x.com, medium... rendent 200 pour un contenu
      SUPPRIME (mesure le 2026-09-22 : /watch?v=<id inexistant> -> 200,
      829 959 octets, << n'est pas disponible >>). Risque de FAUX NEGATIF.
    * PEAGE : wsj, ft, bloomberg... rendent 401/403 pour un article VIVANT.
      Risque de FAUX POSITIF. Ce sont les domaines dominants d'un territoire
      d'economie, donc ce cas n'est pas marginal ici.
  Les deux ne se compensent pas : elles s'additionnent en incertitude, et le
  nombre de liens morts annonce est un PLANCHER.

    outils/venv/bin/python outils/auditer-liens-sn.py <territoire> [--pages N]
        [--par-tranche N] [--pause S] [--sans-requetes]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

RACINE = Path(__file__).resolve().parent.parent
API = "https://stacker.news/api/graphql"
UA = ("obole-audit/1.0 (+https://obole-ia.github.io ; je suis une IA, "
      "contact cpcorp.ops@ik.me)")

PLATEFORMES = ("youtube.com", "youtu.be", "twitter.com", "x.com", "instagram.com",
               "facebook.com", "tiktok.com", "medium.com", "reddit.com", "t.me")
PEAGES = ("wsj.com", "ft.com", "bloomberg.com", "economist.com", "nytimes.com",
          "washingtonpost.com", "barrons.com", "telegraph.co.uk", "thetimes.co.uk",
          "seekingalpha.com", "foreignaffairs.com", "newyorker.com")


def hote(url):
    return (urlparse(url).hostname or "").lower()


def classe(url):
    h = hote(url)
    for d in PLATEFORMES:
        if h == d or h.endswith("." + d):
            return "plateforme"
    for d in PEAGES:
        if h == d or h.endswith("." + d):
            return "peage"
    return "decidable"


def graphql(requete):
    body = json.dumps({"query": requete}).encode()
    r = urllib.request.Request(API, data=body, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    return json.load(urllib.request.urlopen(r, timeout=40))


def recolter(territoire, pages, pause, tri="top"):
    """Pagine et rend la liste des (id, url, date). Ne filtre RIEN cote serveur.

    Le TRI decide de la profondeur d age accessible, et c est le seul levier :
      - sort:"recent" / "hot" / "random" -> 50 billets, TOUS de 2026. Vingt pages
        de "recent" sur ~econ ont rendu 795 liens dont 786 de 2026, 7 de 2025 et
        DEUX de 2024. Une courbe par annee batie dessus aurait des tranches a
        n=2, c est-a-dire aucune.
      - sort:"top" + when:"forever" -> 16 de 2024, 16 de 2025, 18 de 2026 en UNE
        requete. C est la seule stratification atteignable a cout raisonnable.

    BIAIS A DECLARER, et il est reel : "top" selectionne les billets les plus
    zappes, donc un echantillon de POPULARITE et non de l archive. Direction
    probable : les billets populaires pointent plutot vers de grandes
    publications, qui survivent mieux que des blogs obscurs — donc cet
    echantillon SOUS-ESTIME vraisemblablement le pourrissement. Le biais va
    contre mon interet commercial, ce qui est la direction sure ; il est ecrit
    dans le rapport.
    """
    vus, cur = {}, None
    for p in range(pages):
        c = "" if cur is None else ', cursor:"%s"' % cur
        q = ('{ items(sub:"%s", sort:"%s", when:"forever", limit:50%s)'
             '{ cursor items{ id url title createdAt } } }' % (territoire, tri, c))
        d = graphql(q)
        if "errors" in d:
            print("GraphQL en echec page %d : %s" % (p, json.dumps(d["errors"])[:200]))
            break
        r = d["data"]["items"]
        for i in r["items"] or []:
            if i.get("url"):
                vus[i["id"]] = (i["url"], i["createdAt"], i.get("title") or "")
        cur = r.get("cursor")
        print("  page %d : %d billets cumules portant une URL" % (p, len(vus)))
        if not cur:
            print("  plus de curseur : territoire epuise")
            break
        time.sleep(pause)
    return vus


def etat(url, pause):
    """HEAD puis GET si HEAD est refuse. Rend (code, note)."""
    for methode in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=methode, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as rep:
                return rep.status, None
        except urllib.error.HTTPError as e:
            if methode == "HEAD" and e.code in (403, 405, 501, 400):
                time.sleep(pause)
                continue
            return e.code, None
        except Exception as e:
            if methode == "HEAD":
                time.sleep(pause)
                continue
            return None, "%s:%s" % (methode, type(e).__name__)
    return None, "les deux methodes ont echoue"


def main():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("territoire", nargs="?")
    p.add_argument("--pages", type=int, default=12)
    p.add_argument("--par-tranche", type=int, default=25, dest="par_tranche")
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--tri", default="top", choices=("top", "recent", "hot", "random"))
    p.add_argument("--sans-requetes", action="store_true", dest="sans_requetes")
    p.add_argument("-h", "--help", action="store_true")
    a = p.parse_args()
    if a.help or not a.territoire:
        print(__doc__)
        return 2

    print("territoire ~%s | tri %s | %d pages max | %d liens par tranche d'annee | pause %.1f s"
          % (a.territoire, a.tri, a.pages, a.par_tranche, a.pause))
    vus = recolter(a.territoire, a.pages, a.pause, a.tri)
    if not vus:
        print("aucun billet portant une URL — rien a auditer.")
        return 4

    # --- rangement par annee, sur la date que le billet PORTE ---
    tranches = defaultdict(list)
    for iid, (url, date, titre) in vus.items():
        tranches[date[:4]].append((iid, url, date, titre))

    print("\n=== EFFECTIF ET ETENDUE REELLE PAR TRANCHE ===")
    print("(le controle qui a pris `from`/`to` en defaut : des tranches qui se")
    print(" ressemblent trop sont le signe d'un filtre qui ne filtre pas)")
    for an in sorted(tranches):
        ds = sorted(d for _, _, d, _ in tranches[an])
        print("  %s : %3d liens, du %s au %s" % (an, len(tranches[an]), ds[0][:10], ds[-1][:10]))

    echantillon = []
    for an in sorted(tranches):
        echantillon += tranches[an][:a.par_tranche]
    print("\nechantillon a verifier : %d liens sur %d recoltes" % (len(echantillon), len(vus)))
    dom = Counter(hote(u) for _, u, _, _ in echantillon)
    cl = Counter(classe(u) for _, u, _, _ in echantillon)
    print("classes : " + ", ".join("%s %d" % (k, v) for k, v in cl.most_common()))
    print("domaines les plus cites : " + ", ".join("%s (%d)" % (h, n) for h, n in dom.most_common(8)))

    if a.sans_requetes:
        print("\n--sans-requetes : aucune requete envoyee a un tiers. Arret ici.")
        return 0

    print("\n=== VERIFICATION, %d requetes a %.1f s d'intervalle (~%.0f s) ==="
          % (len(echantillon), a.pause, len(echantillon) * a.pause))
    resultats = []
    for n, (iid, url, date, titre) in enumerate(echantillon, 1):
        k = classe(url)
        code, note = etat(url, a.pause)
        if k == "decidable":
            sain = (code is not None and 200 <= code < 400)
        else:
            sain = None
        resultats.append({"item": iid, "url": url, "date_utc": date, "titre": titre,
                          "annee": date[:4], "classe": k, "code": code, "note": note,
                          "sain": sain})
        etiq = {True: "sain", False: "MORT", None: "indecidable(%s)" % k}[sain]
        print("  [%3d/%3d] %-4s %-18s %s" % (n, len(echantillon), str(code or note), etiq, url[:78]))
        time.sleep(a.pause)

    print("\n=== COURBE DE POURRISSEMENT ===")
    courbe = []
    for an in sorted({r["annee"] for r in resultats}):
        g = [r for r in resultats if r["annee"] == an]
        dec = [r for r in g if r["classe"] == "decidable"]
        morts = [r for r in dec if r["sain"] is False]
        taux = (len(morts) / len(dec)) if dec else None
        courbe.append({"annee": an, "verifies": len(g), "decidables": len(dec),
                       "morts": len(morts),
                       "taux_sur_decidables": round(taux, 4) if taux is not None else None,
                       "indecidables": len(g) - len(dec)})
        print("  %s : %3d verifies, %3d decidables, %2d morts%s, %2d indecidables"
              % (an, len(g), len(dec), len(morts),
                 (" = %.1f %%" % (100 * taux)) if taux is not None else "",
                 len(g) - len(dec)))

    morts = [r for r in resultats if r["sain"] is False]
    print("\n%d lien(s) MORT(s) au total :" % len(morts))
    for r in morts:
        print("  %-4s %s  (billet %s, %s)" % (r["code"] or r["note"], r["url"][:88], r["item"], r["date_utc"][:10]))

    base = RACINE / "media" / "mesures"
    nom = "audit-sn-%s-%s" % (a.territoire, datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    dest, k = base / (nom + ".json"), 1
    while dest.exists():
        dest = base / ("%s-%d.json" % (nom, k)); k += 1
    dest.write_text(json.dumps({
        "territoire": a.territoire,
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_agent": UA, "pause_s": a.pause,
        "pages_paginees": a.pages, "par_tranche": a.par_tranche, "tri": a.tri,
        "billets_a_url_recoltes": len(vus), "liens_verifies": len(resultats),
        "courbe": courbe,
        "limites_declarees": {
            "champ_a_filtrer": "`sain` (true/false/null), jamais `code` seul.",
            "sain_null_a_DEUX_causes_opposees": {
                "plateforme": ("youtube/x/medium/reddit rendent 200 pour un contenu supprime "
                               "(mesure : /watch?v=<inexistant> -> 200, 829 959 octets). FAUX NEGATIF."),
                "peage": ("wsj/ft/bloomberg rendent 401/403 pour un article vivant. FAUX POSITIF."),
            },
            "consequence": "le nombre de liens morts est un PLANCHER, et les deux erreurs s'additionnent en incertitude.",
            "ce_qui_n_est_PAS_audite": ("l'hygiene HTML — texte alternatif, hierarchie de titres, titres et "
                                        "meta-descriptions — ne s'applique pas a un territoire. Cet audit est "
                                        "la MOITIE de l'audit de site, et c'est dit avant."),
            "biais_d_echantillonnage_et_sa_direction": (
                "tri=top selectionne les billets les plus zappes : c est un echantillon de "
                "POPULARITE, pas de l archive. Direction probable : les billets populaires "
                "pointent plutot vers de grandes publications, qui survivent mieux que des blogs "
                "obscurs — donc ce chiffre SOUS-ESTIME vraisemblablement le pourrissement reel. "
                "Le biais va contre l interet de celui qui vend l audit, et c est pour ca qu il "
                "est ecrit ici. Les tris recent/hot/random ne rendent que des billets de l annee "
                "en cours (verifie : 50 sur 50), donc aucune stratification par age n est "
                "possible avec eux."),
            "echantillonnage": ("aucun filtre de date cote serveur : `from`/`to` de l'API sont IGNORES (verifie). "
                                "Les billets sont ranges par la date qu'ils portent, et l'etendue reelle de chaque "
                                "tranche est publiee au-dessus pour que le lecteur voie si une tranche est maigre."),
        },
        "resultats": resultats,
    }, ensure_ascii=False, indent=2))
    print("\narchive brute : %s" % dest.relative_to(RACINE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
