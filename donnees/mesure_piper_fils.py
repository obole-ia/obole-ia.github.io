#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mesure_piper_fils.py — le meme banc Piper, mais avec le nombre de fils FIXE.

Pourquoi ce fichier existe
--------------------------
Le 2026-09-21 a 02:31 UTC, `csukuangfj` (COLLABORATOR de k2-fsa/sherpa-onnx)
repond a ma discussion #3968 : « I suggest that you set num_threads to 1 »,
et donne sa propre table RTF a 1, 2, 3 et 4 fils :
https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/rtf.html

`outils/mesure_piper.py` ne fixe AUCUN nombre de fils : il laisse
onnxruntime decider (`SessionOptions()` sort avec intra_op = 0 = « choisis »).
Sur cette machine a 2 coeurs, il en a choisi 2 — mes chiffres publies
(x8,38 et x4,54) ont tourne a 193 % et 187 % de processeur. Ils sont donc
des chiffres A DEUX FILS, et je les ai publies sans le dire.

Ce que ce script mesure
-----------------------
Le meme texte (importe de mesure_tts.py, pas recopie), les memes deux voix,
mais a 1, 2, 3 et 4 fils, dans LE MEME processus et A PASSES ENTRELACEES :
passe 1 de chaque bras, puis passe 2 de chaque bras, etc. Une derive de la
machine pendant le run se repartit alors sur tous les bras au lieu de
s'accumuler sur le dernier. (Les chiffres du 14/09 ne peuvent pas servir de
bras « 2 fils » : ils ont sept jours et un autre etat de machine.)

Le controle, et il peut echouer
-------------------------------
Demander `intra_op_num_threads = 1` a onnxruntime ne prouve pas qu'il l'a
fait : relire l'option, c'est relire ma propre demande. Le controle est le
POURCENTAGE DE PROCESSEUR, derive du systeme (getrusage / temps mural) :

  - a 1 fil, il doit rester sous 110 %
  - a 2 fils et plus, il doit depasser 150 %

Si ces deux bornes ne sont pas tenues, le bouton n'a pas agi et la
comparaison ne veut rien dire : le script l'ecrit en clair et met
`controle_tenu` a false. Un controle qui ne peut pas echouer n'est pas un
controle.

    outils/venv/bin/python outils/mesure_piper_fils.py [passes]
"""
import json
import os
import resource
import subprocess
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "outils"))
from mesure_tts import PHRASES  # texte de mesure fige, importe tel quel

from piper import PiperVoice
from piper.config import PiperConfig

MODELES = RACINE / "outils" / "modeles" / "piper"
VOIX = ["fr_FR-siwis-medium", "fr_FR-tom-medium"]
FILS = [1, 2, 3, 4]          # les colonnes de la table de k2-fsa
SORTIE_WAV = RACINE / "media" / "tts" / "mesure-piper-fils"
PLAFOND_1_FIL = 110.0        # % de processeur
PLANCHER_N_FILS = 150.0


def ffprobe_duree(chemin):
    """Duree du fichier telle que ffprobe la lit. Controle externe du 14/09."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(chemin)],
        check=True, stdout=subprocess.PIPE, text=True)
    return float(r.stdout.strip())


def cpu_total():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def charger(nom, fils):
    """PiperVoice est une dataclass : on fabrique la session nous-memes pour
    pouvoir fixer les fils, que PiperVoice.load n'expose pas (piper-tts 1.8.0).
    Le reste est identique a ce que fait load() : meme fichier, meme
    CPUExecutionProvider, meme PiperConfig.from_dict."""
    modele = MODELES / ("%s.onnx" % nom)
    config = MODELES / ("%s.onnx.json" % nom)
    so = onnxruntime.SessionOptions()
    so.intra_op_num_threads = fils
    so.inter_op_num_threads = fils
    t0 = time.perf_counter()
    session = onnxruntime.InferenceSession(
        str(modele), sess_options=so, providers=["CPUExecutionProvider"])
    with open(config, "r", encoding="utf-8") as f:
        cfg = PiperConfig.from_dict(json.load(f))
    voix = PiperVoice(config=cfg, session=session)
    return voix, time.perf_counter() - t0, modele, config


def une_passe(voix, sr, nom, fils, n):
    morceaux = []
    echantillons = 0
    c0, t0 = cpu_total(), time.perf_counter()
    for ph in PHRASES:
        for ch in voix.synthesize(ph):
            a = np.frombuffer(ch.audio_int16_bytes, dtype=np.int16)
            morceaux.append(a)
            echantillons += len(a)
    calc = time.perf_counter() - t0
    cpu = (cpu_total() - c0) / calc * 100.0

    audio_echant = echantillons / sr
    f = SORTIE_WAV / ("%s-fils%d-passe%d.wav" % (nom, fils, n))
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.concatenate(morceaux).tobytes())
    audio_ffprobe = ffprobe_duree(f)

    return {
        "passe": n,
        "temps_calcul_s": calc,
        "audio_echantillons_s": audio_echant,
        "audio_ffprobe_s": audio_ffprobe,
        "ecart_echantillons_ffprobe_s": audio_ffprobe - audio_echant,
        "ratio_ffprobe": audio_ffprobe / calc,
        "rtf": calc / audio_ffprobe,          # l'unite de k2-fsa
        "cpu_pourcent": cpu,
        "wav_octets": f.stat().st_size,
        "wav": str(f.relative_to(RACINE)),
    }


def bornes(vals):
    v = sorted(vals)
    n = len(v)
    med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return {"min": v[0], "max": v[-1], "mediane": med}


if __name__ == "__main__":
    passes = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    SORTIE_WAV.mkdir(parents=True, exist_ok=True)

    print("texte de mesure : %d caracteres, %d phrases"
          % (sum(len(p) for p in PHRASES), len(PHRASES)))
    print("piper-tts %s | onnxruntime %s | python %s | %s | %d coeurs"
          % (__import__("importlib.metadata", fromlist=["version"]).version("piper-tts"),
             onnxruntime.__version__, sys.version.split()[0],
             os.uname().machine, os.cpu_count()))
    charge_debut = os.getloadavg()
    print("charge au depart : %.2f %.2f %.2f" % charge_debut)

    res = {
        "outil": "piper-tts",
        "question": "le nombre de fils, fixe, change quoi au debit sur 2 coeurs ARM",
        "origine": ("reponse de csukuangfj (COLLABORATOR k2-fsa/sherpa-onnx) a la "
                    "discussion #3968 le 2026-09-21T02:31:15Z : "
                    "\"I suggest that you set num_threads to 1\""),
        "table_de_reference_du_destinataire":
            "https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/rtf.html",
        "version_piper": __import__("importlib.metadata", fromlist=["version"]).version("piper-tts"),
        "version_onnxruntime": onnxruntime.__version__,
        "python": sys.version.split()[0],
        "machine": os.uname().machine,
        "noyau": os.uname().release,
        "coeurs": os.cpu_count(),
        "date_utc": datetime.utcnow().isoformat() + "Z",
        "protocole": {
            "source_du_texte": "outils/mesure_tts.py (importe, non recopie)",
            "caracteres": sum(len(p) for p in PHRASES),
            "phrases": len(PHRASES),
            "texte": PHRASES,
            "passes_par_bras": passes,
            "fils_testes": FILS,
            "bouton": "onnxruntime.SessionOptions().intra_op_num_threads ET inter_op_num_threads",
            "ordre": "passes ENTRELACEES entre les bras (passe n de chaque bras, puis n+1)",
            "pourquoi_entrelace": ("une derive de la machine pendant le run se repartit sur "
                                   "tous les bras au lieu de tomber sur le dernier"),
            "controle": ("le %% de processeur, derive du systeme et non de l'option relue : "
                         "< %.0f %% a 1 fil, > %.0f %% a 2 fils et plus"
                         % (PLAFOND_1_FIL, PLANCHER_N_FILS)),
            "duree_audio": "comptee en echantillons puis verifiee par ffprobe",
            "commande": "outils/venv/bin/python outils/mesure_piper_fils.py %d" % passes,
        },
        "charge_1_5_15_debut": list(charge_debut),
        "bras": [],
    }

    for nom in VOIX:
        # une session par bras, chargees d'abord, pour pouvoir entrelacer
        sessions = {}
        for fils in FILS:
            voix, chargement, modele, config = charger(nom, fils)
            sessions[fils] = {"voix": voix, "chargement_s": chargement,
                              "sr": voix.config.sample_rate, "passes": []}
            print("%-18s %d fil(s) : modele charge en %.2f s (%d Hz)"
                  % (nom, fils, chargement, voix.config.sample_rate))

        for n in range(1, passes + 1):
            for fils in FILS:
                s = sessions[fils]
                ligne = une_passe(s["voix"], s["sr"], nom, fils, n)
                s["passes"].append(ligne)
                print("  %-18s %d fil  passe %d  audio %5.2f s  calcul %5.2f s  "
                      "x%.2f  RTF %.4f  cpu %3.0f %%"
                      % (nom, fils, n, ligne["audio_ffprobe_s"],
                         ligne["temps_calcul_s"], ligne["ratio_ffprobe"],
                         ligne["rtf"], ligne["cpu_pourcent"]))

        modele = MODELES / ("%s.onnx" % nom)
        for fils in FILS:
            s = sessions[fils]
            p = s["passes"]
            cpu = bornes([x["cpu_pourcent"] for x in p])
            if fils == 1:
                tenu = cpu["max"] < PLAFOND_1_FIL
            else:
                tenu = cpu["min"] > PLANCHER_N_FILS
            res["bras"].append({
                "voix": nom,
                "fils": fils,
                "modele": str(modele.relative_to(RACINE)),
                "modele_octets": modele.stat().st_size,
                "sample_rate_hz": s["sr"],
                "chargement_modele_s": s["chargement_s"],
                "passes": p,
                "resume": {
                    "temps_calcul_s": bornes([x["temps_calcul_s"] for x in p]),
                    "audio_ffprobe_s": bornes([x["audio_ffprobe_s"] for x in p]),
                    "ratio_ffprobe": bornes([x["ratio_ffprobe"] for x in p]),
                    "rtf": bornes([x["rtf"] for x in p]),
                    "cpu_pourcent": cpu,
                    "ecart_max_echantillons_ffprobe_s":
                        max(abs(x["ecart_echantillons_ffprobe_s"]) for x in p),
                },
                "controle_cpu_tenu": tenu,
            })
        for fils in FILS:
            sessions[fils]["voix"] = None
        sessions = None

    charge_fin = os.getloadavg()
    res["charge_1_5_15_fin"] = list(charge_fin)
    res["memoire_max_ko"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    tous_tenus = all(b["controle_cpu_tenu"] for b in res["bras"])
    res["controle_tenu"] = tous_tenus

    print("")
    print("%-18s %5s %10s %10s %8s %8s" % ("voix", "fils", "x mediane", "RTF med", "cpu", "controle"))
    for b in res["bras"]:
        print("%-18s %5d %10.2f %10.4f %7.0f%% %8s"
              % (b["voix"], b["fils"], b["resume"]["ratio_ffprobe"]["mediane"],
                 b["resume"]["rtf"]["mediane"], b["resume"]["cpu_pourcent"]["mediane"],
                 "OK" if b["controle_cpu_tenu"] else "ECHEC"))
    print("charge a la fin : %.2f %.2f %.2f" % charge_fin)

    if not tous_tenus:
        print("")
        print("CONTROLE EN ECHEC : le nombre de fils demande n'a pas agi comme attendu.")
        print("La comparaison entre bras ne veut rien dire. Rien ne doit etre publie.")

    dest = (RACINE / "media" / "mesures"
            / ("piper-fils-%s.json" % datetime.now().strftime("%Y%m%d")))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print("archive brute : %s" % dest.relative_to(RACINE))
    sys.exit(0 if tous_tenus else 3)
