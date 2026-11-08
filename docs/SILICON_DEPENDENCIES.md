# Silicon toolchain: install and what was measured

Status: phase 0 of `docs/SILICON_INTEGRATION_PLAN.md` (feasibility). Everything below was run in the development container (Linux x86_64, 4 cores, 15 GB, no working Docker daemon).

## Install (no Docker, nothing built)
`tools/silicon_setup.sh [version]` fetches LibreLane's published tool build from its container image on ghcr.io (a registry download, no container runtime), unpacks the Nix store
layer into `/nix/store`, installs the pure-Python `librelane==<version>` from PyPI with `--no-deps` into a private directory, and writes `<data_dir>/silicon/env.json`
(tool directories, PDK root, the PDK index source). `silicon.toolchain` reads that file; `sparky doctor` shows every tool and version. Resumable (the proxy cut the 1.5 GB
download once; the script re-requests ranges). Needs /nix (Nix installed) and roughly 6 GB for the tools plus about 2 GB per PDK family. Where Docker works,
`librelane --dockerized` is the supported alternative.

Pinned set (LibreLane 3.0.17 image): OpenROAD 2026-02-17 build, Yosys 0.62, Verilator 5.044, Magic 8.3.623, Netgen 1.5.316, KLayout 0.30.7, OpenSTA 2.7.0, Icarus Verilog 13 (dev), Tcl 8.6, Ruby 3.3, Python 3.13.9.
Older conda builds (litex-hub OpenROAD of early 2024, Yosys 0.38) were tried first and rejected: LibreLane 3 expects its own tool versions.

## PDKs
Downloaded on first use by `ciel` (LibreLane's PDK manager). Its default index host has moved; `CIEL_DATA_SOURCE=static-web:https://www-archive.fossi-foundation.org/ciel-releases`
serves the manifests (the setup script writes it). Families verified here: `sky130A`, `gf180mcuD`, `ihp-sg13g2`.

## Measured (8-bit enable counter, 120 x 120 um die, 20 ns clock, one run each)
| PDK | Wall time | Layout produced | Instances (std cells) | Worst setup slack | KLayout DRC | Magic DRC | LVS (Netgen) |
|---|---|---|---|---|---|---|---|
| sky130A | 78 s | yes | 199 | 13.44 ns | 0 errors | 0 | 0 errors |
| gf180mcuD | 73 s | yes | 144 | 9.52 ns | **metric not reported (KLayout DRC skipped)** | 0 | 0 |
| ihp-sg13g2 | 95 s | yes | 56 | 13.76 ns | 0 errors | 0 | 0 |
LibreLane's own `--smoke-test` (the `spm` design on SKY130) also passes in 76 s.

Finding that shapes the verdict layer: on gf180mcuD the flow completes without a KLayout DRC result and says only "KLayout.DRC may have been skipped". A consumer that reads
`klayout__drc_error__count` and treats a missing key as zero would report a clean layout that was never checked. Our signoff layer must treat a missing metric as NOT RUN.

## Licences
LibreLane Apache-2.0 (PyPI package, used as an external process); OpenROAD BSD-3; Yosys ISC; Verilator LGPL-3/Artistic; Magic and Netgen BSD-style; KLayout GPL-3 (external tool, optional
Python module); OpenSTA GPL-3 (external binary). Nothing here is bundled or linked into sparky.
