---
title: Navidrome lyrics — priority, sidecars and the plugin
description: How LyricsPriority resolution works, what nd-lyrics writes to disk, and the risks of granting it write access.
permalink: /navidrome-lyrics/
---

Companion to section 12a of the [rebuild guide](/). Everything here is Navidrome **0.63.2** with [navidrome-lyrics-plugin](https://github.com/J0R6IT0/navidrome-lyrics-plugin) **v7.2.0**.

---

## How `LyricsPriority` resolves

A comma-separated list, evaluated **left to right, first hit wins**. Any entry that is not `embedded` and not a file extension is treated as a **plugin name** — the filename of the `.ndp` without its extension.

The 0.63 default is already ordered by sync richness:

```toml
LyricsPriority = ".ttml,.yaml,.yml,.elrc,.lrc,.srt,.txt,embedded"
```

| Entry | Sync level |
|---|---|
| `.ttml`, `.yaml`, `.yml` | word-by-word + multi-voice (agent layers) |
| `.elrc` | word-by-word |
| `.lrc`, `.srt` | line-synced |
| `.txt` | plain |
| `embedded` | plain (`USLT`), occasionally line-synced (`SYLT`) |

Because the **extension encodes the sync level**, keeping the default extension order *is* sync-descending. You never have to reason about sync separately — only about where the plugin sits in the list.

### Ordering recipes

```toml
# stock: everything local wins, plugin is a last resort
".ttml,.yaml,.yml,.elrc,.lrc,.srt,.txt,embedded,nd-lyrics"

# online beats baked-in tags, local sidecars still win
".ttml,.yaml,.yml,.elrc,.lrc,.srt,.txt,nd-lyrics,embedded"

# synced highest, and online preferred over any local plain text
".ttml,.yaml,.yml,.elrc,.lrc,.srt,nd-lyrics,.txt,embedded"

# online-first, period
"nd-lyrics,.ttml,.yaml,.yml,.elrc,.lrc,.srt,.txt,embedded"
```

The third is usually what people mean by "prefer online synced lyrics": any synced sidecar on disk wins, then the plugin gets a chance to find something synced, then plain fallbacks.

---

## Sidecar vs embedded

**Embedded** lyrics live inside the audio file's own tag block (`USLT`/`SYLT` in ID3, a `LYRICS` field in FLAC). **Sidecar** lyrics are a separate file beside the track, matched by basename:

```
Music/Radiohead/In Rainbows/01 - 15 Step.flac
Music/Radiohead/In Rainbows/01 - 15 Step.lrc   <- sidecar
```

| | Editing | Survives a file copy | Read by the web UI |
|---|---|---|---|
| Embedded | needs a tag editor | yes, it's one file | yes |
| Sidecar | any text editor | only if you copy both | yes |
| Plugin (live) | n/a | n/a | **no** |

Sidecars are parsed **during a scan** into `media_file.lyrics`, so serving one is a local DB read.

### What the plugin writes

**Sidecar files, always.** The plugin has no tag-writing capability — your audio files are never modified. Two details that matter:

- The extension is chosen from the lyrics type, not by you. `{type}` resolves to `plain`, `lrc`, `elrc`, `ttml`, `srt`, `lyricsfile` or `instrumental`, and the extension is appended automatically. A word-synced hit lands as `.ttml`/`.elrc`, line-synced as `.lrc`, plain as `.txt`.
- **"Write to custom path" must stay disabled.** With it on, files go to a parallel tree like `<library_root>/_lyrics/lrc/<album>/01 - Track.lrc`, which Navidrome will not match to the media file — written and never read.

---

## Does the client still hit the server?

Two different requests, and only one of them stops.

**Client → Navidrome: always.** Lyrics are fetched over the Subsonic/OpenSubsonic API (`getLyricsBySongId` / `getLyrics`) every time. A sidecar on the server changes nothing here; some clients keep their own local cache.

**Navidrome → internet: stops, once the sidecar is indexed.** Resolution short-circuits on first match, so if a sidecar extension outranks `nd-lyrics`, the plugin is never invoked — no plugin call, no outbound HTTP.

Two caveats:

- **The scan is what completes it.** Between "plugin wrote the file" and "next scan indexed it", Navidrome still falls through to the plugin, answered from its cache (plain 3 d, LRC 7 d, TTML/ELRC 14 d, negative 24 h).
- **Plain-only tracks never settle** if `nd-lyrics` sits above `.txt`/`embedded`. With no synced lyrics available anywhere, the plugin is always consulted first and re-queries roughly daily as the negative cache lapses. Moving `nd-lyrics` below `.txt` lets those go quiet.

---

## Fetch-side settings worth changing

| Option | Default | Why |
|---|---|---|
| `providerMode` | `priority` | First hit wins, so a plain result beats a word-synced one from a later provider. `bestSyncLevel` ranks `word-by-word > line-by-line > plain`. **Required** if you want synced lyrics; ordering alone won't do it. |
| `providersList` | `lrclib`, `lyrics.ovh` | `bestSyncLevel` can only pick the best of what it queries. `lrcmux`, `netease`, `kugou`, `qqmusic` widen coverage a lot. |
| `durationToleranceSeconds` | `3` | Rejects results whose track length differs by more than this. Raising it finds more matches and more wrong ones — costlier once results are persisted to disk. |
| `writeLyrics` | `false` | Also needs "allow write access" granted to the plugin, plus filesystem permission. |
| `overwriteLyrics` | `false` | Leave off — protects hand-made sidecars. |
| `stripSectionLabels` | `false` | Drops `[Chorus]`-style labels and credits. |

---

## Risks of granting write access

1. **A wrong result becomes permanent.** Once written, a sidecar sits at the *top* of the priority list — above the plugin and above embedded tags — and `overwriteLyrics = false` means it is never corrected. One bad fetch silently outranks good embedded lyrics forever.
2. **Directory write implies deletion.** On POSIX, unlinking needs write on the *directory*, not the file. Granting write on album folders technically permits deleting or renaming the audio files in them.
3. **Clutter.** `plainExtension` and `instrumentalExtension` are both `txt`, so every instrumental gets a `.txt` containing one word. Across a large library that is thousands of small files hitting sync tooling and backup diffs.
4. **Ownership mismatch.** Files land as `navidrome:navidrome` 644 — deletable over SMB (you own the directory) but not editable in place.
5. **Library roots define the blast radius.** A library rooted at a whole disk means the write path is scoped to that whole disk.

### Mitigations

| Measure | Effect |
|---|---|
| ACL on the music directories only, never a whole-disk root | Everything outside the music tree stays unwritable |
| ACL on **directories only**, not existing files | Plugin can create sidecars; cannot modify an audio file |
| **Sticky bit** (`chmod +t`) on those directories | Plugin can add files but never delete or rename yours |
| `overwriteLyrics = false` | Hand-made sidecars never replaced |
| `writeToSpecificFolder = false` | Required for native reads anyway |
| Stage one library first | Verify accuracy before widening |

Grant it with ACLs rather than group-write, so existing modes are untouched:

```bash
sudo apt install -y acl
D=/mnt/nvme/files/music_now
sudo find "$D" -type d -exec setfacl -m u:navidrome:rwx -m d:u:navidrome:rwx {} +
sudo find "$D" -type d -exec chmod +t {} +
```

Rollback is clean, because everything the plugin creates is owned by `navidrome`:

```bash
find /mnt/nvme/files/music_now -user navidrome \
  \( -name '*.lrc' -o -name '*.ttml' -o -name '*.elrc' -o -name '*.srt' -o -name '*.txt' \) -print
# swap -print for -delete once the list looks right
```

---

## Gotchas

- **The web UI does not render plugin lyrics.** Live plugin results need a third-party client (Symfonium, Amperfy, Feishin, Substreamer). Sidecars the plugin *writes* do render, because Navidrome reads those natively.
- **Word-by-word needs a client that asks for it** — the OpenSubsonic v2 lyrics extension. Symfonium handles TTML.
- **The plugin registers disabled**, and needs libraries *and* users granted in Settings → Plugins or it never fires.
- `error getting lyrics ... error="context canceled" source=nd-lyrics` in the journal means the client gave up before the plugin returned — expected on a first, uncached lookup with several providers.

## State on this box as of 2026-08-23

`LyricsPriority` is `.ttml,.yaml,.yml,.elrc,.lrc,.srt,nd-lyrics,.txt,embedded` — synced sidecars first, then the plugin, then plain text and embedded tags. Chosen so that online synced lyrics beat plain or baked-in ones, while any synced file already on disk still wins.

Everything write-related is **off**: `writeLyrics = false`, `writeToSpecificFolder = false`, `allow_write_access = 0`, and `navidrome` (uid 997) has no write permission anywhere under `/mnt/nvme` — the music tree is `755` owned by the Samba user. So the plugin serves lyrics live and persists nothing.

Consequences of that, by design:

- **The web UI shows no lyrics from the plugin.** Use Symfonium/Amperfy/Feishin. Tracks that have embedded lyrics will look empty in the browser player, because `embedded` now sits below `nd-lyrics`.
- **Nothing self-warms.** Every lookup for a track without a synced sidecar goes to the providers (or their cache), so first-play latency and `context canceled` timeouts are expected.
- `providerMode` is still `priority` with the two default providers (`lrclib`, `lyrics.ovh`), so the first hit wins regardless of sync level. Switching to `bestSyncLevel` and widening `providersList` is the next lever if synced coverage disappoints — both are plugin settings needing no filesystem access.
