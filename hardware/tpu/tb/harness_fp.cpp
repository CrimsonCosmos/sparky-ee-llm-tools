// stdin: uint32 n, then n pairs (a, b) of float32 bit patterns; stdout: n uint32 results of the fp32_add RTL.
#include "Vfp32_add.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <vector>
int main() {
  Vfp32_add* d = new Vfp32_add;
  uint32_t n; if (fread(&n, 4, 1, stdin) != 1) return 2;
  std::vector<uint32_t> ab(2 * (size_t)n), y(n);
  if (fread(ab.data(), 4, ab.size(), stdin) != ab.size()) return 2;
  for (uint32_t i = 0; i < n; i++) { d->a = ab[2 * i]; d->b = ab[2 * i + 1]; d->eval(); y[i] = d->y; }
  fwrite(y.data(), 4, n, stdout);
  return 0;
}
