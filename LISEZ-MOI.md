# Myostoox

Lecteur audio/vidéo pour **Lubuntu 26.04 LTS (Resolute Raccoon)** (et autres bureaux Linux GTK - LXQt / XFCE), écrit en Python 3 + GTK3 + GStreamer.

Fonctionnalités principales : playlist, radios, TV / bouquets M3U, CD / DVD / Blu-ray (expérimental), égaliseur, pitch/tempo, spectre, sous-titres, paroles (LRC), thèmes, MPRIS2, mini-lecteur.

---

## Dépendances

### Obligatoires

```bash
sudo apt update && sudo apt install -y \
  python3 python3-gi python3-gi-cairo python3-cairo \
  gir1.2-gtk-3.0 gir1.2-gst-plugins-base-1.0 gir1.2-gstreamer-1.0 \
  gir1.2-gdkpixbuf-2.0 gir1.2-pango-1.0 gir1.2-notify-0.7 \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  gstreamer1.0-gl \
  gstreamer1.0-x \
  gstreamer1.0-alsa \
  gstreamer1.0-pulseaudio \
  gstreamer1.0-gtk3 \
  gstreamer1.0-vaapi \
  python3-mutagen \
  libnotify4 \
  libdvd-pkg libdvdread8t64 libdvdnav4 libbluray2 \
  ubuntu-restricted-extras
```

### Fortement recommandées (Lubuntu / XFCE)

```bash
# CD audio
sudo apt install libcdio-dev libcdio-paranoia2

# DVD chiffrés CSS)
Bashsudo dpkg-reconfigure libdvd-pkg

# Pour les GPU intel
sudo apt install -y mesa-va-drivers vainfo

# Pour le bon décodage Nvidia
sudo ubuntu-drivers autoinstall
```

### Optionnelles

```bash
# DVD / Blu-ray (selon la légalité locale et les firmwares)
libaacs0 + KEYDB.cfg (Blu-ray commerciaux)
```

---

## Lancement

```bash
python3 Myostoox.py
# ou
chmod +x Myostoox.py
./Myostoox.py
```

Fichiers transmis en argument (Thunar, double-clic) sont envoyés à l’instance déjà ouverte s’il y en a une.

Configuration : `~/.config/Myostoox/` (playlist, état, radios, thèmes…).

---

## Menu — nouveautés utiles

| Entrée | Rôle |
|--------|------|
| **Importer / Exporter une playlist…** | Fichiers `.m3u` / `.m3u8` / `.pls` |
| **Mini-lecteur** | Barre de transport seule, toujours au-dessus & titre défilant possible |
| **Paroles** | Affiche `.lrc` / `.txt` / tags (audio uniquement) |

- **Fichier manquant** : les pistes absentes (USB débranché) apparaissent en *italique* avec ⚠.
- **Veille** : inhibée automatiquement pendant la **lecture vidéo** (via `org.freedesktop.ScreenSaver`).
- **Notifications** : titre / artiste à chaque changement de piste (si `libnotify` est installé).

---

## Dépannage rapide

| Symptôme | Piste |
|----------|--------|
| Pas de son / format inconnu | Installer `gstreamer1.0-plugins-ugly` et `gstreamer1.0-libav` |
| MKV E-AC3 silencieux ou crash | Le pipeline MKV custom interne gère ce cas ; vérifier `gstreamer1.0-libav` |
| Pas de notifications | `sudo apt install gir1.2-notify-0.7` |
| CD non détecté | `libcdio` + plugins ugly ; CD réellement audio (pas data) |
| Écran qui s’éteint en film | Vérifier que la session expose ScreenSaver D-Bus (XFCE le fait) |

---

## Licence / usage

Ce projet est un logiciel libre distribué sous la **GNU General Public License version 3** (GPL-3.0).

Voir le fichier [`LICENSE`](LICENSE) pour le texte complet. 

Utiliser conformément aux lois locales (DVD/Blu-ray, sources des flux).
'''


---

## Veille / écran sous LXQt

Myostoox tente d’inhiber la veille pendant la **lecture vidéo** via D-Bus
(`org.freedesktop.ScreenSaver`, puis `org.freedesktop.PowerManagement.Inhibit`).

Sous **LXQt** (Lubuntu), ces services sont souvent absents : ce n’est pas un bug
de lecture. Pour éviter l’extinction de l’écran pendant un film :

1. **Configuration → Gestion de l’énergie (LXQt)** (`lxqt-config-powermanagement`)
2. Allonger ou désactiver la mise en veille / l’extinction de l’écran
3. Optionnel : installer un fournisseur d’écran de veille compatible D-Bus

Les messages `ServiceUnknown` liés à ScreenSaver / Notifications sont ignorés
silencieusement.

---

## Auteur

O. FRAISSE ( MilOvni )
