#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mesure_kokoro_parallele.py — K flux Kokoro simultanes, UN FIL chacun, sur 2 coeurs.

La question, et elle est celle du mainteneur lui-meme
-----------------------------------------------------
`thewh1teagle/kokoro-onnx` issue **#40 « Improve speed »**, ouverte par le
mainteneur le 2025-01-16 et toujours ouverte, dit : *« Things we tried: Using
more threads / PARALLEL mode »*. Et son issue **#191** a etabli que
`Kokoro.create()` appele depuis plusieurs fils sur une meme instance corrompait
la sortie, parce qu'espeak-ng garde un etat global. **Le correctif est en place
en 0.6.1** : `kokoro_onnx/tokenizer.py` porte `_espeak_lock = threading.Lock()`
et `phonemize()` fait `with _espeak_lock:`.

Or ce verrou est **au niveau du module**, donc partage par TOUTES les instances
d'un meme processus. **La phonemisation ne peut donc plus etre parallelisee par
des fils, par construction.** Restent les processus separes, qui ont chacun leur
espeak.

`mesure_piper_parallele.py` a montre sur Piper que deux processus a un fil
battent un processus a deux fils de +17 %. **Je n'ai pas mesure Kokoro comme ca,
et je ne l'extrapole pas** : c'est un autre moteur, et surtout Kokoro passe une
part bien plus grande de son temps ailleurs que dans onnxruntime. Ce script le
mesure.

Methode
-------
K processus enfants, chacun `intra_op = inter_op = 1`, chacun sa propre instance
Kokoro donc son propre espeak. **Une barriere avant chaque passe** : sans elle un
enfant qui finit tot mesurerait une machine libre, et le chiffre serait faux dans
le sens qui m'arrange.

Le controle peut echouer : part de processeur de chaque enfant sous 110 %, et
somme au-dessus de 150 % a K=2. Seuils **importes** de `mesure_piper_fils.py`,
jamais recopies.

    outils/venv/bin/python outils/mesure_kokoro_parallele.py [passes]
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
from mesure_tts import PHRASES, MODELE, VOIX_BIN
from mesure_piper_fils import bornes, cpu_total, PLAFOND_1_FIL, PLANCHER_N_FILS

VOIX = "ff_siwis"
LANGUE = "fr-fr"
PLAFOND_ENFANT = PLAFOND_1_FIL
PLANCHER_SOMME_K2 = PLANCHER_N_FILS


def enfant(passes, barriere, tube, rang, affinite=False):
    # affinite : csukuangfj (COLLABORATOR k2-fsa/sherpa-onnx), 2026-09-21T03:56:46Z,
    # << Process 0 runs on CPU 0, and process 1 runs on CPU 1. You can use taskset >>.
    # Posee AVANT la session ORT, pour que ses fils naissent dans l ensemble restreint.
    if affinite:
        os.sched_setaffinity(0, {rang % os.cpu_count()})
    vu = sorted(os.sched_getaffinity(0))
    from kokoro_onnx import Kokoro
    so = onnxruntime.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    session = onnxruntime.InferenceSession(
        str(MODELE), sess_options=so, providers=["CPUExecutionProvider"])
    k = Kokoro.from_session(session, str(VOIX_BIN))

    lignes = []
    for n in range(1, passes + 1):
        barriere.wait()
        ech, sr = 0, None
        c0, t0 = cpu_total(), time.perf_counter()
        for ph in PHRASES:
            s, sr = k.create(ph, voice=VOIX, speed=1.0, lang=LANGUE)
            ech += len(np.asarray(s))
        calc = time.perf_counter() - t0
        cpu = (cpu_total() - c0) / calc * 100.0
        audio = ech / sr
        lignes.append({"passe": n, "temps_calcul_s": calc, "audio_s": audio,
                       "ratio": audio / calc, "rtf": calc / audio,
                       "cpu_pourcent": cpu, "sample_rate_hz": sr})
    tube.put({"rang": rang, "passes": lignes, "affinite_effective": vu})


def un_bras(K, passes, affinite=False):
    barriere = mp.Barrier(K)
    tube = mp.Queue()
    procs = [mp.Process(target=enfant, args=(passes, barriere, tube, r, affinite))
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
        enfants.append({"rang": r["rang"], "affinite_effective": r["affinite_effective"],
                        "passes": p, "resume": {
            "ratio": bornes([x["ratio"] for x in p]),
            "rtf": bornes([x["rtf"] for x in p]),
            "cpu_pourcent": bornes([x["cpu_pourcent"] for x in p])}})
    debits = [e["resume"]["ratio"]["mediane"] for e in enfants]
    cpus = [e["resume"]["cpu_pourcent"]["mediane"] for e in enfants]
    tenu = all(e["resume"]["cpu_pourcent"]["max"] < PLAFOND_ENFANT for e in enfants)
    if K >= 2:
        tenu = tenu and sum(cpus) > PLANCHER_SOMME_K2
    # Controle de l affinite : sans lui, un sched_setaffinity qui ne prend pas
    # rendrait deux bras identiques et je conclurais << aucun effet >> en ayant
    # mesure deux fois la meme chose. Singletons DISTINCTS si demandee, ensemble
    # COMPLET sinon. Predicat eprouve sur 6 cas dont 4 pannes le 2026-09-22.
    vus = [e["affinite_effective"] for e in enfants]
    if affinite:
        aff_ok = (all(len(v) == 1 for v in vus)
                  and len({v[0] for v in vus}) == min(K, os.cpu_count()))
    else:
        aff_ok = all(len(v) == os.cpu_count() for v in vus)
    tenu = tenu and aff_ok
    return {"flux_simultanes": K, "fils_par_flux": 1, "mural_total_s": mural,
            "affinite_demandee": affinite, "affinites_vues": vus,
            "controle_affinite_tenu": aff_ok,
            "enfants": enfants, "debit_par_flux_mediane": debits,
            "debit_agrege_mediane": sum(debits), "cpu_somme_medianes": sum(cpus),
            "controle_tenu": tenu}


if __name__ == "__main__":
    passes = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    mp.set_start_method("spawn")
    print("texte : %d caracteres | %d coeurs | charge %.2f"
          % (sum(len(p) for p in PHRASES), os.cpu_count(), os.getloadavg()[0]))

    res = {
        "outil": "kokoro-onnx",
        "question": ("pour K flux Kokoro simultanes a 1 fil sur 2 coeurs ARM, quel est le debit "
                     "agrege, et bat-il un seul processus a 2 fils"),
        "origine": ("thewh1teagle/kokoro-onnx issue #40 << Improve speed >>, ouverte par le "
                    "mainteneur : << Things we tried: Using more threads / PARALLEL mode >>. Et "
                    "issue #191 : le verrou espeak ajoute en 0.6.1 est AU NIVEAU DU MODULE, donc "
                    "la phonemisation ne peut pas etre parallelisee par des fils dans un processus."),
        "verrou_lu_dans_leur_code": ("kokoro_onnx/tokenizer.py : _espeak_lock = threading.Lock() "
                                     "(module), et phonemize() fait `with _espeak_lock:`"),
        "version_kokoro_onnx": __import__("importlib.metadata", fromlist=["version"]).version("kokoro-onnx"),
        "version_onnxruntime": onnxruntime.__version__,
        "machine": os.uname().machine, "coeurs": os.cpu_count(),
        "date_utc": datetime.utcnow().isoformat() + "Z",
        "protocole": {
            "source_du_texte": "outils/mesure_tts.py (importe, non recopie)",
            "passes_par_bras": passes,
        "conditions": "(K=1, sans affinite), (K=2, sans affinite), (K=2, un coeur par processus)",
            "une_instance_Kokoro_par_processus": True,
            "synchronisation": "multiprocessing.Barrier avant chaque passe",
            "controle": ("%% de processeur par enfant < %.0f %%, somme > %.0f %% a K=2"
                         % (PLAFOND_ENFANT, PLANCHER_SOMME_K2)),
            "seuils_importes_de": "outils/mesure_piper_fils.py",
            "commande": "outils/venv/bin/python outils/mesure_kokoro_parallele.py %d" % passes,
        },
        "bras": [],
    }

    CONDITIONS = [(1, False), (2, False), (2, True)]
    print("DIMENSIONS : 1 voix x %d conditions = %d bras" % (len(CONDITIONS), len(CONDITIONS)))
    for K, aff in CONDITIONS:
        b = un_bras(K, passes, aff)
        res["bras"].append(b)
        print("K=%d aff=%-5s  debit/flux %s  agrege x%.3f  cpu %.0f %%  aff_vue %s  %s"
              % (K, str(aff), " ".join("x%.3f" % d for d in b["debit_par_flux_mediane"]),
                 b["debit_agrege_mediane"], b["cpu_somme_medianes"], b["affinites_vues"],
                 "OK" if b["controle_tenu"] else "CONTROLE EN ECHEC"))

    res["controle_tenu"] = all(b["controle_tenu"] for b in res["bras"])
    res["memoire_max_ko"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if not res["controle_tenu"]:
        print("CONTROLE EN ECHEC : rien a publier de ce run.")
    # Nom a la SECONDE : a la journee, un second releve du meme jour ecrasait le
    # premier en silence (3e instance de ce defaut, cf. auditer-liens.py le 21/09).
    base = RACINE / "media" / "mesures"
    dest = base / ("kokoro-parallele-%s.json" % datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    n = 0
    while dest.exists():
        n += 1
        dest = base / ("kokoro-parallele-%s-%d.json" % (datetime.utcnow().strftime("%Y%m%d-%H%M%S"), n))
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print("archive brute : %s" % dest.relative_to(RACINE))
    sys.exit(0 if res["controle_tenu"] else 3)
