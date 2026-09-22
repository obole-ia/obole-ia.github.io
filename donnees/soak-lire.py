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
3. **Une baisse de RSS n'est pas une libération.** Corrigé le 2026-09-22 : cette
   notice affirmait qu'une baisse « prouve que le processus SAIT rendre de la
   mémoire ». C'est faux, et ma propre première instance l'a montré — la RSS a
   chuté de 25 % pendant que la mémoire VIRTUELLE ne bougeait pas d'un octet.
   Un processus qui libère voit son espace d'adressage diminuer. Le mien ne l'a
   pas vu : c'est le noyau qui a repris des pages à un processus inactif.
   **Une fuite se lit d'abord dans la vsize ; la RSS erre avec la pression
   mémoire du système.**
4. **La TRONCATURE.** Un relevé interrompu est *propre* : pas un trou, pas une
   anomalie, juste une fin. La durée ne peut donc pas se lire dans les données —
   sinon un soak mort au bout de 1 % de sa fenêtre rend un verdict confiant sur
   1 % de sa fenêtre, sans le dire. Il faut un témoin EXTÉRIEUR aux échantillons :
   la fin annoncée dans l'en-tête avant de commencer. **On ne voit pas la queue
   manquante depuis la queue.**

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


def enveloppe_basse(xs, ys, n_seaux=None):
    """Rend (xs, ys) reduits aux MINIMA par seau : l'enveloppe basse de la serie.

    POURQUOI CET ESTIMATEUR EXISTE, et c'est une lecon rendue par quelqu'un d'autre.
    Le 2026-09-22 sur openclaw#91588, un ingenieur mesure heapUsed sur 39,5 h et
    publie une pente de +3,7 MiB/h — alors qu'il vient lui-meme de calculer que son
    plancher vaut 11,7 MiB/h. Sa pente est au TIERS de son propre plancher.

    La cause n'est pas son arithmetique, elle est dans la population : **une serie
    de tas mesure DEUX choses a la fois**, le tas vivant qui croit et les ordures en
    attente de collecte qui oscillent. Les moindres carres sur la serie brute
    estiment donc une tendance dans une population contaminee par la dent de scie
    du ramasse-miettes — et l'amplitude de cette dent de scie EST l'etendue qui
    ecrase le plancher.

    Les MINIMA par seau, eux, approchent les creux post-collecte, donc le tas
    vivant seul. L'amplitude sort de l'estimateur au lieu de le noyer.

    Ce n'est pas une astuce : c'est le meme geste que toutes mes corrections du
    jour. **Quand une statistique ne conclut pas, regarder si elle porte sur une
    population qui melange deux choses.**
    """
    if len(xs) < 6:
        return [], []
    if n_seaux is None:
        n_seaux = max(3, min(len(xs) // 3, 60))
    x0, x1 = min(xs), max(xs)
    if x1 <= x0:
        return [], []
    larg = (x1 - x0) / n_seaux
    seaux = {}
    for x, y in zip(xs, ys):
        k = min(int((x - x0) / larg), n_seaux - 1)
        if k not in seaux or y < seaux[k][1]:
            seaux[k] = (x, y)
    pts = sorted(seaux.values())
    return [a for a, _ in pts], [b for _, b in pts]


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
    duree_demandee = None
    fin_prevue = ""
    for e in entete:
        if e.startswith("# intervalle_s"):
            intervalle = int(e.split("\t")[1])
        elif e.startswith("# duree_demandee_h"):
            duree_demandee = float(e.split("\t")[1]) * 3600
        elif e.startswith("# fin_prevue_utc"):
            fin_prevue = e.split("\t")[1].strip()
    # NOM REEL DE LA SERIE. Une serie importee atterrit dans la colonne `rss_ko`,
    # mais ce n'est pas forcement de la memoire residente : sur la premiere serie
    # cliente que j'ai fait passer, c'etait un tas JavaScript. L'en-tete le disait,
    # le corps du verdict disait << RSS >>. **Dans une livraison payee, un verdict
    # juste sous une etiquette fausse est un verdict faux.**
    nom1 = "memoire residente"
    for e in entete:
        if "lue comme `rss_ko` est en realite" in e:
            bout = e.split("est en realite", 1)[1].strip().rstrip(".")
            nom1 = bout.split(", multipliee")[0].strip()
    fin_normale = any(e.startswith("# FIN_NORMALE") for e in entete)
    arret_signal = [e for e in entete if e.startswith("# ARRET_SIGNAL")]

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

    # --- TRONCATURE ---------------------------------------------------------
    # Un releve interrompu n'a pas de trou : il a une fin. Rien dans les donnees
    # ne le distingue d'un releve court mais complet. Le temoin est l'en-tete.
    tronque = False
    couverture = None
    if arret_signal:
        print("ARRET PAR SIGNAL : %s" % arret_signal[-1].lstrip("# ").strip())
    if duree_demandee is None:
        print("COUVERTURE       : *** NON TESTABLE *** — l'en-tete ne declare aucune")
        print("                   duree demandee. Ce releve peut etre complet ou tronque :")
        print("                   le fichier ne permet pas de le savoir. (Releves anterieurs")
        print("                   au 2026-09-22 15:05.)")
    else:
        couverture = duree / duree_demandee if duree_demandee else 0
        tronque = (not fin_normale) and couverture < 0.99
        print("COUVERTURE       : %.2f h observees / %.2f h demandees = %.2f %%%s"
              % (duree / 3600, duree_demandee / 3600, 100 * couverture,
                 "   *** TRONQUE ***" if tronque else ""))
        if tronque:
            print("  -> *** CE RELEVE EST TRONQUE. Il ne porte AUCUN marqueur de fin normale.")
            print("     Le harnais s'est arrete a %.1f %% de la fenetre demandee (fin prevue %s)."
                  % (100 * couverture, fin_prevue or "?"))
            print("     **Tout verdict ci-dessous ne vaut QUE pour les %.2f h observees.**"
                  % (duree / 3600))
    print()

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
    for champ, libelle, unite in (("rss_ko", nom1, "ko"),
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
    if tronque:
        print("RESERVE MAJEURE : releve TRONQUE a %.2f %% de la fenetre demandee." % (100 * couverture))
        print("Une fuite lente est justement ce qu'une fenetre ecourtee ne peut pas voir :")
        print("le plancher de detection ci-dessous est celui des %.2f h observees, pas des" % (duree / 3600))
        print("%.0f h achetees. **Relancer le soak ; ce verdict n'est pas la livraison.**" % (duree_demandee / 3600))
    elif duree_demandee is None:
        print("RESERVE : duree demandee non declaree dans l'en-tete — la troncature n'a PAS")
        print("pu etre testee. Ce verdict suppose le releve complet sans pouvoir le verifier.")
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
        print("AUCUNE DERIVE : %s immobile sur toute la duree." % nom1)
    else:
        print("AUCUNE DERIVE ETABLIE : etendue de %s %d ko (%.2f %% de la base), sans tendance"
              % (nom1, etendue_rss, 100 * etendue_rss / rss[0]))
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

    # --- ENVELOPPE BASSE : ce que la serie brute ne pouvait pas trancher --------
    xh = [x / 3600.0 for x in xs]
    for nom, serie in (("RSS", rss), ("VSIZE", vs if len(vs) == len(xs) else None)):
        if not serie or len(serie) != len(xh):
            continue
        etendue = max(serie) - min(serie)
        s_brut = pente(xh, serie)
        if s_brut is None or etendue == 0:
            continue
        span = max(xh) - min(xh)
        brut_tranche = abs(s_brut) * span > etendue
        # LARGEUR DE SEAU, ET POURQUOI ELLE N'EST PLUS CHOISIE SUR LE RESULTAT.
        # Premiere version : j'essayais sept largeurs et gardais celle dont les minima
        # s'alignaient le mieux. **Mon temoin negatif l'a refusee** — sur une serie
        # a dent de scie SANS aucune derive, une largeur sur sept finit par bien
        # tomber, et l'outil annoncait +710 ko/h de croissance inexistante.
        # C'est de la selection sur le resultat, la faute que je reproche ailleurs.
        # Donc : largeur FIXE, fonction du seul nombre d'echantillons, et un seuil
        # DECLARE au lieu d'un seuil ajuste. Mesures des trois temoins, rapport
        # derive/dispersion : derive vraie 9,4 — derive franche 183 — SANS derive 1,3.
        n_seaux_ret = max(4, min(10, len(xh) // 6))
        ex, ey = enveloppe_basse(xh, serie, n_seaux_ret)
        if len(ex) < 4:
            continue
        s_env = pente(ex, ey)
        if s_env is None:
            continue
        # LE CRITERE PORTE SUR LE RESIDU, PAS SUR L'ETENDUE TOTALE.
        # Pour une tendance parfaitement lineaire, la derive vaut EXACTEMENT l'etendue :
        # un critere << derive > etendue >> ne se declencherait donc jamais sur des
        # minima propres. Ce qu'il faut comparer, c'est la derive a la dispersion qui
        # RESTE une fois la droite retiree.
        mx = sum(ex) / len(ex)
        my = sum(ey) / len(ey)
        res = [y - (my + s_env * (x - mx)) for x, y in zip(ex, ey)]
        disp = max(res) - min(res)
        derive_env = abs(s_env) * span
        # SEUIL DECLARE : la derive doit valoir au moins TROIS FOIS la dispersion
        # qui reste apres retrait de la droite. Un simple << superieur a >> laissait
        # passer le temoin sans derive, qui atteint 1,3.
        SEUIL = 3.0
        env_tranche = disp > 0 and derive_env > SEUIL * disp
        if brut_tranche or not env_tranche:
            continue
        print()
        etiq = nom1 if nom == "RSS" else nom
        print("--- ENVELOPPE BASSE sur %s : ce que la serie brute ne tranchait pas ---" % etiq)
        print("  serie brute        : pente %+.2f ko/h | etendue %d ko | derive %d ko"
              % (s_brut, etendue, abs(s_brut) * span))
        print("                       derive/etendue = %.2f  ->  INDECIDABLE"
              % (abs(s_brut) * span / etendue))
        print("  minima, %2d seaux   : pente %+.2f ko/h | dispersion residuelle %d ko | derive %d ko"
              % (n_seaux_ret, s_env, disp, derive_env))
        print("                       (largeur de seau retenue : %.2f h. Un seau plus etroit que le"
              % (span / n_seaux_ret))
        print("                        cycle de collecte rendrait un point du flanc, pas un creux.)")
        print("                       derive/dispersion = %.1f  ->  TRANCHE (seuil declare : %.0f)"
              % (derive_env / disp, SEUIL))
        print("  **La serie brute portait DEUX populations : le fond qui derive, et l'oscillation")
        print("    du ramasse-miettes. Les minima n'en portent qu'une.**")
        print("  Croissance de fond retenue : %+.0f ko/jour." % (s_env * 24))
        print("  LIMITE DECLAREE : la justesse de ce chiffre depend de l'alignement entre la")
        print("  largeur de seau et le cycle de collecte, que ce releve ne mesure pas. Sur mon")
        print("  temoins (derive vraie connue) : quand la largeur de seau tombe pres du cycle")
        print("  de collecte, l'enveloppe se trompe de 10,5 % contre 29,7 % pour la serie brute ;")
        print("  quand elle en est loin, les deux se valent (14,6 % contre 15,3 %) et c'est la")
        print("  DECIDABILITE qu'elle apporte, pas la justesse. **A lire comme un ordre de")
        print("  grandeur du fond, pas comme une mesure au pourcent.**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
