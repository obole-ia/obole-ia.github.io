#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soak-lire.py — lit un relevé de soak.py et rend le VERDICT ÉCRIT que je vends.

Pourquoi ce fichier existe
--------------------------
Mes offres promettent << le CSV brut, le harnais, une page datée, et un verdict
écrit — y compris << aucune dérive trouvée >>, si c'est la réponse >>. Le harnais
produit le fichier ; il ne produit pas le verdict. Sans cet outil, le verdict
serait ma prose, donc un endroit de plus où un chiffre peut être recopié de
travers. **Chaque nombre du verdict sort du fichier au moment où il est écrit.**

Ce qu'il refuse de conclure, et pourquoi
----------------------------------------
1. **Les TROUS.** Un soak qui a manqué des minutes n'est pas un soak plus court :
   c'est un soak dont on ignore ce qui s'est passé pendant les trous. Ils sont
   comptés et affichés AVANT toute autre statistique, et au-delà de 5 % le
   verdict porte une réserve explicite.
2. **La PENTE seule ne prouve rien.** Une pente de quelques ko/jour sur un
   processus dont l'étendue dépasse déjà ce chiffre est du bruit. On compare donc
   toujours la dérive projetée sur la durée à l'ÉTENDUE observée — et si la
   seconde domine, le verdict dit << indécidable >>, pas << aucune dérive >>.
3. **Une baisse n'est pas une fuite négative**, mais elle est informative : elle
   prouve que le processus SAIT rendre de la mémoire, donc qu'une hausse
   ultérieure serait une vraie croissance et non un allocateur qui ne rend rien.
   C'est dit dans le verdict quand ça se produit.

    outils/venv/bin/python outils/soak-lire.py <fichier.tsv>
"""
import sys
from pathlib import Path

COLS = ("horodatage_utc", "seconde_ecoulee", "pid", "vivant", "rss_ko", "vsize_ko",
        "fils", "descripteurs", "instances_inotify", "cpu_ticks", "redemarrages",
        "enfants", "charge_1min", "note")


def pente(xs, ys):
    """Moindres carres, sans dependance. Rend la pente en unites de y par unite de x."""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return (num / den) if den else None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    p = Path(sys.argv[1])
    entete, lignes = [], []
    for l in p.read_text(encoding="utf-8").splitlines():
        if l.startswith("#"):
            entete.append(l)
            continue
        if l.startswith("horodatage"):
            continue
        ch = l.split("\t")
        if len(ch) >= len(COLS):
            lignes.append(ch)
    if not lignes:
        print("aucun echantillon.")
        return 4

    i = {c: n for n, c in enumerate(COLS)}
    intervalle = 60
    for e in entete:
        if e.startswith("# intervalle_s"):
            intervalle = int(e.split("\t")[1])

    print("=" * 72)
    for e in entete:
        print(e)
    print("=" * 72)

    duree = int(lignes[-1][i["seconde_ecoulee"]])
    attendus = duree // intervalle + 1
    trous = attendus - len(lignes)
    part_trous = trous / attendus if attendus else 0
    print("duree            : %.2f h (%d s)" % (duree / 3600, duree))
    print("echantillons     : %d observes / %d attendus" % (len(lignes), attendus))
    print("TROUS            : %d (%.1f %%)%s" % (trous, 100 * part_trous,
          "  *** au-dela de 5 %, le verdict porte une reserve ***" if part_trous > 0.05 else ""))

    morts = [x for x in lignes if x[i["vivant"]] == "0"]
    redem = int(lignes[-1][i["redemarrages"]])
    print("minutes sans processus : %d" % len(morts))
    print("REDEMARRAGES     : %d" % redem)
    enfants = max(int(x[i["enfants"]]) for x in lignes if x[i["enfants"]].isdigit())
    arbre = any(e.startswith("# arbre\tTrue") for e in entete)
    print("enfants (max)    : %d%s" % (enfants,
          "" if (arbre or not enfants) else "  *** comptes NON inclus : relevé fait sans --arbre ***"))
    print()

    vivants = [x for x in lignes if x[i["vivant"]] == "1" and x[i["rss_ko"]] not in ("None", "")]
    xs = [int(x[i["seconde_ecoulee"]]) for x in vivants]
    for champ, libelle, unite in (("rss_ko", "memoire residente", "ko"),
                                  ("vsize_ko", "memoire virtuelle", "ko"),
                                  ("descripteurs", "descripteurs ouverts", ""),
                                  ("fils", "fils", ""),
                                  ("instances_inotify", "instances inotify", "")):
        ys = []
        for x in vivants:
            v = x[i[champ]]
            ys.append(int(v) if v.isdigit() else None)
        if any(y is None for y in ys):
            print("%-22s : incomplet, ignore" % libelle)
            continue
        etendue = max(ys) - min(ys)
        s = pente(xs, ys)
        par_jour = s * 86400 if s is not None else None
        ligne = "%-22s : %s -> %s %s | etendue %s | " % (libelle, ys[0], ys[-1], unite, etendue)
        if par_jour is None:
            ligne += "pente indisponible"
        else:
            projete = abs(par_jour) * (duree / 86400)
            if etendue == 0:
                verdict = "IMMOBILE"
            elif projete <= etendue:
                verdict = "INDECIDABLE (derive projetee %.0f <= etendue %d)" % (projete, etendue)
            elif par_jour > 0:
                verdict = "HAUSSE nette"
            else:
                verdict = "BAISSE nette"
            ligne += "pente %+.1f %s/jour | %s" % (par_jour, unite or "unite", verdict)
        print(ligne)

    rss = [int(x[i["rss_ko"]]) for x in vivants]
    baisses = sum(1 for k in range(1, len(rss)) if rss[k] < rss[k - 1])
    hausses = sum(1 for k in range(1, len(rss)) if rss[k] > rss[k - 1])
    print()
    print("pas de RSS       : %d hausse(s), %d baisse(s)" % (hausses, baisses))
    if baisses and not hausses:
        print("  -> le processus SAIT rendre de la memoire. Une hausse ulterieure serait donc")
        print("     une vraie croissance et non un allocateur qui ne rend jamais rien.")
    print()
    print("--- VERDICT ---")
    if redem:
        print("REDEMARRAGES DETECTES : %d. Tout le reste se lit sous cette reserve." % redem)
    if part_trous > 0.05:
        print("RESERVE : %.1f %% des minutes manquent. On ignore ce qui s'est passe pendant." % (100 * part_trous))
    etendue_rss = max(rss) - min(rss)
    s = pente(xs, rss)
    if s is not None and abs(s * 86400) * (duree / 86400) > etendue_rss and s > 0:
        print("DERIVE DE MEMOIRE : hausse de %+.0f ko/jour, superieure a l'etendue observee." % (s * 86400))
    elif etendue_rss == 0:
        print("AUCUNE DERIVE : memoire residente immobile sur toute la duree.")
    else:
        print("AUCUNE DERIVE ETABLIE : etendue de %d ko (%.2f %% de la base), sans tendance" % (etendue_rss, 100 * etendue_rss / rss[0]))
        print("qui la depasse. **Ce n'est pas la preuve d'une absence de fuite : c'est l'absence")
        print("de preuve d'une fuite sur %.1f h.** Une fuite plus lente que %.0f ko/jour resterait invisible ici." % (duree / 3600, etendue_rss / max(duree / 86400, 1e-9)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
