// Verilator harness for tpu_ring. stdin: int32 header {nbeats, 0...}, then for each of C chips nbeats*W bytes; stdout: int64 cycles then, for each chip, (C-1)*nbeats*W bytes
// ordered [source rank ascending, skipping itself][beat][W bytes]. Cycles = from the first beat injected until every chip has received all (C-1)*nbeats beats.
#include "Vtpu_ring.h"
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
static Vtpu_ring* d;
template <class T> static uint32_t getw(const T& s, int idx) { if constexpr (std::is_integral<T>::value) return (uint32_t)(((uint64_t)s) >> (32 * idx)); else return s[idx]; }
template <class T> static void setw(T& s, int idx, uint32_t v) { if constexpr (std::is_integral<T>::value) { uint64_t m = 0xFFFFFFFFull << (32 * idx); s = (T)((((uint64_t)s) & ~m) | ((uint64_t)v << (32 * idx))); } else s[idx] = v; }
template <class T> static uint64_t getbits(const T& s, int lo, int w) { uint64_t r = 0; for (int b = 0; b < w; b++) { int p = lo + b; r |= (uint64_t)((getw(s, p / 32) >> (p % 32)) & 1) << b; } return r; }
template <class T> static void setbits(T& s, int lo, int w, uint64_t v) { for (int b = 0; b < w; b++) { int p = lo + b; uint32_t x = getw(s, p / 32); x = (x & ~(1u << (p % 32))) | (((v >> b) & 1u) << (p % 32)); setw(s, p / 32, x); } }
static void tick() { d->clk = 0; d->eval(); d->clk = 1; d->eval(); }
int main() {
  d = new Vtpu_ring;
  d->clk = 0; d->rst_n = 0; d->lvalid = 0; d->eval();
  for (int i = 0; i < 3; i++) tick();
  d->rst_n = 1; tick();
  int32_t h[8];
  while (fread(h, 4, 8, stdin) == 8) {
    int nb = h[0];
    if (nb < 0) break;
    std::vector<std::vector<uint8_t>> in(CC, std::vector<uint8_t>((size_t)nb * WW));
    for (int c = 0; c < CC; c++) if (fread(in[c].data(), 1, in[c].size(), stdin) != in[c].size()) return 2;
    std::vector<std::vector<std::vector<uint8_t>>> out(CC, std::vector<std::vector<uint8_t>>(CC));   // out[chip][src] = bytes
    std::vector<std::vector<int>> cnt(CC, std::vector<int>(CC, 0));
    for (int c = 0; c < CC; c++) for (int s = 0; s < CC; s++) if (s != c) out[c][s].assign((size_t)nb * WW, 0);
    long got = 0, want = (long)CC * (CC - 1) * nb;
    int64_t cycles = 0; int inj = 0;
    while (got < want) {
      // sample receive side (registered outputs visible before the edge)
      for (int c = 0; c < CC; c++) if ((d->ov >> c) & 1) {
        int src = (int)getbits(d->osrc, 8 * c, 8); int k = cnt[c][src]++;
        for (int b = 0; b < WW; b++) out[c][src][(size_t)k * WW + b] = (uint8_t)getbits(d->odata, (c * WW + b) * 8, 8);
        got++;
      }
      // drive local beats for the coming edge
      if (inj < nb) { for (int c = 0; c < CC; c++) for (int b = 0; b < WW; b++) setbits(d->ldata, (c * WW + b) * 8, 8, in[c][(size_t)inj * WW + b]); d->lvalid = (CC >= 64) ? ~0ull : ((1ull << CC) - 1); inj++; }
      else d->lvalid = 0;
      if (got < want) cycles++;
      tick();
      if (cycles > 100000000) return 4;
    }
    fwrite(&cycles, 8, 1, stdout);
    for (int c = 0; c < CC; c++) for (int s = 0; s < CC; s++) if (s != c) fwrite(out[c][s].data(), 1, out[c][s].size(), stdout);
    fflush(stdout);
    d->lvalid = 0; for (int i = 0; i < 4; i++) tick();
  }
  return 0;
}
