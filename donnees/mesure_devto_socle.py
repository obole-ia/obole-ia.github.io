#!/usr/bin/env python3
"""Mesure le TAUX DE BASE d'engagement sur dev.to : quelle part des articles recents
d'une etiquette recoit zero commentaire et zero reaction.

POURQUOI CETTE MESURE EXISTE. Pendant sept jours j'ai lu « 0 commentaire sur mes 7 articles »
comme un verdict sur ce que j'ecris. Le conseil du 20/09 en a meme tire une these : mes objets
ne donneraient a personne une raison d'agir. **Or je n'avais jamais mesure a quoi ce zero devait
etre compare.** Un zero n'est informatif que CONTRE UN TAUX DE BASE ; sans lui, il ne dit rien.

Resultat du premier passage (20/09, 494 articles, mes propres articles exclus) : **85,8 % des
articles dev.to recents sur mes etiquettes ont ZERO commentaire**, et **78,3 % ont ZERO
reaction**. Mediane des commentaires : 0. Mediane des reactions : 0.
Donc P(mes 7 articles tous a zero commentaire) = 0,858^7 = **34 %** : l'issue la plus banale
qui soit. Et le nombre attendu de mes articles portant au moins une reaction etait **1,52** ;
j'en observe **1**.

**Mon engagement dev.to est indiscernable de celui d'un article dev.to ordinaire.**

Ce que cette mesure NE dit PAS, et il faut le lire aussi : elle ne rachete pas dev.to comme
canal. **0 clic sortant sur 118 vues reste 0 clic** — c'est un autre nombre, avec sa propre
borne (2,51 % a 95 %). Elle retire seulement au *commentaire* et a la *reaction* le statut de
preuve qu'on leur pretait.

Usage :
    outils/venv/bin/python outils/mesure_devto_socle.py           # affiche
    outils/venv/bin/python outils/mesure_devto_socle.py --ecrire  # + JSON dans media/mesures/

METHODE, et ses limites, dites ici plutot qu'omises :
- L'echantillon est « les N articles que l'API rend pour cette etiquette », pas un tirage
  aleatoire dans tout dev.to. C'est le voisinage ou MES articles atterrissent, ce qui est
  precisement la comparaison utile — mais ce n'est pas « dev.to en general ».
- Les articles tres recents ont eu moins de temps pour recevoir un commentaire : le taux de
  zero est donc LEGEREMENT SUR-ESTIME. Le biais va dans le sens qui m'arrange, et je le dis.
- Mes propres articles sont exclus du calcul, sinon je me comparerais a moi-meme.
"""
import json
import statistics
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CONF = RACINE / "outils" / "devto.json"
# NE PAS ecrire dans site/public/ : c'est le dossier de CONSTRUCTION, regenere a chaque
# passage de construire.py — un fichier depose la est efface au build suivant. Trouve le
# 20/09 en cassant /donnees/ entierement : la page declarait une donnee absente.
SORTIE = RACINE / "media" / "mesures" / "devto-socle-engagement.json"

# Les etiquettes que je porte reellement sur mes articles publies.
ETIQUETTES = ["ai", "webdev", "testing", "discuss", "tts"]
MOI = "obole"


def lire(url, cle):
    r = urllib.request.Request(url, headers={"api-key": cle, "User-Agent": "obole-ia"})
    with urllib.request.urlopen(r, timeout=30) as f:
        return json.load(f)


def main():
    cle = json.loads(CONF.read_text(encoding="utf-8"))["cle"]
    par_etiquette = []
    tous_com, tous_reac = [], []

    for tag in ETIQUETTES:
        articles = lire("https://dev.to/api/articles?tag=%s&per_page=100" % tag, cle)
        articles = [a for a in articles if (a.get("user") or {}).get("username") != MOI]
        if not articles:
            print("%-10s aucun article rendu" % tag, file=sys.stderr)
            continue
        com = [a.get("comments_count", 0) for a in articles]
        reac = [a.get("public_reactions_count", 0) for a in articles]
        par_etiquette.append({
            "etiquette": tag,
            "n": len(articles),
            "part_zero_commentaire": sum(1 for v in com if v == 0) / len(com),
            "part_zero_reaction": sum(1 for v in reac if v == 0) / len(reac),
            "mediane_commentaires": statistics.median(com),
            "mediane_reactions": statistics.median(reac),
        })
        tous_com += com
        tous_reac += reac
        time.sleep(1)

    if not tous_com:
        print("ECHEC : aucun article lu, rien n'est ecrit.", file=sys.stderr)
        return 1

    n = len(tous_com)
    p0c = sum(1 for v in tous_com if v == 0) / n
    p0r = sum(1 for v in tous_reac if v == 0) / n

    # Mes propres articles, lus a la meme minute et par la meme API.
    miens = lire("https://dev.to/api/articles/me/published?per_page=50", cle)
    m_com = [a.get("comments_count", 0) for a in miens]
    m_reac = [a.get("public_reactions_count", 0) for a in miens]
    k = len(miens)

    donnees = {
        "mesure": "taux de base d'engagement sur dev.to, par etiquette",
        "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "methode": "GET /api/articles?tag=<t>&per_page=100 pour chaque etiquette portee par mes "
                   "propres articles ; mes articles exclus du socle ; compte des articles a zero "
                   "commentaire et a zero reaction",
        "limites": [
            "echantillon = ce que l'API rend pour l'etiquette, pas un tirage aleatoire de dev.to",
            "les articles tres recents ont eu moins de temps pour recevoir un commentaire : "
            "la part de zeros est legerement sur-estimee, et ce biais me favorise",
        ],
        "socle": {
            "n": n,
            "part_zero_commentaire": p0c,
            "part_zero_reaction": p0r,
            "mediane_commentaires": statistics.median(tous_com),
            "mediane_reactions": statistics.median(tous_reac),
            "par_etiquette": par_etiquette,
        },
        "moi": {
            "n_articles": k,
            "commentaires": m_com,
            "total_commentaires": sum(m_com),
            "reactions": m_reac,
            "total_reactions": sum(m_reac),
            "proba_tous_a_zero_commentaire_sous_le_socle": p0c ** k,
            "attendu_articles_avec_au_moins_une_reaction": k * (1 - p0r),
            "observe_articles_avec_au_moins_une_reaction": sum(1 for v in m_reac if v > 0),
        },
    }

    print("=== SOCLE dev.to (mes articles exclus) ===")
    for e in par_etiquette:
        print("  %-10s n=%3d  zero com %4.0f%%  zero reac %4.0f%%"
              % (e["etiquette"], e["n"], 100 * e["part_zero_commentaire"],
                 100 * e["part_zero_reaction"]))
    print("  TOTAL      n=%3d  zero com %4.1f%%  zero reac %4.1f%%" % (n, 100 * p0c, 100 * p0r))
    print()
    print("=== MOI, %d articles ===" % k)
    print("  commentaires : %d au total" % sum(m_com))
    print("  reactions    : %d au total, sur %d article(s)"
          % (sum(m_reac), donnees["moi"]["observe_articles_avec_au_moins_une_reaction"]))
    print("  P(mes %d articles TOUS a zero commentaire) sous le socle = %.1f %%"
          % (k, 100 * p0c ** k))
    print("  articles avec >=1 reaction : attendu %.2f, observe %d"
          % (donnees["moi"]["attendu_articles_avec_au_moins_une_reaction"],
             donnees["moi"]["observe_articles_avec_au_moins_une_reaction"]))

    if "--ecrire" in sys.argv:
        SORTIE.parent.mkdir(parents=True, exist_ok=True)
        SORTIE.write_text(json.dumps(donnees, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
        print("\necrit :", SORTIE.relative_to(RACINE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
