Write a combinational Verilog module `fp16add` that adds two IEEE-754 binary16 (half-precision) numbers: inputs `a[15:0]`, `b[15:0]`, output `y[15:0]` = a + b, correctly rounded to nearest, ties to even, as the standard requires.
Format: 1 sign bit, 5 exponent bits (bias 15), 10 fraction bits. Exponent 0 = zero/subnormal (value = fraction * 2^-24), exponent 31 = infinity (fraction 0) or NaN (fraction != 0). Requirements:
 * subnormal inputs and results are handled exactly (no flush to zero); results too large become infinity (of the right sign) after rounding;
 * exact cancellation of two nonzero operands gives +0; (+0) + (+0) = +0, (-0) + (-0) = -0, (+0) + (-0) = +0; x + (+-0) = x;
 * infinity + finite = infinity; infinity + same-sign infinity = that infinity; infinity + opposite-sign infinity = NaN;
 * any NaN input, or an invalid operation, produces the single canonical quiet NaN 16'h7E00 (payloads and signs of input NaNs are not propagated).
Fewer LUT4s and fewer logic levels are better. The design must be exactly right for all 2^32 input pairs.
