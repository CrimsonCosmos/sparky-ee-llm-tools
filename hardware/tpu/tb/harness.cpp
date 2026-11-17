// Persistent Verilator harness for any tpu_core version (same port interface). Protocol on stdin/stdout (little endian):
//   in : int32 header[8] = {m, k, n, bwn, bwd, lat, 0, 0} (m=-1 -> quit), then int8 x[m][k], int8 w[k][n]
//   out: int64 cycles, then int32 c[m][n]
// Memory model: after a request, `lat` idle cycles, then one N-byte beat whenever the credit counter (+bwn per cycle, -bwd per beat) allows.
// Stimulus/sampling order matches tb_core.sv (inputs registered at the edge, DUT samples the old values).
#include "Vtpu_core.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include <type_traits>
#ifndef NN
#define NN 16
#endif
#ifndef ROWREV
#define ROWREV 1          // 1: tile rows stored in reverse order (v1 shift-chain load); 0: ascending
#endif
#ifndef HAS_RREADY
#define HAS_RREADY 0      // 1: core exports mem_rready (backpressure) and takes ONE request per command for all tiles
#endif
#ifndef MMAXV
#define MMAXV 64
#endif
#ifndef KTMAXV
#define KTMAXV 8
#endif
static Vtpu_core* d;
template <class T> static void setb(T& sig, const uint8_t* bytes) {                 // bytes[0] = least-significant byte
  if constexpr (std::is_integral<T>::value) { uint64_t v = 0; for (int i = 0; i < NN; i++) v |= (uint64_t)bytes[i] << (8 * i); sig = (T)v; }
  else { for (size_t w = 0; w < sizeof(sig.m_storage) / 4; w++) { uint32_t v = 0; for (int b = 0; b < 4; b++) { size_t i = w * 4 + b; if (i < (size_t)NN) v |= (uint32_t)bytes[i] << (8 * b); } sig[w] = v; } }
}
template <class T> static uint32_t word(const T& sig, int idx) {                      // idx-th 32-bit word
  if constexpr (std::is_integral<T>::value) return (uint32_t)(((uint64_t)sig) >> (32 * idx)); else return sig[idx];
}
static void tick() { d->clk = 0; d->eval(); d->clk = 1; d->eval(); }
int main() {
  d = new Vtpu_core;
  d->clk = 0; d->rst_n = 0; d->cmd_valid = 0; d->abuf_we = 0; d->mem_req_ready = 0; d->mem_rvalid = 0; d->eval();
  for (int i = 0; i < 4; i++) tick();
  d->rst_n = 1; tick(); tick();
  int32_t h[8];
  while (fread(h, 4, 8, stdin) == 8) {
    int m = h[0], k = h[1], n = h[2], bwn = h[3], bwd = h[4], lat = h[5];
    if (m < 0) break;
    int kt = (k + NN - 1) / NN, nt = (n + NN - 1) / NN;
    std::vector<int8_t> x(m * k), w(k * n);
    if (fread(x.data(), 1, x.size(), stdin) != x.size() || fread(w.data(), 1, w.size(), stdin) != w.size()) return 2;
    if (m > MMAXV || kt > KTMAXV) { fprintf(stderr, "shape exceeds build limits m=%d kt=%d\n", m, kt); return 3; }
    // activation buffer: entry kt_i*MMAX + row holds x[row][kt_i*N .. +N) zero padded
    for (int i = 0; i < kt; i++) for (int r = 0; r < m; r++) {
      uint8_t b[NN]; for (int e = 0; e < NN; e++) { int kk = i * NN + e; b[e] = kk < k ? (uint8_t)x[r * k + kk] : 0; }
      setb(d->abuf_wdata, b); d->abuf_waddr = i * MMAXV + r; d->abuf_we = 1; tick();
    }
    d->abuf_we = 0; tick();
    // DRAM: tiles [nt][kt], rows in reverse order, one beat per row
    std::vector<uint8_t> dram((size_t)nt * kt * NN * NN, 0);
    for (int j = 0; j < nt; j++) for (int i = 0; i < kt; i++) for (int r = 0; r < NN; r++) for (int c = 0; c < NN; c++) {
      int kk = i * NN + r, nn = j * NN + c; uint8_t v = (kk < k && nn < n) ? (uint8_t)w[(size_t)kk * n + nn] : 0;
      dram[(((size_t)(j * kt + i) * NN) + (ROWREV ? (NN - 1 - r) : r)) * NN + c] = v;
    }
    std::vector<int32_t> out((size_t)m * n, 0); long got = 0, want = (long)m * nt;
    d->cmd_m = m; d->cmd_kt = kt; d->cmd_nt = nt; d->cmd_wbase = 0; d->cmd_valid = 1;
    bool busy = false; int wait_c = 0, credit = 0, sent = 0; uint32_t base = 0, total = 0; bool cmd_taken = false, have_beat = false, pending_req = false; uint8_t held[NN] = {0};
    int64_t cycles = 0; bool done = false;
    while (!done) {
      // sample pre-edge outputs
      bool req = d->mem_req_valid; uint32_t addr = d->mem_req_addr; uint32_t beats = d->mem_req_beats; bool rdy_now = d->cmd_ready;
      if (d->out_valid) {
        int nti = d->out_nt, mi = d->out_m;
        for (int c = 0; c < NN; c++) { int col = nti * NN + c; if (col < n && mi < m) out[(size_t)mi * n + col] = (int32_t)word(d->out_data, c); }
        got++;
      }
      if (d->cmd_done) done = true;
      // memory model (registered outputs for the next cycle)
      int nrvalid = 0, nready = 0; uint8_t beat[NN] = {0};
#if HAS_RREADY
      if (have_beat && d->mem_rready) { have_beat = false; }          // beat accepted at the coming edge
      if (req && !busy && !pending_req) { nready = 1; busy = true; wait_c = lat; credit = 0; sent = 0; base = addr / NN; total = beats; }
      if (busy && !have_beat) {
        if (wait_c > 0) wait_c--;
        else { credit += bwn; if (credit >= bwd) { credit -= bwd; have_beat = true; memcpy(held, &dram[(size_t)(base + sent) * NN], NN); sent++; if ((uint32_t)sent == total) busy = false; } }
      }
      if (have_beat) { nrvalid = 1; memcpy(beat, held, NN); }
      // note: a beat presented for the next cycle stays presented until accepted
#else
      if (req && !busy) { nready = 1; busy = true; wait_c = lat; credit = 0; sent = 0; base = addr / NN; }
      else if (busy) {
        if (wait_c > 0) wait_c--;
        else { credit += bwn; if (credit >= bwd) { credit -= bwd; nrvalid = 1; memcpy(beat, &dram[(size_t)(base + sent) * NN], NN); sent++; if ((uint32_t)sent == beats) busy = false; } }
      }
#endif
      if (cmd_taken) d->cmd_valid = 0;
      if (d->cmd_valid && rdy_now) cmd_taken = true;     // taken at the coming edge
      if (!done) cycles++;
      tick();
      d->mem_req_ready = nready; d->mem_rvalid = nrvalid; if (nrvalid) setb(d->mem_rdata, beat); d->eval();
      if (cycles > 2000000000LL) return 4;
    }
    if (got != want) { fprintf(stderr, "outputs %ld != %ld\n", got, want); return 5; }
    fwrite(&cycles, 8, 1, stdout); fwrite(out.data(), 4, out.size(), stdout); fflush(stdout);
    tick();
  }
  return 0;
}
