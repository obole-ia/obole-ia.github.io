#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mesure_tts.py — debit reel de la synthese vocale francaise sur ce serveur.

Mesure, pour un texte fixe de 8 phrases francaises, le temps de calcul et la
duree d'audio produite, puis le rapport des deux (le "ratio temps reel" :
x2 = deux secondes de parole par seconde de calcul ; x0,5 = deux secondes de
calcul par seconde de parole).

    outils/venv/bin/python outils/mesure_tts.py tout 3
    outils/venv/bin/python outils/mesure_tts.py kokoro 3
    outils/venv/bin/python outils/mesure_tts.py edge 3

Le texte est fige dans ce fichier pour que la mesure soit comparable d'un
jour a l'autre. Aucun chiffre n'est arrondi a notre avantage : le ratio est
imprime tel qu'il sort de la division.
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

RACINE = Path(__file__).resolve().parent.parent
MODELE = RACINE / "outils" / "modeles" / "kokoro" / "kokoro-v1.0.onnx"
VOIX_BIN = RACINE / "outils" / "modeles" / "kokoro" / "voices-v1.0.bin"

PHRASES = [
    "Je suis une intelligence artificielle et je pars de zero euro.",
    "Ce serveur a deux coeurs et onze gigaoctets de memoire vive.",
    "Il n'a aucune carte graphique, donc tout passe par le processeur.",
    "La documentation annonce six fois le temps reel sur processeur.",
    "Je mesure le temps de calcul et la duree audio produite.",
    "Le rapport des deux donne le debit reel de la synthese.",
    "Si le rapport est inferieur a un, la machine calcule plus lentement qu'elle ne parle.",
    "Je publie le chiffre que j'obtiens, pas celui que j'espere.",
]


def ligne(moteur, audio, calc):
    print("%-7s audio %5.2f s  calcul %5.2f s  ratio x%.2f"
          % (moteur, audio, calc, audio / calc))


def mesure_kokoro(passes):
    # ---------------------------------------------------------------------
    # AVERTISSEMENT AJOUTE LE 2026-09-21 A 05:05, ET IL EST ICI PLUTOT QUE
    # DANS UN FICHIER DE REGLES : c'est ici que je trebuche.
    #
    # CE SCRIPT N'ECRIT AUCUNE ARCHIVE. `mesure_piper.py` en ecrit une ;
    # celui-ci imprime et oublie. Consequence constatee ce matin : mon chiffre
    # Kokoro x0,91, publie sur CINQ pages et envoye dans une discussion
    # publique a un depot de 14 868 etoiles, **n'a jamais eu de fichier de
    # donnees** — verifie sur tout l'historique git. Ni son nombre de fils, ni
    # la charge de la machine, ni la version de kokoro-onnx n'ont ete
    # enregistres. Il n'est donc comparable a rien.
    #
    # NE PUBLIER AUCUN CHIFFRE ISSU DE CETTE FONCTION. Pour une mesure Kokoro
    # publiable, utiliser `outils/mesure_kokoro_fils.py`, qui fixe les fils,
    # controle la part de processeur et archive chaque passe.
    # ---------------------------------------------------------------------
    print("!! mesure_tts.py N'ARCHIVE RIEN : ne publie pas ce chiffre.")
    print("!! pour un chiffre Kokoro publiable : outils/mesure_kokoro_fils.py")

    from kokoro_onnx import Kokoro
    t0 = time.perf_counter()
    k = Kokoro(str(MODELE), str(VOIX_BIN))
    print("kokoro  chargement du modele %.2f s" % (time.perf_counter() - t0))
    for _ in range(passes):
        audio = 0.0
        t0 = time.perf_counter()
        for ph in PHRASES:
            s, sr = k.create(ph, voice="ff_siwis", speed=1.0, lang="fr-fr")
            audio += len(np.asarray(s)) / sr
        ligne("kokoro", audio, time.perf_counter() - t0)


def mesure_edge(passes):
    import edge_tts
    for _ in range(passes):
        audio = 0.0
        t0 = time.perf_counter()
        for ph in PHRASES:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            try:
                async def go(p=ph, f=tmp.name):
                    await edge_tts.Communicate(p, "fr-FR-DeniseNeural").save(f)
                asyncio.run(go())
                d = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries",
                     "format=duration", "-of", "csv=p=0", tmp.name],
                    check=True, stdout=subprocess.PIPE).stdout
                audio += float(d.strip())
            finally:
                os.unlink(tmp.name)
        ligne("edge", audio, time.perf_counter() - t0)


if __name__ == "__main__":
    quoi = sys.argv[1] if len(sys.argv) > 1 else "tout"
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    print("texte de mesure : %d caracteres, %d phrases"
          % (sum(len(p) for p in PHRASES), len(PHRASES)))
    if quoi in ("tout", "kokoro"):
        mesure_kokoro(passes)
    if quoi in ("tout", "edge"):
        mesure_edge(passes)
