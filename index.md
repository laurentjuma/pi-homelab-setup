---
title: Raspberry Pi 5 — Full Rebuild Guide
description: Rebuilding a Pi 5 homelab from a blank SD card — NVMe/LVM, Samba, Proxmox on ARM64, LXC, Navidrome, Audiobookshelf, Plex, Emby, radio track logging, Tailscale.
---

Everything running on a Raspberry Pi 5 (`raspberrypi`, 192.168.8.191), in the order you'd need to rebuild it from a blank SD card. Each section is standalone — skip any service you don't want.

**Captured:** 2026-08-08 from the live machine. **Updated:** 2026-08-28 — Emby in CT 104 (section 9), Plex in CT 103 (section 8). **2026-08-24** — Audiobookshelf in CT 102 (section 7). **2026-08-22** — Navidrome 0.63.2 + lyrics plugin (10a), whole-disk library removed (10b).

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
| CT 102 | Audiobookshelf (Docker in LXC) | 192.168.8.214, web UI `:13378` |
| CT 103 | Plex (Docker in LXC) | 192.168.8.215, web UI `:32400` |
| CT 104 | Emby (Docker in LXC) | 192.168.8.216, web UI `:8096` |
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

`/mnt/nvme/files` holds the music library (`music`, `music_african`, `music_kenyan`, `music_urban`, `music_oldies`, `music_soul_r_n_b`, `music_dancehall_reggae`, `music_eurodance`, `music_nwa`, `_Serato_`, …) plus `icyscan/`, and — from sections 7a and 8a — `audiobooks/`, `podcasts/`, `movies/` and `tv/`. PVE later adds `dump/ images/ private/ snippets/ template/` at `/mnt/nvme` — leave those alone.

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

> **Gotcha to remember:** anything systemd writes here with `StandardOutput=append:` is created as root even when the unit has `User=`, so it won't be writable over the share. See section 11 — that's why the icyscan units don't use `append:`.

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

## 10. Navidrome

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
MusicFolder = "/var/lib/navidrome/no-music"   # empty on purpose — see 10b
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

### 10a. Lyrics plugin (`nd-lyrics`)

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

### 10b. Libraries — never let two overlap

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

## 11. ICY radio metadata loggers

Two systemd services scrape ICY stream metadata and append every track change to a `.txt` on the share.

### 11a. The script

The repo is your fork, which carries a fix (`Fix permanent desync when metadata straddles a chunk boundary`) not in upstream:

```bash
sudo mkdir -p /opt/icy-meta
sudo chown youruser:youruser /opt/icy-meta
git clone git@github.com:laurentjuma/icy-meta.git /opt/icy-meta
cd /opt/icy-meta
git remote add upstream https://github.com/lucvanbraekel/icy-meta.git
```

Needs `python3-requests` (installed in section 1).

### 11b. Units

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

> **90s90s is not on ICY.** An `icyscan-90shiphop.service` briefly existed and was removed on 2026-08-08. The 90s90s streams do send `icy-metaint: 8192`, so the scanner connects and looks healthy, but `StreamTitle` is the static station name (`90s90s - HipHop`) rather than per-track data — same on the `mp3-192`, `mp3-128` and `aac-64` variants. **A valid `icy-metaint` header is not evidence a station publishes track metadata; check that `StreamTitle` actually changes before adding any station.** 90s90s is handled by section 11c instead.

**Why awk owns the file and not systemd:** `StandardOutput=append:` resolves the path once at start and binds to the *inode*. Finder replacing the file over SMB (unlink-then-create) left the service writing into an orphaned inode while the visible file stayed at 0 bytes — silently, nothing in the journal. `close(LOG)` after each line forces a path re-resolve, so a deleted log comes back within one song. **Any new station unit must use this pattern.**

Two known, expected behaviours: the script reconnects to the stream roughly once per second (no poll-interval flag upstream — patch locally if a station ever rate-limits you), and dedup state is in-memory so a restart re-logs the currently playing track.

Logs land at `smb://192.168.8.191/mbogiservershare/icyscan/`.

### 11c. 90s90s — `flowscan`, polling the iris feed

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

## 12. Tailscale remote access

The LAN sits behind a GL.iNet router whose IPv4 egress is an M247 commercial-VPN range, on top of likely CGNAT — **there is no inbound path**, so port forwarding and DDNS are impossible. An outbound tunnel is the only option.

### 12a. Install and join

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

Bring it up as a **subnet router** advertising the whole LAN:

```bash
sudo tailscale up --advertise-routes=192.168.8.0/24
```

Follow the printed URL and log in as `your-tailscale-account`. Then in the Tailscale admin console → Machines → `raspberrypi` → **approve the 192.168.8.0/24 subnet route**. It won't work until you do.

Result: tailnet IP `YOUR_TAILNET_IP`, MagicDNS `raspberrypi.your-tailnet.ts.net`.

### 12b. Persist IP forwarding

```bash
printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' \
  | sudo tee /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
```

### 12c. UDP GRO tuning on the bridge

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

### 12d. Using it

Reach services at the tailnet IP instead of the LAN IP — plain HTTP is fine, WireGuard already encrypts it:

- Navidrome — `http://YOUR_TAILNET_IP:4533`
- Proxmox — `https://YOUR_TAILNET_IP:8006`
- SSH — `ssh youruser@YOUR_TAILNET_IP`
- Music Assistant — `http://192.168.8.213:8095` (via the subnet route)
- Plex — `http://192.168.8.215:32400/web` (via the subnet route)
- Emby — `http://192.168.8.216:8096` (via the subnet route)

`tailscale netcheck` reports `MappingVariesByDestIP: true` (symmetric NAT), so connections often relay through the London DERP rather than going peer-to-peer. Fine for audio; expect less headroom for 4K video.

---

## 13. Verification checklist

```bash
# storage
df -h /mnt/nvme                                    # ~916G, ext4

# services
systemctl is-active navidrome icyscan-afrobeats icyscan-afrohouse flowscan@265 tailscaled smbd

# navidrome + plugin
/usr/bin/navidrome --version                       # 0.63.2
ls /var/lib/navidrome/plugins                      # nd-lyrics.ndp

# containers
sudo pct list                                      # 100 + 101 + 102 + 103 + 104 running
curl -s http://192.168.8.214:13378/status          # audiobookshelf, serverVersion 2.36.0
curl -s http://192.168.8.215:32400/identity        # plex, version 1.43.3.10896
curl -s http://192.168.8.216:8096/System/Info/Public   # emby, Version 4.9.5.0

# listening ports
sudo ss -tlnp | grep -E ':(22|80|445|4533|8006|32400)'

# radio logs growing
tail -3 /mnt/nvme/files/icyscan/afrobeats.txt
tail -3 /mnt/nvme/files/icyscan/afrohouse.txt

# tailscale
tailscale status
tailscale ip -4                                    # YOUR_TAILNET_IP
```

Then from the Mac: `smb://192.168.8.191/mbogiservershare`, `http://192.168.8.191:4533`, `http://192.168.8.214:13378`, `http://192.168.8.215:32400/web`, `http://192.168.8.216:8096`, `https://192.168.8.191:8006`.

---

## 14. Things worth knowing before you rebuild

- **Back up first.** The music library under `/mnt/nvme/files` and the icyscan `.txt` history are the only irreplaceable data. Also grab `/etc/navidrome/navidrome.toml` (Last.fm keys), `/etc/pve/lxc/*.conf`, `/var/azuracast/.env`, and `/var/lib/navidrome/` (playlists, play counts, users).
- **Don't restart Navidrome mid-migration.** A version jump applies schema migrations on first start, and the FTS5 search index alone takes ~13 s on this library. Restarting during that aborts the running transaction (`level=fatal ... failed to begin transaction: context canceled`); the next start does resume at the interrupted migration and finish the rest, but wait for `Navidrome server is ready!` before touching the service.
- **No PVE backup jobs are configured.** `/etc/pve/jobs.cfg` is empty and both `dump/` directories are empty — nothing is being backed up automatically. Worth adding a vzdump job to `nvme` storage if you care about the containers.
- **`zfsutils-linux` is installed** (pulled in by PXVIRT) but no pool exists and the module isn't loaded. Ignore it.
- **nginx on :80 serves a hand-written "Home Server" landing page** (`/var/www/html/index.html`) — a dark card grid linking out to Music Assistant, Navidrome, Audiobookshelf, Plex and Emby. Nothing is *proxied* through nginx; the cards are plain absolute links to each service's own host and port, so adding a service means adding an `<a class="card">` block by hand. The stock Debian page is still there as `index.nginx-debian.html`.
- **`samba-ad-dc.service` is enabled** but the server is a standalone file server. Harmless.
- **`postfix` is running** on localhost only, for PVE's mail notifications.
- **Order matters** in one place: PXVIRT (section 4) must come after the `vmbr0` bridge exists, and the containers (5–9) after PXVIRT. Everything else is independent.
