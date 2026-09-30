<p align="center"> <img src="logo_big.png" alt="Myostoox logo" width="300"> </p> <p align="center"><br><br> <em>A complete audio player built with GTK3 and GStreamer... but a bit of a video player too.</em> </p><br> <p align="center"> 🎵 Music &nbsp;•&nbsp; 🎬 Video &nbsp;•&nbsp; 📻 Radio &nbsp;•&nbsp; 📺 M3U / IPTV &nbsp;•&nbsp; 💿 CD / DVD / Blu-ray </p><br><br> <p align="center"> <strong><a href="https://github.com/MilOvni/Myostoox/raw/refs/heads/main/myostoox_1.0-1_all.deb">📦 Download Myostoox 1.0 (.deb)</a></strong> </p><br> 

# Myostoox

<br>Myostoox is an **audio and video player** designed primarily for *Lubuntu 26.04 LTS*, while also working on other Linux desktop environments using **GTK**, including **LXQt** and **XFCE**.

Written in Python 3, GTK3 and GStreamer, Myostoox brings music, video, Internet radio, TV/M3U playlists and optical media together in a single application.<br><br><br><br>


<p align="center">
  <img src="screenshots/Player_05.jpg" alt="Playlist + EQ + Pitch/tempo" width="700">
</p>

<br><br>
## **Feature**s<br><br>
### **Audio**

    • Local music playback

    • Internet radio

    • M3U / M3U8 / PLS playlists

    • Audio CD playback

    • Equalizer

    • Pitch and tempo control

    • Spectrum analyzer

    • LRC lyrics

    • Metadata and track information

    • MPRIS2 support

    • Desktop notifications

### **Video**

    • Local video playback

    • Subtitles

    • Full-screen playback

    • Video playlists

    • TV / IPTV M3U playlists

    • Automatic screen-blanking inhibition during playback

    • Custom handling for some MKV / E-AC3 playback cases

### **Radio, TV & playlists**

    • Internet radio stations

    • TV / IPTV bouquets

    • M3U / M3U8 / PLS import and export

    • Playlist persistence

    • Missing files are clearly identified

    • Files opened from the file manager can be sent to an already running Myostoox instance

### **Optical media**

    • Audio CDs

    • DVDs

    • Blu-rays (experimental)

### **Interface**

    • Mini-player

    • Themes

    • Full-screen mode

    • Spectrum analyzer

    • MPRIS2 integration

    • GTK3 interface designed for lightweight Linux desktops

<br>
<br>



## Screenshots

<p align="center">
  <img src="screenshots/Player_03.jpg" alt="TV" width="700">
</p>

<p align="center">
  <img src="screenshots/Player_10.jpg" alt="Full screen" width="700">
</p>

<p align="center">
  <img src="screenshots/Player_07.jpg" alt="Themes" width="700">
</p>

<p align="center">
  <img src="screenshots/Player_08.jpg" alt="Mini-player" width="700">
</p>

<p align="center">
  <img src="screenshots/Player_04.jpg" alt="Bouquets" width="700">
</p>

<p align="center">
  <img src="screenshots/Player_02.jpg" alt="Radios" width="700">
</p>

<p align="center">
  <img src="screenshots/Player_09.jpg" alt="Lyrics" width="700">
</p>


---

<br>

## 📦 Installation

### Debian / Ubuntu / Lubuntu

Download the `.deb` package:

<p align="center">
  <strong><a href="https://github.com/MilOvni/Myostoox/raw/refs/heads/main/myostoox_1.0-1_all.deb">📦 Download Myostoox 1.0 (.deb)</a></strong>
</p>

Then install it with:

```bash
sudo apt install ./myostoox_1.0-1_all.deb
```

### From source

Clone the repository:

```bash
git clone https://github.com/MilOvni/Myostoox.git
cd Myostoox
```

Then launch:

```bash
python3 Myostoox.py
```

Or:

```bash
chmod +x Myostoox.py
./Myostoox.py
```

## Dependencies

### Required

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

### Strongly recommended (Lubuntu / XFCE)

```bash
# Audio CD
sudo apt install libcdio-dev libcdio-paranoia2

# Encrypted CSS DVDs
sudo dpkg-reconfigure libdvd-pkg

# Intel GPUs
sudo apt install -y mesa-va-drivers vainfo

# NVIDIA GPUs
sudo ubuntu-drivers autoinstall
```

### Optional

```bash
# DVD / Blu-ray (depending on local legality and firmwares)
libaacs0 + KEYDB.cfg (commercial Blu-rays)
```


## Launching

```bash
python3 Myostoox.py
# or
chmod +x Myostoox.py
./Myostoox.py
```

Files passed as arguments (Thunar, double-click) are sent to the already running instance if one exists.

Configuration: `~/.config/Myostoox/` (playlist, state, radios, themes…).<br>


<p align="center">
  <img src="screenshots/Player_06.jpg" alt="Videos" width="700">
</p>
<br>

---

## Menu — useful new features

| Entry | Purpose |
|-------|---------|
| **Import / Export playlist…** | `.m3u` / `.m3u8` / `.pls` files |
| **Mini-player** | Transport bar only, always on top & optional scrolling title |
| **Lyrics** | Displays `.lrc` / `.txt` / tags (audio only) |

- **Missing file**: absent tracks (unplugged USB) appear in *italics* with ⚠.
- **Screen blanking**: automatically inhibited during **video playback** (via `org.freedesktop.ScreenSaver`).
- **Notifications**: title / artist on every track change (if `libnotify` is installed).

---

## Quick troubleshooting

| Symptom | Hint |
|---------|------|
| No sound / unknown format | Install `gstreamer1.0-plugins-ugly` and `gstreamer1.0-libav` |
| Silent or crashing MKV E-AC3 | The internal custom MKV pipeline handles this case; check `gstreamer1.0-libav` |
| No notifications | `sudo apt install gir1.2-notify-0.7` |
| CD not detected | `libcdio` + ugly plugins; make sure it is a real audio CD (not data) |
| Screen turns off during a movie | Check that the session exposes the ScreenSaver D-Bus interface (XFCE does) |

---

## License / usage

This project is free software distributed under the **GNU General Public License version 3** (GPL-3.0).

See the [`LICENSE`](LICENSE) file for the full text.

Use in accordance with local laws (DVD/Blu-ray, stream sources).

---

## Screen blanking / sleep under LXQt

Myostoox tries to inhibit screen blanking during **video playback** via D-Bus  
(`org.freedesktop.ScreenSaver`, then `org.freedesktop.PowerManagement.Inhibit`).

Under **LXQt** (Lubuntu), these services are often missing: this is not a playback bug.  
To prevent the screen from turning off during a movie:

1. **Settings → Power Management (LXQt)** (`lxqt-config-powermanagement`)
2. Extend or disable screen blanking / sleep
3. Optional: install a D-Bus-compatible screensaver provider

`ServiceUnknown` messages related to ScreenSaver / Notifications are silently ignored.

---

## Inspirations

 **AIMP**<br>
 **Winamp**<br>
 **Audacious**<br>
 **DeaDBeeF**<br>
 **VLC**<br>
 **mpv**<br>
 **SMPlayer**<br>
 ...<br>

---

## Author

**O. FRAISSE (MilOvni)**

GitHub — MilOvni/Myostoox<br><br>


<p align="center">
  <img src="screenshots/Player_11.jpg" alt="About" width="700">
</p>
<br>

```
