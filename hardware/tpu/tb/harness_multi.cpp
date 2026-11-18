// Persistent Verilator harness for tpu_multi (K cores, one weight port each). Same style as harness.cpp (see there for the memory model).
//   in : int32 header[8] = {m, k, n, bwn, bwd, lat, 0, 0} (m=-1 -> quit), then int8 x[m][k], int8 w[k][n]
//   out: int64 cycles, then int32 c[m][n]
// Column tiles are split contiguously over the K cores (a core with no tile is given one dummy tile whose output is discarded).
#include "Vtpu_multi.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include <type_traits>
#ifndef NN
#define NN 16
#endif
#ifndef KK
#define KK 2
#endif
#ifndef HAS_GROUP
#define HAS_GROUP 0
#endif
#ifndef MMAXV
#define MMAXV 64
#endif
#ifndef KTMAXV
#define KTMAXV 8
#endif
static Vtpu_multi* d;
// generic bit-field access on Verilator ports (integral or VlWide)
template <class T> static uint32_t getw(const T& s, int idx) { if constexpr (std::is_integral<T>::value) return (uint32_t)(((uint64_t)s) >> (32 * idx)); else return s[idx]; }
template <class T> static void setw(T& s, int idx, uint32_t v) { if constexpr (std::is_integral<T>::value) { uint64_t m = 0xFFFFFFFFull << (32 * idx); s = (T)((((uint64_t)s) & ~m) | ((uint64_t)v << (32 * idx))); } else s[idx] = v; }
template <class T> static uint64_t getbits(const T& s, int lo, int w) { uint64_t r = 0; for (int b = 0; b < w; b++) { int p = lo + b; r |= (uint64_t)((getw(s, p / 32) >> (p % 32)) & 1) << b; } return r; }
template <class T> static void setbits(T& s, int lo, int w, uint64_t v) { for (int b = 0; b < w; b++) { int p = lo + b; uint32_t x = getw(s, p / 32); x = (x & ~(1u << (p % 32))) | (((v >> b) & 1u) << (p % 32)); setw(s, p / 32, x); } }
static void tick() { d->clk = 0; d->eval(); d->clk = 1; d->eval(); }
int main() {
  d = new Vtpu_multi;
  d->clk = 0; d->rst_n = 0; d->cmd_valid = 0; d->abuf_we = 0; d->mem_req_ready = 0; d->mem_rvalid = 0; d->eval();
  for (int i = 0; i < 4; i++) tick();
  d->rst_n = 1; tick(); tick();
  int32_t h[8];
  while (fread(h, 4, 8, stdin) == 8) {
    int m = h[0], k = h[1], n = h[2], bwn = h[3], bwd = h[4], lat = h[5];
    if (m < 0) break;
    int kt = (k + NN - 1) / NN, nt = (n + NN - 1) / NN;
    int gk = HAS_GROUP ? h[6] : 0, ng = (gk > 0) ? (kt + gk - 1) / gk : 1;
    std::vector<int8_t> x(m * k), w(k * n);
    if (fread(x.data(), 1, x.size(), stdin) != x.size() || fread(w.data(), 1, w.size(), stdin) != w.size()) return 2;
    if (m > MMAXV || kt > KTMAXV) { fprintf(stderr, "shape exceeds build limits\n"); return 3; }
    for (int i = 0; i < kt; i++) for (int r = 0; r < m; r++) {
      uint8_t b[NN]; for (int e = 0; e < NN; e++) { int kk = i * NN + e; b[e] = kk < k ? (uint8_t)x[r * k + kk] : 0; }
      for (int e = 0; e < NN; e++) setbits(d->abuf_wdata, 8 * e, 8, b[e]);
      d->abuf_waddr = i * MMAXV + r; d->abuf_we = 1; tick();
    }
    d->abuf_we = 0; tick();
    // split column tiles over cores
    std::vector<int> nt_c(KK), off_c(KK); int acc = 0;
    for (int c = 0; c < KK; c++) { int share = nt / KK + (c < nt % KK ? 1 : 0); off_c[c] = acc; nt_c[c] = share; acc += share; }
    std::vector<std::vector<uint8_t>> dram(KK);
    for (int c = 0; c < KK; c++) {
      int ntc = nt_c[c] ? nt_c[c] : 1;
      dram[c].assign((size_t)ntc * kt * NN * NN, 0);
      for (int j = 0; j < ntc; j++) for (int i = 0; i < kt; i++) for (int r = 0; r < NN; r++) for (int cc = 0; cc < NN; cc++) {
        int kk = i * NN + r, nn = (off_c[c] + j) * NN + cc; uint8_t v = (nt_c[c] && kk < k && nn < n) ? (uint8_t)w[(size_t)kk * n + nn] : 0;
        dram[c][(((size_t)(j * kt + i) * NN) + r) * NN + cc] = v;
      }
    }
    std::vector<int32_t> out((size_t)ng * m * n, 0);
    d->cmd_m = m; d->cmd_kt = kt;
#if HAS_GROUP
    d->cmd_gk = gk; d->cmd_ng = ng;
#endif
    for (int c = 0; c < KK; c++) { setbits(d->cmd_nt_vec, 16 * c, 16, nt_c[c] ? nt_c[c] : 1); setbits(d->cmd_wbase_vec, 32 * c, 32, 0); }
    d->cmd_valid = 1;
    struct Mem { bool busy = false, have = false; int wait_c = 0, credit = 0, sent = 0; uint32_t base = 0, total = 0; uint8_t held[NN] = {0}; };
    std::vector<Mem> mem(KK);
    bool cmd_taken = false; int64_t cycles = 0; bool done = false;
    while (!done) {
      bool rdy_now = d->cmd_ready;
      std::vector<int> nready(KK, 0), nrvalid(KK, 0); std::vector<std::vector<uint8_t>> beat(KK, std::vector<uint8_t>(NN, 0));
      for (int c = 0; c < KK; c++) {
        Mem& M = mem[c];
        if ((d->out_valid >> c) & 1) {
          int nti = (int)getbits(d->out_nt, 16 * c, 16), mi = (int)getbits(d->out_m, 16 * c, 16);
#if HAS_GROUP
          int gi = (int)getbits(d->out_g, 16 * c, 16);
#else
          int gi = 0;
#endif
          if (nt_c[c]) for (int cc = 0; cc < NN; cc++) { int col = (off_c[c] + nti) * NN + cc; if (col < n && mi < m) out[((size_t)gi * m + mi) * n + col] = (int32_t)getbits(d->out_data, (c * NN + cc) * 32, 32); }
        }
        bool req = (d->mem_req_valid >> c) & 1; uint32_t addr = (uint32_t)getbits(d->mem_req_addr, 32 * c, 32), beats = (uint32_t)getbits(d->mem_req_beats, 32 * c, 32);
        bool rr = (d->mem_rready >> c) & 1;
        if (M.have && rr) M.have = false;
        if (req && !M.busy) { nready[c] = 1; M.busy = true; M.wait_c = lat; M.credit = 0; M.sent = 0; M.base = addr / NN; M.total = beats; }
        if (M.busy && !M.have) {
          if (M.wait_c > 0) M.wait_c--;
          else { M.credit += bwn; if (M.credit >= bwd) { M.credit -= bwd; M.have = true; memcpy(M.held, &dram[c][(size_t)(M.base + M.sent) * NN], NN); M.sent++; if ((uint32_t)M.sent == M.total) M.busy = false; } }
        }
        if (M.have) { nrvalid[c] = 1; memcpy(beat[c].data(), M.held, NN); }
      }
      if (d->cmd_done) done = true;
      if (cmd_taken) d->cmd_valid = 0;
      if (d->cmd_valid && rdy_now) cmd_taken = true;
      if (!done) cycles++;
      tick();
      for (int c = 0; c < KK; c++) {
        setbits(d->mem_req_ready, c, 1, nready[c]); setbits(d->mem_rvalid, c, 1, nrvalid[c]);
        if (nrvalid[c]) for (int e = 0; e < NN; e++) setbits(d->mem_rdata, (c * NN + e) * 8, 8, beat[c][e]);
      }
      d->eval();
      if (cycles > 2000000000LL) return 4;
    }
    fwrite(&cycles, 8, 1, stdout); fwrite(out.data(), 4, out.size(), stdout); fflush(stdout);
    tick();
  }
  return 0;
}
