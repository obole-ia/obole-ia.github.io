#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soak-importer.py — convertit la série d'un client au format que soak-lire.py lit.

Pourquoi ce fichier existe
--------------------------
Mon offre dit « je lis VOTRE série ». Mon analyseur, lui, ne savait lire que le
TSV de mon propre harnais. **C'est un trou dans l'offre au moment même où je la
chiffre** : quelqu'un qui a déjà ses données — et 87 % des tickets de fuite en
ont — devait d'abord relancer ma sonde pour que je puisse le lire. Absurde, et
contraire à tout l'argument (la sonde est prisonnière de ma machine, le lecteur
ne l'est pas).

Ce qu'il refuse de faire, et pourquoi
-------------------------------------
1. **Il ne devine pas les colonnes.** On les nomme. Deviner « la colonne qui
   ressemble à de la mémoire » est exactement la façon dont on analyse la
   mauvaise population sans s'en apercevoir.
2. **Il ne renomme pas en silence.** La série du client atterrit dans une
   colonne que mon analyseur appelle `rss_ko` ; l'en-tête écrit donc d'où elle
   vient et avec quel facteur. Sans ça le verdict dirait « mémoire résidente »
   sur un tas de JavaScript.
3. **Il ne comble aucun trou.** Un horodatage manquant reste manquant : c'est
   la première chose que le verdict compte.

    outils/venv/bin/python outils/soak-importer.py ENTREE.csv \
        --horodatage time --serie heapUsed --facteur 1024 \
        --unite-source "MiB" --sortie sortie.tsv [--serie2 rss]
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

COLONNES = ("horodatage_utc", "seconde_ecoulee", "pid", "vivant", "rss_ko", "vsize_ko",
            "fils", "descripteurs", "instances_inotify", "cpu_ticks", "redemarrages",
            "enfants", "charge_1min", "note")


def lire_instant(v):
    """ISO 8601, epoch en secondes, ou epoch en millisecondes. Rend un datetime UTC."""
    v = (v or "").strip()
    if not v:
        return None
    try:
        n = float(v)
        if n > 1e11:          # millisecondes
            n /= 1000.0
        if n > 1e8:           # plausible en secondes depuis 1973
            return datetime.fromtimestamp(n, timezone.utc)
    except ValueError:
        pass
    t = v.replace("Z", "+00:00")
    for essai in (t, t.replace(" ", "T")):
        try:
            d = datetime.fromisoformat(essai)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def nombre(v):
    v = (v or "").strip().replace(",", ".").replace("_", "")
    for suf in ("MiB", "GiB", "KiB", "MB", "GB", "kB", "ko", "B", "%"):
        if v.endswith(suf):
            v = v[:-len(suf)].strip()
    try:
        return float(v)
    except ValueError:
        return None


def main():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("entree")
    p.add_argument("--horodatage", required=True)
    p.add_argument("--serie", required=True, help="colonne analysee en premier (-> rss_ko)")
    p.add_argument("--serie2", default="", help="seconde colonne (-> vsize_ko)")
    p.add_argument("--facteur", type=float, default=1.0,
                   help="multiplie la valeur source pour obtenir des ko")
    p.add_argument("--unite-source", default="", dest="unite")
    p.add_argument("--sortie", required=True)
    p.add_argument("--etiquette", default="")
    p.add_argument("-h", "--help", action="store_true")
    a = p.parse_args()
    if a.help:
        print(__doc__)
        return 0

    brut = Path(a.entree).read_text(encoding="utf-8", errors="replace")
    delim = "\t" if brut.count("\t") > brut.count(",") else ","
    lignes = list(csv.DictReader(brut.splitlines(), delimiter=delim))
    if not lignes:
        print("entree vide ou sans en-tete", file=sys.stderr)
        return 4
    champs = list(lignes[0].keys())
    for besoin in [a.horodatage, a.serie] + ([a.serie2] if a.serie2 else []):
        if besoin not in champs:
            print("colonne absente : %r\ncolonnes disponibles : %s"
                  % (besoin, ", ".join(champs)), file=sys.stderr)
            return 5

    pts = []
    rejets = 0
    for l in lignes:
        t = lire_instant(l.get(a.horodatage))
        v = nombre(l.get(a.serie))
        if t is None or v is None:
            rejets += 1
            continue
        v2 = nombre(l.get(a.serie2)) if a.serie2 else None
        pts.append((t, v * a.facteur, (v2 * a.facteur) if v2 is not None else None))
    if len(pts) < 3:
        print("moins de trois points exploitables", file=sys.stderr)
        return 6
    pts.sort(key=lambda x: x[0])
    t0 = pts[0][0]
    span = (pts[-1][0] - t0).total_seconds()
    # L'intervalle declare est la MEDIANE des ecarts, pas la moyenne : un seul
    # long trou deplacerait la moyenne et ferait sous-compter les trous.
    ecarts = sorted((pts[i + 1][0] - pts[i][0]).total_seconds() for i in range(len(pts) - 1))
    inter = max(1, int(ecarts[len(ecarts) // 2]))

    s = Path(a.sortie)
    s.parent.mkdir(parents=True, exist_ok=True)
    with open(s, "w", encoding="utf-8") as f:
        f.write("# soak-importer.py — %s\n" % (a.etiquette or Path(a.entree).name))
        f.write("# debut_utc\t%s\n" % t0.strftime("%Y-%m-%dT%H:%M:%SZ"))
        # PAS DE duree_demandee_h. Trouve le 2026-09-23 a 02:35 en lisant la sortie
        # ENTIERE sur la premiere serie cliente : j'ecrivais ici la duree OBSERVEE comme
        # duree demandee, donc l'analyseur annoncait << COUVERTURE 100,00 % >> — une
        # TAUTOLOGIE presentee comme un controle. Or cette serie-la etait la queue d'un
        # processus de 49,8 h dont les journaux avaient tourne : 28 heures manquaient, et
        # mon rapport disait << fenetre complete >>.
        # **Une donnee importee ne connait pas la fenetre qu'on voulait mesurer.** Ne pas
        # la declarer force l'analyseur a dire << couverture NON TESTABLE >>, qui est vrai.
        f.write("# duree_observee_h\t%.4f (OBSERVEE, pas demandee : une serie importee ne "
                "sait pas quelle fenetre on voulait)\n" % (span / 3600.0))
        f.write("# dernier_echantillon_utc\t%s\n" % pts[-1][0].strftime("%Y-%m-%dT%H:%M:%SZ"))
        # LA PROSE NE VA PAS DANS UN CHAMP QU'UN PROGRAMME LIT. Premiere version : j'ai
        # colle l'explication apres la valeur, et soak-lire.py faisait int() sur la ligne
        # entiere — il est tombe en panne net. **Un champ analyse par un programme ne
        # contient que la valeur ; l'explication prend sa propre ligne.** Casse a 02:40 en
        # ajoutant un avertissement sur la mauvaise lecture des donnees.
        f.write("# intervalle_s\t%d\n" % inter)
        f.write("# NOTE intervalle_s est la MEDIANE des ecarts. Si la source est un journal et "
                "non un echantillonneur regulier, les << trous >> comptes par l'analyseur sont "
                "de l'IRREGULARITE et non de la perte.\n")
        f.write("# arbre\tTrue\n")
        f.write("# SERIE IMPORTEE — la colonne lue comme `rss_ko` est en realite %r%s, "
                "multipliee par %g.\n" % (a.serie, (" en " + a.unite) if a.unite else "", a.facteur))
        if a.serie2:
            f.write("# SERIE IMPORTEE — la colonne lue comme `vsize_ko` est en realite %r.\n" % a.serie2)
            f.write("# nom_reel_vsize\t%s\n" % a.serie2)
        else:
            f.write("# AUCUNE seconde serie fournie : le plancher retombe sur la premiere, "
                    "et la discrimination vsize/RSS n'est pas disponible.\n")
        f.write("# %d ligne(s) rejetee(s) a l'import (horodatage ou valeur illisible).\n" % rejets)
        f.write("# NON MESURE : tout le reste. Descripteurs, fils, inotify, enfants et "
                "redemarrages valent 0 parce qu'ils sont ABSENTS de la source, pas parce "
                "qu'ils ont ete mesures a zero.\n")
        f.write("\t".join(COLONNES) + "\n")
        for t, v, v2 in pts:
            f.write("\t".join([t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "%d" % int((t - t0).total_seconds()), "0", "1",
                               "%d" % round(v), ("%d" % round(v2)) if v2 is not None else "0",
                               "0", "0", "0", "0", "0", "0", "", ""]) + "\n")
    print("importe : %d points, %d rejetes, %.2f h, intervalle median %d s -> %s"
          % (len(pts), rejets, span / 3600.0, inter, s))
    print("ATTENTION : les colonnes absentes de la source valent 0 et non 'non mesure'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
