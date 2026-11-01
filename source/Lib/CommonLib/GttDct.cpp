#include "GttDct.h"

#if HHI_GTT_COMMON
#include "Rom.h"
#include <array>
#include <limits>

namespace
{
template<int N> const TMatrixCoeff *matrix(int direction);
template<> const TMatrixCoeff *matrix<2>(int direction) { return g_trCoreDCT2P2[direction][0]; }
template<> const TMatrixCoeff *matrix<4>(int direction) { return g_trCoreDCT2P4[direction][0]; }
template<> const TMatrixCoeff *matrix<8>(int direction) { return g_trCoreDCT2P8[direction][0]; }
template<> const TMatrixCoeff *matrix<16>(int direction) { return g_trCoreDCT2P16[direction][0]; }
template<> const TMatrixCoeff *matrix<32>(int direction) { return g_trCoreDCT2P32[direction][0]; }

// The original DCT-II matrix's even rows embed the next smaller matrix;
// mirrored input pairs supply sums to the even branch and differences to odd
// rows. Round only after the entire 1-D transform, never inside the recursion.
template<int N> void forwardButterfly(const int64_t *src, int64_t *dst)
{
  const auto *m = matrix<N>(TRANSFORM_FORWARD);
  if constexpr (N == 2)
  {
    dst[0] = m[0] * (src[0] + src[1]);
    dst[1] = m[2] * (src[0] - src[1]);
  }
  else
  {
    int64_t even[N / 2], odd[N / 2], evenResult[N / 2];
    for (int j = 0; j < N / 2; ++j)
    {
      even[j] = src[j] + src[N - 1 - j];
      odd[j] = src[j] - src[N - 1 - j];
    }
    forwardButterfly<N / 2>(even, evenResult);
    for (int k = 0; k < N / 2; ++k)
    {
      dst[2 * k] = evenResult[k];
      int64_t sum = 0;
      for (int j = 0; j < N / 2; ++j) { sum += m[(2 * k + 1) * N + j] * odd[j]; }
      dst[2 * k + 1] = sum;
    }
  }
}

template<int N> void inverseButterfly(const int64_t *src, int64_t *dst)
{
  const auto *m = matrix<N>(TRANSFORM_INVERSE);
  if constexpr (N == 2)
  {
    dst[0] = m[0] * src[0] + m[2] * src[1];
    dst[1] = m[0] * src[0] - m[2] * src[1];
  }
  else
  {
    int64_t even[N / 2], evenResult[N / 2];
    for (int k = 0; k < N / 2; ++k) { even[k] = src[2 * k]; }
    inverseButterfly<N / 2>(even, evenResult);
    for (int j = 0; j < N / 2; ++j)
    {
      int64_t odd = 0;
      for (int k = 0; k < N / 2; ++k) { odd += m[(2 * k + 1) * N + j] * src[2 * k + 1]; }
      dst[j] = evenResult[j] + odd;
      dst[N - 1 - j] = evenResult[j] - odd;
    }
  }
}

int64_t roundedShift(int64_t value, int shift)
{
  // Equivalent to the baseline arithmetic right shift, including negative
  // half ties, without relying on implementation-defined signed shifts.
  const int64_t divisor = int64_t(1) << shift;
  value += divisor / 2;
  return value >= 0 ? value / divisor : -((-value + divisor - 1) / divisor);
}

TCoeff checkedCoeff(int64_t value)
{
  CHECK(value < std::numeric_limits<TCoeff>::min() || value > std::numeric_limits<TCoeff>::max(),
        "GTT DCT exceeds TCoeff range");
  return TCoeff(value);
}

template<int N> void forwardBlock(const CPelBuf &resi, CoeffBuf &coeff, int shift1, int shift2)
{
  std::array<TCoeff, N * N> intermediate;
  int64_t in[N], out[N];
  for (int y = 0; y < N; ++y)
  {
    for (int x = 0; x < N; ++x) { in[x] = resi.buf[y * resi.stride + x]; }
    forwardButterfly<N>(in, out);
    for (int x = 0; x < N; ++x) { intermediate[y * N + x] = checkedCoeff(roundedShift(out[x], shift1)); }
  }
  for (int x = 0; x < N; ++x)
  {
    for (int y = 0; y < N; ++y) { in[y] = intermediate[y * N + x]; }
    forwardButterfly<N>(in, out);
    for (int y = 0; y < N; ++y) { coeff.buf[y * coeff.stride + x] = checkedCoeff(roundedShift(out[y], shift2)); }
  }
}

template<int N> void inverseBlock(const CCoeffBuf &coeff, PelBuf &resi, int shift1, int shift2, int dynamicRange)
{
  std::array<TCoeff, N * N> intermediate;
  int64_t in[N], out[N];
  const int64_t clipMin = -(int64_t(1) << dynamicRange), clipMax = (int64_t(1) << dynamicRange) - 1;
  for (int x = 0; x < N; ++x)
  {
    for (int y = 0; y < N; ++y)
    {
      in[y] = coeff.buf[y * coeff.stride + x];
      CHECK(in[y] < -55929 || in[y] > 55929, "GTT inverse coefficient exceeds the proved range");
    }
    inverseButterfly<N>(in, out);
    for (int y = 0; y < N; ++y) { intermediate[y * N + x] = TCoeff(Clip3(clipMin, clipMax, roundedShift(out[y], shift1))); }
  }
  for (int y = 0; y < N; ++y)
  {
    for (int x = 0; x < N; ++x) { in[x] = intermediate[y * N + x]; }
    inverseButterfly<N>(in, out);
    for (int x = 0; x < N; ++x)
    {
      resi.buf[y * resi.stride + x] = Pel(Clip3<int64_t>(std::numeric_limits<Pel>::min(),
        std::numeric_limits<Pel>::max(), roundedShift(out[x], shift2)));
    }
  }
}

template<typename S, typename D> int checkBuffers(const AreaBuf<S> &src, const AreaBuf<D> &dst,
                                                 int bitDepth, int dynamicRange)
{
  const int n = src.width;
  CHECK(n != 4 && n != 8 && n != 16 && n != 32, "Unsupported wide GTT DCT size");
  CHECK(!src.buf || !dst.buf || src.height != n || dst.width != n || dst.height != n ||
          src.stride < n || dst.stride < n, "Wide GTT DCT requires complete square buffers");
  CHECK(bitDepth != 10 || dynamicRange != 15, "Unsupported GTT numeric contract");
  return n;
}
}

void forwardGttDctWide(const CPelBuf &resi, CoeffBuf &coeff, int bitDepth, int dynamicRange)
{
  const int n = checkBuffers(resi, coeff, bitDepth, dynamicRange);
  const int shift1 = floorLog2(n) + bitDepth + g_transformMatrixShift[TRANSFORM_FORWARD] - dynamicRange + COM16_C806_TRANS_PREC;
  const int shift2 = floorLog2(n) + g_transformMatrixShift[TRANSFORM_FORWARD] + COM16_C806_TRANS_PREC;
  CHECK(shift1 <= 0 || shift2 <= 0 || shift1 > 30 || shift2 > 30, "Invalid GTT forward shifts");
  switch (n)
  {
  case 4: forwardBlock<4>(resi, coeff, shift1, shift2); break;
  case 8: forwardBlock<8>(resi, coeff, shift1, shift2); break;
  case 16: forwardBlock<16>(resi, coeff, shift1, shift2); break;
  case 32: forwardBlock<32>(resi, coeff, shift1, shift2); break;
  }
}

void inverseGttDctWide(const CCoeffBuf &coeff, PelBuf &resi, int bitDepth, int dynamicRange)
{
  const int n = checkBuffers(coeff, resi, bitDepth, dynamicRange);
  const int shift1 = g_transformMatrixShift[TRANSFORM_INVERSE] + 1 + COM16_C806_TRANS_PREC;
  const int shift2 = g_transformMatrixShift[TRANSFORM_INVERSE] + dynamicRange - 1 - bitDepth + COM16_C806_TRANS_PREC;
  CHECK(shift1 <= 0 || shift2 <= 0 || shift1 > 30 || shift2 > 30, "Invalid GTT inverse shifts");
  switch (n)
  {
  case 4: inverseBlock<4>(coeff, resi, shift1, shift2, dynamicRange); break;
  case 8: inverseBlock<8>(coeff, resi, shift1, shift2, dynamicRange); break;
  case 16: inverseBlock<16>(coeff, resi, shift1, shift2, dynamicRange); break;
  case 32: inverseBlock<32>(coeff, resi, shift1, shift2, dynamicRange); break;
  }
}
#endif
