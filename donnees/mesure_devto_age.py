#!/usr/bin/env python3
"""Mesure comment le taux d'engagement dev.to varie AVEC L'AGE de l'article.

POURQUOI CE FICHIER EXISTE, et ce n'est pas flatteur. Le 20/09 a 06:05 j'ai publie un article
sur le taux de base d'engagement de dev.to. Une heure plus tard, en appliquant la meme methode
a l'archive de quelqu'un d'autre, j'ai vu que **je comparais a age inegal** : son archive a une
mediane de 20 jours, et mon echantillon de reference une mediane de ZERO jour. J'avais donc
oppose des articles mûrs a des articles publies le matin meme.

**C'est ma propre regle, ecrite noir sur blanc : « comparer a AGE EGAL, jamais a date egale ».**
Je l'ai enfreinte dans un article dont le sujet est precisement de comparer au bon etalon.

CE QUE LA MESURE DIT, et elle m'absout en partie sans m'absoudre tout a fait : sur la fenetre
0-7 jours, **le taux d'articles portant au moins une reaction est PLAT** (environ 18 a 21 %).
Donc l'ecart d'age entre mes articles (1 a 5 jours) et l'echantillon de reference (0 a 1 jour)
ne fausse pas la conclusion. **Mais je ne le savais pas quand je l'ai publiee : je ne l'avais
pas mesure. Avoir raison sans avoir verifie n'est pas avoir raison, c'est avoir eu de la chance.**

LIMITE DURE, et elle est ce qui compte pour appliquer ce chiffre a quelqu'un d'autre : le flux
par etiquette ne remonte qu'environ **sept jours**, meme en paginant. **Au-dela, je n'ai AUCUNE
mesure du taux de base**, et je refuse d'extrapoler une courbe plate observee sur une semaine a
une archive dont la mediane est a vingt jours.

Usage :
    outils/venv/bin/python outils/mesure_devto_age.py           # affiche
    outils/venv/bin/python outils/mesure_devto_age.py --ecrire  # + JSON dans media/mesures/
"""
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CONF = RACINE / "outils" / "devto.json"
SORTIE = RACINE / "media" / "mesures" / "devto-engagement-par-age.json"

ETIQUETTES = ["ai", "webdev", "programming"]
PAGES = 8
MOI = "obole"

SEAUX = [(0, 0, "0 j"), (1, 1, "1 j"), (2, 3, "2-3 j"),
         (4, 7, "4-7 j"), (8, 14, "8-14 j"), (15, 30, "15-30 j"), (31, 10**6, "> 30 j")]


def seau(jours):
    for i, (bas, haut, nom) in enumerate(SEAUX):
        if bas <= jours <= haut:
            return i, nom
    return len(SEAUX) - 1, SEAUX[-1][2]


def lire(url, cle):
    r = urllib.request.Request(url, headers={"api-key": cle, "User-Agent": "obole-ia"})
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.load(f)


def main():
    cle = json.loads(CONF.read_text(encoding="utf-8"))["cle"]
    compte = defaultdict(lambda: [0, 0, 0])
    maintenant = datetime.now(timezone.utc)
    total = 0

    for tag in ETIQUETTES:
        for page in range(1, PAGES + 1):
            try:
                lot = lire("https://dev.to/api/articles?tag=%s&per_page=100&page=%d" % (tag, page), cle)
            except Exception as e:  # noqa: BLE001
                print("echec %s page %d : %s" % (tag, page, e), file=sys.stderr)
                break
            if not lot:
                break
            for a in lot:
                if (a.get("user") or {}).get("username") == MOI:
                    continue
                jours = (maintenant - datetime.fromisoformat(
                    a["published_at"].replace("Z", "+00:00"))).days
                i, _ = seau(jours)
                compte[i][0] += 1
                if a.get("public_reactions_count", 0) > 0:
                    compte[i][1] += 1
                if a.get("comments_count", 0) > 0:
                    compte[i][2] += 1
                total += 1
            time.sleep(0.6)

    if not total:
        print("ECHEC : aucun article lu, rien n'est ecrit.", file=sys.stderr)
        return 1

    lignes = []
    print("=== ENGAGEMENT PAR AGE (n = %d, etiquettes %s) ===" % (total, ", ".join(ETIQUETTES)))
    print("%-9s %6s %14s %16s" % ("age", "n", ">=1 reaction", ">=1 commentaire"))
    for i in sorted(compte):
        n, r, c = compte[i]
        nom = SEAUX[i][2]
        lignes.append({"age": nom, "n": n,
                       "part_avec_reaction": r / n, "part_avec_commentaire": c / n})
        print("%-9s %6d %13.1f%% %15.1f%%" % (nom, n, 100 * r / n, 100 * c / n))

    couverts = [l["age"] for l in lignes]
    print()
    print("Tranches d'age REELLEMENT couvertes :", ", ".join(couverts))
    print("Au-dela, le flux par etiquette ne remonte pas : AUCUNE mesure, et je n'extrapole pas.")

    if "--ecrire" in sys.argv:
        donnees = {
            "mesure": "taux d'engagement dev.to par tranche d'age de l'article",
            "date_utc": maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "methode": "GET /api/articles?tag=<t>&per_page=100&page=1..%d sur %s ; "
                       "regroupement par age depuis published_at" % (PAGES, ", ".join(ETIQUETTES)),
            "limites": [
                "le flux par etiquette ne remonte qu'environ sept jours meme en paginant : "
                "les tranches au-dela ne sont pas mesurees et ne doivent pas etre extrapolees",
                "les pages profondes d'un flux ne sont pas un tirage aleatoire des articles de "
                "cet age : c'est ce que le flux surface encore, ce qui peut biaiser a la baisse",
            ],
            "n_total": total,
            "tranches": lignes,
        }
        SORTIE.parent.mkdir(parents=True, exist_ok=True)
        SORTIE.write_text(json.dumps(donnees, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        print("ecrit :", SORTIE.relative_to(RACINE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
