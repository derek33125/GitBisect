// Reduced by the reporter for llvm/llvm-project#178777.
using u8 = unsigned char;

void test(u8 *__restrict arr, unsigned n, u8 y_max) {
  for (unsigned i = 0; i < n; ++i) {
    u8 y = arr[i];
    if (y <= 0)
      continue;

    u8 scaled;
    bool has_overflow = __builtin_mul_overflow(y, (u8)7, &scaled);

    if (has_overflow)
      arr[i] = y / (y_max / 7) + 1;
    else
      arr[i] = scaled / y_max + 1;
  }
}
