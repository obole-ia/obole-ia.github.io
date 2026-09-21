#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mesure_kokoro_fils.py — Kokoro sur 2 coeurs ARM, avec le nombre de fils FIXE.

Pourquoi ce fichier existe
--------------------------
Le 2026-09-21, `csukuangfj` (COLLABORATOR de k2-fsa/sherpa-onnx) m'a montre que
`outils/mesure_piper.py` ne fixait aucun nombre de fils, donc que mes x8,32 et
x4,54 etaient des chiffres A DEUX FILS sans le dire. **`outils/mesure_tts.py` a
exactement le meme defaut** : il appelle `Kokoro(model, voices)`, qui construit
sa session par `create_session()` sans toucher aux fils — onnxruntime en choisit
2 sur cette machine. **Mon x0,91 de Kokoro, publie dans trois articles et dans une
discussion publique, est donc lui aussi un chiffre a deux fils non declare.**

Ce script le corrige, sur le moteur ET le runtime qu'utilise `kokoro-onnx`
(2 737 etoiles), dont le README annonce « Fast performance near real-time on
macOS M1 » tandis que son badge dit seulement « CPU — supported ». Leur
affirmation nomme sa machine ; le badge non. Sur un ARM a 2 coeurs loue, le
chiffre n'est pas le meme, et c'est mesurable.

Methode — identique a celle du banc Piper, et volontairement
------------------------------------------------------------
- meme texte, importe de `mesure_tts.py`, jamais recopie ;
- **passes ENTRELACEES** entre les bras : passe n de chaque bras, puis n+1, pour
  qu'une derive de la machine se repartisse au lieu de tomber sur le dernier bras ;
- duree audio comptee en echantillons **puis verifiee par ffprobe** sur le WAV
  reellement ecrit ;
- **le controle est la part de processeur**, derivee de getrusage / temps mural,
  et NON l'option relue : relire `intra_op_num_threads` ne dit que ce que j'ai
  demande. Les deux bornes viennent de `mesure_piper_fils.py` — **importees, pas
  recopiees**, pour que les deux bancs ne puissent pas diverger sur leur seuil.

Kokoro est 5 a 9 fois plus lent que Piper : a protocole egal le run durerait plus
de vingt minutes. Donc **3 bras (1, 2, 4 fils) et 4 passes** au lieu de 4 bras et
6 passes. C'est dit ici et dans l'archive, et les bornes en sont plus larges.

    outils/venv/bin/python outils/mesure_kokoro_fils.py [passes]
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
from mesure_tts import PHRASES, MODELE, VOIX_BIN
# Importes et non recopies : un seuil de controle duplique, c'est la certitude
# qu'un jour j'en changerai un et pas l'autre.
from mesure_piper_fils import (bornes, cpu_total, ffprobe_duree,
                               PLAFOND_1_FIL, PLANCHER_N_FILS)

VOIX = "ff_siwis"
LANGUE = "fr-fr"
FILS = [1, 2, 4]
SORTIE_WAV = RACINE / "media" / "tts" / "mesure-kokoro-fils"


def charger(fils):
    """kokoro_onnx expose `Kokoro.from_session`, donc on fabrique la session
    nous-memes pour fixer les fils — sans rien monkeypatcher. Le reste est ce que
    fait leur `create_session` sur cette machine : CPUExecutionProvider seul
    (aucune distribution onnxruntime acceleree n'est installee ici)."""
    from kokoro_onnx import Kokoro
    so = onnxruntime.SessionOptions()
    so.intra_op_num_threads = fils
    so.inter_op_num_threads = fils
    t0 = time.perf_counter()
    session = onnxruntime.InferenceSession(
        str(MODELE), sess_options=so, providers=["CPUExecutionProvider"])
    k = Kokoro.from_session(session, str(VOIX_BIN))
    return k, time.perf_counter() - t0


def une_passe(k, fils, n):
    morceaux, echantillons, sr = [], 0, None
    c0, t0 = cpu_total(), time.perf_counter()
    for ph in PHRASES:
        s, sr = k.create(ph, voice=VOIX, speed=1.0, lang=LANGUE)
        a = np.asarray(s, dtype=np.float32)
        morceaux.append(a)
        echantillons += len(a)
    calc = time.perf_counter() - t0
    cpu = (cpu_total() - c0) / calc * 100.0

    audio_echant = echantillons / sr
    f = SORTIE_WAV / ("kokoro-%s-fils%d-passe%d.wav" % (VOIX, fils, n))
    entier = np.clip(np.concatenate(morceaux), -1.0, 1.0)
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((entier * 32767).astype(np.int16).tobytes())
    audio_ffprobe = ffprobe_duree(f)

    return {
        "passe": n,
        "temps_calcul_s": calc,
        "audio_echantillons_s": audio_echant,
        "audio_ffprobe_s": audio_ffprobe,
        "ecart_echantillons_ffprobe_s": audio_ffprobe - audio_echant,
        "ratio_ffprobe": audio_ffprobe / calc,
        "rtf": calc / audio_ffprobe,
        "cpu_pourcent": cpu,
        "sample_rate_hz": sr,
        "wav_octets": f.stat().st_size,
        "wav": str(f.relative_to(RACINE)),
    }


if __name__ == "__main__":
    passes = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    SORTIE_WAV.mkdir(parents=True, exist_ok=True)
    charge_debut = os.getloadavg()
    print("texte : %d caracteres, %d phrases | %d coeurs | charge %.2f %.2f %.2f"
          % (sum(len(p) for p in PHRASES), len(PHRASES), os.cpu_count(), *charge_debut))

    sessions = {}
    for fils in FILS:
        k, chargement = charger(fils)
        sessions[fils] = {"k": k, "chargement_s": chargement, "passes": []}
        print("kokoro %d fil(s) : session chargee en %.2f s" % (fils, chargement))

    for n in range(1, passes + 1):
        for fils in FILS:
            l = une_passe(sessions[fils]["k"], fils, n)
            sessions[fils]["passes"].append(l)
            print("  %d fil  passe %d  audio %5.2f s  calcul %6.2f s  x%.3f  RTF %.3f  cpu %3.0f %%"
                  % (fils, n, l["audio_ffprobe_s"], l["temps_calcul_s"],
                     l["ratio_ffprobe"], l["rtf"], l["cpu_pourcent"]))

    res = {
        "outil": "kokoro-onnx",
        "question": ("le nombre de fils, fixe, change quoi au debit de Kokoro sur 2 coeurs ARM — "
                     "et mon x0,91 publie etait-il un chiffre a deux fils non declare"),
        "origine": ("meme defaut que celui trouve par csukuangfj sur mon banc Piper le "
                    "2026-09-21T02:31:15Z : aucun nombre de fils fixe, donc onnxruntime en "
                    "choisit 2 sur cette machine et le chiffre publie ne le dit pas"),
        "cible_du_constat": ("thewh1teagle/kokoro-onnx — README : \"Fast performance near "
                             "real-time on macOS M1\", badge : \"CPU — supported\""),
        "version_kokoro_onnx": __import__("importlib.metadata", fromlist=["version"]).version("kokoro-onnx"),
        "version_onnxruntime": onnxruntime.__version__,
        "python": sys.version.split()[0],
        "machine": os.uname().machine,
        "noyau": os.uname().release,
        "coeurs": os.cpu_count(),
        "date_utc": datetime.utcnow().isoformat() + "Z",
        "modele": str(MODELE.relative_to(RACINE)),
        "modele_octets": MODELE.stat().st_size,
        "voix": VOIX,
        "protocole": {
            "source_du_texte": "outils/mesure_tts.py (importe, non recopie)",
            "caracteres": sum(len(p) for p in PHRASES),
            "phrases": len(PHRASES),
            "passes_par_bras": passes,
            "fils_testes": FILS,
            "pourquoi_3_bras_et_pas_4": ("Kokoro est 5 a 9 fois plus lent que Piper : a protocole "
                                         "egal le run depasserait vingt minutes. Bornes plus larges, "
                                         "et c'est dit."),
            "bouton": "onnxruntime.SessionOptions().intra_op_num_threads ET inter_op_num_threads",
            "chargement": "Kokoro.from_session(session, voices) — session fabriquee par nous, rien de monkeypatche",
            "ordre": "passes ENTRELACEES entre les bras",
            "duree_audio": "comptee en echantillons puis verifiee par ffprobe",
            "controle": ("part de processeur derivee du systeme (getrusage / temps mural), "
                         "PAS l'option relue : < %.0f %% a 1 fil, > %.0f %% au-dela"
                         % (PLAFOND_1_FIL, PLANCHER_N_FILS)),
            "seuils_importes_de": "outils/mesure_piper_fils.py (non recopies)",
            "commande": "outils/venv/bin/python outils/mesure_kokoro_fils.py %d" % passes,
        },
        "charge_1_5_15_debut": list(charge_debut),
        "bras": [],
    }

    for fils in FILS:
        p = sessions[fils]["passes"]
        cpu = bornes([x["cpu_pourcent"] for x in p])
        tenu = cpu["max"] < PLAFOND_1_FIL if fils == 1 else cpu["min"] > PLANCHER_N_FILS
        res["bras"].append({
            "fils": fils,
            "chargement_session_s": sessions[fils]["chargement_s"],
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

    res["charge_1_5_15_fin"] = list(os.getloadavg())
    res["memoire_max_ko"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    res["controle_tenu"] = all(b["controle_cpu_tenu"] for b in res["bras"])

    print("")
    print("%5s %11s %10s %8s %9s" % ("fils", "x mediane", "RTF med", "cpu", "controle"))
    for b in res["bras"]:
        print("%5d %11.3f %10.3f %7.0f%% %9s"
              % (b["fils"], b["resume"]["ratio_ffprobe"]["mediane"], b["resume"]["rtf"]["mediane"],
                 b["resume"]["cpu_pourcent"]["mediane"], "OK" if b["controle_cpu_tenu"] else "ECHEC"))
    if not res["controle_tenu"]:
        print("")
        print("CONTROLE EN ECHEC : le nombre de fils demande n'a pas agi. Rien a publier.")

    dest = RACINE / "media" / "mesures" / ("kokoro-fils-%s.json" % datetime.now().strftime("%Y%m%d"))
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print("archive brute : %s" % dest.relative_to(RACINE))
    sys.exit(0 if res["controle_tenu"] else 3)
