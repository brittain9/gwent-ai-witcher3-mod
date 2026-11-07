# Setup tool downloads

The player installer builds the modified Gwent screen from your installed
game. This preparation uses Java, Node.js and JPEXS FFDec; the finished
opponent runs inside the game without those tools running beside it.

Setup uses an installed JDK 11+ and Node.js 24+ when available. Otherwise it
downloads the pinned x64 tools below from their official distributions.
FFDec always uses the complete pinned distribution. Downloads and generated
game files stay in this extracted release folder. No PyTorch or NumPy install
is needed for the player download.

| Tool | Version | Official download and checksum source |
|---|---|---|
| FFDec | 26.4.0 | [JPEXS release](https://github.com/jindrapetrik/jpexs-decompiler/releases/tag/version26.4.0), ZIP asset SHA-256 |
| Node.js | 24.11.1 | [Node release archive](https://nodejs.org/en/download/archive/v24.11.1), [official checksums](https://nodejs.org/dist/v24.11.1/SHASUMS256.txt) |
| Temurin JDK | 21.0.13+9 | [Eclipse Temurin release](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.13%2B9), archive asset SHA-256 |

Every downloaded archive must match its SHA-256 before extraction. The
checksums in `scripts/setup_player.py` cover the full archives, including
FFDec's companion libraries. The local cache records every extracted file
and compares them with the retained, hash-verified official archive before reuse.
Setup rejects unsafe archive paths and
escaping links. `--offline` uses installed tools and a verified cache only.

External tools retain their own licenses and notices in their downloaded
distributions. They are downloaded separately rather than included in the
mod's player ZIP. See [NOTICE](../NOTICE.md).
