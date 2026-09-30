---
title: Raspberry Pi 5 — Full Rebuild Guide
description: Rebuilding a Pi 5 homelab from a blank SD card — NVMe/LVM, Samba, Proxmox on ARM64, LXC, Navidrome, Audiobookshelf, Plex, Emby, goPodder, m3ugoat, headless Kodi, radio track logging, Tailscale.
---

Everything running on a Raspberry Pi 5 (`raspberrypi`, 192.168.8.191), in the order you'd need to rebuild it from a blank SD card. Each section is standalone — skip any service you don't want.

**Captured:** 2026-08-08 from the live machine. **Updated:** 2026-09-29 — read-only WebDAV of the share on `:8081` (3a), and Samba no longer lets Macs write `._*`/`.DS_Store` files (3); Kodi, headless, in CT 109 (section 14); sections 14–18 renumbered to 15–19. **2026-09-22** — the *arr stack in CT 107 (section 12) and Jellyfin in CT 108 (section 13); sections 12–16 renumbered to 14–18 to keep the container sections contiguous. **2026-09-10** — m3ugoat in CT 106 (section 11). **2026-09-07** — goPodder in CT 105 (section 10), dashboard now `hqdash` (section 19). **2026-08-28** — Emby in CT 104 (section 9), Plex in CT 103 (section 8). **2026-08-24** — Audiobookshelf in CT 102 (section 7). **2026-08-22** — Navidrome 0.63.2 + lyrics plugin (15a), whole-disk library removed (15b).

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
| Hypervisor | PXVIRT (Proxmox VE 9.0 ARM64 port) | web UI `:8006` |
| CT 100 | Music Assistant (Docker in LXC) | 192.168.8.213 |
| CT 101 | AzuraCast (Docker in LXC) — **installed, not running** | 192.168.8.192 |
| CT 102 | Audiobookshelf (Docker in LXC) | 192.168.8.214, web UI `:13378` |
| CT 103 | Plex (Docker in LXC) | 192.168.8.215, web UI `:32400` |
| CT 104 | Emby (Docker in LXC) | 192.168.8.216, web UI `:8096` |
| CT 105 | goPodder (Docker in LXC) — podcast sync, gpodder.net API | 192.168.8.217, web UI `:8080` |
| CT 106 | m3ugoat (Node + systemd in LXC, no Docker) — IPTV playlist/EPG manager | 192.168.8.218, web UI `:8080` |
| CT 107 | *arr stack (Docker Compose in LXC) — qBittorrent, Prowlarr, Sonarr, Radarr, Bazarr, Recyclarr | 192.168.8.219, web UIs `:8080` `:9696` `:8989` `:7878` `:6767` |
| CT 108 | Jellyfin (Docker in LXC) | 192.168.8.220, web UI `:8096` |
| CT 109 | Kodi (headless, Xvfb + systemd in LXC, no Docker) — music, movies and TV over JSON-RPC | 192.168.8.221, web server `:8080` |
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

`/mnt/nvme/files` holds the music library (`music`, `music_african`, `music_kenyan`, `music_urban`, `music_oldies`, `music_soul_r_n_b`, `music_dancehall_reggae`, `music_eurodance`, `music_nwa`, `_Serato_`, …) plus `icyscan/`, and — from sections 7a, 8a and 12b — `audiobooks/`, `podcasts/`, `movies/`, `tv/` and `downloads/`. PVE later adds `dump/ images/ private/ snippets/ template/` at `/mnt/nvme` — leave those alone.

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

> **Gotcha to remember:** anything systemd writes here with `StandardOutput=append:` is created as root even when the unit has `User=`, so it won't be writable over the share. See section 16 — that's why the icyscan units don't use `append:`.

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

It has a card on the dashboard (19) under *Host services*, from the entry `"webdav": {"kind": "unit", "unit": "webdav.service", ...}` in hqdash's `TARGETS`.

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

## 7. CT 102 — Audiobookshelf

Unprivileged LXC, 2 cores / 2 GB, static 192.168.8.214, rootfs on the NVMe, Docker inside. Web UI on `:13378`.

### 7a. Library directories on the host

Audiobookshelf writes to its library folders (podcast downloads land there), so unlike CT 100's music mount this one can't be read-only. The directories stay owned by `youruser` so they're still writable over SMB; the container gets in through a POSIX ACL instead of a chown.

An unprivileged LXC maps container uid 0 → host uid 100000, so that's the id to grant:

```bash
sudo apt install -y acl
sudo mkdir -p /mnt/nvme/files/audiobooks /mnt/nvme/files/podcasts
sudo chown youruser:youruser /mnt/nvme/files/audiobooks /mnt/nvme/files/podcasts

# container root (host uid 100000) rwx — now, and by default on anything created later
sudo setfacl -R    -m u:100000:rwx /mnt/nvme/files/audiobooks /mnt/nvme/files/podcasts
sudo setfacl -R -d -m u:100000:rwx /mnt/nvme/files/audiobooks /mnt/nvme/files/podcasts
```

Both live under the Samba share root, so they appear as `mbogiservershare/audiobooks` and `mbogiservershare/podcasts` with no extra config — that's how you get books onto the box.

### 7b. The container

Built from the Debian 13 template, not the 2023 jammy one CT 100/101 use:

```bash
sudo pveam update
sudo pveam download local debian-13-standard_13.6-1_arm64.tar.zst

sudo pct create 102 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname audiobookshelf \
  --arch arm64 --ostype debian \
  --cores 2 --memory 2048 --swap 512 \
  --rootfs nvme:16 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.214/24,gw=192.168.8.1,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1

sudo pct set 102 -mp0 /mnt/nvme/files/audiobooks,mp=/media/audiobooks
sudo pct set 102 -mp1 /mnt/nvme/files/podcasts,mp=/media/podcasts

sudo pct start 102
```

`nesting=1` for Docker, same as CT 100. Rootfs on `nvme:16`, not the SD card.

### 7c. Audiobookshelf itself

```bash
sudo pct enter 102

apt update && apt install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh

mkdir -p /opt/audiobookshelf/config /opt/audiobookshelf/metadata

docker run -d \
  --name audiobookshelf \
  --restart unless-stopped \
  -p 13378:80 \
  -e TZ=Europe/London \
  -v /opt/audiobookshelf/config:/config \
  -v /opt/audiobookshelf/metadata:/metadata \
  -v /media/audiobooks:/audiobooks \
  -v /media/podcasts:/podcasts \
  ghcr.io/advplyr/audiobookshelf:latest

exit
```

The image listens on port 80 inside the container; 13378 is Audiobookshelf's conventional external port. Config and metadata — the SQLite DB, cached covers, backups — sit on the container rootfs, so nothing it generates ends up in the share.

Then open `http://192.168.8.214:13378` and create the root account on first load; the server stays uninitialised until you do. Add the libraries in the UI afterwards: `/audiobooks` as a Books library, `/podcasts` as a Podcasts library.

Check it without a browser:

```bash
curl -s http://192.168.8.214:13378/status
# {"app":"audiobookshelf","serverVersion":"2.36.0","isInit":false,...}
```

> `isInit: false` flips to `true` once the root account exists — a one-line way to tell "server up" from "server up and set up".

Finally, add a card for it to the `Home Server` landing page nginx serves at `http://192.168.8.191` (`/var/www/html/index.html`) — copy an existing `<a class="card">` block and point it at `http://192.168.8.214:13378`.

---

## 8. CT 103 — Plex

Unprivileged LXC, 4 cores / 4 GB, static 192.168.8.215, rootfs on the NVMe, Docker inside. Web UI on `:32400`.

### 8a. Library directories on the host

Same treatment as 7a. Plex itself writes nothing into the library, but the directories still have to be writable over SMB — that's how media gets onto the box — *and* readable by the container, so they're owned by `youruser` with a POSIX ACL for container root (host uid 100000):

```bash
sudo mkdir -p /mnt/nvme/files/movies /mnt/nvme/files/tv
sudo chown youruser:youruser /mnt/nvme/files/movies /mnt/nvme/files/tv

sudo setfacl -R    -m u:100000:rwx /mnt/nvme/files/movies /mnt/nvme/files/tv
sudo setfacl -R -d -m u:100000:rwx /mnt/nvme/files/movies /mnt/nvme/files/tv
```

Both sit under the share root, so they show up as `mbogiservershare/movies` and `mbogiservershare/tv` with no extra Samba config.

### 8b. The container

Debian 13 template, same as CT 102:

```bash
sudo pct create 103 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname plex \
  --arch arm64 --ostype debian \
  --cores 4 --memory 4096 --swap 512 \
  --rootfs nvme:32 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.215/24,gw=192.168.8.1,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1

sudo pct set 103 -mp0 /mnt/nvme/files/movies,mp=/media/movies
sudo pct set 103 -mp1 /mnt/nvme/files/tv,mp=/media/tv
sudo pct set 103 -mp2 /mnt/nvme/files,mp=/media/music,ro=1

sudo pct start 103
```

More cores and RAM than the other containers because every transcode here is CPU-only — see the note at the end of 8c. `nvme:32` rather than CT 102's 16: Plex's metadata, thumbnails and transcode scratch all live on the container rootfs and grow with the library. `mp2` is the whole `files` tree read-only, the same mount CT 100 gets, so Plex can serve the music without a second copy of it.

### 8c. Plex itself

```bash
sudo pct enter 103

apt update && apt install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh

mkdir -p /opt/plex/config /opt/plex/transcode

docker run -d \
  --name plex \
  --restart unless-stopped \
  --network host \
  -e PUID=0 -e PGID=0 \
  -e TZ=Europe/London \
  -e VERSION=docker \
  -e ADVERTISE_IP=http://192.168.8.215:32400/ \
  -e ALLOWED_NETWORKS=192.168.8.0/24 \
  -v /opt/plex/config:/config \
  -v /opt/plex/transcode:/transcode \
  -v /media/movies:/movies \
  -v /media/tv:/tv \
  -v /media/music:/music:ro \
  lscr.io/linuxserver/plex:latest

exit
```

`lscr.io/linuxserver/plex` has a real arm64 build — check with `docker image inspect --format '{{.Architecture}}'` if in doubt.

**Host networking, not `-p 32400:32400`.** Plex's GDM client discovery broadcasts on 32410–32414/udp and bridged Docker networking swallows it; host mode gives the container the LXC's own network namespace instead. `ADVERTISE_IP` then pins what the server tells clients about itself, and `ALLOWED_NETWORKS` keeps the LAN from being prompted to log in.

`PUID=0`/`PGID=0` is container root, which an unprivileged LXC maps to host uid 100000 — exactly the uid 8a handed the ACL to.

Check it without a browser:

```bash
curl -s http://192.168.8.215:32400/identity
# <MediaContainer size="0" apiVersion="1.2.2" claimed="0" machineIdentifier="…" version="1.43.3.10896-cb3ebc72d">
```

> `claimed="0"` is Plex's version of Audiobookshelf's `isInit: false` — the server is up but not yet tied to an account.

Then open `http://192.168.8.215:32400/web` **from a machine on 192.168.8.0/24** and run the wizard. Plex only lets you claim an unclaimed server from its own subnet; to sidestep that, grab a token from <https://www.plex.tv/claim/> (valid 4 minutes) and add `-e PLEX_CLAIM=claim-xxxxxxxx` to the `docker run` so it comes up already linked.

Add `/movies` and `/tv` as libraries. For music, point the library at a specific subfolder — `/music/music_albums`, say — **not** at `/music`, which is the whole `files` tree and would drag in `audiobooks/`, `icyscan/` and `_Serato_`.

> **No hardware transcoding on this box.** The Pi 5's video engine isn't something Plex can drive, so every transcode is software on 4 Cortex-A76 cores. Direct play is fine (it's just a file read), audio transcode is fine, 1080p video transcode is marginal and 4K isn't happening. Set clients to "Original" quality and it stops mattering.

Finally, add a card for it to the `Home Server` landing page, same as 7c — `http://192.168.8.215:32400/web`.

---

## 9. CT 104 — Emby

Unprivileged LXC, 4 cores / 3 GB, static 192.168.8.216, rootfs on the NVMe, Docker inside. Web UI on `:8096`. It points at the same `movies`/`tv` directories as Plex — neither server writes into the library, so the two coexist with no extra work.

### 9a. Library directories on the host

Nothing new to do: 8a already created `/mnt/nvme/files/movies` and `/mnt/nvme/files/tv` owned by `youruser` with a POSIX ACL for container root (host uid 100000), and unprivileged CT 104 maps to that same uid. If you're building this box without Plex, run 8a first — it's the only part of section 8 Emby depends on.

### 9b. The container

Debian 13 template, same as CT 102 and 103:

```bash
sudo pct create 104 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname emby \
  --arch arm64 --ostype debian \
  --cores 4 --memory 3072 --swap 512 \
  --rootfs nvme:24 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.216/24,gw=192.168.8.1,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1

sudo pct set 104 -mp0 /mnt/nvme/files/movies,mp=/media/movies
sudo pct set 104 -mp1 /mnt/nvme/files/tv,mp=/media/tv
sudo pct set 104 -mp2 /mnt/nvme/files,mp=/media/music,ro=1

sudo pct start 104
```

Same three mounts as CT 103, so both servers see an identical view of the media.

4 cores because transcoding here is CPU-only, same as Plex. **3 GB rather than 4**, though: the box has 8 GB and CT 103 already claims 4. LXC `memory` is a cap and not a reservation, so nothing breaks the moment you overcommit, but two media servers both free to take 4 GB while Navidrome and Samba run on the host is how you meet the OOM killer. `nvme:24` rather than CT 103's 32 — Emby's metadata, images and transcode scratch also live on the container rootfs, but its appetite is smaller than Plex's.

### 9c. Emby itself

```bash
sudo pct enter 104

apt update && apt install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh

mkdir -p /opt/emby/config /opt/emby/transcode

docker run -d \
  --name emby \
  --restart unless-stopped \
  --network host \
  -e PUID=0 -e PGID=0 \
  -e TZ=Europe/London \
  -v /opt/emby/config:/config \
  -v /opt/emby/transcode:/transcode \
  -v /media/movies:/movies \
  -v /media/tv:/tv \
  -v /media/music:/music:ro \
  lscr.io/linuxserver/emby:latest

exit
```

`lscr.io/linuxserver/emby` is multi-arch and resolves to a real arm64 build — `docker image inspect --format '{{.Architecture}}'` says `arm64`. Emby's own `emby/embyserver` is multi-arch too; `emby/embyserver_arm64v8` publishes no manifest you can inspect, so don't reach for it.

Host networking again, for the same class of reason as Plex: Emby's apps find the server by broadcasting on 7359/udp, and DLNA uses 1900/udp — bridged Docker swallows both. `PUID=0`/`PGID=0` is container root → host uid 100000, the uid 8a handed the ACL to.

Check it without a browser:

```bash
curl -s http://192.168.8.216:8096/System/Info/Public
# {"LocalAddresses":[],"RemoteAddresses":[],"ServerName":"emby","Version":"4.9.5.0","Id":"fb08c7d3…"}
```

> Unlike Plex there's no claim token and no account to link — but also nothing guarding the wizard. **The first browser to reach `:8096` gets to create the admin user**, so open `http://192.168.8.216:8096` and finish the wizard now rather than later.

Add `/movies` and `/tv` as libraries. For music, point the library at a specific subfolder — `/music/music_albums`, say — **not** at `/music`, which is the whole `files` tree and would drag in `audiobooks/`, `icyscan/` and `_Serato_`.

> **No hardware transcoding here either** — see the note at the end of 8c; it applies unchanged. Emby gates HW acceleration behind Premiere anyway, and the Pi 5's video engine isn't something it can drive.

Finally, add a card for it to the `Home Server` landing page, same as 7c — `http://192.168.8.216:8096`.

---

## 10. CT 105 — goPodder

Unprivileged LXC, 1 core / 512 MB, static 192.168.8.217, rootfs on the NVMe, Docker inside. Web UI on `:8080`.

[goPodder](https://github.com/cbrgm/gopodder) is a gpodder.net-compatible **sync** server — subscriptions, episode progress and device registrations, kept in step across podcast apps. It is not a media server and not a feed reader: it never fetches a feed and makes no outbound connections at all, so unlike every other container here it needs no view of the library and touches nothing under `/mnt/nvme`. AntennaPod on the phone and gPodder on the desktop both point at it and share one state.

### 10a. The container

Same Debian 13 template as CT 102–104, sized far smaller — the whole server is one Go binary:

```bash
sudo pct create 105 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname gopodder \
  --arch arm64 --ostype debian \
  --cores 1 --memory 512 --swap 512 \
  --rootfs nvme:8 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.217/24,gw=192.168.8.1,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1

sudo pct start 105
```

No `pct set … -mp0` here. Every other container in this guide gets the media tree bind-mounted; this one stores a single SQLite file and would never read the library, so handing it a mount is only a wider blast radius. `nvme:8` and 512 MB rather than CT 104's 24 GB and 3 GB for the same reason — there is no metadata cache and no transcode scratch to hold.

### 10b. goPodder itself

```bash
sudo pct enter 105

apt update && apt install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh

mkdir -p /opt/gopodder/data

docker run -d \
  --name gopodder \
  --restart unless-stopped \
  -p 8080:8080 \
  -e TZ=Europe/London \
  -v /opt/gopodder/data:/data \
  ghcr.io/cbrgm/gopodder:v1.2.5 \
  serve --db-path /data/gopodder.db

exit
```

`ghcr.io/cbrgm/gopodder` publishes a real arm64 manifest — `docker image inspect --format '{{.Architecture}}'` says `arm64`.

**Pin the tag.** `:latest` is built from `main` on every push, not from a release: pulling it here gave `version=1-5-g1b7a47c-dirty`, five commits past a tag and flagged dirty. `v1.2.5` is the actual release. For something that owns your listening state, take the tagged one and bump it deliberately.

Published ports rather than the `--network host` that Plex and Emby need — nothing here depends on broadcast or DLNA discovery, so the container keeps its own netns and exposes exactly one port.

**`--db-path /data/gopodder.db` is the entire persistence story.** Left off, the image writes `gopodder.db` into its own working directory inside the writable layer: it survives restarts, looks completely fine, and disappears the first time the container is recreated to pick up a new tag. Point it at the bind mount or you are running a database that your next upgrade throws away.

Check it without a browser:

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://192.168.8.217:8080/
# 303 http://192.168.8.217:8080/setup

sudo pct exec 105 -- docker logs gopodder | tail -2
# level=INFO msg=goPodder version=1.2.5 revision=6d043cf go=go1.27.0 platform=linux/arm64
# level=INFO msg="starting server" addr=0.0.0.0:8080
```

> Same trap as Emby: **the first browser to reach `/setup` creates the admin account**, and nothing guards it. Open `http://192.168.8.217:8080` and finish setup now rather than later.

Then create a goPodder **user** on the Users tab — a separate thing from the admin login — and give those credentials to the podcast apps, not the admin ones.

Backups are a file copy; it runs in WAL mode, so it is safe while the server is up:

```bash
sudo pct exec 105 -- cp /opt/gopodder/data/gopodder.db /tmp/gopodder-$(date +%F).db
```

### 10c. Pointing the apps at it, and the HTTPS catch

In the app this is the "gpodder.net sync" / "Synchronize subscriptions" setting with a custom server — `http://192.168.8.217:8080`, then the goPodder user's credentials.

**AntennaPod requires HTTPS and goPodder does not terminate TLS**, so plain `http://` will not do for the phone. Desktop gPodder and Cardo accept HTTP and work as-is. The cheap fix for AntennaPod is the tailnet (section 17) — `tailscale cert` plus `tailscale serve` on the host gives a real certificate on a MagicDNS name with no port forwarding; nginx on :80 could also front it, but then you are minting certificates for a LAN name. Either way, do not reach for Funnel unless the phone genuinely has to sync from outside the tailnet: that publishes the login page to the internet.

Finally, add a card for it to the dashboard — an entry in `TARGETS` in `/opt/hqdash/hqdash.py`, then `sudo systemctl restart hqdash`.

---

## 11. CT 106 — m3ugoat

Unprivileged LXC, 2 cores / 2 GB, static 192.168.8.218, rootfs on the NVMe. Web UI on `:8080`.

[m3ugoat](https://github.com/m3ugoat/m3ugoat-playlist-manager) is a self-hosted IPTV playlist and EPG manager — a fork of [m3u4me](https://github.com/andrei-savin/m3u4me) adding multi-user accounts, per-device sign-in and a documented sync API. It manages M3U playlists; it serves no streams and you bring your own content.

**This is the one container here that does not run Docker.** There is no published image, and the app is a Node server plus a static frontend — wrapping that in a hand-rolled Dockerfile would add an image to rebuild on every update and buy nothing. It runs as a plain systemd unit instead, the same way Navidrome does on the host.

> `:8080` again, the same port goPodder uses. Not a clash — each container has its own IP and its own network namespace, so `192.168.8.217:8080` and `192.168.8.218:8080` are unrelated. Nothing is published to the host.

### 11a. The container

```bash
sudo pct create 106 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname m3ugoat \
  --arch arm64 --ostype debian \
  --cores 2 --memory 2048 --swap 512 \
  --rootfs nvme:12 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.218/24,gw=192.168.8.1,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --unprivileged 1 \
  --onboot 1

sudo pct start 106
```

No `--features nesting=1` here, unlike CT 100–105: nesting is what lets Docker run inside an unprivileged container, and there is no Docker in this one. No media mount either — the app stores playlists, not files.

2 GB is sized for `npm run build`, not for serving. Rollup is the peak and it is brief; the running server sits far below that.

### 11b. Node 24

The app needs **Node 24+** — it uses Node's built-in SQLite, and runs `server.ts` through Node's native TypeScript support with no build step for the server. Debian 13 ships 20.19, so this comes from NodeSource:

```bash
sudo pct enter 106

apt update && apt install -y ca-certificates curl git
curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
apt install -y nodejs

node -v          # v24.21.0 — must be >= 24, apt's own nodejs is 20.19 and will not do
```

### 11c. The app

It runs as its own unprivileged user rather than root, with the checkout under that user's home:

```bash
useradd --system --create-home --home-dir /opt/m3ugoat --shell /usr/sbin/nologin m3ugoat

git clone https://github.com/m3ugoat/m3ugoat-playlist-manager.git /opt/m3ugoat/app
chown -R m3ugoat:m3ugoat /opt/m3ugoat
install -d -o m3ugoat -g m3ugoat /opt/m3ugoat/app/data

runuser -u m3ugoat -- bash -c 'cd /opt/m3ugoat/app && npm ci && npm run build'
```

Run every `git` and `npm` command as `m3ugoat`, not as root. Cloning as root and then `chown`-ing leaves git refusing to touch the tree afterwards — *"detected dubious ownership in repository"* — because the working directory is no longer owned by the user running git.

`npm run build` only builds the **frontend** into `dist/`. The server is not compiled; Node strips the types out of `server.ts` at load time.

### 11d. The unit

The rest of this box is supervised by systemd, so this is too. The project ships an `ecosystem.config.cjs` and its README installs PM2 — PM2 would be a second supervisor whose `pm2 startup` generates a systemd unit to launch it anyway.

```ini
# /etc/systemd/system/m3ugoat.service
[Unit]
Description=m3ugoat - self-hosted M3U playlist manager and sync API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=m3ugoat
Group=m3ugoat
WorkingDirectory=/opt/m3ugoat/app
Environment=NODE_ENV=production
Environment=PORT=8080
# server.ts is run directly - Node 24 strips the types itself, no build step for the server.
ExecStart=/usr/bin/node server.ts
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

# It only ever reads its own tree and writes the SQLite file under data/.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/m3ugoat/app/data
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now m3ugoat
exit
```

`WorkingDirectory` is load-bearing: the app resolves its data directory as `<cwd>/data`, so a different working directory silently gives you a different, empty database. `ProtectSystem=strict` makes the whole filesystem read-only apart from `ReadWritePaths`, which is why `data/` has to exist before first start — the app would otherwise try to `mkdir` it into a read-only tree.

Check it without a browser:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.8.218:8080/   # 200
curl -s http://192.168.8.218:8080/api/auth/status
# {"enabled":false,"userCount":0,"multiUser":false}
```

> **`"enabled":false` means the API has no authentication at all** — that is the documented default for a fresh install, and it makes every `/api/*` route open to anyone who can reach the port. Playlists routinely embed provider credentials in stream URLs. **Set a password in the UI before adding anything real.**

Note also that the fork **disables the old numeric playlist URLs** (`/1`, `/2`, …) and returns `410 Gone`: they are unauthenticated and sequential, so anyone on the LAN could walk them and read every account's playlists. Use the `/e/<token>` links from the Export dialog. `ALLOW_INSECURE_SHORT_IDS=1` brings the old ones back if you are mid-migration from upstream.

### 11e. Updating and backup

```bash
sudo pct exec 106 -- runuser -u m3ugoat -- bash -c \
  'cd /opt/m3ugoat/app && git pull --ff-only && npm ci && npm run build'
sudo pct exec 106 -- systemctl restart m3ugoat
```

If `git pull` complains that `package-lock.json` would be overwritten, `git restore package-lock.json` first — `npm ci` rewrites it in place.

The database is one SQLite file in WAL mode, safe to copy while the server runs:

```bash
sudo pct exec 106 -- cp /opt/m3ugoat/app/data/m3ugoat.db /tmp/m3ugoat-$(date +%F).db
```

Finally, add a card for it to the dashboard — an entry in `TARGETS` in `/opt/hqdash/hqdash.py`, then `sudo systemctl restart hqdash`.

---

## 12. CT 107 — the *arr stack

Unprivileged LXC, 2 cores / 2 GB, static 192.168.8.219, rootfs on the NVMe, Docker inside. One Compose project holding six containers: qBittorrent, Prowlarr, Sonarr, Radarr, Bazarr and Recyclarr.

This is the first container that **writes** into the library. Plex, Emby and Navidrome only ever read, which is why they could be scattered across separate CTs without a thought. The moment something imports files, one structural decision dominates everything else: a finished download has to become a library file by *hardlink*, not by copy.

### 12a. One `/data` mount, and why it is not three

A hardlink cannot cross a filesystem. If downloads and library arrive as two unrelated mounts, Sonarr and Radarr can't link between them — and they don't error, they quietly fall back to copying. That doubles the disk cost of every release and rewrites tens of gigabytes to the NVMe on each import.

Everything here already lives on one ext4 (`/mnt/nvme`), so the fix is to mount the *parent*, once:

```bash
sudo pct set 107 -mp0 /mnt/nvme/files,mp=/data
```

Not `movies`, `tv` and `downloads` as three mountpoints. One `/data`, at the identical path in every container in the stack. That also deletes a whole category of problem: no Sonarr/Radarr "remote path mapping" is needed, because qBittorrent reports `/data/downloads/complete/...` and Sonarr sees precisely that path.

Prove it rather than assume it:

```bash
sudo pct exec 107 -- docker exec sonarr sh -c '
  echo probe > /data/downloads/complete/.probe
  ln /data/downloads/complete/.probe /data/movies/.probe && echo LINK_OK
  stat -c "%h links, inode %i" /data/downloads/complete/.probe /data/movies/.probe
  rm -f /data/downloads/complete/.probe /data/movies/.probe'

# LINK_OK
# 2 links, inode 38567701
# 2 links, inode 38567701
```

Same inode, link count 2. If that prints two different inodes — or fails outright — stop here, because every import afterwards is silently wrong.

> **What one `/data` costs.** Mounting the whole `files` tree read-write means the stack can also see `music*`, `audiobooks` and `_Serato_`. Nothing touches them as long as the root folders stay `/data/movies` and `/data/tv`, but this is the one place a mistyped root folder could do real damage. Set them explicitly (12e) and never leave one pointing at `/data` itself.

### 12b. Host directories

`movies/` and `tv/` already exist from 8a. Add the download tree beside them and give all three the same ACL treatment:

```bash
sudo mkdir -p /mnt/nvme/files/downloads/complete /mnt/nvme/files/downloads/incomplete
sudo chown -R youruser:youruser /mnt/nvme/files/downloads

sudo setfacl -R    -m u:100000:rwx /mnt/nvme/files/downloads /mnt/nvme/files/movies /mnt/nvme/files/tv
sudo setfacl -R -d -m u:100000:rwx /mnt/nvme/files/downloads /mnt/nvme/files/movies /mnt/nvme/files/tv
```

The `-d` default ACL is the half that matters here. Unlike Plex, this stack *creates* files, and the default entry is what makes each new season folder inherit container-root access instead of needing the `setfacl` re-run by hand.

`complete/` and `incomplete/` are split so qBittorrent writes partial files somewhere the *arrs never scan. Both sit under the share root, so they appear over SMB as `mbogiservershare/downloads` with no extra Samba config.

### 12c. The container

```bash
sudo pct create 107 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname arr \
  --arch arm64 --ostype debian \
  --cores 2 --memory 2048 --swap 512 \
  --rootfs nvme:16 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.219/24,gw=192.168.8.1,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1

sudo pct set 107 -mp0 /mnt/nvme/files,mp=/data

sudo pct start 107
```

2 cores / 2 GB is plenty — these are .NET web apps that spend their lives idle and wake up to parse RSS. Nothing here transcodes. `nvme:16` covers six config directories and the Docker images; the actual media never touches the rootfs.

Then Docker, same as every other CT:

```bash
sudo pct enter 107
apt update && apt install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh
```

### 12d. The Compose stack

Six services is past the point where `docker run` lines stay readable, so this CT gets a Compose file — the first one on the box that isn't AzuraCast's.

```yaml
# /opt/arr/docker-compose.yml
name: arr

x-common: &common
  restart: unless-stopped
  environment: &env
    PUID: 0          # container root -> host uid 100000, the uid holding the ACL
    PGID: 0
    TZ: Europe/London
  networks: [arrnet]

networks:
  arrnet:
    driver: bridge

services:
  qbittorrent:
    <<: *common
    image: lscr.io/linuxserver/qbittorrent:latest
    container_name: qbittorrent
    environment:
      <<: *env
      WEBUI_PORT: 8080
      TORRENTING_PORT: 6881
    ports:
      - 8080:8080
      - 6881:6881/tcp
      - 6881:6881/udp
    volumes:
      - /opt/arr/qbittorrent:/config
      - /data/downloads:/data/downloads

  prowlarr:
    <<: *common
    image: lscr.io/linuxserver/prowlarr:latest
    container_name: prowlarr
    ports: [9696:9696]
    volumes:
      - /opt/arr/prowlarr:/config

  sonarr:
    <<: *common
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    ports: [8989:8989]
    volumes:
      - /opt/arr/sonarr:/config
      - /data:/data

  radarr:
    <<: *common
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    ports: [7878:7878]
    volumes:
      - /opt/arr/radarr:/config
      - /data:/data

  bazarr:
    <<: *common
    image: lscr.io/linuxserver/bazarr:latest
    container_name: bazarr
    ports: [6767:6767]
    volumes:
      - /opt/arr/bazarr:/config
      - /data:/data

  recyclarr:
    <<: *common
    image: ghcr.io/recyclarr/recyclarr:edge    # see 12f — not :latest, not :7
    container_name: recyclarr
    user: "0:0"
    environment:
      <<: *env
      CRON_SCHEDULE: "@daily"
    volumes:
      - /opt/arr/recyclarr:/config
```

```bash
mkdir -p /opt/arr/{qbittorrent,prowlarr,sonarr,radarr,bazarr,recyclarr}
cd /opt/arr && docker compose up -d
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
```

A user-defined bridge rather than `--network host`: unlike Plex (8b) nothing here does broadcast discovery, and a bridge buys DNS between the services — Sonarr reaches the client at `http://qbittorrent:8080`, Prowlarr reaches `http://sonarr:8989`. Those names only resolve on `arrnet`, which is why every wiring step below uses them instead of IPs.

All six images are genuine arm64 — worth confirming, since a stack this size is where a `linux/amd64`-only image usually turns up:

```bash
for c in qbittorrent prowlarr sonarr radarr bazarr; do
  printf '%-12s ' $c
  docker image inspect --format '{{.Architecture}}' $(docker inspect -f '{{.Config.Image}}' $c)
done
# all five print arm64
```

### 12e. Wiring it together

Sonarr and Radarr write their API key into `config.xml` on first start, so read them from there rather than the UI:

```bash
for a in sonarr radarr prowlarr; do
  printf '%-9s ' $a
  grep -o '<ApiKey>[^<]*' /opt/arr/$a/config.xml | cut -d'>' -f2
done
```

What has to exist, and it is short: a root folder and a download client in each of Sonarr and Radarr, and both of them registered in Prowlarr. All of it is `POST`-able, so it can be done from a script instead of six passes through four web UIs — the one in this repo is idempotent and safe to re-run.

The settings that matter:

| Where | Setting | Value |
|---|---|---|
| Sonarr | Root folder | `/data/tv` |
| Radarr | Root folder | `/data/movies` |
| Sonarr / Radarr | Download client | qBittorrent, host `qbittorrent`, port 8080 |
| Sonarr | Category | `tv-sonarr` |
| Radarr | Category | `radarr` |
| Prowlarr | Applications | Sonarr `http://sonarr:8989`, Radarr `http://radarr:7878` |
| Prowlarr | Prowlarr server | `http://prowlarr:9696` |
| Prowlarr | Sync level | Full Sync |

Verify by asking the apps rather than by looking at the screen — `accessible: true` is the field that proves the mount and the ACL both landed:

```bash
curl -s -H "X-Api-Key: $SONARR_KEY" http://192.168.8.219:8989/api/v3/rootfolder
# [{"path":"/data/tv","accessible":true,"freeSpace":...}]

curl -s -X POST -H "X-Api-Key: $SONARR_KEY" -H 'Content-Type: application/json' \
  -d "$(curl -s -H "X-Api-Key: $SONARR_KEY" http://192.168.8.219:8989/api/v3/downloadclient/1)" \
  http://192.168.8.219:8989/api/v3/downloadclient/test -o /dev/null -w '%{http_code}\n'
# 200 — Sonarr authenticated against qBittorrent
```

**Indexers are deliberately not in this list.** Prowlarr ships none; which ones you add is yours to decide. Add them in Prowlarr only — with Full Sync set, they propagate to Sonarr and Radarr on their own, and an indexer added directly to Sonarr will be deleted on the next sync.

Bazarr is the exception to the API-driven approach: it has no setup API worth using, and it reads its config once at boot. Edit `config.yaml` with the container stopped, then start it:

```bash
docker stop bazarr
# in /opt/arr/bazarr/config/config.yaml:
#   general:  use_sonarr: true   use_radarr: true
#   sonarr:   ip: sonarr   port: 8989   apikey: <SONARR_KEY>
#   radarr:   ip: radarr   port: 7878   apikey: <RADARR_KEY>
docker start bazarr

docker logs bazarr 2>&1 | grep SignalR
# BAZARR SignalR client for Radarr is connected and waiting for events.
# BAZARR SignalR client for Sonarr is connected and waiting for events.
```

Those two SignalR lines are the whole test. Bazarr shows a green connection dot in its UI whether or not it can actually subscribe; the log is what tells the truth.

### 12f. Recyclarr — and the tag trap

Recyclarr syncs TRaSH Guides quality profiles and custom formats into Sonarr and Radarr on a schedule, so quality settings stop being something you hand-tune and forget.

Its Docker tags are the problem, and this cost an hour:

- **There is no `:latest`.** Pulling it fails outright — the repository simply has no such tag.
- **`:7` is the current stable release, and it does not work.** The 7.x binary's own `config create` emits *v8-format* templates — `custom_format_groups`, and quality profiles keyed by `trash_id` — which the 7.x parser then rejects with a uselessly vague `Exception at line 17`. The upstream config-template repo it downloads has moved to v8 wholesale, so there is no combination of stable binary and current templates that parses.
- **`:edge` (v8.x) is what works**, and is what this stack pins.

```bash
docker run --rm --user 0:0 -v /opt/arr/recyclarr:/config ghcr.io/recyclarr/recyclarr:edge \
  config create -t web-1080p -t hd-bluray-web --force
# [INF] Created configuration file: /config/configs/hd-bluray-web.yml
# [INF] Created configuration file: /config/configs/web-1080p.yml
```

`--user 0:0` is required — the image's default user cannot write `/config/cache`, and the failure is reported as an unrelated logger-initialisation exception.

Then fill in the two placeholder lines per file (`base_url`, `api_key`) using the service names, and sync:

```bash
cd /opt/arr && docker compose run --rm recyclarr sync

# hd-bluray-web: Created 40 New Custom Formats
# hd-bluray-web: Created 1 Profiles: ["HD Bluray + WEB"]
# web-1080p:     Created 37 New Custom Formats
# web-1080p:     Created 1 Profiles: ["WEB-1080p"]
```

**`web-1080p` and `hd-bluray-web`, not the 2160p templates** — that is a hardware decision, not a taste one. See the note at the end of 13, and 8c before it: nothing on this box can transcode. 1080p maximises the chance every client direct-plays, and keeps a film at 3–10 GB instead of 40–80.

After the first sync the container runs `sync` daily on its own; `CRON_SCHEDULE` is the image default, written into the Compose file only so it is visible.

### 12g. qBittorrent, and the VPN that is already there

**No gluetun here, on purpose.** The usual pattern puts qBittorrent inside a VPN container's network namespace for a killswitch. On this network the GL.iNet router already carries the whole of `192.168.8.0/24` out over a ProtonVPN WireGuard tunnel, so the container's egress is tunnelled before it reaches the router. A second WireGuard inside would be a tunnel in a tunnel: less throughput, another MTU to get wrong, and no privacy that isn't already there.

Confirm the assumption rather than inheriting it — from the host, and again from inside the container:

```bash
curl -s https://ipinfo.io/json | head -5
# "ip": "146.70.133.131"
# "city": "Manchester"
# "org": "AS9009 M247 Europe SRL"      <- a ProtonVPN exit, not the ISP
```

If that ever returns the real ISP, the router tunnel is down and this container has no protection of its own.

> **Two things the router does not give you, that gluetun would.**
> **1. A killswitch.** If the tunnel drops, GL.iNet falls back to plain WAN unless *Block Non-VPN Traffic* is enabled in the router's VPN settings. Turn it on; it is the single toggle that makes this design equivalent to the gluetun one.
> **2. Port forwarding.** ProtonVPN's forwarded ports come over NAT-PMP, which the router's WireGuard client does not request. qBittorrent therefore has no incoming port — connectable peers still work, so this is a speed ceiling on poorly-seeded torrents, not a breakage. `upnp` is set `false` because there is nothing on the path that could honour it.

The setup that does have to happen inside qBittorrent:

```bash
# paths, so imports land where the *arrs expect
save_path         /data/downloads/complete
temp_path         /data/downloads/incomplete   (enabled)
listen_port       6881    (random_port off)
max_ratio         2.0     (seed to 2.0, then stop)
```

And a real password. The linuxserver image generates a **temporary** one into the log on every start, which is fine for a human and useless for Sonarr:

```bash
docker logs qbittorrent 2>&1 | grep -i 'temporary password'
# A temporary password is provided for this session: xxxxxxxxx
```

Set a permanent one via `/api/v2/app/setPreferences` (`web_ui_password`) or the UI, then use it in both download client configs.

> **The session cookie is not called `SID`.** qBittorrent 5.x renamed it to `QBT_SID_<port>` — `QBT_SID_8080` here. Any script that greps the login response for `SID=` gets nothing and reports a login failure that looks like wrong credentials. Use curl's cookie jar (`-c`/`-b`) and the name stops mattering.

On the `Home Server` landing page these get **six separate cards**, not one. `hqdash` originally understood only whole containers and host units, so a Compose stack collapsed into a single entry; it now has a `docker` kind that addresses one service inside an LXC:

```python
"sonarr": {"kind": "docker", "vmid": "107", "container": "sonarr",
           "label": "Sonarr", "group": "*arr stack",
           "url": "http://192.168.8.219:8989", "note": "192.168.8.219:8989 - TV"},
```

Start/stop/restart then act on that service alone, leaving the other five and the LXC running. Status is read from the container's cgroup under `/sys/fs/cgroup/lxc/107/ns/system.slice/docker-<id>.scope`, which is free; only the id→name mapping needs `pct exec`, and that is cached until the set of running ids changes — the same trade `lxc_state` makes to avoid `pct status`.

---

## 13. CT 108 — Jellyfin

Unprivileged LXC, 4 cores / 2 GB, static 192.168.8.220, rootfs on the NVMe, Docker inside. Web UI on `:8096`. Third media server on the box, pointed at the same `movies`/`tv` directories as Plex (8a) and Emby (9a) — none of the three writes into the library, so they coexist exactly as those two already do.

```bash
sudo pct create 108 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname jellyfin \
  --arch arm64 --ostype debian \
  --cores 4 --memory 2048 --swap 512 \
  --rootfs nvme:24 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.220/24,gw=192.168.8.1,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1

sudo pct set 108 -mp0 /mnt/nvme/files/movies,mp=/media/movies
sudo pct set 108 -mp1 /mnt/nvme/files/tv,mp=/media/tv
sudo pct set 108 -mp2 /mnt/nvme/files,mp=/media/music,ro=1

sudo pct start 108
```

Three mountpoints here, not the single `/data` of 12a — Jellyfin never imports anything, so it has no reason to hardlink and no reason to hold the library read-write beyond what it scans. `nvme:24` because Jellyfin's metadata, images and trickplay thumbnails live on the rootfs and grow with the library.

```bash
sudo pct enter 108

apt update && apt install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh

mkdir -p /opt/jellyfin/config /opt/jellyfin/cache

docker run -d \
  --name jellyfin \
  --restart unless-stopped \
  --network host \
  -e PUID=0 -e PGID=0 \
  -e TZ=Europe/London \
  -e JELLYFIN_PublishedServerUrl=http://192.168.8.220:8096 \
  -v /opt/jellyfin/config:/config \
  -v /opt/jellyfin/cache:/cache \
  -v /media/movies:/media/movies \
  -v /media/tv:/media/tv \
  -v /media/music:/media/music:ro \
  lscr.io/linuxserver/jellyfin:latest

exit
```

Host networking for the same reason as Plex (8b): Jellyfin answers DLNA and client auto-discovery on `1900/udp` and `7359/udp`, and a bridge swallows both. `JELLYFIN_PublishedServerUrl` is the equivalent of Plex's `ADVERTISE_IP` — it pins what the server tells clients about itself.

**No `--device /dev/dri`.** On an Intel box that flag is what enables QuickSync and the whole point of the exercise; on a Pi 5 there is nothing behind it that Jellyfin can drive. Leave it off and leave hardware acceleration set to None in the UI — enabling VAAPI here produces failed streams, not faster ones.

Check it without a browser:

```bash
curl -s http://192.168.8.220:8096/System/Info/Public
# {"LocalAddress":"http://192.168.8.220:8096","ServerName":"jellyfin","Version":"12.1.0",
#  "ProductName":"Jellyfin Server","Id":"0cd3a371…","StartupWizardCompleted":false}
```

`StartupWizardCompleted: false` is Jellyfin's version of Audiobookshelf's `isInit: false` and Plex's `claimed="0"` — running, not yet set up. Open `http://192.168.8.220:8096` and run the wizard; unlike Plex there is no claim token and no same-subnet requirement.

Add `/media/movies` and `/media/tv` as libraries. For music, point the library at a specific subfolder — `/media/music/music_albums` — **not** at `/media/music`, which is the whole `files` tree and would pull in `audiobooks/`, `downloads/`, `icyscan/` and `_Serato_`. Same trap as 8c, with `downloads/` newly added to it.

> **What "no transcoding" actually means.** Direct play is a file read, and the Pi serves it happily — the CPU is irrelevant when the client can play the file as-is. A transcode only starts when the *client* can't: a codec it can't decode (HEVC 10-bit, AV1), audio it can't render (TrueHD, DTS-HD), a bitrate above the link, or image-based subtitles (PGS/VOBSUB) which must be burned in and force a full video transcode on their own. HDR→SDR tone mapping is the worst case. None of that is *caused* by 4K, but 4K hits all of it far more often — which is why 12f pins 1080p profiles, and why the fix on this box is always a client that direct-plays rather than more cores.

Best clients for direct play: Findroid (Android), Swiftfin or Infuse (Apple/tvOS), the native Android TV app, Jellyfin Media Player (desktop). The browser is the one to avoid, especially for anything above 1080p.

Finally, add a card for it to the `Home Server` landing page, same as 7c — `http://192.168.8.220:8096`.

---

## 14. CT 109 — Kodi (headless)

Unprivileged LXC, 2 cores / 1 GB, static 192.168.8.221, rootfs on the NVMe. Kodi 21.2 from Debian's own packages, no Docker, running as a systemd unit under a virtual X server. Web server and JSON-RPC on `:8080`.

Kodi is a player, not a server, and this box has nothing plugged into either HDMI port. It is here for one reason: its library and JSON-RPC API, so that clients which speak Kodi — Symfonium's Kodi provider, Yatse, Kore — can browse the library and stream from it without a Kodi device being switched on somewhere. It scans the same music folders Navidrome serves as libraries (15b) and the same `movies`/`tv` the media servers do (14f), read-only, and ends up with the same tracks and titles.

> **If the client is Symfonium, Navidrome is the better source.** Symfonium's Subsonic support is its most complete: offline sync, scrobbles, stars, the `nd-lyrics` lyrics and the multi-artist tag splits all come through. Kodi gives it a second copy of the same library with less of that. Run this for Kodi-specific clients, or when you want Kodi's library as such.

### 14a. The container

```bash
sudo pct create 109 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname kodi \
  --arch arm64 --ostype debian \
  --cores 2 --memory 1024 --swap 512 \
  --rootfs nvme:8 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.8.221/24,gw=192.168.8.1,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --unprivileged 1 \
  --onboot 1

sudo pct set 109 -mp0 /mnt/nvme/files,mp=/media/files,ro=1

sudo pct start 109
```

No `nesting=1` — as with CT 106, nothing in here runs Docker. The whole `files` tree is mounted, read-only, because the sources below mirror Navidrome's per-genre libraries and those are spread across it. Kodi never writes into a source; its database, thumbnails and settings live under the service user's home on the rootfs. The library files are owned by host uid 1000 and show up as `nobody` in here, which is fine: they are world-readable, and reading is all Kodi does.

### 14b. Kodi, Xvfb and a silent sound card

```bash
sudo pct enter 109

apt update && apt install -y kodi xvfb xauth curl sqlite3
useradd --system --create-home --home-dir /var/lib/kodi --shell /usr/sbin/nologin kodi

# Kodi insists on an audio device. There isn't one; give it ALSA's null sink.
printf 'pcm.!default { type null }\n' > /etc/asound.conf
```

Debian trixie ships Kodi 21.2 for arm64 — no PPA, no Flatpak, no image to track. linuxserver's `kodi-headless` image, the usual answer to this, is deprecated, so the distro package is the least fragile way to run it.

**Why Xvfb.** Kodi has no headless mode: the Debian build knows `x11`, `wayland` and `gbm`, and all three want a display. GBM would need `/dev/dri` passed into the container. Xvfb is an X server that renders into memory, costs ~55 MB, and Kodi cannot tell it from a real one.

**Why the null ALSA device.** Without a sound card Kodi does not give up on audio, it retries — `CActiveAESink::OpenSink - no sink was returned` twice a second, forever, into `kodi.log`. Setting `audiooutput.audiodevice` to `NULL` in `guisettings.xml` does not stop it. A default ALSA PCM of `type null` does: Kodi opens it on the first attempt and the log goes quiet.

### 14c. Settings and sources, seeded before first start

Kodi reads `userdata/` on start, so the web server, its password and the music sources can all be written before it ever runs — no GUI needed at any point.

```bash
U=/var/lib/kodi/.kodi/userdata
mkdir -p $U
PW=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)
echo "$PW" > /root/kodi-webserver-password && chmod 600 /root/kodi-webserver-password

cat > $U/guisettings.xml <<EOF
<settings version="2">
    <setting id="services.webserver">true</setting>
    <setting id="services.webserverport">8080</setting>
    <setting id="services.webserverauthentication">true</setting>
    <setting id="services.webserverusername">kodi</setting>
    <setting id="services.webserverpassword">$PW</setting>
    <setting id="services.esenabled">true</setting>
    <setting id="services.esallinterfaces">true</setting>
    <setting id="services.zeroconf">false</setting>
    <setting id="musiclibrary.updateonstartup">true</setting>
    <setting id="musiclibrary.backgroundupdate">true</setting>
    <setting id="audiooutput.audiodevice">ALSA:default</setting>
</settings>
EOF

cat > $U/advancedsettings.xml <<'EOF'
<advancedsettings version="1.0">
    <splash>false</splash>
    <gui>
        <!-- Only redraw what changed. Nobody is looking at this GUI; without it
             Kodi software-renders full frames into Xvfb for nothing. -->
        <algorithmdirtyregions>3</algorithmdirtyregions>
    </gui>
    <musiclibrary>
        <artistseparators>
            <separator>;</separator><separator> / </separator><separator> feat. </separator><separator> ft. </separator>
        </artistseparators>
    </musiclibrary>
</advancedsettings>
EOF

# One source per Navidrome library (15b) - same folders, same exclusions.
{
  echo '<sources>'
  echo '    <music>'
  echo '        <default pathversion="1"></default>'
  for d in music music_dancehall_reggae music_kenyan music_nwa music_nwa_ music_soul_r_n_b \
           music_urban music_oldies music_eurodance music_albums music_now \
           music_african/AFROEXTENDED music_african/AfrobeatsCentral music_afrohouse/AfrohouseCentral; do
    echo "        <source><name>$(basename $d)</name><path pathversion=\"1\">/media/files/$d/</path><allowsharing>true</allowsharing></source>"
  done
  echo '    </music>'
  echo '</sources>'
} > $U/sources.xml

chown -R kodi:kodi /var/lib/kodi
```

`webserverauthentication` matters. Kodi's JSON-RPC is not read-only: it can play, stop, delete library entries, change settings and quit the app, so it gets a password even on a LAN. Symfonium, Yatse and Kore all take one. The `es*` settings are the EventServer (`9777/udp`), which remotes use for button presses; `esallinterfaces` is Kodi's *Allow remote control from applications on other systems*.

The sources list is Navidrome's library table (15b), not the whole `files` tree, for the same reasons given there: `music_nwa_backup` and the other folders outside it would come in as duplicates, and `audiobooks/`, `downloads/`, `icyscan/` and `_Serato_` have no business in a music library. macOS `._*` files over the share need no exclusion — Kodi treats dotfiles as hidden and never scans them.

The artist separators split `Artist A; Artist B` and `feat.` credits into separate artists, the Kodi counterpart of Navidrome's multi-artist tag splitting.

### 14d. The unit

```bash
cat > /etc/systemd/system/kodi.service <<'EOF'
[Unit]
Description=Kodi (headless, library + JSON-RPC for remote clients)
After=network-online.target
Wants=network-online.target

[Service]
User=kodi
Group=kodi
WorkingDirectory=/var/lib/kodi
Environment=HOME=/var/lib/kodi LANG=C.UTF-8 KODI_AE_SINK=ALSA
# No display on this box: Xvfb gives Kodi a virtual X server to render its GUI into.
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 640x480x24 -nolisten tcp" /usr/bin/kodi --windowing=x11
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now kodi
```

A small helper, since every check from here on is a JSON-RPC call:

```bash
cat > /usr/local/bin/kodi-rpc <<'EOF'
#!/bin/sh
# kodi-rpc <Method> ['<params json>'] - call Kodi's JSON-RPC as the web server user.
exec curl -s -u "kodi:$(cat /root/kodi-webserver-password)" -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":${2:-{\}}}" http://localhost:8080/jsonrpc
EOF
chmod 700 /usr/local/bin/kodi-rpc

kodi-rpc Application.GetProperties '{"properties":["version"]}'
# {"id":1,"jsonrpc":"2.0","result":{"version":{"major":21,"minor":2,...,"tag":"stable"}}}
```

`KODI_AE_SINK=ALSA` pins the audio backend so Kodi does not probe PipeWire and PulseAudio first; it logs that the variable is deprecated in favour of `--audio-backend`, and the `/usr/bin/kodi` wrapper translates it into exactly that. `LANG=C.UTF-8` because the container has no locales generated, and a UTF-8 locale keeps the non-ASCII album and artist names intact.

### 14e. The first scan — one source at a time

**On an empty library, *Update library* does nothing.** `AudioLibrary.Scan` with no directory — which is also what `updateonstartup` runs — does not walk the sources. It rescans the folders that already have songs in the database, and on a fresh install there are none, so it logs `operation took 0s` and returns having found nothing. No error, just an empty library. The first import has to name each source:

```bash
for p in $(sqlite3 /var/lib/kodi/.kodi/userdata/Database/MyMusic83.db \
             "select replace(strPath,' ','%20') from source_path"); do
  p=${p//%20/ }
  kodi-rpc AudioLibrary.Scan "{\"directory\":\"$p\"}" >/dev/null
  sleep 3
  until kodi-rpc XBMC.GetInfoBooleans '{"booleans":["Library.IsScanningMusic"]}' | grep -q :false; do sleep 5; done
  echo "$p done"
done

kodi-rpc AudioLibrary.GetSongs '{"limits":{"end":1}}' | grep -o '"total":[0-9]*'
# "total":13753
```

Kodi only runs one scan at a time, hence the wait between sources. The whole library took a little over two minutes. After that, the plain no-argument scan on each start does pick up new albums, because every source root is now a known path whose contents it can compare.

The count lands within a handful of tracks of Navidrome's across all 14 libraries — the way to check that nothing was missed:

```bash
# on the host
sudo sqlite3 /var/lib/navidrome/navidrome.db \
  "select l.path, count(*) from media_file m join library l on l.id=m.library_id
   where m.missing=0 group by l.path order by l.path;"
```

At idle it sits around 1% CPU and ~310 MB resident, Xvfb included. That is `algorithmdirtyregions` doing its job — Kodi's home screen is static, so nothing gets redrawn.

### 14f. Movies and TV

The same `/media/files` mount already covers `movies/` and `tv/` — the directories Plex, Emby and Jellyfin scan, and where Radarr and Sonarr import to (12). Kodi's scrapers for both, TMDb's, ship in `kodi-data`, so nothing needs installing.

A video source is two things in Kodi: an entry in `sources.xml`, and a row in the video database's `path` table saying what the folder holds and which scraper to use. The second is what *Set content* writes in the GUI, and there is no settings file for it. With Kodi stopped, write both:

```bash
systemctl stop kodi
U=/var/lib/kodi/.kodi/userdata

python3 - <<'P'
p = "/var/lib/kodi/.kodi/userdata/sources.xml"
s = open(p).read()
video = """    <video>
        <default pathversion="1"></default>
        <source><name>Movies</name><path pathversion="1">/media/files/movies/</path><allowsharing>true</allowsharing></source>
        <source><name>TV Shows</name><path pathversion="1">/media/files/tv/</path><allowsharing>true</allowsharing></source>
    </video>
"""
open(p, "w").write(s.replace("<sources>\n", "<sources>\n" + video, 1))
P

sqlite3 $U/Database/MyVideos131.db "
insert into path (strPath,strContent,strScraper,scanRecursive,useFolderNames,strSettings,noUpdate,exclude,allAudio)
values ('/media/files/movies/','movies','metadata.themoviedb.org.python',2147483647,1,'',0,0,0),
       ('/media/files/tv/','tvshows','metadata.tvshows.themoviedb.org.python',0,0,'',0,0,0);"

chown -R kodi:kodi /var/lib/kodi
systemctl start kodi
kodi-rpc VideoLibrary.Scan
```

`useFolderNames=1` for movies because Radarr's folder names (`Mayday (2026)`) are clean title-and-year, where the release filenames inside them (`Mayday 2026 2160p ATVP WEB-DL DDP5.1 Atmos DV HDR H265-FLUX.mkv`) are not. For TV each subfolder of the source is one show, and Sonarr's `Season N/…S01E01…` naming is what the episode matcher expects. An empty `strSettings` means scraper defaults: English, TMDb artwork. Change them in the GUI's *Set content* if you need to — it rewrites that column.

The video database version (`MyVideos131`) is tied to the Kodi release; after a major upgrade check `ls $U/Database` for the new name. Unlike music, the no-argument `VideoLibrary.Scan` does walk every source with content set, so there is no per-source loop here.

```bash
kodi-rpc VideoLibrary.GetMovies '{"properties":["year"]}'
# "Avatar Aang: The Last Airbender" 2026, "Mayday" 2026
kodi-rpc VideoLibrary.GetTVShows '{"properties":["year","episode"]}'
# "Lioness" 2023 (1 ep), "The Polygamist" 2026 (4 eps)
```

**New imports don't appear on their own.** `videolibrary.updateonstartup` is off by default, and nothing tells Kodi when Radarr or Sonarr drop a file. So both of them get a *Kodi* connection (*Settings → Connect → + → Kodi*):

| Field | Value |
|---|---|
| Name | `Kodi (CT 109)` |
| Host / Port | `192.168.8.221` / `8080`, URL base `/jsonrpc` |
| Username / Password | `kodi` / the contents of `/root/kodi-webserver-password` in CT 109 |
| GUI Notification | off (there's no screen) |
| Update Library | on |
| Clean Library | on (deletes and upgrades drop the stale entry) |
| Always Update | on (skip the "is something playing?" check) |
| Triggers, Sonarr | On Import, On Upgrade, On Rename, On Series Delete, On Episode File Delete |
| Triggers, Radarr | On Import, On Upgrade, On Rename, On Movie Delete, On Movie File Delete |

*Test* must pass before *Save*. The *arrs see the library as `/data/...` and Kodi as `/media/files/...`, and that mismatch doesn't matter: for a show or movie Kodi already has, they look it up in Kodi's library (by id, or title for shows) and scan Kodi's own path for it, and for anything new they fall back to a full `VideoLibrary.Scan`.

Sonarr's *On Import Complete* is left off on purpose. It fires once per batch on top of *On Import* per episode, so a season pack would trigger two scans.

### 14g. Pointing clients at it

From anywhere on the LAN or the tailnet:

```bash
PW=<the password from /root/kodi-webserver-password in CT 109>
curl -s -u "kodi:$PW" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"JSONRPC.Version"}' http://192.168.8.221:8080/jsonrpc
# {"id":1,"jsonrpc":"2.0","result":{"version":{"major":13,"minor":5,"patch":0}}}
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.8.221:8080/jsonrpc   # 401 - no password, no access
```

**Symfonium:** *Add media provider → Kodi*, host `192.168.8.221`, port `8080`, user `kodi`, the password above. Symfonium reads the library over JSON-RPC and streams each file through Kodi's web server (`Files.PrepareDownload` → a `/vfs/` URL), so the phone never needs the SMB share. Yatse and Kore take the same four values, and are the better fit for movies and TV — Symfonium is built around music.

Streaming is a plain file read with no transcoding, the same as direct play on the media servers.

Finally, it has a card on the dashboard: an `lxc` entry in hqdash's `TARGETS` (19) pointing at `http://192.168.8.221:8080`.

---

## 15. Navidrome

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
MusicFolder = "/var/lib/navidrome/no-music"   # empty on purpose — see 15b
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
- `MusicFolder` points at an empty directory on purpose — libraries live in the DB, not the config. Pointing it at `/mnt/nvme` is what caused the duplicate-indexing problem in 7b, so don't put it back.
- `/opt/navidrome/music` exists, owned by `navidrome` — vestigial from an earlier install-script attempt. Harmless, and not needed on a rebuild.

### 15a. Lyrics plugin (`nd-lyrics`)

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

### 15b. Libraries — never let two overlap

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

## 16. ICY radio metadata loggers

Two systemd services scrape ICY stream metadata and append every track change to a `.txt` on the share.

### 16a. The script

The repo is your fork, which carries a fix (`Fix permanent desync when metadata straddles a chunk boundary`) not in upstream:

```bash
sudo mkdir -p /opt/icy-meta
sudo chown youruser:youruser /opt/icy-meta
git clone git@github.com:laurentjuma/icy-meta.git /opt/icy-meta
cd /opt/icy-meta
git remote add upstream https://github.com/lucvanbraekel/icy-meta.git
```

Needs `python3-requests` (installed in section 1).

### 16b. Units

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

> **90s90s is not on ICY.** An `icyscan-90shiphop.service` briefly existed and was removed on 2026-08-08. The 90s90s streams do send `icy-metaint: 8192`, so the scanner connects and looks healthy, but `StreamTitle` is the static station name (`90s90s - HipHop`) rather than per-track data — same on the `mp3-192`, `mp3-128` and `aac-64` variants. **A valid `icy-metaint` header is not evidence a station publishes track metadata; check that `StreamTitle` actually changes before adding any station.** 90s90s is handled by section 16c instead.

**Why awk owns the file and not systemd:** `StandardOutput=append:` resolves the path once at start and binds to the *inode*. Finder replacing the file over SMB (unlink-then-create) left the service writing into an orphaned inode while the visible file stayed at 0 bytes — silently, nothing in the journal. `close(LOG)` after each line forces a path re-resolve, so a deleted log comes back within one song. **Any new station unit must use this pattern.**

Two known, expected behaviours: the script reconnects to the stream roughly once per second (no poll-interval flag upstream — patch locally if a station ever rate-limits you), and dedup state is in-memory so a restart re-logs the currently playing track.

Logs land at `smb://192.168.8.191/mbogiservershare/icyscan/`.

### 16c. 90s90s — `flowscan`, polling the iris feed

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

## 17. Tailscale remote access

The LAN sits behind a GL.iNet router whose IPv4 egress is an M247 commercial-VPN range, on top of likely CGNAT — **there is no inbound path**, so port forwarding and DDNS are impossible. An outbound tunnel is the only option.

### 17a. Install and join

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

Bring it up as a **subnet router** advertising the whole LAN:

```bash
sudo tailscale up --advertise-routes=192.168.8.0/24
```

Follow the printed URL and log in as `your-tailscale-account`. Then in the Tailscale admin console → Machines → `raspberrypi` → **approve the 192.168.8.0/24 subnet route**. It won't work until you do.

Result: tailnet IP `YOUR_TAILNET_IP`, MagicDNS `raspberrypi.your-tailnet.ts.net`.

### 17b. Persist IP forwarding

```bash
printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' \
  | sudo tee /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
```

### 17c. UDP GRO tuning on the bridge

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

### 17d. Using it

Reach services at the tailnet IP instead of the LAN IP — plain HTTP is fine, WireGuard already encrypts it:

- Navidrome — `http://YOUR_TAILNET_IP:4533`
- Proxmox — `https://YOUR_TAILNET_IP:8006`
- SSH — `ssh youruser@YOUR_TAILNET_IP`
- Music Assistant — `http://192.168.8.213:8095` (via the subnet route)
- Plex — `http://192.168.8.215:32400/web` (via the subnet route)
- Emby — `http://192.168.8.216:8096` (via the subnet route)
- WebDAV — `http://YOUR_TAILNET_IP:8081`, read-only (3a)

`tailscale netcheck` reports `MappingVariesByDestIP: true` (symmetric NAT), so connections often relay through the London DERP rather than going peer-to-peer. Fine for audio; expect less headroom for 4K video.

---

## 18. Verification checklist

```bash
# storage
df -h /mnt/nvme                                    # ~916G, ext4

# services
systemctl is-active navidrome icyscan-afrobeats icyscan-afrohouse flowscan@265 tailscaled smbd webdav

# navidrome + plugin
/usr/bin/navidrome --version                       # 0.63.2
ls /var/lib/navidrome/plugins                      # nd-lyrics.ndp

# containers
sudo pct list                                      # 100-109; 100 and 103 are stopped by choice
curl -s http://192.168.8.214:13378/status          # audiobookshelf, serverVersion 2.36.0
curl -s http://192.168.8.215:32400/identity        # plex, version 1.43.3.10896
curl -s http://192.168.8.216:8096/System/Info/Public   # emby, Version 4.9.5.0
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.8.217:8080/   # gopodder, 303 -> /setup
curl -s http://192.168.8.218:8080/api/auth/status      # m3ugoat, {"enabled":true,...}
curl -s http://192.168.8.220:8096/System/Info/Public    # jellyfin, Version 12.1.0
sudo pct exec 109 -- /usr/local/bin/kodi-rpc AudioLibrary.GetSongs '{"limits":{"end":1}}'   # kodi, "total":13753
sudo pct exec 107 -- docker compose -f /opt/arr/docker-compose.yml ps   # 6 services Up

# *arr stack — the two things that actually break
sudo pct exec 107 -- docker exec sonarr sh -c '
  echo p > /data/downloads/complete/.probe
  ln /data/downloads/complete/.probe /data/movies/.probe && echo HARDLINKS_OK
  rm -f /data/downloads/complete/.probe /data/movies/.probe'
curl -s -H "X-Api-Key: $SONARR_KEY" http://192.168.8.219:8989/api/v3/rootfolder
#   [{"path":"/data/tv","accessible":true,...}]   <- accessible:true is the ACL working

# egress is still inside the ProtonVPN tunnel
curl -s https://ipinfo.io/json | grep -E '"(ip|org)"'
#   "org": "AS9009 M247 Europe SRL"   <- NOT the ISP

# listening ports
sudo ss -tlnp | grep -E ':(22|80|445|4533|8006|32400)'

# radio logs growing
tail -3 /mnt/nvme/files/icyscan/afrobeats.txt
tail -3 /mnt/nvme/files/icyscan/afrohouse.txt

# tailscale
tailscale status
tailscale ip -4                                    # YOUR_TAILNET_IP
```

Then from the Mac: `smb://192.168.8.191/mbogiservershare`, `http://192.168.8.191:4533`, `http://192.168.8.214:13378`, `http://192.168.8.215:32400/web`, `http://192.168.8.216:8096`, `http://192.168.8.217:8080`, `http://192.168.8.218:8080`, `http://192.168.8.220:8096`, `http://192.168.8.221:8080` (Kodi, password-protected), `https://192.168.8.191:8006` — plus the *arr stack on `192.168.8.219`: qBittorrent `:8080`, Prowlarr `:9696`, Sonarr `:8989`, Radarr `:7878`, Bazarr `:6767`.

---

## 19. Things worth knowing before you rebuild

- **Back up first.** The music library under `/mnt/nvme/files` and the icyscan `.txt` history are the only irreplaceable data. Also grab `/etc/navidrome/navidrome.toml` (Last.fm keys), `/etc/pve/lxc/*.conf`, `/var/azuracast/.env`, and `/var/lib/navidrome/` (playlists, play counts, users).
- **Don't restart Navidrome mid-migration.** A version jump applies schema migrations on first start, and the FTS5 search index alone takes ~13 s on this library. Restarting during that aborts the running transaction (`level=fatal ... failed to begin transaction: context canceled`); the next start does resume at the interrupted migration and finish the rest, but wait for `Navidrome server is ready!` before touching the service.
- **No PVE backup jobs are configured.** `/etc/pve/jobs.cfg` is empty and both `dump/` directories are empty — nothing is being backed up automatically. Worth adding a vzdump job to `nvme` storage if you care about the containers.
- **`zfsutils-linux` is installed** (pulled in by PXVIRT) but no pool exists and the module isn't loaded. Ignore it.
- **nginx on :80 serves the dashboard** — `/var/www/html/index.html`, a live status grid that polls `/api/status` every 5 seconds. That API is `hqdash`, a stdlib-Python service at `/opt/hqdash/hqdash.py` bound to `127.0.0.1:8787`; nginx proxies `/api/` to it and is what keeps it off the LAN. Every service it can see or control is an entry in the `TARGETS` dict at the top of that file, so **adding a container means editing `TARGETS` and `sudo systemctl restart hqdash`** — a target is an LXC (`kind: lxc`), a host systemd unit (`kind: unit`), or, since CT 107, a single Docker service inside an LXC (`kind: docker`, with `vmid` + `container`), which is how the six *arr services get their own cards instead of one — the page itself is entirely data-driven and needs no edit. `/opt/hqdash/README.md` documents it. It replaced a hand-written grid of static links, kept as `index.html.bak-pre-hqdash-20260901`; the stock Debian page is still there as `index.nginx-debian.html`.
- **The dashboard has no authentication.** Anyone on the LAN or the tailnet can start, stop and restart every service on the box, and reboot the Pi. That is the same trust boundary as the Proxmox UI on :8006 and it holds only because :80 is not forwarded and the line is behind CGNAT. Put auth in front of it before exposing it to anything.
- **`samba-ad-dc.service` is enabled** but the server is a standalone file server. Harmless.
- **`postfix` is running** on localhost only, for PVE's mail notifications.
- **Hardlinks are the whole design of section 12, and they fail silently.** If the *arrs and the download client don't see the library through one shared mount, imports become copies — no error, just double the disk and a rewritten file every time. The probe in 12a takes five seconds; run it after any change to CT 107's mountpoints.
- **Recyclarr's Docker tags are a trap.** There is no `:latest`, and `:7` — the current *stable* release — cannot parse the templates its own `config create` writes, because upstream's template repo has moved to the v8 schema. Pin `:edge` until v8 ships stable. The symptom is `Exception at line 17` and nothing more useful. Full story in 12f.
- **qBittorrent is not behind gluetun**, and that is a deliberate call (12g): the GL.iNet router already tunnels the whole subnet over ProtonVPN. It relies on *Block Non-VPN Traffic* being enabled on the router — without it, a dropped tunnel fails open. Re-check that toggle after any router firmware update, and re-run the `ipinfo.io` check in 18.
- **Kodi's first library scan must name each source.** *Update library* — and `updateonstartup` — only rescans folders already holding songs, so on a fresh `userdata/` it finishes in 0 s having found nothing, with no error. Run the per-source loop in 14e after any rebuild of CT 109. The web server password lives only in `/root/kodi-webserver-password` inside the container and in `guisettings.xml`; Symfonium stops connecting if it changes.
- **Order matters** in one place: PXVIRT (section 4) must come after the `vmbr0` bridge exists, and the containers (5–14) after PXVIRT. Everything else is independent.
