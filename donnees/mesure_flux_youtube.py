#!/usr/bin/env python3
"""Mesure la FIABILITE du flux Atom de YouTube comme source de compteurs de vues.

CE QUE CE SCRIPT MESURE, ET POURQUOI IL EXISTE
----------------------------------------------
Le flux `https://www.youtube.com/feeds/videos.xml?channel_id=...` porte
`<media:statistics views="N">` pour chaque video de la chaine. Il ne demande ni session ni cle
d'API, il pese quelques kilo-octets, et c'est donc la source evidente pour suivre ses propres
vues par programme.

Le 18/09 j'ai decouvert qu'il sert des valeurs CONTRADICTOIRES pour la meme video a quelques
secondes d'intervalle, et qu'il rend regulierement HTTP 404 sur une URL qui existe. Ce script
mesure les deux, pour que le chiffre soit verifiable au lieu d'etre raconte.

Il ne mesure PAS si le flux dit vrai : je n'ai aucun acces a la valeur de reference (elle vit
dans YouTube Studio, derriere une session). Il mesure la COHERENCE INTERNE de la source — ce
qu'elle repond a la meme question posee plusieurs fois de suite. Une source qui se contredit
elle-meme ne peut pas etre plus exacte que sa propre dispersion.

METHODE, et les deux precautions qui la rendent lisible
------------------------------------------------------
* Le meme User-Agent a chaque appel, pour qu'une difference ne vienne pas de la negociation.
* La TAILLE de la reponse est enregistree a chaque appel. C'est ce qui permet d'affirmer que
  deux reponses differentes ne se distinguent pas par leur poids : le 18/09, douze lectures
  rendaient 6 118 octets a chaque fois, alors que la valeur de vues changeait. Sans cette
  colonne, on croirait avoir recu deux documents differents.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = "obole-mesure/1.0 (+https://obole-ia.github.io)"


def une_lecture(url):
    """Rend un dict decrivant UN appel : code, octets, et les vues par video."""
    debut = time.monotonic()
    ligne = {"t_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as rep:
            corps = rep.read()
            ligne["http"] = rep.status
            ligne["octets"] = len(corps)
            texte = corps.decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        ligne["http"] = err.code
        ligne["octets"] = 0
        ligne["erreur"] = "HTTPError %s" % err.code
        texte = ""
    except (urllib.error.URLError, OSError) as err:
        ligne["http"] = None
        ligne["octets"] = 0
        ligne["erreur"] = str(err)[:120]
        texte = ""
    ligne["ms"] = round((time.monotonic() - debut) * 1000)

    vues = {}
    entrees = re.findall(r"<entry>.*?</entry>", texte, re.S)
    ligne["entrees"] = len(entrees)
    for entree in entrees:
        ident = re.search(r"<yt:videoId>(.*?)</yt:videoId>", entree)
        v = re.search(r"views=\"(\d+)\"", entree)
        if ident:
            vues[ident.group(1)] = int(v.group(1)) if v else None
    ligne["vues"] = vues
    return ligne


def resume(lectures, videos):
    """Ce qui fait la valeur de la mesure : la dispersion, pas la moyenne."""
    utilisables = [l for l in lectures if l["entrees"] > 0]
    echecs = [l for l in lectures if l["entrees"] == 0]
    out = {
        "lectures": len(lectures),
        "utilisables": len(utilisables),
        "sans_entree": len(echecs),
        "taux_echec": round(len(echecs) / len(lectures), 3) if lectures else None,
        "codes_http": {},
        "tailles_octets": {},
        "par_video": {},
    }
    for l in lectures:
        c = str(l["http"])
        out["codes_http"][c] = out["codes_http"].get(c, 0) + 1
        if l["octets"]:
            t = str(l["octets"])
            out["tailles_octets"][t] = out["tailles_octets"].get(t, 0) + 1
    for vid in videos:
        serie = [l["vues"].get(vid) for l in utilisables if vid in l["vues"]]
        serie = [x for x in serie if x is not None]
        distinctes = sorted(set(serie))
        compte = {str(v): serie.count(v) for v in distinctes}
        out["par_video"][vid] = {
            "lectures_avec_valeur": len(serie),
            "valeurs_distinctes": distinctes,
            "occurrences": compte,
            "min": min(serie) if serie else None,
            "max": max(serie) if serie else None,
            "ecart": (max(serie) - min(serie)) if serie else None,
            "se_contredit": len(distinctes) > 1,
        }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chaine", default="UCbvUo1iacAqo9OeyuN0ErgA")
    ap.add_argument("--lectures", type=int, default=30)
    ap.add_argument("--pause", type=float, default=4.0)
    ap.add_argument("--sortie", default=None)
    a = ap.parse_args()

    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + a.chaine
    print("mesure : %d lectures, %.1f s d'intervalle, meme UA" % (a.lectures, a.pause))
    lectures = []
    for i in range(a.lectures):
        l = une_lecture(url)
        lectures.append(l)
        vues = " ".join("%s=%s" % (k[:6], v) for k, v in sorted(l["vues"].items()))
        print("  %02d  HTTP=%-4s %6d o  %4d ms  entrees=%d  %s"
              % (i + 1, l["http"], l["octets"], l["ms"], l["entrees"], vues))
        if i < a.lectures - 1:
            time.sleep(a.pause)

    videos = sorted({v for l in lectures for v in l["vues"]})
    r = resume(lectures, videos)
    doc = {
        "mesure": "fiabilite du flux Atom de YouTube comme source de compteurs de vues",
        "url": url,
        "user_agent": UA,
        "debut_utc": lectures[0]["t_utc"],
        "fin_utc": lectures[-1]["t_utc"],
        "intervalle_s": a.pause,
        "ce_qui_est_mesure": ("la COHERENCE INTERNE de la source : ce qu'elle repond a la meme "
                              "question posee plusieurs fois de suite. Pas son exactitude, que "
                              "je ne peux pas evaluer faute d'acces a une valeur de reference."),
        "resume": r,
        "lectures": lectures,
    }
    chemin = Path(a.sortie or ("media/mesures/flux-youtube-%s.json"
                               % datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")))
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("\n== resume")
    print("  lectures utilisables : %d / %d (taux d'echec %.1f %%)"
          % (r["utilisables"], r["lectures"], 100 * (r["taux_echec"] or 0)))
    print("  codes HTTP  : %s" % r["codes_http"])
    print("  tailles     : %s" % r["tailles_octets"])
    for vid, d in r["par_video"].items():
        print("  %s : %d valeur(s) distincte(s) %s, ecart %s %s"
              % (vid, len(d["valeurs_distinctes"]), d["valeurs_distinctes"], d["ecart"],
                 "<- SE CONTREDIT" if d["se_contredit"] else ""))
    print("  -> %s" % chemin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
