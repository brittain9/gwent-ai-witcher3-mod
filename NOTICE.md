# Notice

The MIT license covers this project's original code, documentation and the
inference parameters included in the player download, except
where a file states other terms. It does not cover The Witcher 3, its assets,
card text or data, shipped bytecode, or source recovered from that bytecode.
Those materials remain with their respective rights holders.

The source build tools read an installed copy of the game and create the catalog,
decompiled classes and modified game screen locally. Prebuilt player packages
include the modified Gwent screen, the trained policies and original integration
scripts. The modified screen includes CD PROJEKT RED material, which remains
under its respective terms and is not covered by this project's MIT license.
Learned inference parameters contain the network's numeric tensors and
architecture metadata; their card vocabulary and effect tables are derived
from the compatible game.

Locally generated model headers contain game-derived metadata, and a locally
generated mod package contains a modified game resource. Neither becomes
MIT-licensed merely because these tools produced it. Rights to third-party
game material are governed by the applicable game terms and
[CD PROJEKT RED's fan-content guidelines](https://www.cdprojektred.com/en/fan-content).

JPEXS Free Flash Decompiler, Python, PyTorch, NumPy, Node.js, Java and other
external tools and dependencies retain their own licenses. They are not
relicensed by this project.

This is an independent fan project, not an official CD PROJEKT RED product.
The Witcher and Gwent names belong to their respective rights holders.

## Format reference credit

The bundle-format tooling was developed with reference to
[Better Gwent](https://github.com/mateusands/witcher3-better-gwent).
Its original-contribution MIT notice is retained in
[licenses/Better-Gwent-MIT.txt](licenses/Better-Gwent-MIT.txt).
