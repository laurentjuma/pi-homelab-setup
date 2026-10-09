---
title: Raspberry Pi 5 — Full Rebuild Guide
description: Rebuilding a Pi 5 homelab from a blank SD card — NVMe/LVM, Samba and WebDAV, the Mediagg Arr Stack in Docker (Jellyfin, Navidrome, Audiobookshelf, the *arrs), radio track logging, Tailscale.
---

Everything running on a Raspberry Pi 5 (`raspberrypi`, 192.168.8.191), in the order you'd need to rebuild it from a blank SD card. Each section is standalone — skip any service you don't want.

**Captured:** 2026-08-08 from the live machine. **Updated:** 2026-10-09 — Proxmox (PXVIRT) removed; the Mediagg Arr Stack now runs in Docker on the host (section 4), and every LXC container — Music Assistant, AzuraCast, goPodder, m3ugoat, Kodi — is gone. How they were built is kept in the [Proxmox archive](proxmox-archive.html). Sections renumbered.

> **Placeholders.** A few values are specific to my setup and have been replaced so this is safe to publish. Substitute your own:
> `youruser` (the Linux/Samba account, uid 1000) · `YOUR_TAILNET_IP` (Tailscale 100.x address) · `your-tailnet` (tailnet name in the MagicDNS host) · `your-tailscale-account` (the account you log the node in as).
> LAN addressing (`192.168.8.x`) is left as-is — it's private-range and makes the examples concrete. Change it to match your own network.

---

## 0. What's on the box

| Layer | What | Where |
|---|---|---|
| OS | Raspberry Pi OS / Debian 13 trixie, kernel 6.12 rpi-2712 | SD card `mmcblk0`, 119 GB |
| Storage | 931 GB NVMe → LVM → ext4 | `/mnt/nvme` |
| File sharing | Samba share `mbogiservershare`; the same tree read-only over WebDAV | `/mnt/nvme/files`, WebDAV `:8081` |
| Media | Mediagg Arr Stack, Docker on the host — Jellyfin, Navidrome, Audiobookshelf, Sonarr, Radarr, Lidarr, Bazarr, Prowlarr, qBittorrent, Seerr, Hearr | 192.168.8.191, manager `:7979` |
| Music server | Navidrome in the Mediagg stack (`:4533`); the host's own Navidrome 0.63.2 + `nd-lyrics` is **disabled** but still installed | — |
| Radio logging | `icyscan-afrobeats`, `icyscan-afrohouse` (ICY), `flowscan@265` (90s90s API) | logs in `/mnt/nvme/files/icyscan/` |
| Dashboard | `hqdash` behind nginx | `http://192.168.8.191` |
| Remote access | Tailscale + subnet router for 192.168.8.0/24 | `YOUR_TAILNET_IP` |

Two NICs on the same box: `eth0` = **192.168.8.191** (static, NetworkManager connection `eth0-static` — the one to use), `wlan0` = 192.168.8.193.

---|---|---|
| OS | Raspberry Pi OS / Debian 13 trixie, kernel 6.12 rpi-2712 | SD card `mmcblk0`, 119 GB |
| Storage | 931 GB NVMe → LVM → ext4 | `/mnt/nvme` |
| File sharing | Samba share `mbogiservershare`; the same tree read-only over WebDAV | `/mnt/nvme/files`, WebDAV `:8081` |
| Hypervisor | PXVIRT (Proxmox VE 9.0 ARM64 port) | web UI `:8006` |
| CT 100 | Music Assistant (Docker in LXC) | 192.168.8.213 |
| CT 101 | AzuraCast (Docker in LXC) — **installed, not running** | 192.168.8.192 |
| CT 105 | goPodder (Docker in LXC) — podcast sync, gpodder.net API | 192.168.8.217, web UI `:8080` |
| CT 106 | m3ugoat (Node + systemd in LXC, no Docker) — IPTV playlist/EPG manager | 192.168.8.218, web UI `:8080` |
| CT 109 | Kodi (headless, Xvfb + systemd in LXC, no Docker) — music, movies and TV over JSON-RPC | 192.168.8.221, web server `:8080` |
| CT 110 | Mediagg Arr Stack (Docker in LXC) — Jellyfin, Navidrome, Audiobookshelf, Sonarr, Radarr, Lidarr, Prowlarr, qBittorrent, Seerr, Hearr | 192.168.8.187 (DHCP), manager `:7979` |
| Music server | Navidrome in CT 110 (`192.168.8.187:4533`); the host's Navidrome 0.63.2 + `nd-lyrics` is **disabled** but still installed | — |
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

`/mnt/nvme/files` holds the music library (`music`, `music_african`, `music_kenyan`, `music_urban`, `music_oldies`, `music_soul_r_n_b`, `music_dancehall_reggae`, `music_eurodance`, `music_nwa`, `_Serato_`, …) plus `icyscan/`, and — for the Mediagg stack (section 4) — `audiobooks/`, `podcasts/`, `movies/`, `tv/` and `downloads/`, plus `muzak/`, Mediagg's own music folder. `/mnt/nvme/backups` holds one-off backups (Navidrome databases, tag dumps).

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
veto files = /._*/.DS_Store/
delete veto files = yes
```

And at the top of `[global]`:

```ini
vfs objects = catia fruit streams_xattr
fruit:metadata = stream
fruit:resource = xattr
```

**Why:** without `vfs_fruit`, every Mac that copies a file in leaves a `._<name>` AppleDouble twin beside it, plus a `.DS_Store` in every folder it opens. By 2026-09-29 the share had 16,302 of them, nearly all in the music folders, where they turn up as fake `._track.mp3` entries in anything that doesn't skip dotfiles. `fruit` + `streams_xattr` store the same Finder metadata in ext4 xattrs instead, and `veto files` refuses the files outright, even from a Mac that tries anyway. `delete veto files` stops those files from making a folder undeletable. `fruit` goes in `[global]`, not the share: `testparm` warns that a Mac mounting a mix of fruit and non-fruit shares (the stock `[homes]` counts) is undefined behaviour.

Clearing out an existing crop, with a tar kept in case: check the `._*` files really are AppleDouble first (header `00 05 16 07`), then:

```bash
cd /mnt/nvme/files
sudo find . -type f \( -name '._*' -o -name .DS_Store \) -print0 > /tmp/macjunk.list
sudo tar --null -T /tmp/macjunk.list -cf /mnt/nvme/mac-metadata-$(date +%Y%m%d).tar
sudo xargs -0 rm -f -- < /tmp/macjunk.list
```

Set the Samba password for your Unix user, then restart:

```bash
sudo smbpasswd -a youruser
sudo systemctl restart smbd nmbd
sudo systemctl enable smbd nmbd
```

From the Mac: `smb://192.168.8.191/mbogiservershare` (user `youruser`).

> **Gotcha to remember:** anything systemd writes here with `StandardOutput=append:` is created as root even when the unit has `User=`, so it won't be writable over the share. See section 6 — that's why the icyscan units don't use `append:`.

### 3a. Read-only WebDAV on `:8081`

The same tree over HTTP, for clients that don't speak SMB: phone file managers, media players that browse WebDAV, and anything connecting over the tailnet. It is **read-only by design**. Samba stays the only way to write to the share, so a misbehaving WebDAV client can't rename or delete anything in the music library.

It runs as `rclone serve webdav` on the host, not in a container. Because it runs as `youruser`, it reads the tree exactly as Samba does, with no bind mount and no uid mapping.

```bash
sudo apt install -y rclone apache2-utils        # Debian's rclone 1.60 is fine for this
```

One user with a bcrypt hash, in a file only `youruser` can read. The password is random and kept in root's home, like Kodi's in 14:

```bash
sudo install -d -m 0750 -o root -g youruser /etc/webdav
PW=$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)
echo "$PW" | sudo tee /root/webdav-password >/dev/null && sudo chmod 600 /root/webdav-password
sudo htpasswd -cbB /etc/webdav/htpasswd youruser "$PW"
sudo chown root:youruser /etc/webdav/htpasswd && sudo chmod 640 /etc/webdav/htpasswd
```

`/etc/systemd/system/webdav.service`:

```ini
[Unit]
Description=WebDAV (read-only) of /mnt/nvme/files via rclone
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/mnt/nvme/files
StartLimitIntervalSec=0

[Service]
Type=simple
User=youruser
Group=youruser
ExecStart=/usr/bin/rclone serve webdav /mnt/nvme/files \
  --addr :8081 \
  --read-only \
  --htpasswd /etc/webdav/htpasswd \
  --realm mbogiservershare \
  --exclude ._* --exclude .DS_Store \
  --dir-cache-time 30s \
  --log-level NOTICE
Restart=always
RestartSec=10

# Hardening: read-only share, so it needs no write access anywhere.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
Environment=RCLONE_CONFIG=/dev/null
Environment=XDG_CACHE_HOME=/tmp

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now webdav
```

`RCLONE_CONFIG=/dev/null`: serving a local path needs no remotes, and without this rclone goes looking for `~/.config/rclone`, which `ProtectHome=yes` hides. `--dir-cache-time 30s` makes a file dropped in over SMB appear in a WebDAV listing within half a minute. The default is five minutes, and a five-minute delay looks like a bug.

The two `--exclude`s hide the `._*` AppleDouble and `.DS_Store` files that Macs leave behind over SMB, so they don't clutter listings or turn up as fake `._track.mp3` entries in players that browse WebDAV.

Check it. The unauthenticated request must be refused, and so must any write:

```bash
PW=$(sudo cat /root/webdav-password); U=http://192.168.8.191:8081
curl -s -o /dev/null -w '%{http_code}\n' -X PROPFIND $U/                               # 401
curl -s -o /dev/null -w '%{http_code}\n' -u youruser:$PW -X PROPFIND -H 'Depth: 1' $U/  # 207
curl -s -o /dev/null -w '%{http_code}\n' -u youruser:$PW -T /etc/hostname $U/x.txt       # 404 - refused
curl -s -o /dev/null -w '%{http_code}\n' -u youruser:$PW -X DELETE $U/icyscan/90shiphop.txt  # 405 - refused
```

rclone 1.60 answers a refused `PUT` with `404`, not `403`. It looks wrong, but nothing is written, and `DELETE` gets a `405`.

**Clients.** In Finder, choose *Go → Connect to Server*, enter `http://192.168.8.191:8081`, log in as `youruser` and use the password from `/root/webdav-password`. Over the tailnet the address is `http://YOUR_TAILNET_IP:8081`. Plain HTTP with Basic auth is acceptable on the LAN and inside WireGuard, but **don't forward :8081 anywhere**. Windows' built-in client refuses Basic auth over HTTP unless `HKLM\SYSTEM\CurrentControlSet\Services\WebClient\Parameters\BasicAuthLevel` is set to `2`, so use a third-party client there.

It has a card on the dashboard (9) under *Host services*, from the entry `"webdav": {"kind": "unit", "unit": "webdav.service", ...}` in hqdash's `TARGETS`.

---

## 4. Mediagg Arr Stack (Docker on the host)

Mediagg Arr Stack installs and wires together a whole media server from one setup page: Jellyfin for films and series, Navidrome for music, Audiobookshelf for audiobooks and podcasts, and the services that find and fetch more (Sonarr, Radarr, Lidarr, Bazarr, Prowlarr, FlareSolverr, qBittorrent, Seerr, Hearr). Each one runs in its own Docker container, already pointed at the others. The manager then pairs the whole stack with the Mediagg app on phones and TVs using a single code.

It ran in an LXC (CT 110) from 2026-10-04 and moved straight onto the host on 2026-10-08, the day before Proxmox was removed. The container build is in the [archive](proxmox-archive.html).

### 4a. Installing

As `youruser`, not root:

```bash
curl -fsSL https://mediagg.app/install.sh | sh
```

The script installs Docker Engine and starts the manager, `mediagg-manager`. Open `http://192.168.8.191:7979`, set a name and password, and answer the setup questions. For the media folder, choose `/mnt/nvme/files`. The manager then pulls and configures every service you picked. The setup page warns that it's served over plain HTTP, so your password crosses the LAN unencrypted. It isn't forwarded anywhere, so that's acceptable here.

Every service runs as uid/gid 1000 — `youruser` — so whatever the stack writes (imports, downloads, metadata) is owned by you and editable over SMB with no ACLs. In CT 110 that took an `lxc.idmap`; on the host it comes for free.

Everything it owns lives under `~/MediaggStack`:

| Path | What it is |
|---|---|
| `compose.yaml` | the stack. **Written by the manager and rewritten on every change** — edit it and your edit is gone on the next change in the UI |
| `config/<service>/` | each service's config and database |
| `manager/state.json` | the manager's own settings |

Changes go through the UI at `:7979`. Run `docker compose` against the file only to look at it (`ps`, `logs`), never to change it.

### 4b. The services

| Service | Port | Image | Does |
|---|---|---|---|
| Manager | `:7979` | `ghcr.io/m3ugoat/mediagg-arr-stack` | setup, pairing, settings |
| Jellyfin | `:8096` | `jellyfin/jellyfin:12.1` | films and series |
| Navidrome | `:4533` | `deluan/navidrome:0.64.2` | music |
| Audiobookshelf | `:13378` | `ghcr.io/advplyr/audiobookshelf:2.37.1` | audiobooks and podcasts |
| Sonarr | `:8989` | `lscr.io/linuxserver/sonarr` | finds and fetches series |
| Radarr | `:7878` | `lscr.io/linuxserver/radarr` | finds and fetches films |
| Lidarr | `:8686` | `lscr.io/linuxserver/lidarr` | finds and fetches music |
| Bazarr | `:6767` | `lscr.io/linuxserver/bazarr` | subtitles for Sonarr and Radarr |
| Prowlarr | `:9696` | `lscr.io/linuxserver/prowlarr` | the indexer list the *arrs search |
| FlareSolverr | `127.0.0.1:8191` | `ghcr.io/flaresolverr/flaresolverr` | gets Prowlarr past browser checks; loopback only |
| qBittorrent | `:8080` | `lscr.io/linuxserver/qbittorrent` | downloads |
| Seerr | `:5055` | `ghcr.io/seerr-team/seerr:v3.5.0` | film and series requests |
| Hearr | `:8282` | `ghcr.io/m3ugoat/hearr:0.4.1` | album and track requests |
| Hearr backend | `:8484` | `ghcr.io/m3ugoat/hearr-backend:0.3.2` | fetches what Hearr is asked for |

Containers are named `mediagg-<service>`, all on one Docker network, `mediagg`. Images are pinned, and the manager moves those pins when it updates the stack.

> **The host's own Navidrome (5) must stay disabled.** It listens on `:4533` too, and whichever starts second fails to bind.

### 4c. Where things land

Inside the stack, `/data` is `/mnt/nvme/files`, read-write. Folders you add as existing libraries in the manager are mounted a second time under `/existing/<kind>-<id>`:

| Service | Root folders |
|---|---|
| Sonarr | `/mnt/nvme/files/tv`, plus `test/Shows` |
| Radarr | `/mnt/nvme/files/movies`, plus `test/Movies` |
| Lidarr | `/mnt/nvme/files/muzak`, plus `music_albums`, `music_african/AfrobeatsCentral` and `music_afrohouse` |
| Navidrome | `muzak` as its music folder; the same three existing folders as libraries; database in `~/MediaggStack/config/navidrome` |

> **Lidarr has root folders on hand-curated music.** Lidarr renames and moves what it manages into `Artist/Album (Year)/`. With `music_albums`, `AfrobeatsCentral` and `music_afrohouse` as root folders, anything it imports or upgrades there gets reorganised inside folders you sort by hand. If that matters to you, remove those as Lidarr root folders and let it write only to `muzak`. And keep 5b in mind: no two Navidrome libraries may overlap.

### 4d. On the dashboard

Each container gets a `docker` card under its own **Mediagg Arr Stack** group (9), so start, stop and restart act on one service and leave the rest running:

```python
"mediagg-sonarr": {"kind": "docker", "container": "mediagg-sonarr",
    "label": "Sonarr", "group": "Mediagg Arr Stack",
    "url": "http://192.168.8.191:8989",
    "note": "192.168.8.191:8989 - TV"},
```

…one per container in 4b. FlareSolverr's has `"url": None`, because it only listens on loopback. **The group must also be listed in `GROUP_ORDER`.** The API reports a card whose group isn't listed, but the page never draws it.

---

## 5. Navidrome (host, disabled)

> **Disabled 2026-10-04.** Navidrome now runs in the Mediagg stack (4), on the same `http://192.168.8.191:4533`, so phones need no change — but it is a fresh install, with its own users, playlists and play counts. The host install was stopped and disabled, not removed: `/usr/bin/navidrome`, `/etc/navidrome/navidrome.toml` and the database in `/var/lib/navidrome` (users, playlists, play counts, the `nd-lyrics` plugin) are all still there. **Don't re-enable it while the stack runs** — both want `:4533`. The rest of this section is how the host install was built. 5b still applies to any Navidrome: never let two libraries overlap.

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
MusicFolder = "/var/lib/navidrome/no-music"   # empty on purpose — see 5b
LyricsPriority = ".ttml,.yaml,.yml,.elrc,.lrc,.srt,nd-lyrics,.txt,embedded"

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
- `MusicFolder` points at an empty directory on purpose — libraries live in the DB, not the config. Pointing it at `/mnt/nvme` is what caused the duplicate-indexing problem in 5b, so don't put it back.
- `/opt/navidrome/music` exists, owned by `navidrome` — vestigial from an earlier install-script attempt. Harmless, and not needed on a rebuild.

### 5a. Lyrics plugin (`nd-lyrics`)

Navidrome has a plugin system as of v0.63 (`Plugins.Enabled`, on by default). Plugins are single `.ndp` bundles read from `<DataFolder>/plugins`. [navidrome-lyrics-plugin](https://github.com/J0R6IT0/navidrome-lyrics-plugin) fetches lyrics from online providers on demand.

Requires Navidrome **≥ v0.63.0**.

Full reference — priority ordering, sidecar behaviour, write-access risks and mitigations: **[Navidrome lyrics](/navidrome-lyrics/)** (`navidrome-lyrics.md`).

```bash
sudo mkdir -p /var/lib/navidrome/plugins
sudo curl -fsSL -o /var/lib/navidrome/plugins/nd-lyrics.ndp \
  "https://github.com/J0R6IT0/navidrome-lyrics-plugin/releases/download/v7.2.0/nd-lyrics.ndp"
sudo chown -R navidrome:navidrome /var/lib/navidrome/plugins
sudo systemctl restart navidrome
```

The plugin also has to be named in `LyricsPriority` (already in the toml above) — the filename without its extension. **Where you put it decides what wins.** Last means local files and embedded tags always beat it; this box puts it after the synced extensions but ahead of `.txt` and `embedded`, so a synced sidecar still wins, then online lyrics are preferred over plain text or baked-in tags:

```toml
LyricsPriority = ".ttml,.yaml,.yml,.elrc,.lrc,.srt,nd-lyrics,.txt,embedded"
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

### 5b. Libraries — never let two overlap

Libraries in 0.63 are **rows in the DB**, not toml entries. `MusicFolder` only seeds a default library (id 1) on first run; after that the web UI is the source of truth. This box runs one library per genre folder under `/mnt/nvme/files/`, each granted to its own playback-only user, with the admin granted all of them.

**A library whose root contains another library's root indexes the same files twice.** Leaving `MusicFolder = "/mnt/nvme"` in place alongside per-genre libraries did exactly that here: 13,259 of 15,224 files ended up with two `media_file` rows under two different IDs. That means:

- Play counts and stars attach to a track ID, so listening history splits by whichever library the client happened to browse.
- Browse and search return everything twice for any account granted both.
- Backup folders inside the tree get served as real music.
- The scan walks everything else on the disk — here ~2 GB of Proxmox VM images — to find no audio at all.
- Clients cache IDs that later vanish, producing `Song not found` and silently dropped scrobbles.

Removing the whole-disk library took the DB from 145 MB to 82 MB and a scan down to ~0.5 s. Check any setup with:

```bash
sudo sqlite3 /var/lib/navidrome/navidrome.db \
  "select count(*) from (select l.path||'/'||m.path ap from media_file m
    join library l on l.id=m.library_id group by ap having count(*)>1);"
# expect: 0
```

Cleanup is **not** a one-line `DELETE FROM library`. `media_file`, `album` and `folder` reference `library` *without* `ON DELETE CASCADE`, and `annotation.item_id` has no foreign key at all, so play counts must be merged onto the surviving duplicate first. Two things make it tractable: the FTS indexes are contentless and trigger-maintained, so a plain `DELETE` keeps search consistent; and `media_file.path` is **library-relative**, so duplicates only surface if you compare `library.path || '/' || media_file.path`.

Leave `Scanner.PurgeMissing` at its default `never`. Rows flagged `missing=1` keep their play history and let Navidrome re-match a file that moved — and the setting is global, so using it to tidy one library destroys history everywhere.

Deleting the default library is safe: Navidrome did not recreate one on restart, which is why `MusicFolder` can point at an empty directory afterwards.

---

## 6. ICY radio metadata loggers

Two systemd services scrape ICY stream metadata and append every track change to a `.txt` on the share.

### 6a. The script

The repo is your fork, which carries a fix (`Fix permanent desync when metadata straddles a chunk boundary`) not in upstream:

```bash
sudo mkdir -p /opt/icy-meta
sudo chown youruser:youruser /opt/icy-meta
git clone git@github.com:laurentjuma/icy-meta.git /opt/icy-meta
cd /opt/icy-meta
git remote add upstream https://github.com/lucvanbraekel/icy-meta.git
```

Needs `python3-requests` (installed in section 1).

### 6b. Units

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

> **90s90s is not on ICY.** An `icyscan-90shiphop.service` briefly existed and was removed on 2026-08-08. The 90s90s streams do send `icy-metaint: 8192`, so the scanner connects and looks healthy, but `StreamTitle` is the static station name (`90s90s - HipHop`) rather than per-track data — same on the `mp3-192`, `mp3-128` and `aac-64` variants. **A valid `icy-metaint` header is not evidence a station publishes track metadata; check that `StreamTitle` actually changes before adding any station.** 90s90s is handled by section 6c instead.

**Why awk owns the file and not systemd:** `StandardOutput=append:` resolves the path once at start and binds to the *inode*. Finder replacing the file over SMB (unlink-then-create) left the service writing into an orphaned inode while the visible file stayed at 0 bytes — silently, nothing in the journal. `close(LOG)` after each line forces a path re-resolve, so a deleted log comes back within one song. **Any new station unit must use this pattern.**

Two known, expected behaviours: the script reconnects to the stream roughly once per second (no poll-interval flag upstream — patch locally if a station ever rate-limits you), and dedup state is in-memory so a restart re-logs the currently playing track.

Logs land at `smb://192.168.8.191/mbogiservershare/icyscan/`.

### 6c. 90s90s — `flowscan`, polling the iris feed

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

## 7. Tailscale remote access

The LAN sits behind a GL.iNet router whose IPv4 egress is an M247 commercial-VPN range, on top of likely CGNAT — **there is no inbound path**, so port forwarding and DDNS are impossible. An outbound tunnel is the only option.

### 7a. Install and join

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

Bring it up as a **subnet router** advertising the whole LAN:

```bash
sudo tailscale up --advertise-routes=192.168.8.0/24
```

Follow the printed URL and log in as `your-tailscale-account`. Then in the Tailscale admin console → Machines → `raspberrypi` → **approve the 192.168.8.0/24 subnet route**. It won't work until you do.

Result: tailnet IP `YOUR_TAILNET_IP`, MagicDNS `raspberrypi.your-tailnet.ts.net`.

### 7b. Persist IP forwarding

```bash
printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' \
  | sudo tee /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
```

### 7c. UDP GRO tuning on `eth0`

Throughput fix for subnet routing — needs to reapply on every boot, hence the oneshot unit.

`/etc/systemd/system/tailscale-gro.service`:

```ini
[Unit]
Description=Enable UDP GRO forwarding on eth0 for Tailscale subnet routing
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/ethtool -K eth0 rx-udp-gro-forwarding on rx-gro-list off

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tailscale-gro
```

> Until 2026-10-09 this targeted `vmbr0`, the Proxmox bridge. When the bridge went away the unit failed on the next boot, silently: subnet routing still works without it, just slower.

### 7d. Using it

Reach services at the tailnet IP instead of the LAN IP — plain HTTP is fine, WireGuard already encrypts it:

- Dashboard — `http://YOUR_TAILNET_IP`
- Mediagg — `http://YOUR_TAILNET_IP:7979`, and each service on its own port (4b)
- SSH — `ssh youruser@YOUR_TAILNET_IP`
- WebDAV — `http://YOUR_TAILNET_IP:8081`, read-only (3a)
- Anything else on the LAN — its 192.168.8.x address, via the subnet route

`tailscale netcheck` reports `MappingVariesByDestIP: true` (symmetric NAT), so connections often relay through the London DERP rather than going peer-to-peer. Fine for audio; expect less headroom for 4K video.

---

## 8. Verification checklist

```bash
# storage
df -h /mnt/nvme                                    # ~916G, ext4

# services
systemctl is-active icyscan-afrobeats icyscan-afrohouse flowscan@265 tailscaled tailscale-gro smbd webdav docker hqdash
systemctl --failed                                 # 0 loaded units

# mediagg
docker ps --format '{{.Names}}' | grep -c '^mediagg-'   # 14
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.8.191:7979/   # manager, 302 -> /login

# dashboard: every card running
curl -s http://192.168.8.191/api/status | python3 -c 'import json,sys; print({t["state"] for t in json.load(sys.stdin)["targets"]})'   # {'running'}

# egress is still inside the ProtonVPN tunnel
curl -s https://ipinfo.io/json | grep -E '"(ip|org)"'
#   "org": "AS9009 M247 Europe SRL"   <- NOT the ISP

# listening ports
sudo ss -tlnp | grep -E ':(22|80|445|4533|7979|8081) '

# radio logs growing
tail -3 /mnt/nvme/files/icyscan/afrobeats.txt
tail -3 /mnt/nvme/files/icyscan/afrohouse.txt

# tailscale
tailscale status
tailscale ip -4                                    # YOUR_TAILNET_IP
```

Then from the Mac: `smb://192.168.8.191/mbogiservershare`, `http://192.168.8.191` (dashboard), and Mediagg on `192.168.8.191`: manager `:7979`, Jellyfin `:8096`, Navidrome `:4533`, Audiobookshelf `:13378`, Sonarr `:8989`, Radarr `:7878`, Lidarr `:8686`, Bazarr `:6767`, Prowlarr `:9696`, qBittorrent `:8080`, Seerr `:5055`, Hearr `:8282`.

---

## 9. Things worth knowing before you rebuild

- **Back up first.** The music library under `/mnt/nvme/files` and the icyscan `.txt` history are the only irreplaceable data. Also grab `~/MediaggStack` (every service's config and database, and the manager's state), `/etc/navidrome/navidrome.toml` (Last.fm keys) and `/var/lib/navidrome/` (the old host Navidrome's playlists, play counts and users).
- **Nothing is backed up automatically.** `/mnt/nvme/backups` only holds one-off copies taken by hand before risky changes.
- **Don't restart Navidrome mid-migration.** A version jump applies schema migrations on first start, and the FTS5 search index alone takes ~13 s on this library. Restarting during that aborts the running transaction (`level=fatal ... failed to begin transaction: context canceled`); the next start does resume at the interrupted migration and finish the rest, but wait for `Navidrome server is ready!` before touching the service.
- **nginx on :80 serves the dashboard** — `/var/www/html/index.html`, a live status grid that polls `/api/status` every 5 seconds. That API is `hqdash`, a stdlib-Python service at `/opt/hqdash/hqdash.py` bound to `127.0.0.1:8787`; nginx proxies `/api/` to it and is what keeps it off the LAN. Every service it can see or control is an entry in the `TARGETS` dict at the top of that file, so **adding a service means editing `TARGETS` and `sudo systemctl restart hqdash`** — a target is a host systemd unit (`kind: unit`) or a Docker container on the host (`kind: docker`, with `container`), which is how the 14 Mediagg services get their own cards (4d). The page itself is entirely data-driven and needs no edit. Docker state comes from the containers' cgroups under `/sys/fs/cgroup/system.slice/docker-<id>.scope`, names from `docker ps` (hqdash runs as `youruser`, who is in the `docker` group) only when the set of running containers changes. Until 2026-10-09 it also had `lxc` cards and reached Docker through `pct exec`; that version is kept as `hqdash.py.bak-pre-noproxmox-20261009`. `/opt/hqdash/README.md` documents it. A version stripped of this box's services, which installs on any Debian host with one command, is `hqdash-install.sh` in this repo.
- **The dashboard has no authentication.** Anyone on the LAN or the tailnet can start, stop and restart every service on the box, and reboot the Pi. That holds only because :80 is not forwarded and the line is behind CGNAT. Put auth in front of it before exposing it to anything.
- **`samba-ad-dc.service` is enabled** but the server is a standalone file server. Harmless.
- **`postfix` is still installed**, listening on localhost only. Proxmox pulled it in for mail notifications; nothing uses it now, and `sudo apt purge postfix` is safe.
- **qBittorrent is not behind a VPN container**, and that is a deliberate call: the GL.iNet router already tunnels the whole subnet over ProtonVPN. It relies on *Block Non-VPN Traffic* being enabled on the router — without it, a dropped tunnel fails open. Re-check that toggle after any router firmware update, and re-run the `ipinfo.io` check in 8.
- **The rebuild order is free.** Sections 1–3 first; everything after that is independent.
