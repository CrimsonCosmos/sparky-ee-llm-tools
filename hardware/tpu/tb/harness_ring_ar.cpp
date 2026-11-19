// Verilator harness for tpu_ring_ar. stdin: int32 header {nbc, 0...} then C vectors of C*nbc*W bytes (chunk-major); stdout: int64 cycles (start pulse to the last node's done), then C result vectors.
#include "Vtpu_ring_ar.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include <type_traits>
#ifndef CC
#define CC 4
#endif
#ifndef WW
#define WW 16
#endif
#ifndef NBCMAX
#define NBCMAX 64      // the RTL's chunk stride (parameter NBC)
#endif
static Vtpu_ring_ar* d;
template <class T> static uint32_t getw(const T& s, int idx) { if constexpr (std::is_integral<T>::value) return (uint32_t)(((uint64_t)s) >> (32 * idx)); else return s[idx]; }
template <class T> static void setw(T& s, int idx, uint32_t v) { if constexpr (std::is_integral<T>::value) { uint64_t m = 0xFFFFFFFFull << (32 * idx); s = (T)((((uint64_t)s) & ~m) | ((uint64_t)v << (32 * idx))); } else s[idx] = v; }
template <class T> static uint64_t getbits(const T& s, int lo, int w) { uint64_t r = 0; for (int b = 0; b < w; b++) { int p = lo + b; r |= (uint64_t)((getw(s, p / 32) >> (p % 32)) & 1) << b; } return r; }
template <class T> static void setbits(T& s, int lo, int w, uint64_t v) { for (int b = 0; b < w; b++) { int p = lo + b; uint32_t x = getw(s, p / 32); x = (x & ~(1u << (p % 32))) | (((v >> b) & 1u) << (p % 32)); setw(s, p / 32, x); } }
static void tick() { d->clk = 0; d->eval(); d->clk = 1; d->eval(); }
int main() {
  d = new Vtpu_ring_ar;
  d->clk = 0; d->rst_n = 0; d->lwe = 0; d->start = 0; d->eval();
  for (int i = 0; i < 3; i++) tick();
  d->rst_n = 1; tick();
  int32_t h[8];
  while (fread(h, 4, 8, stdin) == 8) {
    int nbc = h[0];
    if (nbc < 0) break;
    size_t beats = (size_t)CC * nbc;
    std::vector<std::vector<uint8_t>> in(CC, std::vector<uint8_t>(beats * WW));
    for (int c = 0; c < CC; c++) if (fread(in[c].data(), 1, in[c].size(), stdin) != in[c].size()) return 2;
    for (int c = 0; c < CC; c++) for (size_t b = 0; b < beats; b++) {
      for (int e = 0; e < WW; e++) setbits(d->ldata, 8 * e, 8, in[c][b * WW + e]);
      d->laddr = (uint32_t)((b / nbc) * NBCMAX + (b % nbc)); d->lwe = 1ull << c; tick();
    }
    d->lwe = 0; d->nbc = nbc; d->start = 1; tick(); d->start = 0;
    int64_t cycles = 1; uint64_t all = (CC >= 64) ? ~0ull : ((1ull << CC) - 1); uint64_t seen = 0;
    while (seen != all) {
      tick(); cycles++;
      seen |= (uint64_t)d->done;
      if (cycles > 100000000) return 4;
    }
    tick();
    std::vector<std::vector<uint8_t>> out(CC, std::vector<uint8_t>(beats * WW));
    for (size_t b = 0; b < beats; b++) {
      d->raddr = (uint32_t)((b / nbc) * NBCMAX + (b % nbc)); d->eval();
      for (int c = 0; c < CC; c++) for (int e = 0; e < WW; e++) out[c][b * WW + e] = (uint8_t)getbits(d->rdata, (c * WW + e) * 8, 8);
    }
    fwrite(&cycles, 8, 1, stdout);
    for (int c = 0; c < CC; c++) fwrite(out[c].data(), 1, out[c].size(), stdout);
    fflush(stdout);
  }
  return 0;
}
