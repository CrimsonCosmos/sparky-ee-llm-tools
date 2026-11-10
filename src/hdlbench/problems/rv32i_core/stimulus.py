"""Random RV32I programs (256 words) for the core testbench: mostly valid instructions over a small register set (so values flow between instructions), with immediates/shamts biased to useful values,
loads/stores, branches and jumps landing anywhere in the (wrapping) instruction memory, and a few illegal words. Visible and held-out programs differ only by seed and count."""
import random

REGS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 31]


def word(rng):
    r = rng.random()
    rd, ra, rb = rng.choice(REGS), rng.choice(REGS), rng.choice(REGS)
    imm = rng.choice([0, 1, -1, 4, -4, 8, 12, 0x7FF, -0x800, rng.randint(-2048, 2047), rng.randint(-64, 64)]) & 0xFFF
    if r < 0.03:
        return rng.getrandbits(32)                                                  # illegal / random
    if r < 0.10:
        return (rng.getrandbits(20) << 12) | (rd << 7) | rng.choice([0x37, 0x17])   # LUI / AUIPC
    if r < 0.20:                                                                    # OP-IMM
        f3 = rng.choice([0, 1, 2, 3, 4, 5, 6, 7])
        i = imm
        if f3 in (1, 5):
            i = rng.randint(0, 31) | (0x400 if (f3 == 5 and rng.random() < 0.5) else 0) | (rng.choice([0, 0, 0, 0x200]) if rng.random() < 0.1 else 0)
        return (i << 20) | (ra << 15) | (f3 << 12) | (rd << 7) | 0x13
    if r < 0.35:                                                                    # OP
        f3 = rng.randint(0, 7)
        f7 = rng.choice([0, 0x20 if f3 in (0, 5) else 0, 0]) if rng.random() < 0.95 else rng.choice([1, 0x20, 0x7F])
        return (f7 << 25) | (rb << 20) | (ra << 15) | (f3 << 12) | (rd << 7) | 0x33
    if r < 0.50:                                                                    # LOAD (all funct3 incl. illegal ones rarely)
        f3 = rng.choice([0, 1, 2, 4, 5, 0, 1, 2, 4, 5, 3, 6, 7])
        return (imm << 20) | (ra << 15) | (f3 << 12) | (rd << 7) | 0x03
    if r < 0.62:                                                                    # STORE
        f3 = rng.choice([0, 1, 2, 0, 1, 2, 3, 4])
        return ((imm >> 5) << 25) | (rb << 20) | (ra << 15) | (f3 << 12) | ((imm & 31) << 7) | 0x23
    if r < 0.80:                                                                    # BRANCH
        f3 = rng.choice([0, 1, 4, 5, 6, 7, 0, 1, 4, 5, 6, 7, 2, 3])
        off = rng.choice([4, 8, 12, 16, 20, 2, 6, rng.randint(1, 40) * 2]) & 0x1FFF                # forward only, so the program makes progress
        return (((off >> 12) & 1) << 31) | (((off >> 5) & 0x3F) << 25) | (rb << 20) | (ra << 15) | (f3 << 12) | (((off >> 1) & 0xF) << 8) | (((off >> 11) & 1) << 7) | 0x63
    if r < 0.88:                                                                    # JAL
        off = rng.choice([4, 8, 16, 24, rng.randint(1, 50) * 2]) & 0x1FFFFF
        return (((off >> 20) & 1) << 31) | (((off >> 1) & 0x3FF) << 21) | (((off >> 11) & 1) << 20) | (((off >> 12) & 0xFF) << 12) | (rd << 7) | 0x6F
    f3 = 0 if rng.random() < 0.9 else rng.randint(1, 7)                              # JALR
    return (imm << 20) | (ra << 15) | (f3 << 12) | (rd << 7) | 0x67


def programs(which, seed, n):
    rng = random.Random(seed * 104729 + (1 if which == "heldout" else 0))
    progs = []
    for _ in range(n):
        w = [word(rng) for _ in range(256)]
        w[255] = 0xC05FF06F                                                           # JAL x0, -1020: back to address 0, so the program runs repeatedly with evolving state
        progs.append(w)
    return progs
