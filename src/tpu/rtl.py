"""Build and run the TPU RTL (Verilator) on arbitrary int8 GEMMs; compare against the exact integer reference."""
from __future__ import annotations
import re, shutil, subprocess, tempfile
from pathlib import Path
import numpy as np
from silicon import toolchain

HW = Path(__file__).resolve().parents[2] / "hardware" / "tpu"


def build(version: str, n: int, mmax: int, ktmax: int, workdir: Path, top_tb: str = "tb_core.sv") -> Path:
    """Compile hardware/tpu/<version>/*.v with the shared testbench; returns the simulator binary."""
    srcs = sorted((HW / version).glob("*.v")) + [HW / "tb" / top_tb]
    cmd = ["verilator", "--binary", "--timing", "-Wno-fatal", "-Wno-lint", "-Wno-style", "--top-module", "tb_core",
           f"-GN={n}", f"-GMMAX={mmax}", f"-GKTMAX={ktmax}", "-O2", "--Mdir", str(workdir / "obj"), "-o", "sim"] + [str(s) for s in srcs]
    vl = toolchain.which("verilator")
    if not vl:
        raise RuntimeError("NOT RUN: verilator not available")
    cmd[0] = vl
    workdir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir, env=toolchain.env())
    if r.returncode:
        raise RuntimeError("verilator failed:\n" + r.stderr[-3000:])
    return workdir / "obj" / "sim"


def _hex_beat(vec: np.ndarray) -> str:
    """int8 vector (element 0 = least-significant byte) -> hex string of the beat."""
    return "".join(f"{int(v) & 0xFF:02x}" for v in vec[::-1])


def gemm(sim: Path, workdir: Path, x: np.ndarray, w: np.ndarray, n: int, mmax: int, bw: tuple[int, int] = (1, 1), lat: int = 0):
    """Run C[M,N'] = x[M,K] @ w[K,N'] (int8) on the RTL. Returns (C int32, cycles)."""
    m, k = x.shape
    k2, nn = w.shape
    assert k == k2 and m <= mmax
    kt, nt = -(-k // n), -(-nn // n)
    xp = np.zeros((m, kt * n), np.int8); xp[:, :k] = x
    wp = np.zeros((kt * n, nt * n), np.int8); wp[:k, :nn] = w
    dram = []
    for j in range(nt):
        for i in range(kt):
            tile = wp[i * n:(i + 1) * n, j * n:(j + 1) * n]       # tile[k, n]
            for r in range(n - 1, -1, -1):                        # rows in reverse order
                dram.append(_hex_beat(tile[r]))
    (workdir / "dram.hex").write_text("\n".join(dram) + "\n")
    act = ["0" * (2 * n)] * (kt * mmax)
    for i in range(kt):
        for r in range(m):
            act[i * mmax + r] = _hex_beat(xp[r, i * n:(i + 1) * n])
    (workdir / "act.hex").write_text("\n".join(act) + "\n")
    out = subprocess.run([str(sim), f"+m={m}", f"+kt={kt}", f"+nt={nt}", f"+bwn={bw[0]}", f"+bwd={bw[1]}", f"+lat={lat}"],
                         capture_output=True, text=True, cwd=workdir, timeout=600)
    cyc = re.search(r"CYCLES (\d+)", out.stdout)
    if not cyc:
        raise RuntimeError("simulation did not finish:\n" + out.stdout[-1500:])
    c = np.zeros((m, nt * n), np.int32)
    got = 0
    for line in (workdir / "out.txt").read_text().split("\n"):
        if not line.strip():
            continue
        a, b, h = line.split()
        a, b = int(a), int(b)
        hx = h.zfill(8 * n)
        for col in range(n):
            v = int(hx[8 * n - 8 * (col + 1): 8 * n - 8 * col], 16)
            c[b, a * n + col] = v - (1 << 32) if v >= 1 << 31 else v
        got += 1
    assert got == m * nt, f"expected {m*nt} output vectors, got {got}"
    return c[:, :nn], int(cyc.group(1))


def build_harness(version: str, n: int, mmax: int, ktmax: int, workdir: Path) -> Path:
    """Compile the persistent C++ harness (hardware/tpu/tb/harness.cpp) around hardware/tpu/<version>/tpu_core; returns the executable."""
    vl = toolchain.which("verilator")
    if not vl:
        raise RuntimeError("NOT RUN: verilator not available")
    workdir.mkdir(parents=True, exist_ok=True)
    srcs = [str(p) for p in sorted((HW / version).glob("*.v"))]
    cmd = [vl, "--cc", "--exe", "--build", "-Wno-fatal", "-Wno-lint", "-Wno-style", "--top-module", "tpu_core", f"-GN={n}", f"-GMMAX={mmax}", f"-GKTMAX={ktmax}", "-O3",
           "-CFLAGS", f"-DNN={n} -DMMAXV={mmax} -DKTMAXV={ktmax} -DROWREV={0 if version != 'v1' else 1} -DHAS_RREADY={1 if version not in ('v1', 'v2') else 0} -DHAS_GROUP={1 if int(version[1:]) >= 9 else 0} -DHAS_COUNTS={1 if int(version[1:]) >= 10 else 0} -O2", "--Mdir", str(workdir / "hobj"), "-o", "harness", *srcs, str(HW / "tb" / "harness.cpp")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir, env=toolchain.env())
    if r.returncode:
        raise RuntimeError("verilator harness build failed:\n" + (r.stderr + r.stdout)[-3000:])
    return workdir / "hobj" / "harness"


class Sim:
    """Persistent simulator process; `gemm(x, w)` computes x[M,K] @ w[K,N] exactly as the RTL does (M chunked to MMAX) and accumulates cycle statistics."""

    def __init__(self, exe: Path, mmax: int, bw: tuple[int, int] = (1, 1), lat: int = 0, n: int = 0):
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.mmax, self.bw, self.lat, self.n = mmax, bw, lat, n
        self.cycles = 0
        self.abuf_cycles = 0                                    # host activation-buffer fill (one entry per cycle, NOT overlapped with compute): reported separately
        self.macs = 0
        self.calls: list[tuple[int, int, int, int]] = []        # (m, k, n, cycles)

    def gemm(self, x: np.ndarray, w: np.ndarray, group_tiles: int = 0) -> np.ndarray:
        """x[M,K] @ w[K,N] -> int32 [M,N]. With group_tiles > 0 (v9+ only) the core returns one partial sum per group of `group_tiles` K tiles: int32 [groups, M, N]."""
        m, k = x.shape
        n = w.shape[1]
        ng = -(-(-(-k // self.n)) // group_tiles) if group_tiles else 1
        parts = []
        for m0 in range(0, m, self.mmax):
            xs = np.ascontiguousarray(x[m0:m0 + self.mmax], dtype=np.int8)
            mm = xs.shape[0]
            hdr = np.array([mm, k, n, self.bw[0], self.bw[1], self.lat, group_tiles, 0], np.int32)
            self.p.stdin.write(hdr.tobytes() + xs.tobytes() + np.ascontiguousarray(w, dtype=np.int8).tobytes())
            self.p.stdin.flush()
            cyc = int(np.frombuffer(self.p.stdout.read(8), np.int64)[0])
            parts.append(np.frombuffer(self.p.stdout.read(4 * ng * mm * n), np.int32).reshape(ng, mm, n))
            self.cycles += cyc
            self.abuf_cycles += mm * (-(-k // self.n)) if getattr(self, 'n', None) else 0
            self.macs += mm * k * n
            self.calls.append((mm, k, n, cyc))
        out = np.concatenate(parts, axis=1)
        return out if group_tiles else out[0]

    def close(self):
        try:
            self.p.stdin.write(np.array([-1, 0, 0, 0, 0, 0, 0, 0], np.int32).tobytes()); self.p.stdin.flush()
        except Exception:
            pass
        self.p.wait(timeout=30)


def build_multi(version: str, n: int, mmax: int, ktmax: int, k: int, workdir: Path) -> Path:
    """Compile the K-core harness (hardware/tpu/tb/harness_multi.cpp) around hardware/tpu/<version>/tpu_multi."""
    vl = toolchain.which("verilator")
    if not vl:
        raise RuntimeError("NOT RUN: verilator not available")
    workdir.mkdir(parents=True, exist_ok=True)
    srcs = [str(p) for p in sorted((HW / version).glob("*.v"))]
    cmd = [vl, "--cc", "--exe", "--build", "-Wno-fatal", "-Wno-lint", "-Wno-style", "--top-module", "tpu_multi", f"-GN={n}", f"-GMMAX={mmax}", f"-GKTMAX={ktmax}", f"-GK={k}", "-O3",
           "-CFLAGS", f"-DNN={n} -DKK={k} -DMMAXV={mmax} -DKTMAXV={ktmax} -DHAS_GROUP={1 if int(version[1:]) >= 9 else 0} -DHAS_COUNTS={1 if int(version[1:]) >= 10 else 0} -O2", "--Mdir", str(workdir / "mobj"), "-o", "harness_multi", *srcs, str(HW / "tb" / "harness_multi.cpp")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir, env=toolchain.env())
    if r.returncode:
        raise RuntimeError("verilator multi harness build failed:\n" + (r.stderr + r.stdout)[-3000:])
    return workdir / "mobj" / "harness_multi"
