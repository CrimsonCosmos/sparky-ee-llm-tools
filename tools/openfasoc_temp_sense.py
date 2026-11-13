"""Generate OpenFASOC's all-digital temperature sensor macro (sky130hd) with the silicon tool environment and check the result independently.
usage: python tools/openfasoc_temp_sense.py <OpenFASOC checkout> <workdir>
Steps: copy `openfasoc/common` and `openfasoc/generators/{common,temp-sense-gen}` into <workdir>, apply tools/openfasoc_compat/temp-sense-gen-openroad-2026.patch (OpenFASOC's flow scripts target an older OpenROAD;
see docs/ANALOG_IP.md for each change), run its generator in macro mode with our OpenROAD/Yosys/Magic/Netgen/KLayout, then re-run KLayout DRC with the PDK's own deck on the produced GDS and print the generator's LVS verdict.
Requires: the silicon tools + PDK (docs/SILICON_DEPENDENCIES.md), `pip install mako`, and an executable /usr/bin/time (the flow hard-codes that path; a tiny shell stand-in is enough)."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from silicon import flow, signoff, toolchain

repo, work = Path(sys.argv[1]), Path(sys.argv[2])
gen = work / "openfasoc" / "generators"
for src, dst in ((repo / "openfasoc" / "common", work / "openfasoc" / "common"), (repo / "openfasoc" / "generators" / "common", gen / "common"), (repo / "openfasoc" / "generators" / "temp-sense-gen", gen / "temp-sense-gen")):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git"), symlinks=True)
tsg = gen / "temp-sense-gen"
patch = Path(__file__).with_name("openfasoc_compat") / "temp-sense-gen-openroad-2026.patch"
subprocess.run(["patch", "-p1", "-d", str(tsg), "-i", str(patch)], check=True)
pdk = toolchain.pdk_root()
pdk_dir = next(Path(pdk).rglob("sky130A/libs.ref")).parent.parent
env = toolchain.env()
env["PDK_ROOT"] = str(pdk_dir)
env["PATH"] = env["PATH"] + os.pathsep + str(Path(sys.executable).parent)
r = subprocess.run([sys.executable, "-W", "ignore", "tools/temp-sense-gen.py", "--specfile", "test.json", "--outputDir", "./work", "--platform", "sky130hd", "--mode", "macro"], cwd=tsg, env=env, capture_output=True, text=True, timeout=7200)
(work / "generator.log").write_text(r.stdout + r.stderr)
print("generator exit code:", r.returncode, "(log:", work / "generator.log", ")")
gds = tsg / "work" / "tempsenseInst_error.gds"
if not gds.is_file():
    sys.exit("no GDS produced")
fr = flow.FlowResult(True, None, 0.0, resolved={"PDK_ROOT": str(pdk_dir), "PDK": "sky130A", "DESIGN_NAME": "tempsenseInst_error"})
v = signoff.rerun_klayout_drc(fr, gds=gds)
print(v.check, v.status, v.detail)
lvs = (tsg / "work" / "6_final_lvs.rpt").read_text()
print("generator LVS:", re.findall(r"Final result: (.*)", lvs)[-1:] or "no verdict line")
print("generator Magic DRC report:", (tsg / "work" / "6_final_drc.rpt").read_text().strip().splitlines()[0])
