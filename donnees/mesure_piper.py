#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mesure_piper.py — debit reel de Piper TTS sur ce serveur.

Meme protocole que outils/mesure_tts.py (article kokoro-82m-vitesse-cpu) :
le texte de mesure n'est PAS recopie ici, il est importe de mesure_tts.py
pour qu'il soit identique au caractere pres (8 phrases, 505 caracteres).

Chaque passe : les 8 phrases sont synthetisees une par une, le temps de
calcul est chronometre, la duree audio est comptee en echantillons PUIS
verifiee par ffprobe sur le WAV reellement ecrit (ligne rouge nº9 : on
verifie que l'instrument mesure). Le processeur est releve par passe via
getrusage, et recoupe sur l'ensemble du run par /usr/bin/time.

    outils/venv/bin/python outils/mesure_piper.py 6

Aucun chiffre n'est arrondi a notre avantage : les ratios sont imprimes
tels qu'ils sortent de la division.
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

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "outils"))
from mesure_tts import PHRASES  # texte de mesure fige, importe tel quel

MODELES = RACINE / "outils" / "modeles" / "piper"
VOIX = ["fr_FR-siwis-medium", "fr_FR-tom-medium"]
SORTIE_WAV = RACINE / "media" / "tts" / "mesure-piper"


def ffprobe_duree(chemin):
    """Duree du fichier telle que ffprobe la lit. Notre controle externe."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(chemin)],
        check=True, stdout=subprocess.PIPE, text=True)
    return float(r.stdout.strip())


def cpu_total():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def mesure_voix(nom, passes):
    from piper import PiperVoice

    modele = MODELES / ("%s.onnx" % nom)
    config = MODELES / ("%s.onnx.json" % nom)
    t0 = time.perf_counter()
    voix = PiperVoice.load(str(modele), str(config))
    chargement = time.perf_counter() - t0
    sr = voix.config.sample_rate
    print("%-18s chargement du modele %.2f s  (%d Hz)" % (nom, chargement, sr))

    lignes = []
    SORTIE_WAV.mkdir(parents=True, exist_ok=True)
    for n in range(1, passes + 1):
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
        # controle externe : on ecrit le WAV et on demande sa duree a ffprobe
        f = SORTIE_WAV / ("%s-passe%d.wav" % (nom, n))
        with wave.open(str(f), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(np.concatenate(morceaux).tobytes())
        audio_ffprobe = ffprobe_duree(f)
        octets = f.stat().st_size

        lignes.append({
            "passe": n,
            "temps_calcul_s": calc,
            "audio_echantillons_s": audio_echant,
            "audio_ffprobe_s": audio_ffprobe,
            "ecart_echantillons_ffprobe_s": audio_ffprobe - audio_echant,
            "ratio_echantillons": audio_echant / calc,
            "ratio_ffprobe": audio_ffprobe / calc,
            "cpu_pourcent": cpu,
            "wav_octets": octets,
            "wav": str(f.relative_to(RACINE)),
        })
        print("passe %d  audio %5.2f s (ffprobe %5.2f s)  calcul %5.2f s  "
              "ratio x%.2f  cpu %.0f %%  wav %d o"
              % (n, audio_echant, audio_ffprobe, calc,
                 audio_ffprobe / calc, cpu, octets))

    return {
        "voix": nom,
        "modele": str(modele.relative_to(RACINE)),
        "modele_octets": modele.stat().st_size,
        "config_octets": config.stat().st_size,
        "sample_rate_hz": sr,
        "chargement_modele_s": chargement,
        "passes": lignes,
    }


def bornes(vals):
    v = sorted(vals)
    n = len(v)
    med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    return {"min": v[0], "max": v[-1], "mediane": med}


if __name__ == "__main__":
    passes = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    texte = " ".join(PHRASES)
    print("texte de mesure : %d caracteres, %d phrases"
          % (sum(len(p) for p in PHRASES), len(PHRASES)))
    print("piper-tts %s | python %s | %s"
          % (__import__("importlib.metadata", fromlist=["version"])
             .version("piper-tts"), sys.version.split()[0], os.uname().machine))

    res = {
        "outil": "piper-tts",
        "version_piper": __import__("importlib.metadata",
                                    fromlist=["version"]).version("piper-tts"),
        "version_onnxruntime": __import__("importlib.metadata",
                                          fromlist=["version"])
                               .version("onnxruntime"),
        "python": sys.version.split()[0],
        "machine": os.uname().machine,
        "noyau": os.uname().release,
        "date_utc": datetime.utcnow().isoformat() + "Z",
        "protocole": {
            "source_du_texte": "outils/mesure_tts.py (importe, non recopie)",
            "caracteres": sum(len(p) for p in PHRASES),
            "phrases": len(PHRASES),
            "texte": PHRASES,
            "passes_demandees": passes,
            "synthese": "phrase par phrase, modele charge une seule fois",
            "duree_audio": "comptee en echantillons puis verifiee par ffprobe",
            "commande": "outils/venv/bin/python outils/mesure_piper.py %d" % passes,
        },
        "voix_mesurees": [],
    }

    for nom in VOIX:
        res["voix_mesurees"].append(mesure_voix(nom, passes))

    for v in res["voix_mesurees"]:
        p = v["passes"]
        v["resume"] = {
            "temps_calcul_s": bornes([x["temps_calcul_s"] for x in p]),
            "audio_ffprobe_s": bornes([x["audio_ffprobe_s"] for x in p]),
            "ratio_ffprobe": bornes([x["ratio_ffprobe"] for x in p]),
            "cpu_pourcent": bornes([x["cpu_pourcent"] for x in p]),
            "ecart_max_echantillons_ffprobe_s":
                max(abs(x["ecart_echantillons_ffprobe_s"]) for x in p),
        }
        r = v["resume"]["ratio_ffprobe"]
        print("%-18s ratio x%.2f a x%.2f  mediane x%.2f"
              % (v["voix"], r["min"], r["max"], r["mediane"]))

    res["memoire_max_ko"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # 2e argument optionnel : suffixe du fichier d'archive, pour garder
    # plusieurs series du meme jour au lieu de les ecraser.
    suffixe = ("-" + sys.argv[2]) if len(sys.argv) > 2 else ""
    dest = (RACINE / "media" / "mesures"
            / ("piper-%s%s.json" % (datetime.now().strftime("%Y%m%d"), suffixe)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print("archive brute : %s" % dest.relative_to(RACINE))
