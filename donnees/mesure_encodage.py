#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mesure_encodage.py — compromis poids / temps / qualite des reglages libx264
sur une video verticale, mesure sur CETTE machine.

Pour chaque reglage : N encodages chronometres en serie (jamais en parallele,
la machine n'a que deux coeurs), puis le poids du fichier, le debit video, et
la qualite objective (SSIM et PSNR de ffmpeg) par rapport a la source.

Rien n'est arrondi a la main : les valeurs sont imprimees telles que ffmpeg
et l'horloge les donnent (identite.md, ligne rouge nº2).

    outils/venv/bin/python outils/mesure_encodage.py \
        media/episodes/jour-000.mp4 /tmp/enc 2

Arguments : <source> <dossier de sortie> [passes, defaut 2] [reglages...]
Sans liste de reglages, les huit reglages de REGLAGES sont mesures ; ceux de
SUPPLEMENT ne le sont que si on les nomme.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

# nom -> arguments video passes a libx264. L'audio est toujours re-encode en
# AAC 128 kb/s pour que la comparaison porte sur la video.
REGLAGES = [
    ("crf20-medium",   ["-crf", "20", "-preset", "medium"]),
    ("crf23-medium",   ["-crf", "23", "-preset", "medium"]),
    ("crf28-medium",   ["-crf", "28", "-preset", "medium"]),
    ("crf32-medium",   ["-crf", "32", "-preset", "medium"]),
    ("crf23-veryfast", ["-crf", "23", "-preset", "veryfast"]),
    ("crf23-slow",     ["-crf", "23", "-preset", "slow"]),
    ("crf28-veryfast", ["-crf", "28", "-preset", "veryfast"]),
    # debit cible, deux passes : le temps mesure est la somme des deux passes
    ("2pass-2M",       ["__2PASS__", "2M", "-preset", "medium"]),
]

# Reglages intermediaires : jamais dans la serie par defaut, mesures seulement
# si on les nomme sur la ligne de commande. Ils servent aux comparaisons a poids
# egal (un preset ne se compare a un autre qu'a poids egal, pas a CRF egal).
SUPPLEMENT = [
    ("crf24-medium", ["-crf", "24", "-preset", "medium"]),
    ("crf25-medium", ["-crf", "25", "-preset", "medium"]),
    ("crf26-medium", ["-crf", "26", "-preset", "medium"]),
]

COMMUN = ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def run(args):
    return subprocess.run(args, cwd=RACINE, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def sonde(chemin):
    """duree, poids, debit video, resolution, fps."""
    r = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(chemin)])
    d = json.loads(r.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    num, den = (v.get("r_frame_rate") or "0/1").split("/")
    return {
        "octets": int(d["format"]["size"]),
        "duree": float(d["format"]["duration"]),
        "debit_total": int(d["format"].get("bit_rate") or 0),
        "debit_video": int(v.get("bit_rate") or 0),
        "debit_audio": int(a.get("bit_rate") or 0) if a else 0,
        "largeur": v["width"], "hauteur": v["height"],
        "fps": (float(num) / float(den)) if float(den) else 0.0,
        "codec": v["codec_name"],
    }


def encode(source, sortie, args_video, journal):
    """Un encodage complet, chronometre. Renvoie le temps mural en secondes."""
    if args_video and args_video[0] == "__2PASS__":
        debit = args_video[1]
        reste = args_video[2:]
        base = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", str(source), "-c:v", "libx264", "-b:v", debit] + reste
        t0 = time.perf_counter()
        r1 = run(base + ["-pass", "1", "-passlogfile", str(journal),
                         "-an", "-f", "null", "/dev/null"])
        r2 = run(base + ["-pass", "2", "-passlogfile", str(journal),
                         "-c:a", "aac", "-b:a", "128k"] + COMMUN + [str(sortie)])
        t = time.perf_counter() - t0
        for r in (r1, r2):
            if r.returncode != 0:
                raise RuntimeError(r.stdout[-2000:])
        for f in Path(journal).parent.glob(Path(journal).name + "*"):
            f.unlink()
        return t

    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
           "-i", str(source), "-c:v", "libx264"] + args_video + \
          ["-c:a", "aac", "-b:a", "128k"] + COMMUN + [str(sortie)]
    t0 = time.perf_counter()
    r = run(cmd)
    t = time.perf_counter() - t0
    if r.returncode != 0:
        raise RuntimeError(r.stdout[-2000:])
    return t


def qualite(encode_, source):
    """SSIM et PSNR du fichier encode par rapport a la source."""
    out = {}
    for filtre, motif in (
        ("ssim", r"SSIM Y:([0-9.]+).*All:([0-9.]+)"),
        ("psnr", r"PSNR y:([0-9.a-z]+).*average:([0-9.a-z]+)"),
    ):
        r = run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info",
                 "-i", str(encode_), "-i", str(source),
                 "-lavfi", "[0:v][1:v]" + filtre, "-f", "null", "-"])
        m = re.search(motif, r.stdout)
        if not m:
            raise RuntimeError("pas de " + filtre + " : " + r.stdout[-800:])
        out[filtre + "_y"] = m.group(1)
        out[filtre + "_all"] = m.group(2)
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    source = Path(sys.argv[1])
    dossier = Path(sys.argv[2])
    passes = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    choix = sys.argv[4:]
    dossier.mkdir(parents=True, exist_ok=True)

    src = sonde(source)
    print("machine : %s coeurs, %s" % (
        subprocess.run(["nproc"], text=True, capture_output=True).stdout.strip(),
        subprocess.run(["uname", "-m"], text=True, capture_output=True).stdout.strip()))
    print("ffmpeg  : %s" % subprocess.run(
        ["ffmpeg", "-version"], text=True,
        capture_output=True).stdout.split("\n")[0].split(" ")[2])
    print("source  : %s" % source.name)
    print("          %dx%d, %.2f fps, %.2f s, %s, %d octets"
          % (src["largeur"], src["hauteur"], src["fps"], src["duree"],
             src["codec"], src["octets"]))
    print("          debit video %d kb/s, audio %d kb/s"
          % (src["debit_video"] / 1000, src["debit_audio"] / 1000))
    print("passes par reglage : %d" % passes)
    print("")
    entete = ("%-14s %8s %8s %15s %9s %9s"
              % ("reglage", "Mo", "kb/s v", "temps (s)", "SSIM Y", "PSNR Y"))
    print(entete)
    print("-" * len(entete))

    lignes = []
    noms_sup = {n for n, _ in SUPPLEMENT}
    for nom, args in REGLAGES + SUPPLEMENT:
        if choix and nom not in choix:
            continue
        if not choix and nom in noms_sup:
            continue
        cible = dossier / (source.stem + "--" + nom + ".mp4")
        temps = []
        for _ in range(passes):
            temps.append(encode(source, cible, args, dossier / ("log-" + nom)))
        q = qualite(cible, source)
        p = sonde(cible)
        lignes.append({"reglage": nom, "args": args, **p, **q,
                       "temps": temps})
        print("%-14s %8.2f %8d %6.2f a %-6.2f %9s %9s"
              % (nom, p["octets"] / 1e6, p["debit_video"] / 1000,
                 min(temps), max(temps), q["ssim_y"], q["psnr_y"]))

    (dossier / "mesures.json").write_text(json.dumps(
        {"source": str(source), "source_info": src, "passes": passes,
         "lignes": lignes}, indent=2), encoding="utf-8")
    print("\ndetail brut : %s" % (dossier / "mesures.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
