"""Structured stimulus for rv32i_exec: valid and near-valid instruction words across every opcode/funct combination (random registers/immediates), plus fully random words and
register/data values biased to boundary cases. Part of the problem definition; the visible and held-out sets differ only by seed and size."""
import random

SPECIAL = [0, 1, 2, 3, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFFFFFE, 0x0000FFFF, 0xFFFF0000, 0x000000FF, 0x80, 0x8000, 0x7F, 0x7FFF, 31, 32, 33]
OPS = [0b0110111, 0b0010111, 0b1101111, 0b1100111, 0b1100011, 0b0000011, 0b0100011, 0b0010011, 0b0110011, 0b0001111, 0b1110011, 0b0000000, 0b1111111]


def value(rng):
    r = rng.random()
    if r < 0.3:
        return rng.choice(SPECIAL)
    if r < 0.4:
        return (rng.choice(SPECIAL) + rng.randint(-2, 2)) & 0xFFFFFFFF
    return rng.getrandbits(32)


def instruction(rng):
    r = rng.random()
    if r < 0.08:
        return rng.getrandbits(32)                                   # mostly illegal
    op = rng.choice(OPS)
    w = rng.getrandbits(32)
    w = (w & ~0x7F) | op | (0 if rng.random() < 0.97 else rng.choice([0, 1, 2]))      # occasionally break the 2'b11 low bits
    f3 = rng.getrandbits(3)
    w = (w & ~(7 << 12)) | (f3 << 12)
    q = rng.random()
    if op in (0b0110011, 0b0010011):                                  # funct7 variants: 0000000, 0100000, 0000001 (M), random
        f7 = rng.choice([0, 0, 0x20, 0x20, 1, rng.getrandbits(7)])
        w = (w & ~(0x7F << 25)) | (f7 << 25)
    if op == 0b0010011 and f3 in (1, 5) and q < 0.7:                  # shift immediates mostly with a valid funct7
        w = (w & ~(0x7F << 25)) | (rng.choice([0, 0x20]) << 25)
    if op == 0b1100111 and q < 0.7:
        w &= ~(7 << 12)                                               # JALR with funct3 = 0 most of the time
    return w & 0xFFFFFFFF


def vectors(which, seed, n):
    rng = random.Random(seed * 7919 + (1 if which == "heldout" else 0))
    cols = {k: [] for k in ("instr", "pc", "rs1", "rs2", "mem_rdata")}
    for _ in range(n):
        cols["instr"].append(instruction(rng))
        cols["pc"].append(rng.getrandbits(32) if rng.random() < 0.5 else (rng.getrandbits(30) << 2))
        cols["rs1"].append(value(rng))
        cols["rs2"].append(value(rng))
        cols["mem_rdata"].append(rng.getrandbits(32))
    return cols
