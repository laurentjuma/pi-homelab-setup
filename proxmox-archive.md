---
title: Proxmox era (archived)
description: How the Pi ran PXVIRT (Proxmox VE 9 for ARM64) and its LXC containers, August to October 2026. Retired 2026-10-09.
---

[← Back to the rebuild guide](./)

> **Archived 2026-10-09. Nothing here is running any more.** PXVIRT was purged from the Pi, every container was destroyed, and their disk images in `/mnt/nvme/images` and the `vzdump` backups in `/mnt/nvme/dump` were deleted as well — none of these containers can be restored. Kept as a record of how they were built.
>
> The Mediagg Arr Stack that lived in CT 110 now runs in Docker straight on the host (section 4 of the [main guide](./)). Sections 0–3 are unchanged in the main guide; references below to sections 11–15 are to the guide as it was then, and those are now: 11 → 5, 12 → 6, 13 → 7, 14 → 8, 15 → 9.
>
> Containers destroyed earlier, on 2026-10-04 — Audiobookshelf (CT 102), Plex (103), Emby (104), the hand-built *arr stack (107) and Jellyfin (108) — are documented in this repo's history up to commit `9053c8f`. `arr-wire.py` and `seerr-wire.py` belong to CT 107.

| CT | What | Address | Notes |
|---|---|---|---|
| — | PXVIRT (Proxmox VE 9.0 ARM64 port) | web UI `192.168.8.191:8006` | LAN IP on the `vmbr0` bridge |
| 100 | Music Assistant (Docker in LXC) | 192.168.8.213:8095 | stopped by choice |
| 101 | AzuraCast (Docker in LXC) | 192.168.8.192 | installed, never in use |
| 105 | goPodder (Docker in LXC) | 192.168.8.217:8080 | gpodder.net-compatible podcast sync |
| 106 | m3ugoat (Node + systemd in LXC) | 192.168.8.218:8080 | IPTV playlist/EPG manager |
| 109 | Kodi, headless (Xvfb + systemd in LXC) | 192.168.8.221:8080 | JSON-RPC for Symfonium/Yatse/Kore |
| 110 | Mediagg Arr Stack (Docker in LXC) | 192.168.8.187:7979 | moved to the host 2026-10-08 |

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

CT 100 and 101 use that one. Every container from CT 105 on is built from Debian 13 instead:

```bash
sudo pveam download local debian-13-standard_13.6-1_arm64.tar.zst
```

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

## 7. CT 105 — goPodder

Unprivileged LXC, 1 core / 512 MB, static 192.168.8.217, rootfs on the NVMe, Docker inside. Web UI on `:8080`.

[goPodder](https://github.com/cbrgm/gopodder) is a gpodder.net-compatible **sync** server — subscriptions, episode progress and device registrations, kept in step across podcast apps. It is not a media server and not a feed reader: it never fetches a feed and makes no outbound connections at all, so unlike every other container here it needs no view of the library and touches nothing under `/mnt/nvme`. AntennaPod on the phone and gPodder on the desktop both point at it and share one state.

### 7a. The container

The Debian 13 template from 4d, sized small. The whole server is one Go binary:

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

### 7b. goPodder itself

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

Published ports rather than `--network host` — nothing here depends on broadcast or DLNA discovery, so the container keeps its own netns and exposes exactly one port.

**`--db-path /data/gopodder.db` is the entire persistence story.** Left off, the image writes `gopodder.db` into its own working directory inside the writable layer: it survives restarts, looks completely fine, and disappears the first time the container is recreated to pick up a new tag. Point it at the bind mount or you are running a database that your next upgrade throws away.

Check it without a browser:

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://192.168.8.217:8080/
# 303 http://192.168.8.217:8080/setup

sudo pct exec 105 -- docker logs gopodder | tail -2
# level=INFO msg=goPodder version=1.2.5 revision=6d043cf go=go1.27.0 platform=linux/arm64
# level=INFO msg="starting server" addr=0.0.0.0:8080
```

> **The first browser to reach `/setup` creates the admin account**, and nothing guards it. Open `http://192.168.8.217:8080` and finish setup now rather than later.

Then create a goPodder **user** on the Users tab — a separate thing from the admin login — and give those credentials to the podcast apps, not the admin ones.

Backups are a file copy; it runs in WAL mode, so it is safe while the server is up:

```bash
sudo pct exec 105 -- cp /opt/gopodder/data/gopodder.db /tmp/gopodder-$(date +%F).db
```

### 7c. Pointing the apps at it, and the HTTPS catch

In the app this is the "gpodder.net sync" / "Synchronize subscriptions" setting with a custom server — `http://192.168.8.217:8080`, then the goPodder user's credentials.

**AntennaPod requires HTTPS and goPodder does not terminate TLS**, so plain `http://` will not do for the phone. Desktop gPodder and Cardo accept HTTP and work as-is. The cheap fix for AntennaPod is the tailnet (section 13) — `tailscale cert` plus `tailscale serve` on the host gives a real certificate on a MagicDNS name with no port forwarding; nginx on :80 could also front it, but then you are minting certificates for a LAN name. Either way, do not reach for Funnel unless the phone genuinely has to sync from outside the tailnet: that publishes the login page to the internet.

Finally, add a card for it to the dashboard — an entry in `TARGETS` in `/opt/hqdash/hqdash.py`, then `sudo systemctl restart hqdash`.

---

## 8. CT 106 — m3ugoat

Unprivileged LXC, 2 cores / 2 GB, static 192.168.8.218, rootfs on the NVMe. Web UI on `:8080`.

[m3ugoat](https://github.com/m3ugoat/m3ugoat-playlist-manager) is a self-hosted IPTV playlist and EPG manager — a fork of [m3u4me](https://github.com/andrei-savin/m3u4me) adding multi-user accounts, per-device sign-in and a documented sync API. It manages M3U playlists; it serves no streams and you bring your own content.

**This is the one container here that does not run Docker.** There is no published image, and the app is a Node server plus a static frontend — wrapping that in a hand-rolled Dockerfile would add an image to rebuild on every update and buy nothing. It runs as a plain systemd unit instead, the same way Navidrome does on the host.

> `:8080` again, the same port goPodder uses. Not a clash — each container has its own IP and its own network namespace, so `192.168.8.217:8080` and `192.168.8.218:8080` are unrelated. Nothing is published to the host.

### 8a. The container

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

### 8b. Node 24

The app needs **Node 24+** — it uses Node's built-in SQLite, and runs `server.ts` through Node's native TypeScript support with no build step for the server. Debian 13 ships 20.19, so this comes from NodeSource:

```bash
sudo pct enter 106

apt update && apt install -y ca-certificates curl git
curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
apt install -y nodejs

node -v          # v24.21.0 — must be >= 24, apt's own nodejs is 20.19 and will not do
```

### 8c. The app

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

### 8d. The unit

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

### 8e. Updating and backup

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

## 9. CT 109 — Kodi (headless)

Unprivileged LXC, 2 cores / 1 GB, static 192.168.8.221, rootfs on the NVMe. Kodi 21.2 from Debian's own packages, no Docker, running as a systemd unit under a virtual X server. Web server and JSON-RPC on `:8080`.

Kodi is a player, not a server, and this box has nothing plugged into either HDMI port. It is here for one reason: its library and JSON-RPC API, so that clients which speak Kodi — Symfonium's Kodi provider, Yatse, Kore — can browse the library and stream from it without a Kodi device being switched on somewhere. It scans the same music folders Navidrome serves as libraries (11b) and the same `movies`/`tv` CT 110's Jellyfin serves (9f), read-only, and ends up with the same tracks and titles.

> **If the client is Symfonium, Navidrome is the better source.** Symfonium's Subsonic support is its most complete: offline sync, scrobbles, stars, the `nd-lyrics` lyrics and the multi-artist tag splits all come through. Kodi gives it a second copy of the same library with less of that. Run this for Kodi-specific clients, or when you want Kodi's library as such.

### 9a. The container

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

### 9b. Kodi, Xvfb and a silent sound card

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

### 9c. Settings and sources, seeded before first start

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

# One source per Navidrome library (11b) - same folders, same exclusions.
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

The sources list is Navidrome's library table (11b), not the whole `files` tree, for the same reasons given there: `music_nwa_backup` and the other folders outside it would come in as duplicates, and `audiobooks/`, `downloads/`, `icyscan/` and `_Serato_` have no business in a music library. macOS `._*` files over the share need no exclusion — Kodi treats dotfiles as hidden and never scans them.

The artist separators split `Artist A; Artist B` and `feat.` credits into separate artists, the Kodi counterpart of Navidrome's multi-artist tag splitting.

### 9d. The unit

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

### 9e. The first scan — one source at a time

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

### 9f. Movies and TV

The same `/media/files` mount already covers `movies/` and `tv/` — the directories CT 110's Jellyfin scans, and where its Radarr and Sonarr import to (10d). Kodi's scrapers for both, TMDb's, ship in `kodi-data`, so nothing needs installing.

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

> **Not set up on CT 110 yet.** These connections lived in the old CT 107 stack, and were destroyed with it. Mediagg's Sonarr (`http://192.168.8.187:8989`) and Radarr (`:7878`) take exactly the same settings. Until they're added, new films and episodes only reach Kodi when its library is updated by hand.

### 9g. Pointing clients at it

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

Finally, it has a card on the dashboard: an `lxc` entry in hqdash's `TARGETS` (15) pointing at `http://192.168.8.221:8080`.

---

## 10. CT 110 — Mediagg Arr Stack

Unprivileged LXC, 4 cores / 4 GB, **DHCP** (192.168.8.187 at the time of writing), rootfs on the NVMe, Docker inside. Manager web UI on `:7979`.

Mediagg Arr Stack installs and wires together a whole media server from one setup page: Jellyfin for films and series, Navidrome for music, Audiobookshelf for audiobooks and podcasts, and, optionally, the services that find and fetch more (Sonarr, Radarr, Lidarr, Prowlarr, FlareSolverr, qBittorrent, Seerr, Hearr). Each one runs in its own Docker container, already pointed at the others. The manager then pairs the whole stack with the Mediagg app on phones and TVs using a single code.

On 2026-10-04 it replaced five containers that did the same jobs one by one: Audiobookshelf (CT 102), Plex (CT 103), Emby (CT 104), the hand-built *arr stack (CT 107) and Jellyfin (CT 108). The host Navidrome (11) was switched off the same day. All five containers were backed up with `vzdump` to `/mnt/nvme/dump` before they were destroyed. To bring one back: `sudo pct restore <id> /mnt/nvme/dump/vzdump-lxc-<id>-….tar.zst --storage nvme`, then re-add its media mounts with `pct set`.

### 10a. The container

Every other container here has its root user mapped to host uid 100000 and gets into the library through a POSIX ACL. This one maps **container uid 1000 straight to host uid 1000** (`youruser`), so whatever the stack writes (imports, downloads, `navidrome.toml`) is owned by you and editable over SMB with no ACLs at all. Proxmox only allows that mapping once the host's `root` is allowed to hand out uid 1000:

```bash
echo 'root:1000:1' | sudo tee -a /etc/subuid /etc/subgid

sudo pct create 110 local:vztmpl/debian-13-standard_13.6-1_arm64.tar.zst \
  --hostname arrstack \
  --arch arm64 --ostype debian \
  --cores 4 --memory 4096 --swap 512 \
  --rootfs nvme:32 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp,type=veth \
  --nameserver "1.1.1.1 8.8.8.8" \
  --features nesting=1,keyctl=1 \
  --unprivileged 1 \
  --onboot 1 \
  --description "Mediagg Arr Stack"

sudo pct set 110 -mp0 /mnt/nvme/files,mp=/mnt/nvme/files

# container uids 0-999 -> 100000+, 1000 -> host 1000, 1001+ -> 101001+
sudo tee -a /etc/pve/lxc/110.conf <<'EOF'
lxc.idmap: u 0 100000 1000
lxc.idmap: g 0 100000 1000
lxc.idmap: u 1000 1000 1
lxc.idmap: g 1000 1000 1
lxc.idmap: u 1001 101001 64535
lxc.idmap: g 1001 101001 64535
EOF

sudo pct start 110
```

The whole `files` tree goes in **read-write at the same path** it has on the host. That way a folder picked in Mediagg's file browser has the same path everywhere, and Sonarr, Radarr and qBittorrent all see one filesystem, so imports can be hardlinks instead of copies. `keyctl=1` is next to `nesting=1` because Docker in an unprivileged Debian 13 LXC needs both. `nvme:32`, because every service's config, metadata and Jellyfin's image cache live on the rootfs.

> **The address is DHCP**, unlike every other container here. Every link to the stack, the dashboard cards (10e) included, assumes 192.168.8.187. Reserve that address on the router, or make it static: `sudo pct set 110 -net0 name=eth0,bridge=vmbr0,ip=192.168.8.187/24,gw=192.168.8.1,type=veth`, which restarts the container's network.

### 10b. Installing Mediagg

Inside the container, as a normal user with uid 1000 (the first user `adduser` makes):

```bash
sudo pct enter 110

apt update && apt install -y curl sudo openssh-server
adduser youruser                      # uid 1000, the one the idmap passes through
usermod -aG sudo youruser
su - youruser

curl -fsSL https://mediagg.app/install.sh | sh
```

The script installs Docker Engine and starts the manager, `mediagg-manager`. Open `http://192.168.8.187:7979`, set a name and password, and answer the setup questions. For the media folder, choose `/mnt/nvme/files`. The manager then pulls and configures every service you picked. The setup page warns that it's served over plain HTTP, so your password crosses the LAN unencrypted. It isn't forwarded anywhere, so that's acceptable here.

Everything it owns lives under `~/MediaggStack`:

| Path | What it is |
|---|---|
| `compose.yaml` | the stack. **Written by the manager and rewritten on every change** — edit it and your edit is gone on the next change in the UI |
| `config/<service>/` | each service's config and database |
| `manager/state.json` | the manager's own settings |

Changes go through the UI at `:7979`. Run `docker compose` against the file only to look at it (`ps`, `logs`), never to change it.

### 10c. The services

| Service | Port | Image | Does |
|---|---|---|---|
| Manager | `:7979` | `ghcr.io/m3ugoat/mediagg-arr-stack` | setup, pairing, settings |
| Jellyfin | `:8096` | `jellyfin/jellyfin:12.1` | films and series |
| Navidrome | `:4533` | `deluan/navidrome:0.64.2` | music |
| Audiobookshelf | `:13378` | `ghcr.io/advplyr/audiobookshelf:2.37.1` | audiobooks and podcasts |
| Sonarr | `:8989` | `lscr.io/linuxserver/sonarr` | finds and fetches series |
| Radarr | `:7878` | `lscr.io/linuxserver/radarr` | finds and fetches films |
| Lidarr | `:8686` | `lscr.io/linuxserver/lidarr` | finds and fetches music |
| Prowlarr | `:9696` | `lscr.io/linuxserver/prowlarr` | the indexer list the three above search |
| FlareSolverr | `127.0.0.1:8191` | `ghcr.io/flaresolverr/flaresolverr` | gets Prowlarr past browser checks; loopback only |
| qBittorrent | `:8080` | `lscr.io/linuxserver/qbittorrent` | downloads |
| Seerr | `:5055` | `ghcr.io/seerr-team/seerr:v3.5.0` | film and series requests |
| Hearr | `:8282` | `ghcr.io/m3ugoat/hearr:0.3.0` | album and track requests |
| Hearr backend | `:8484` | `ghcr.io/m3ugoat/hearr-backend:0.2.0` | fetches what Hearr is asked for |

Containers are named `mediagg-<service>`, all on one Docker network, `mediagg`. Images are pinned, and the manager moves those pins when it updates the stack.

### 10d. Where things land

Inside the stack, `/data` is `/mnt/nvme/files`:

| Service | Folder on the host |
|---|---|
| Sonarr | `/mnt/nvme/files/tv` |
| Radarr | `/mnt/nvme/files/movies` |
| Lidarr | `/mnt/nvme/files/music` and `/mnt/nvme/files/music_albums` |
| Navidrome | `/mnt/nvme/files/music`; its config file is `/mnt/nvme/files/navidrome.toml` |

> **Lidarr has root folders on hand-curated music.** Lidarr renames and moves what it manages into `Artist/Album (Year)/`. With `music` and `music_albums` as root folders, anything it imports or upgrades gets reorganised inside folders you sort by hand. The old CT 107 setup gave Lidarr its own `music_lidarr` folder for exactly this reason. If that matters to you, point Lidarr's root folder at `music_lidarr` in its UI before it imports anything.

The same `movies` and `tv` folders are what Kodi (9f) scans.

### 10e. On the dashboard

The manager gets an `lxc` card under *Containers*. Each service gets a `docker` card under its own **Mediagg Arr Stack** group, so start, stop and restart act on one service and leave the rest running:

```python
"ct110": {"kind": "lxc", "vmid": "110", "label": "Mediagg Arr Stack",
          "group": "Containers", "url": "http://192.168.8.187:7979",
          "note": "192.168.8.187:7979 - hosts the Mediagg services below"},
"mediagg-sonarr": {"kind": "docker", "vmid": "110", "container": "mediagg-sonarr",
    "label": "Sonarr", "group": "Mediagg Arr Stack",
    "url": "http://192.168.8.187:8989",
    "note": "192.168.8.187:8989 - TV"},
```

…one per container in 10c. FlareSolverr's has `"url": None`, because it only listens inside the container. **The group must also be listed in `GROUP_ORDER`.** The API reports a card whose group isn't listed, but the page never draws it.

---

## Proxmox-era notes

Things that only applied while PXVIRT was installed, moved out of the main guide's checklist and gotchas:

- **No PVE backup jobs were ever configured.** `/etc/pve/jobs.cfg` was empty; `/mnt/nvme/dump` only ever held the one-off backups of CT 102–104, 107 and 108 taken before they were destroyed.
- **`zfsutils-linux` came in with PXVIRT** with no pool and the module never loaded.
- **`postfix`** ran on localhost for PVE's mail notifications. It is still installed.
- **Order mattered:** PXVIRT after the `vmbr0` bridge, the containers after PXVIRT.
- **Kodi's first library scan must name each source** (9e). *Update library* — and `updateonstartup` — only rescans folders already holding songs, so on a fresh `userdata/` it finishes in 0 s having found nothing.
- The dashboard had `lxc` cards (state read from `/sys/fs/cgroup/lxc/<vmid>`, actions through `pct`) and `docker` cards that reached into CT 110 with `pct exec 110 -- docker ...`.

The container checks from the old verification list:

```bash
sudo pct list                                      # 100, 101, 105, 106, 109, 110; 100 is stopped by choice
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.8.217:8080/   # gopodder, 303 -> /setup
curl -s http://192.168.8.218:8080/api/auth/status      # m3ugoat, {"enabled":true,...}
sudo pct exec 109 -- /usr/local/bin/kodi-rpc AudioLibrary.GetSongs '{"limits":{"end":1}}'   # kodi, "total":13753
sudo pct exec 110 -- docker ps --format '{{.Names}}' | grep -c '^mediagg-'   # 13
```

---

## How it was removed (2026-10-09)

1. Stopped every container and deleted its config from `/etc/pve/lxc/` — not `pct destroy`, which deletes the disk image too.
2. Moved the LAN address off the bridge: `/etc/network/interfaces` cut down to `lo`, and a NetworkManager connection for `eth0` with the same address:

   ```bash
   sudo nmcli con add type ethernet ifname eth0 con-name eth0-static autoconnect yes \
     ipv4.method manual ipv4.addresses 192.168.8.191/24 ipv4.gateway 192.168.8.1 \
     ipv4.dns 192.168.8.1 ipv4.dns-search lan ipv6.method auto
   ```

   `tailscale-gro.service` then had to be pointed at `eth0` instead of `vmbr0`.
3. Purged every `pve`/`proxmox`/`ceph`/`zfs`/`corosync`/`lxc`/`frr`/`ifupdown2` package, then `apt autoremove --purge`, after `apt-mark manual lvm2 sqlite3` so the NVMe's LVM and the scripts' SQLite stayed. **`proxmox-ve`'s apt hook refuses removal** until `/please-remove-proxmox-ve` exists — the first attempt failed on exactly that.
4. Deleted the PXVIRT apt sources and Proxmox keyrings, `/etc/pve`, `/var/lib/pve-*` and `/var/lib/vz` (CT 100's disk), then rebooted.
5. On the NVMe, one at a time: `images/` (39 GB of container disks), `dump/` (6.9 GB of backups), and the empty `private/`, `snippets/` and `template/`.
