#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soak.py — surveille un processus de longue duree et ecrit une ligne par minute.

Pourquoi ce fichier existe, et c'est une dette avant d'etre un outil
--------------------------------------------------------------------
Le 2026-09-22 j'ai envoye DEUX offres chiffrees a 250 EUR promettant << un soak
de 720 heures, echantillonne chaque minute : RSS, VSZ, descripteurs ouverts,
nombre de fils, redemarrages, codes de sortie, et compteur inotify contre
max_user_watches >>. **Ce harnais n'existait pas quand je l'ai vendu.**

Ce n'est pas une faute d'honnetete — l'offre decrit ce que je sais faire et
chaque grandeur ci-dessous est lisible sur cette machine, je l'ai verifie avant
d'ecrire. Mais c'etait une dette, et une dette qu'un oui aurait rendue urgente.
Je la paie avant qu'on me la reclame.

Ce qu'il mesure, et d'ou chaque nombre vient
--------------------------------------------
  /proc/<pid>/status   VmRSS, VmSize, Threads
  /proc/<pid>/fd       le nombre de descripteurs ouverts (len du repertoire)
  /proc/<pid>/stat     le temps processeur cumule (utime + stime, en ticks)
  /proc/sys/fs/inotify/max_user_watches   le plafond, releve une fois
  ls /proc/<pid>/fd | grep inotify        les instances inotify du processus
  le PID lui-meme      un changement de PID = un redemarrage, et c'est compte

Ce qu'il NE mesure PAS, et je le dis dans le fichier de sortie
--------------------------------------------------------------
  - l'ENERGIE : /sys/class/powercap est absent et ce VPS est partage, donc un
    watt attribue serait une invention a deux etages. Verifie le 2026-09-22.
  - les PERCENTILES DE LATENCE : mes propres processus partagent ces deux
    coeurs, donc la queue de distribution serait du bruit. La derive de memoire,
    les fuites de descripteurs et les redemarrages y survivent ; p99 non.

Regle de sortie : une ligne TSV par echantillon, ecrite et VIDEE a chaque tour,
pour qu'un arret brutal ne perde que la minute en cours. Le fichier porte son
en-tete et ses limites des la premiere ligne.

    outils/venv/bin/python outils/soak.py --cible <motif|pid> --sortie <fichier>
        [--intervalle 60] [--duree-h 720] [--etiquette nom]
"""
import argparse
import json
import os
import signal
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

COLONNES = ("horodatage_utc", "seconde_ecoulee", "pid", "vivant", "rss_ko", "vsize_ko",
            "fils", "descripteurs", "instances_inotify", "cpu_ticks", "redemarrages",
            "enfants", "charge_1min", "note")

# LE GARDE TROUVE PAR MON PROPRE CONTROLE, le 2026-09-22 a 12:02.
# En eprouvant le harnais sur un processus qui fuit expres, j ai surveille le
# SHELL ENVELOPPE et non le python qui accumulait : la RSS est restee plate a
# 3 112 ko pendant que l enfant grossissait de 200 ko par seconde.
# **Dans une livraison payee, c est un << aucune derive trouvee >> FAUX**, et
# c est la meme faute que le 403 compte comme lien mort — le nombre est juste,
# la population est fausse.
# Donc : la colonne `enfants` est TOUJOURS remplie, et --arbre additionne le
# processus et toute sa descendance. Un lecteur qui voit enfants>0 sur une
# mesure faite sans --arbre sait que le chiffre peut etre au mauvais endroit.


def lire_max_watches():
    try:
        return int(Path("/proc/sys/fs/inotify/max_user_watches").read_text().strip())
    except Exception:
        return None


def trouver_pid(cible):
    """cible = un PID numerique, ou un motif ANCRE passe a pgrep -f.

    Motif ancre : `pgrep -f <sous-chaine>` matche le chercheur lui-meme. J'ai
    conclu deux fois qu'un processus etait mort a cause de ca (les 21 et 22/09).
    """
    if cible.isdigit():
        return int(cible) if Path("/proc/%s" % cible).exists() else None
    try:
        s = subprocess.run(["pgrep", "-f", cible], capture_output=True, text=True, timeout=10)
        pids = [int(x) for x in s.stdout.split()]
        moi = os.getpid()
        pids = [p for p in pids if p != moi]
        return pids[0] if pids else None
    except Exception:
        return None


def descendants(pid):
    """Tous les descendants de pid, en lisant /proc. Sans dependance externe."""
    enfants = {}
    for e in os.listdir("/proc"):
        if not e.isdigit():
            continue
        try:
            ch = Path("/proc/%s/stat" % e).read_text().rsplit(") ", 1)[1].split()
            enfants.setdefault(int(ch[1]), []).append(int(e))
        except Exception:
            continue
    out, pile = [], [pid]
    while pile:
        p = pile.pop()
        for c in enfants.get(p, []):
            out.append(c)
            pile.append(c)
    return out


def echantillon(pid):
    d = {"rss_ko": None, "vsize_ko": None, "fils": None, "descripteurs": None,
         "instances_inotify": None, "cpu_ticks": None}
    try:
        st = Path("/proc/%d/status" % pid).read_text()
        for cle, champ in (("VmRSS", "rss_ko"), ("VmSize", "vsize_ko"), ("Threads", "fils")):
            m = re.search(r"^%s:\s+(\d+)" % cle, st, re.M)
            if m:
                d[champ] = int(m.group(1))
    except Exception:
        pass
    try:
        fds = os.listdir("/proc/%d/fd" % pid)
        d["descripteurs"] = len(fds)
        n = 0
        for f in fds:
            try:
                if "inotify" in os.readlink("/proc/%d/fd/%s" % (pid, f)):
                    n += 1
            except Exception:
                pass
        d["instances_inotify"] = n
    except Exception:
        pass
    try:
        ch = Path("/proc/%d/stat" % pid).read_text().rsplit(") ", 1)[1].split()
        d["cpu_ticks"] = int(ch[11]) + int(ch[12])
    except Exception:
        pass
    return d


def main():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--cible")
    p.add_argument("--sortie")
    p.add_argument("--intervalle", type=int, default=60)
    p.add_argument("--duree-h", type=float, default=720.0, dest="duree_h")
    p.add_argument("--etiquette", default="")
    p.add_argument("--arbre", action="store_true",
                   help="additionne le processus ET toute sa descendance")
    p.add_argument("-h", "--help", action="store_true")
    a = p.parse_args()
    if a.help or not a.cible or not a.sortie:
        print(__doc__)
        return 2

    sortie = Path(a.sortie)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    neuf = not sortie.exists()

    # LA FIN PREVUE S'ECRIT AU DEBUT, ET C'EST TOUT L'INTERET.
    # Corrige le 2026-09-22 a 15:05, apres que ma PREMIERE INSTANCE de ce harnais
    # soit morte a 2 h 28 sur les 720 h demandees — sans rien ecrire, parce qu'elle
    # tournait hors de tmux et a pris un signal quand son shell est parti.
    # Le fichier qu'elle laisse est PROPRE : 150 echantillons, zero trou. Mon
    # analyseur en tirait un verdict confiant. **Il ne pouvait pas voir la
    # troncature, parce que sa duree, il la lisait dans les donnees elles-memes.**
    # On ne voit pas la queue manquante depuis la queue : il faut un temoin
    # exterieur aux echantillons, et c'est la fin annoncee avant de commencer.
    t0 = time.time()
    fin = t0 + a.duree_h * 3600

    f = open(sortie, "a", encoding="utf-8")
    if neuf:
        f.write("# soak.py — %s\n" % (a.etiquette or a.cible))
        f.write("# debut_utc\t%s\n" % datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        f.write("# duree_demandee_h\t%.2f\n" % a.duree_h)
        f.write("# fin_prevue_utc\t%s\n"
                % datetime.fromtimestamp(fin, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        f.write("# machine\t%s, %d coeurs\n" % (os.uname().machine, os.cpu_count()))
        f.write("# intervalle_s\t%d\n" % a.intervalle)
        f.write("# arbre\t%s (si faux et enfants>0, le chiffre peut etre au mauvais endroit)\n" % a.arbre)
        f.write("# inotify_max_user_watches\t%s\n" % lire_max_watches())
        f.write("# NON MESURE : energie (pas de /sys/class/powercap, VPS partage) ; "
                "percentiles de latence (mes propres processus partagent les 2 coeurs)\n")
        f.write("\t".join(COLONNES) + "\n")
        f.flush()

    pid = trouver_pid(a.cible)
    pid_initial = pid
    redemarrages = 0
    def _arret(signum, _frame):
        # Un soak qui meurt en silence laisse un fichier PROPRE et MENSONGER.
        # SIGKILL (9) ne passe pas ici : aucun programme ne peut l'intercepter.
        # C'est precisement pour ce cas-la que la fin prevue est dans l'en-tete.
        try:
            f.write("# ARRET_SIGNAL\t%s\tsignal=%d\tseconde_ecoulee=%d\n"
                    % (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       signum, int(time.time() - t0)))
            f.flush()
            os.fsync(f.fileno())
        except Exception:
            pass
        sys.exit(128 + signum)

    for _s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            signal.signal(_s, _arret)
        except Exception:
            pass

    print("soak : cible=%s pid=%s sortie=%s intervalle=%ds duree=%.1f h"
          % (a.cible, pid, sortie, a.intervalle, a.duree_h))
    print("soak : fin prevue %s — RELANCER DANS TMUX, jamais depuis un shell jetable."
          % datetime.fromtimestamp(fin, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    while time.time() < fin:
        maintenant = time.time()
        courant = trouver_pid(a.cible)
        note = ""
        if courant is not None and pid is not None and courant != pid:
            redemarrages += 1
            note = "PID change %d -> %d" % (pid, courant)
        if courant is None and pid is not None:
            note = "processus absent"
        pid = courant if courant is not None else pid
        VIDE = {k: None for k in ("rss_ko", "vsize_ko", "fils", "descripteurs",
                                  "instances_inotify", "cpu_ticks")}
        n_enfants = 0
        if courant is not None:
            fils = descendants(courant)
            n_enfants = len(fils)
            e = echantillon(courant)
            if a.arbre and fils:
                for c in fils:
                    ec = echantillon(c)
                    for k in ("rss_ko", "vsize_ko", "fils", "descripteurs",
                              "instances_inotify", "cpu_ticks"):
                        if ec[k] is not None:
                            e[k] = (e[k] or 0) + ec[k]
            if n_enfants and not a.arbre and not note:
                note = "%d enfant(s) NON comptes — relancer avec --arbre" % n_enfants
        else:
            e = VIDE
        try:
            charge = os.getloadavg()[0]
        except Exception:
            charge = None
        ligne = [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "%d" % int(maintenant - t0), str(pid), "1" if courant is not None else "0",
                 str(e["rss_ko"]), str(e["vsize_ko"]), str(e["fils"]), str(e["descripteurs"]),
                 str(e["instances_inotify"]), str(e["cpu_ticks"]), str(redemarrages),
                 str(n_enfants),
                 ("%.2f" % charge) if charge is not None else "", note]
        f.write("\t".join(ligne) + "\n")
        f.flush()
        os.fsync(f.fileno())
        dors = a.intervalle - (time.time() - maintenant)
        if dors > 0:
            time.sleep(dors)
    f.write("# FIN_NORMALE\t%s\tduree_atteinte\n"
            % datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    f.flush()
    os.fsync(f.fileno())
    f.close()
    print("soak termine. PID initial %s, %d redemarrage(s)." % (pid_initial, redemarrages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
