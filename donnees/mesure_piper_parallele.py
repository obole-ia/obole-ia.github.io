#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mesure_piper_parallele.py — K flux simultanes A UN FIL CHACUN, sur 2 coeurs.

La question a laquelle ce script repond
---------------------------------------
Ma discussion k2-fsa/sherpa-onnx #3968 demandait ce que coute la contention
de processeur. J'y avais repondu avec des chiffres qui ne fixaient pas le
nombre de fils — defaut que j'avais annonce au texte et que `csukuangfj`
(COLLABORATOR) a confirme le 2026-09-21 a 02:31 UTC.

`mesure_piper_fils.py` a montre, le defaut corrige, que sur 2 coeurs le
meilleur reglage POUR UN SEUL FLUX est 2 fils (x8,24 contre x5,07 a 1 fil).
Mais servir deux clients n'est pas servir un client deux fois plus vite. La
vraie question de deploiement est :

    pour DEUX flux simultanes sur 2 coeurs, vaut-il mieux
    deux processus a 1 fil, ou un processus a 2 fils utilise deux fois ?

On ne peut pas la deduire des chiffres precedents : elle demande de mesurer
K processus qui tournent VRAIMENT en meme temps.

Protocole
---------
K processus enfants, chacun a `intra_op = inter_op = 1`, chacun synthetisant
le meme texte (importe de mesure_tts.py). Une BARRIERE fait demarrer les
passes ensemble : sans elle, un enfant qui finit tot mesurerait une machine
libre et le chiffre serait faux dans le sens qui m'arrange.

Le controle, et il peut echouer
-------------------------------
Le %% de processeur de chaque enfant, derive du systeme :
  - chaque enfant doit rester sous 110 %% (sinon son fil unique n'a pas pris)
  - a K=2, la somme doit depasser 150 %% (sinon ils ne tournaient pas
    ensemble, et la barriere a echoue)
Si l'une des deux bornes tombe, `controle_tenu` passe a false et rien ne
doit etre publie.

    outils/venv/bin/python outils/mesure_piper_parallele.py [passes]
"""
import json
import multiprocessing as mp
import os
import resource
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "outils"))
from mesure_tts import PHRASES

from piper import PiperVoice
from piper.config import PiperConfig

MODELES = RACINE / "outils" / "modeles" / "piper"
VOIX = ["fr_FR-siwis-medium", "fr_FR-tom-medium"]
PLAFOND_ENFANT = 110.0
PLANCHER_SOMME_K2 = 150.0


def cpu_total():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def enfant(nom, passes, barriere, tube, rang):
    """Un flux : un fil, son propre modele, ses propres passes."""
    modele = MODELES / ("%s.onnx" % nom)
    config = MODELES / ("%s.onnx.json" % nom)
    so = onnxruntime.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    session = onnxruntime.InferenceSession(
        str(modele), sess_options=so, providers=["CPUExecutionProvider"])
    with open(config, "r", encoding="utf-8") as f:
        cfg = PiperConfig.from_dict(json.load(f))
    voix = PiperVoice(config=cfg, session=session)
    sr = voix.config.sample_rate

    lignes = []
    for n in range(1, passes + 1):
        barriere.wait()                      # tout le monde part ensemble
        ech = 0
        c0, t0 = cpu_total(), time.perf_counter()
        for ph in PHRASES:
            for ch in voix.synthesize(ph):
                ech += len(np.frombuffer(ch.audio_int16_bytes, dtype=np.int16))
        calc = time.perf_counter() - t0
        cpu = (cpu_total() - c0) / calc * 100.0
        audio = ech / sr
        lignes.append({"passe": n, "temps_calcul_s": calc, "audio_s": audio,
                       "ratio": audio / calc, "rtf": calc / audio,
                       "cpu_pourcent": cpu})
    tube.put({"rang": rang, "voix": nom, "sample_rate_hz": sr, "passes": lignes})


def bornes(vals):
    v = sorted(vals)
    n = len(v)
    med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return {"min": v[0], "max": v[-1], "mediane": med}


def un_bras(nom, K, passes):
    barriere = mp.Barrier(K)
    tube = mp.Queue()
    procs = [mp.Process(target=enfant, args=(nom, passes, barriere, tube, r))
             for r in range(K)]
    t0 = time.perf_counter()
    for p in procs:
        p.start()
    recoltes = [tube.get() for _ in range(K)]
    for p in procs:
        p.join()
    mural = time.perf_counter() - t0

    recoltes.sort(key=lambda x: x["rang"])
    enfants = []
    for r in recoltes:
        p = r["passes"]
        enfants.append({
            "rang": r["rang"],
            "passes": p,
            "resume": {"ratio": bornes([x["ratio"] for x in p]),
                       "rtf": bornes([x["rtf"] for x in p]),
                       "cpu_pourcent": bornes([x["cpu_pourcent"] for x in p])},
        })
    debits = [e["resume"]["ratio"]["mediane"] for e in enfants]
    cpus = [e["resume"]["cpu_pourcent"]["mediane"] for e in enfants]
    tenu = all(e["resume"]["cpu_pourcent"]["max"] < PLAFOND_ENFANT for e in enfants)
    if K >= 2:
        tenu = tenu and sum(cpus) > PLANCHER_SOMME_K2
    return {
        "voix": nom,
        "flux_simultanes": K,
        "fils_par_flux": 1,
        "mural_total_s": mural,
        "enfants": enfants,
        "debit_par_flux_mediane": debits,
        "debit_agrege_mediane": sum(debits),
        "cpu_somme_medianes": sum(cpus),
        "controle_tenu": tenu,
    }


if __name__ == "__main__":
    passes = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    mp.set_start_method("spawn")
    print("texte : %d caracteres, %d phrases | %d coeurs | charge %.2f"
          % (sum(len(p) for p in PHRASES), len(PHRASES), os.cpu_count(),
             os.getloadavg()[0]))

    res = {
        "outil": "piper-tts",
        "question": ("pour K flux simultanes a 1 fil sur 2 coeurs ARM, quel est le "
                     "debit agrege, et bat-il un seul processus a 2 fils"),
        "origine": ("csukuangfj (COLLABORATOR k2-fsa/sherpa-onnx), discussion #3968, "
                    "2026-09-21T02:31:15Z : \"I suggest that you set num_threads to 1\""),
        "version_piper": __import__("importlib.metadata", fromlist=["version"]).version("piper-tts"),
        "version_onnxruntime": onnxruntime.__version__,
        "machine": os.uname().machine,
        "coeurs": os.cpu_count(),
        "date_utc": datetime.utcnow().isoformat() + "Z",
        "protocole": {
            "source_du_texte": "outils/mesure_tts.py (importe, non recopie)",
            "passes_par_bras": passes,
            "K_testes": [1, 2],
            "synchronisation": "multiprocessing.Barrier avant chaque passe",
            "pourquoi_barriere": ("sans elle un enfant qui finit tot mesure une machine "
                                  "libre, et le chiffre est faux dans le sens qui m'arrange"),
            "controle": ("%% de processeur par enfant < %.0f %%, et somme > %.0f %% a K=2"
                         % (PLAFOND_ENFANT, PLANCHER_SOMME_K2)),
            "commande": "outils/venv/bin/python outils/mesure_piper_parallele.py %d" % passes,
        },
        "bras": [],
    }

    for nom in VOIX:
        for K in (1, 2):
            b = un_bras(nom, K, passes)
            res["bras"].append(b)
            print("%-18s K=%d  debit/flux %s  agrege x%.2f  cpu somme %.0f %%  %s"
                  % (nom, K, " ".join("x%.2f" % d for d in b["debit_par_flux_mediane"]),
                     b["debit_agrege_mediane"], b["cpu_somme_medianes"],
                     "OK" if b["controle_tenu"] else "CONTROLE EN ECHEC"))

    res["controle_tenu"] = all(b["controle_tenu"] for b in res["bras"])
    if not res["controle_tenu"]:
        print("CONTROLE EN ECHEC : rien ne doit etre publie de ce run.")

    dest = (RACINE / "media" / "mesures"
            / ("piper-parallele-%s.json" % datetime.now().strftime("%Y%m%d")))
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print("archive brute : %s" % dest.relative_to(RACINE))
    sys.exit(0 if res["controle_tenu"] else 3)
