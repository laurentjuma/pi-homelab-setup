---
title: Raspberry Pi 5 — Full Rebuild Guide
description: Rebuilding a Pi 5 homelab from a blank SD card — NVMe/LVM, Samba, Proxmox on ARM64, LXC, Navidrome, radio track logging, Tailscale.
---

Everything running on a Raspberry Pi 5 (`raspberrypi`, 192.168.8.191), in the order you'd need to rebuild it from a blank SD card. Each section is standalone — skip any service you don't want.

**Captured:** 2026-08-08 from the live machine. **Updated:** 2026-08-22 — Navidrome 0.63.2 + lyrics plugin (7a).

> **Placeholders.** A few values are specific to my setup and have been replaced so this is safe to publish. Substitute your own:
> `youruser` (the Linux/Samba account, uid 1000) · `YOUR_TAILNET_IP` (Tailscale 100.x address) · `your-tailnet` (tailnet name in the MagicDNS host) · `your-tailscale-account` (the account you log the node in as).
> LAN addressing (`192.168.8.x`) is left as-is — it's private-range and makes the examples concrete. Change it to match your own network.

---

## 0. What's on the box

| Layer | What | Where |
|---|---|---|
| OS | Raspberry Pi OS / Debian 13 trixie, kernel 6.12 rpi-2712 | SD card `mmcblk0`, 119 GB |
| Storage | 931 GB NVMe → LVM → ext4 | `/mnt/nvme` |
| File sharing | Samba share `mbogiservershare` | `/mnt/nvme/files` |
| Hypervisor | PXVIRT (Proxmox VE 9.0 ARM64 port) | web UI `:8006` |
| CT 100 | Music Assistant (Docker in LXC) | 192.168.8.213 |
| CT 101 | AzuraCast (Docker in LXC) — **installed, not running** | 192.168.8.192 |
| Music server | Navidrome 0.63.2 + `nd-lyrics` plugin | `:4533` |
| Radio logging | `icyscan-afrobeats`, `icyscan-afrohouse` (ICY), `flowscan@265` (90s90s API) | logs in `/mnt/nvme/files/icyscan/` |
| Remote access | Tailscale + subnet router for 192.168.8.0/24 | `YOUR_TAILNET_IP` |

Two NICs on the same box: `eth0` → bridged into `vmbr0` = **192.168.8.191** (the one to use), `wlan0` = 192.168.8.193.

---

## 1. Base OS

Flash **Raspberry Pi OS (64-bit, Debian 13 trixie)** with Raspberry Pi Imager. In the Imager's advanced settings set:

- hostname: `raspberrypi`
- username: `youruser` (not `pi` — a lot below assumes uid 1000 = `youruser`)
- enable SSH, public-key only, paste your Mac's `~/.ssh/id_ed25519.pub`
- WiFi country GB

First boot:

```bash
ssh youruser@raspberrypi.local
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

Passwordless sudo (Imager's default user already gets this via `/etc/sudoers.d/010_pi-nopasswd`; recreate if missing):

```bash
echo 'youruser ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/010_youruser-nopasswd
sudo chmod 440 /etc/sudoers.d/010_youruser-nopasswd
```

Handy packages used later:

```bash
sudo apt install -y git curl ethtool smartmontools ffmpeg python3-requests
```

Debian 13 is PEP-668 managed — install Python libs with `apt install python3-<pkg>`, not `pip`.

---

## 2. NVMe drive

### 2a. Enable the PCIe slot at Gen 3

Append to `/boot/firmware/config.txt` under `[all]`:

```
dtparam=pciex1_gen=3
```

### 2b. Boot order (NVMe before USB, SD first)

```bash
sudo rpi-eeprom-config --edit
```

Set:

```
BOOT_UART=1
POWER_OFF_ON_HALT=0
BOOT_ORDER=0xf461
```

Reboot. Confirm the drive appears: `lsblk` should show `nvme0n1`, 931.5 GB.

### 2c. Partition → LVM → ext4

The whole disk is one PV, one VG `master_vg`, one LV `master_lv` filling it.

```bash
sudo parted /dev/nvme0n1 --script mklabel gpt
sudo parted /dev/nvme0n1 --script mkpart primary 0% 100%
sudo parted /dev/nvme0n1 --script set 1 lvm on

sudo apt install -y lvm2
sudo pvcreate /dev/nvme0n1p1
sudo vgcreate master_vg /dev/nvme0n1p1
sudo lvcreate -l 100%FREE -n master_lv master_vg
sudo mkfs.ext4 /dev/master_vg/master_lv
```

### 2d. Mount at `/mnt/nvme`

```bash
sudo mkdir -p /mnt/nvme
echo '/dev/master_vg/master_lv  /mnt/nvme  ext4  defaults  0  2' | sudo tee -a /etc/fstab
sudo mount -a
df -h /mnt/nvme          # expect ~916G
```

### 2e. Directory layout

```bash
sudo mkdir -p /mnt/nvme/files
sudo chown -R youruser:youruser /mnt/nvme/files
mkdir -p /mnt/nvme/files/icyscan
```

`/mnt/nvme/files` holds the music library (`music`, `music_african`, `music_kenyan`, `music_urban`, `music_oldies`, `music_soul_r_n_b`, `music_dancehall_reggae`, `music_eurodance`, `music_nwa`, `_Serato_`, …) plus `icyscan/`. PVE later adds `dump/ images/ private/ snippets/ template/` at `/mnt/nvme` — leave those alone.

> **Restoring the music library** is a data copy, not a config step — pull it back from your backup over SMB once section 3 is up.

---

## 3. Samba share `mbogiservershare`

```bash
sudo apt install -y samba samba-common-bin
```

Append to `/etc/samba/smb.conf`:

```ini
[mbogiservershare]
path = /mnt/nvme/files
writeable = yes
browseable = yes
public = no
```

Set the Samba password for your Unix user, then restart:

```bash
sudo smbpasswd -a youruser
sudo systemctl restart smbd nmbd
sudo systemctl enable smbd nmbd
```

From the Mac: `smb://192.168.8.191/mbogiservershare` (user `youruser`).

> **Gotcha to remember:** anything systemd writes here with `StandardOutput=append:` is created as root even when the unit has `User=`, so it won't be writable over the share. See section 7 — that's why the icyscan units don't use `append:`.

---

## 4. PXVIRT (Proxmox VE 9 for ARM64)

Upstream Proxmox has no ARM64 build; this box runs the **Lierfang PXVIRT** port.

### 4a. Static IP + bridge first

Proxmox needs a static address on a bridge. Replace `/etc/network/interfaces`:

```
# interfaces(5) file used by ifup(8) and ifdown(8)
auto lo
  iface lo inet loopback

auto eth0
  iface eth0 inet manual

auto vmbr0
iface vmbr0 inet manual
        address 192.168.8.191
        gateway 192.168.8.1
        netmask 255.255.255.0
        bridge-ports eth0
        bridge-stp off
        bridge-fd 0
```

Make sure `/etc/hosts` maps `192.168.8.191 raspberrypi.local raspberrypi` before installing, then reboot.

### 4b. Repo + install

```bash
curl -fsSL https://mirrors.lierfang.com/pxcloud/pxvirt/lierfang.gpg \
  | sudo tee /usr/share/keyrings/lierfang.gpg >/dev/null

echo 'deb [arch=arm64 signed-by=/usr/share/keyrings/lierfang.gpg] https://mirrors.lierfang.com/pxcloud/pxvirt trixie main' \
  | sudo tee /etc/apt/sources.list.d/pxvirt.list

sudo apt update
sudo apt install -y proxmox-ve
sudo reboot
```

Web UI: `https://192.168.8.191:8006` (self-signed cert; log in as `root`, realm "Linux PAM" — set a root password with `sudo passwd root` first if you never have).

### 4c. Storage

`/etc/pve/storage.cfg` — the `nvme` dir entry is what CT 101 actually uses:

```
dir: local
	path /var/lib/vz
	content vztmpl,rootdir,images,snippets,backup,iso
	prune-backups keep-all=1

dir: nvme
	path /mnt/nvme
	content rootdir,snippets,backup,images,iso
```

Add via the CLI:

```bash
sudo pvesm add dir nvme --path /mnt/nvme --content rootdir,snippets,backup,images,iso
```

> There's also a stale `lvm: master` entry pointing at `vgname master`, which doesn't exist (the real VG is `master_vg`). It's unused — don't recreate it.

### 4d. Container template

```bash
sudo pveam update
sudo pveam download local ubuntu-22.04-standard_22.04-1_arm64.tar.zst
```

The existing containers were built from `ubuntu-jammy-20231124_arm64.tar.xz` in `/var/lib/vz/template/cache/`. Any Ubuntu 22.04 arm64 template works.

---

## 5. CT 100 — Music Assistant

Unprivileged LXC, 2 cores / 2 GB, static 192.168.8.213, with the music library bind-mounted read-only.

```bash
sudo pct create 100 local:vztmpl/ubuntu-jammy-20231124_arm64.tar.xz \
  --hostname music-assistant \
  --arch arm64 --ostype ubuntu \
  --cores 2 --memory 2048 --swap 512 \
  --rootfs local:10 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.213/24,gw=192.168.8.1,type=veth \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1

# music library, read-only, from the host
sudo pct set 100 -mp0 /mnt/nvme/files,mp=/media/music,ro=1

sudo pct start 100
```

`nesting=1` is required for Docker inside an unprivileged container.

Inside the container:

```bash
sudo pct enter 100

apt update && apt install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh

mkdir -p /data/music-assistant

docker run -d \
  --name music-assistant \
  --network host \
  --restart unless-stopped \
  -v /data/music-assistant:/data \
  -v /media/music:/media/music:ro \
  ghcr.io/music-assistant/server:latest

exit
```

Host networking, so the web UI is `http://192.168.8.213:8095`. Add `/media/music` as a local filesystem provider in the UI.

---

## 6. CT 101 — AzuraCast

> **Current state: installed but no containers running.** `docker ps -a` inside CT 101 is empty — the AzuraCast stack was never brought up (or was torn down). The files under `/var/azuracast` are there. Rebuild it only if you actually want it; otherwise skip this section.

```bash
sudo pct create 101 local:vztmpl/ubuntu-jammy-20231124_arm64.tar.xz \
  --hostname azuracast \
  --arch arm64 --ostype ubuntu \
  --cores 2 --memory 4096 --swap 1024 \
  --rootfs nvme:50 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.192/24,gw=192.168.8.1,type=veth,firewall=1,mtu=1420 \
  --nameserver "1.1.1.1 8.8.8.8" \
  --features nesting=1,keyctl=1 \
  --unprivileged 1 \
  --onboot 1

sudo pct start 101
```

`keyctl=1` is needed for AzuraCast's MariaDB. `mtu=1420` matches the Tailscale-friendly MTU. Rootfs lives on the NVMe (`nvme:50`), not the SD card.

Inside:

```bash
sudo pct enter 101

apt update && apt install -y curl ca-certificates
curl -fsSL https://get.docker.com | sh

mkdir -p /var/azuracast && cd /var/azuracast
curl -fsSL https://raw.githubusercontent.com/AzuraCast/AzuraCast/main/docker.sh > docker.sh
chmod +x docker.sh
./docker.sh install
```

`/var/azuracast/.env` as configured:

```
COMPOSE_PROJECT_NAME=azuracast
AZURACAST_HTTP_PORT=80
AZURACAST_HTTPS_PORT=443
AZURACAST_SFTP_PORT=2022
AZURACAST_PUID=1000
AZURACAST_PGID=1000
NGINX_TIMEOUT=1800
AZURACAST_VERSION=latest
```

Then `http://192.168.8.192` to finish setup.

---

## 7. Navidrome

Installed from the official `.deb` (not from a repo — `apt-cache policy` shows it as local-only), running on the **host**, not in a container.

```bash
VER=0.63.2
curl -fsSL -o /tmp/navidrome.deb \
  "https://github.com/navidrome/navidrome/releases/download/v${VER}/navidrome_${VER}_linux_arm64.deb"
sudo dpkg -i /tmp/navidrome.deb
```

That drops `/usr/bin/navidrome`, a `navidrome` system user, and `navidrome.service`.

Config at `/etc/navidrome/navidrome.toml`:

```toml
DataFolder = "/var/lib/navidrome"
MusicFolder = "/mnt/nvme"
LyricsPriority = ".ttml,.yaml,.yml,.elrc,.lrc,.srt,.txt,embedded,nd-lyrics"

[LastFM]
ApiKey = "<your last.fm api key>"
Secret = "<your last.fm secret>"
```

> Get the Last.fm key/secret from https://www.last.fm/api/account/create — the live values are in the file on the Pi; don't paste them into anything shared.

```bash
sudo systemctl enable --now navidrome
```

Web UI: `http://192.168.8.191:4533` — or `http://YOUR_TAILNET_IP:4533` remotely.

Upgrading in place from an older version is the same `dpkg -i` over the top, but back the DB up first — schema migrations are one-way:

```bash
D=/mnt/nvme/backups/navidrome-$(date +%F)
sudo mkdir -p "$D"                                 # .backup won't create it
sudo sqlite3 /var/lib/navidrome/navidrome.db ".backup $D/navidrome.db"
sudo sqlite3 "$D/navidrome.db" "PRAGMA integrity_check;"   # expect: ok
sudo cp /etc/navidrome/navidrome.toml "$D/"
```

Two notes on the current state:
- `MusicFolder` is `/mnt/nvme`, i.e. the whole drive, not just `/mnt/nvme/files`. That's how it is now; narrow it to `/mnt/nvme/files` if you'd rather Navidrome not scan PVE's directories.
- `/opt/navidrome/music` exists, owned by `navidrome` — vestigial from an earlier install-script attempt. Harmless, and not needed on a rebuild.

### 7a. Lyrics plugin (`nd-lyrics`)

Navidrome has a plugin system as of v0.63 (`Plugins.Enabled`, on by default). Plugins are single `.ndp` bundles read from `<DataFolder>/plugins`. [navidrome-lyrics-plugin](https://github.com/J0R6IT0/navidrome-lyrics-plugin) fetches lyrics from online providers on demand.

Requires Navidrome **≥ v0.63.0**.

```bash
sudo mkdir -p /var/lib/navidrome/plugins
sudo curl -fsSL -o /var/lib/navidrome/plugins/nd-lyrics.ndp \
  "https://github.com/J0R6IT0/navidrome-lyrics-plugin/releases/download/v7.2.0/nd-lyrics.ndp"
sudo chown -R navidrome:navidrome /var/lib/navidrome/plugins
sudo systemctl restart navidrome
```

The plugin also has to be named in `LyricsPriority` (already in the toml above). The value is the filename without its extension, and it goes **last** so embedded tags and sidecar files on disk always win:

```toml
LyricsPriority = ".ttml,.yaml,.yml,.elrc,.lrc,.srt,.txt,embedded,nd-lyrics"
```

Confirm it was picked up:

```bash
sudo journalctl -u navidrome | grep -i "new plugin"
# level=info msg="Discovered new plugin" plugin=nd-lyrics
```

**It registers disabled.** Finish in the web UI → Settings → Plugins → `nd-lyrics`: enable it, then grant it libraries and users. Without that scoping it sits there and never fires.

How it behaves:

- **On demand only.** Nothing is fetched in bulk — a lookup happens when a client asks for one track's lyrics. Results are cached with per-format TTLs (plain 3 days, LRC 7, TTML/ELRC 14) plus a 24 h *negative* cache, so a miss isn't re-queried on every play.
- **The web UI does not render plugin lyrics.** You need a third-party client — Symfonium, Amperfy, Feishin, Substreamer. The alternative is `writeLyrics`, which saves sidecar `.lrc`/`.ttml` files that Navidrome then reads natively; that also needs "allow write access" granted to the plugin in the UI.
- **No per-song picker.** Library and user scoping in the UI is the closest thing — enable it for `Albums` and `Now Music`, leave the radio-scrape libraries out.

Worth changing from the defaults:

| Option | Default | Why |
|---|---|---|
| `providersList` | `lrclib`, `lyrics.ovh` | Only two. Adding `lrcmux`, `kugou`, `netease`, `qqmusic` widens coverage a lot where LRCLIB is thin. |
| `providerMode` | `priority` | First hit wins. `bestSyncLevel` keeps looking for word-by-word over line-synced over plain, at the cost of more requests per lookup. |
| `durationToleranceSeconds` | `3` | The accuracy guard — a result is rejected unless the provider's track length is within this many seconds of yours. Raising it finds more matches, and more wrong ones. |
| `writeLyrics` | `false` | Turn on to persist lyrics as sidecar files under the music tree. |
| `stripSectionLabels` | `false` | Drops `[Chorus]`-style labels and credits from the text. |

---

## 8. ICY radio metadata loggers

Two systemd services scrape ICY stream metadata and append every track change to a `.txt` on the share.

### 8a. The script

The repo is your fork, which carries a fix (`Fix permanent desync when metadata straddles a chunk boundary`) not in upstream:

```bash
sudo mkdir -p /opt/icy-meta
sudo chown youruser:youruser /opt/icy-meta
git clone git@github.com:laurentjuma/icy-meta.git /opt/icy-meta
cd /opt/icy-meta
git remote add upstream https://github.com/lucvanbraekel/icy-meta.git
```

Needs `python3-requests` (installed in section 1).

### 8b. Units

`/etc/systemd/system/icyscan-afrobeats.service`:

```ini
[Unit]
Description=ICY metadata scanner - Exclusive Radio Afrobeats
Documentation=https://github.com/lucvanbraekel/icy-meta
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/mnt/nvme/files
# Never stop retrying, no matter how many times it has failed.
StartLimitIntervalSec=0

[Service]
Type=simple
User=youruser
Group=youruser
WorkingDirectory=/mnt/nvme/files/icyscan

# Track lines (which start with a timestamp) are appended to afrobeats.txt by
# awk itself, which closes the file after every line. The reopen re-resolves
# the path, so if something over the SMB share deletes or replaces the file,
# the next track recreates it instead of writing into an orphaned inode.
# Everything else the script prints (Warning:/Error: on stream hiccups) goes
# to stdout and lands in the journal instead of polluting the log.
ExecStart=/bin/sh -c 'exec /usr/bin/python3 -u /opt/icy-meta/icy_meta.py "https://streaming.exclusive.radio/er-app/afrobeats/icecast.audio" --continuous \
  | awk -v LOG=/mnt/nvme/files/icyscan/afrobeats.txt "/^[0-9][0-9][0-9][0-9]-/ { print >> LOG; close(LOG); next } { print; fflush() }"'

StandardOutput=journal
StandardError=journal
SyslogIdentifier=icyscan-afrobeats

Restart=always
RestartSec=10

# Hardening: it only needs to read the script and append to its log.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/mnt/nvme/files/icyscan
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/icyscan-afrohouse.service` is identical except:

- `Description=ICY metadata scanner - Techno Lovers Afro House`
- `SyslogIdentifier=icyscan-afrohouse`
- log → `/mnt/nvme/files/icyscan/afrohouse.txt`
- stream URL:
  ```
  https://0nlineradio.stream13.radiohost.de/technolovers-afro-house?ref=liveonlineradionet&upd-meta&upd-scheme=https&_art=dD0xNzgxNTMyMDM0JmQ9OGYxZTY3MjFkMTYwZTY4NzY1MWM
  ```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now icyscan-afrobeats icyscan-afrohouse
```

> **90s90s is not on ICY.** An `icyscan-90shiphop.service` briefly existed and was removed on 2026-08-08. The 90s90s streams do send `icy-metaint: 8192`, so the scanner connects and looks healthy, but `StreamTitle` is the static station name (`90s90s - HipHop`) rather than per-track data — same on the `mp3-192`, `mp3-128` and `aac-64` variants. **A valid `icy-metaint` header is not evidence a station publishes track metadata; check that `StreamTitle` actually changes before adding any station.** 90s90s is handled by section 8b instead.

**Why awk owns the file and not systemd:** `StandardOutput=append:` resolves the path once at start and binds to the *inode*. Finder replacing the file over SMB (unlink-then-create) left the service writing into an orphaned inode while the visible file stayed at 0 bytes — silently, nothing in the journal. `close(LOG)` after each line forces a path re-resolve, so a deleted log comes back within one song. **Any new station unit must use this pattern.**

Two known, expected behaviours: the script reconnects to the stream roughly once per second (no poll-interval flag upstream — patch locally if a station ever rate-limits you), and dedup state is in-memory so a restart re-logs the currently playing track.

Logs land at `smb://192.168.8.191/mbogiservershare/icyscan/`.

### 8c. 90s90s — `flowscan`, polling the iris feed

90s90s doesn't publish tracks over ICY (see the note above), but its web player does. The Nuxt bundles call an "iris" endpoint:

```
https://iris-90s90s.loverad.io/flow.json?station=<id>&offset=1&count=1&ts=<epoch_ms>
```

Track data is at `result.entry[0]` → `airtime`, `duration`, and `song.entry[0]` → `title`, `artist.entry[0].name`, plus cover art and an iTunes link.

**`offset` and `count` are accepted but ignored** — every call returns only the currently playing track, so there's no history to backfill and the poll interval has to be shorter than the shortest track. 60s is safe and far gentler than icy-meta's ~1/sec reconnect.

Station IDs come from the `station_id` fields in the Nuxt state payload on `https://www.90s90s.de/`. Some useful ones:

| Channel | ID | | Channel | ID |
|---|---|---|---|---|
| HIPHOP & RAP | 265 | | TECHNO | 261 |
| HIPHOP DEUTSCH | 439 | | HOUSE | 655 |
| EURODANCE | 188 | | RAVE | 705 |
| GRUNGE | 253 | | TRANCE | 704 |
| CLUBHITS | 140 | | REGGAE | 778 |

Install the script (from this repo's copy, or rewrite from the spec above):

```bash
sudo install -d -m 0755 /opt/flowscan /etc/flowscan
sudo install -m 0755 flowscan.py /opt/flowscan/flowscan.py
```

Per-station config, `/etc/flowscan/265.env`:

```ini
# 90s90s "HIPHOP & RAP" (iris station 265)
LOG=/mnt/nvme/files/icyscan/90shiphop.txt
POLL=60
```

Template unit `/etc/systemd/system/flowscan@.service` — the instance name **is** the station ID, so adding a channel is one env file plus one `enable`:

```ini
[Unit]
Description=90s90s track logger - iris station %i
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/mnt/nvme/files
StartLimitIntervalSec=0

[Service]
Type=simple
User=youruser
Group=youruser
WorkingDirectory=/mnt/nvme/files/icyscan
EnvironmentFile=/etc/flowscan/%i.env
ExecStart=/usr/bin/python3 -u /opt/flowscan/flowscan.py --station %i --log ${LOG} --interval ${POLL}
StandardOutput=journal
StandardError=journal
SyslogIdentifier=flowscan-%i
Restart=always
RestartSec=30

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/mnt/nvme/files/icyscan
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flowscan@265
```

Behaviour notes:

- Output format matches the icyscan logs exactly (`YYYY-MM-DD HH:MM:SS: Artist - Title`), so all three logs read the same.
- Timestamps use the feed's `airtime` (broadcast time, Berlin `+02:00`) converted to the Pi's local zone — more accurate than detection time, and it keeps lines sortable against the icyscan logs.
- The log is reopened and closed per track, same reason as the awk trick above: an SMB-side delete gets recreated on the next write rather than orphaning the inode.
- Dedup is seeded from the last line of the existing log on startup, so unlike the icyscan units a restart does **not** re-log the current track.
- Network and parse failures warn to the journal and the loop continues; the unit never gives up (`Restart=always`, `StartLimitIntervalSec=0`).

To add another channel:

```bash
printf 'LOG=/mnt/nvme/files/icyscan/90stechno.txt\nPOLL=60\n' | sudo tee /etc/flowscan/261.env
sudo systemctl enable --now flowscan@261
```

---

## 9. Tailscale remote access

The LAN sits behind a GL.iNet router whose IPv4 egress is an M247 commercial-VPN range, on top of likely CGNAT — **there is no inbound path**, so port forwarding and DDNS are impossible. An outbound tunnel is the only option.

### 9a. Install and join

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

Bring it up as a **subnet router** advertising the whole LAN:

```bash
sudo tailscale up --advertise-routes=192.168.8.0/24
```

Follow the printed URL and log in as `your-tailscale-account`. Then in the Tailscale admin console → Machines → `raspberrypi` → **approve the 192.168.8.0/24 subnet route**. It won't work until you do.

Result: tailnet IP `YOUR_TAILNET_IP`, MagicDNS `raspberrypi.your-tailnet.ts.net`.

### 9b. Persist IP forwarding

```bash
printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' \
  | sudo tee /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
```

### 9c. UDP GRO tuning on the bridge

Throughput fix for subnet routing — needs to reapply on every boot, hence the oneshot unit.

`/etc/systemd/system/tailscale-gro.service`:

```ini
[Unit]
Description=Enable UDP GRO forwarding on vmbr0 for Tailscale subnet routing
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/ethtool -K vmbr0 rx-udp-gro-forwarding on rx-gro-list off

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tailscale-gro
```

### 9d. Using it

Reach services at the tailnet IP instead of the LAN IP — plain HTTP is fine, WireGuard already encrypts it:

- Navidrome — `http://YOUR_TAILNET_IP:4533`
- Proxmox — `https://YOUR_TAILNET_IP:8006`
- SSH — `ssh youruser@YOUR_TAILNET_IP`
- Music Assistant — `http://192.168.8.213:8095` (via the subnet route)

`tailscale netcheck` reports `MappingVariesByDestIP: true` (symmetric NAT), so connections often relay through the London DERP rather than going peer-to-peer. Fine for audio; expect less headroom for 4K video.

---

## 10. Verification checklist

```bash
# storage
df -h /mnt/nvme                                    # ~916G, ext4

# services
systemctl is-active navidrome icyscan-afrobeats icyscan-afrohouse flowscan@265 tailscaled smbd

# navidrome + plugin
/usr/bin/navidrome --version                       # 0.63.2
ls /var/lib/navidrome/plugins                      # nd-lyrics.ndp

# containers
sudo pct list                                      # 100 + 101 running

# listening ports
sudo ss -tlnp | grep -E ':(22|80|445|4533|8006)'

# radio logs growing
tail -3 /mnt/nvme/files/icyscan/afrobeats.txt
tail -3 /mnt/nvme/files/icyscan/afrohouse.txt

# tailscale
tailscale status
tailscale ip -4                                    # YOUR_TAILNET_IP
```

Then from the Mac: `smb://192.168.8.191/mbogiservershare`, `http://192.168.8.191:4533`, `https://192.168.8.191:8006`.

---

## 11. Things worth knowing before you rebuild

- **Back up first.** The music library under `/mnt/nvme/files` and the icyscan `.txt` history are the only irreplaceable data. Also grab `/etc/navidrome/navidrome.toml` (Last.fm keys), `/etc/pve/lxc/*.conf`, `/var/azuracast/.env`, and `/var/lib/navidrome/` (playlists, play counts, users).
- **Don't restart Navidrome mid-migration.** A version jump applies schema migrations on first start, and the FTS5 search index alone takes ~13 s on this library. Restarting during that aborts the running transaction (`level=fatal ... failed to begin transaction: context canceled`); the next start does resume at the interrupted migration and finish the rest, but wait for `Navidrome server is ready!` before touching the service.
- **No PVE backup jobs are configured.** `/etc/pve/jobs.cfg` is empty and both `dump/` directories are empty — nothing is being backed up automatically. Worth adding a vzdump job to `nvme` storage if you care about the containers.
- **`zfsutils-linux` is installed** (pulled in by PXVIRT) but no pool exists and the module isn't loaded. Ignore it.
- **nginx is running on :80 with the stock Debian default page** — nothing is proxied through it. It's an artifact of some earlier plan, not load-bearing. Safe to leave, safe to remove.
- **`samba-ad-dc.service` is enabled** but the server is a standalone file server. Harmless.
- **`postfix` is running** on localhost only, for PVE's mail notifications.
- **Order matters** in one place: PXVIRT (section 4) must come after the `vmbr0` bridge exists, and the containers (5–6) after PXVIRT. Everything else is independent.
