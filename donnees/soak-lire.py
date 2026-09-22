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
    # CORRECTION DU 2026-09-22 14:56, trouvee par la premiere instance de cet outil.
    # J'ecrivais ici << le processus SAIT rendre de la memoire >> des que la RSS baissait.
    # **C'est faux, et je l'avais publie.** Sur le releve du pont : la RSS est tombee de
    # 20 904 a 15 612 ko en douze paliers pendant que la memoire VIRTUELLE ne bougeait pas
    # d'un seul kilo-octet (30 244 sur 149 echantillons). Si le processus avait libere,
    # l'espace d'adressage aurait diminue. Il n'a rien libere : **le noyau a repris des pages
    # residentes a un processus inactif.**
    #
    # LA REGLE QUI EN SORT, et elle inverse l'intuition courante :
    #   - la RSS seule ne distingue pas << le processus a libere >> de << le noyau a repris >> ;
    #   - la VSIZE discrimine : plate pendant que la RSS chute = rien n'a ete libere ;
    #   - donc **une fuite se lit d'abord dans la VSIZE**, qui ne bouge que si l'espace
    #     d'adressage grandit, tandis que la RSS erre avec la pression memoire du systeme.
    vs = [int(x[i["vsize_ko"]]) for x in vivants if x[i["vsize_ko"]].isdigit()]
    vsize_plate = len(set(vs)) == 1 if vs else False
    if baisses and not hausses:
        if vsize_plate:
            print("  -> ATTENTION : la memoire VIRTUELLE n'a pas bouge (%d ko sur tout le releve)." % vs[0])
            print("     Le processus n'a donc RIEN libere : c'est le noyau qui a repris des pages")
            print("     residentes. Une baisse de RSS a vsize constante n'est pas une liberation.")
        else:
            print("  -> la vsize a baisse aussi : le processus a rendu de la memoire au systeme.")
    elif baisses and hausses and vsize_plate:
        print("  -> la memoire virtuelle est immobile (%d ko) : les mouvements de RSS sont des" % vs[0])
        print("     pages reprises et reprechargees par le noyau, pas des allocations du processus.")
    print()
    print("--- VERDICT ---")
    if redem:
        print("REDEMARRAGES DETECTES : %d. Tout le reste se lit sous cette reserve." % redem)
    if part_trous > 0.05:
        print("RESERVE : %.1f %% des minutes manquent. On ignore ce qui s'est passe pendant." % (100 * part_trous))
    etendue_rss = max(rss) - min(rss)
    if vsize_plate and etendue_rss:
        print("NOTE DE LECTURE : la memoire virtuelle est restee a %d ko sur tout le releve." % vs[0])
        print("Les %d ko d'etendue de RSS sont donc du mouvement de PAGES, pas d'allocation." % etendue_rss)
        print("**Pour une fuite, c'est la vsize qu'il faut regarder : elle ne grandit que si")
        print("l'espace d'adressage grandit.**")
    s = pente(xs, rss)
    if s is not None and abs(s * 86400) * (duree / 86400) > etendue_rss and s > 0:
        print("DERIVE DE MEMOIRE : hausse de %+.0f ko/jour, superieure a l'etendue observee." % (s * 86400))
    elif etendue_rss == 0:
        print("AUCUNE DERIVE : memoire residente immobile sur toute la duree.")
    else:
        print("AUCUNE DERIVE ETABLIE : etendue de RSS %d ko (%.2f %% de la base), sans tendance" % (etendue_rss, 100 * etendue_rss / rss[0]))
        print("qui la depasse. **Ce n'est pas la preuve d'une absence de fuite : c'est l'absence")
        print("de preuve d'une fuite sur %.1f h.**" % (duree / 3600))
        # LE PLANCHER SE CALCULE SUR LE SIGNAL DE FUITE, PAS SUR LE SIGNAL BRUYANT.
        # Corrige le 2026-09-22 a 14:58 : je le calculais sur l'etendue de RSS, qui vient
        # d'etre etablie comme du mouvement de PAGES. Il rendait 51 490 ko/jour — un plancher
        # si lache qu'il ne disqualifiait rien. **Une statistique calculee sur la mauvaise
        # population**, exactement la faute que je corrige partout aujourd'hui.
        jours = max(duree / 86400, 1e-9)
        if vs:
            etendue_vs = max(vs) - min(vs)
            base_vs = max(etendue_vs, 4)  # une page de 4 ko : on ne detecte pas plus fin
            print("PLANCHER DE DETECTION, sur la memoire VIRTUELLE qui est le signal de fuite :")
            print("  une croissance d'espace d'adressage plus lente que **%.0f ko/jour** serait" % (base_vs / jours))
            print("  restee invisible sur cette duree. L'etendue de vsize observee est de %d ko." % etendue_vs)
            print("  (Le meme plancher calcule sur la RSS vaudrait %.0f ko/jour — beaucoup plus lache," % (etendue_rss / jours))
            print("   parce que la RSS erre avec la pression memoire du systeme.)")
        else:
            print("Plancher de detection non calculable : vsize absente du releve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
