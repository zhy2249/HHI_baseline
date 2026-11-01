#include "Gtt.h"
#include "GttDct.h"
#include "Rom.h"
#include "TrQuant_EMT.h"

#include <array>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <vector>

namespace
{
int checks = 0;
void require(bool condition, const char *message)
{
  ++checks;
  if (!condition) { throw std::runtime_error(message); }
}
template<typename Fn> void requireThrows(Fn fn, const char *message)
{
  bool threw = false;
  try { fn(); } catch (const std::exception &) { threw = true; }
  require(threw, message);
}

void testModes()
{
  constexpr int sizes[] = { 0, 8, 16, 32, 16, 32, 4 };
  constexpr int masks[] = { 0, 2, 4, 8, 4, 8, 1 };
  for (int id = 0; id <= 6; ++id)
  {
    const auto &mode = getGttMode(id);
    require(mode.subExperiment == id && mode.size == sizes[id] && mode.sizeMask == masks[id] &&
              mode.groupMode == (id == 4 || id == 5), "Incorrect experiment mode mapping");
  }
  requireThrows([] { getGttMode(-1); }, "Negative experiment accepted");
  requireThrows([] { getGttMode(7); }, "Unregistered experiment accepted");
}

#if HHI_GTT_COMMON
const TMatrixCoeff *matrix(int n, int direction)
{
  switch (n)
  {
  case 4: return g_trCoreDCT2P4[direction][0];
  case 8: return g_trCoreDCT2P8[direction][0];
  case 16: return g_trCoreDCT2P16[direction][0];
  case 32: return g_trCoreDCT2P32[direction][0];
  default: throw std::runtime_error("Bad reference size");
  }
}

int64_t referenceRound(int64_t value, int shift)
{
  // Independent floor implementation; ties are toward +infinity here.
  const int64_t divisor = int64_t(1) << shift;
  const int64_t biased = value + divisor / 2;
  int64_t result = biased / divisor;
  if (biased < 0 && biased % divisor) { --result; }
  return result;
}

std::vector<TCoeff> referenceForward(const CPelBuf &input)
{
  const int n = input.width;
  const auto *m = matrix(n, TRANSFORM_FORWARD);
  std::vector<TCoeff> stage(n * n), out(n * n);
  const int shift1 = floorLog2(n) + 1, shift2 = floorLog2(n) + 6;
  for (int y = 0; y < n; ++y)
  {
    for (int k = 0; k < n; ++k)
    {
      int64_t sum = 0;
      for (int x = 0; x < n; ++x) { sum += int64_t(m[k * n + x]) * input.buf[y * input.stride + x]; }
      stage[y * n + k] = TCoeff(referenceRound(sum, shift1));
    }
  }
  for (int k = 0; k < n; ++k)
  {
    for (int x = 0; x < n; ++x)
    {
      int64_t sum = 0;
      for (int y = 0; y < n; ++y) { sum += int64_t(m[k * n + y]) * stage[y * n + x]; }
      out[k * n + x] = TCoeff(referenceRound(sum, shift2));
    }
  }
  return out;
}

std::vector<Pel> referenceInverse(const CCoeffBuf &input)
{
  const int n = input.width;
  const auto *m = matrix(n, TRANSFORM_INVERSE);
  std::vector<TCoeff> stage(n * n);
  std::vector<Pel> out(n * n);
  for (int y = 0; y < n; ++y)
  {
    for (int x = 0; x < n; ++x)
    {
      int64_t sum = 0;
      for (int k = 0; k < n; ++k) { sum += int64_t(m[k * n + y]) * input.buf[k * input.stride + x]; }
      stage[y * n + x] = TCoeff(Clip3<int64_t>(-32768, 32767, referenceRound(sum, 7)));
    }
  }
  for (int y = 0; y < n; ++y)
  {
    for (int x = 0; x < n; ++x)
    {
      int64_t sum = 0;
      for (int k = 0; k < n; ++k) { sum += int64_t(m[k * n + x]) * stage[y * n + k]; }
      out[y * n + x] = Pel(Clip3<int64_t>(std::numeric_limits<Pel>::min(),
        std::numeric_limits<Pel>::max(), referenceRound(sum, 10)));
    }
  }
  return out;
}

const ScanElement *scanFor(int n)
{
  return g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG][gp_sizeIdxInfo->idxFrom(n)][gp_sizeIdxInfo->idxFrom(n)];
}

void testLayoutsAndPaths()
{
  for (int modeId = 1; modeId <= 6; ++modeId)
  {
    const auto &mode = getGttMode(modeId);
    const auto layout = makeGttLayout(mode.size, mode.groupMode);
    const int n = layout.width;
    require(layout.numCoeff == n * n && layout.numCg == n * n / 16 && layout.cgSize == 16,
            "Incorrect layout dimensions");
    require(layout.groupBegin[0] == 0 && layout.groupEnd[3] == n * n, "Incomplete logical layout");
    const int first = mode.groupMode ? 16 : n * n / 4;
    require(layout.groupEnd[0] == first, "Incorrect first group");
    std::vector<bool> visited(n * n);
    const auto *scan = scanFor(n);
    for (int i = 0; i < n * n; ++i)
    {
      require(scan[i].idx < unsigned(n * n) && !visited[scan[i].idx], "Scan is not a bijection");
      visited[scan[i].idx] = true;
    }
    for (int group = 1; group < 4; ++group)
    {
      require(layout.groupBegin[group] == layout.groupEnd[group - 1] &&
                layout.groupEnd[group] - layout.groupBegin[group] == (n * n - first) / 3,
              "Incorrect tail group boundary");
    }
    // Golden pair and negative-half rounding repeated at every logical pair.
    std::vector<TCoeff> source(n * n), out(n * n), inv(n * n);
    const int length = layout.groupEnd[1] - layout.groupBegin[1];
    for (int j = 0; j < length; ++j) { source[scan[layout.groupBegin[1] + j].idx] = -128; }
    CoeffBuf result(out.data(), n, n), inverse(inv.data(), n, n);
    forwardGttPath(CCoeffBuf(source.data(), n, n), {1, 0}, result, layout);
    inverseGttPath(result, {1, 0}, inverse, layout);
    for (int j = 0; j < length; ++j)
    {
      require(out[scan[layout.groupBegin[1] + j].idx] == -91 &&
                out[scan[layout.groupBegin[2] + j].idx] == 91, "Rotation golden pair mismatch");
      require(inv[scan[layout.groupBegin[1] + j].idx] == -129 &&
                inv[scan[layout.groupBegin[2] + j].idx] == 0, "Inverse golden pair mismatch");
    }
    for (int pathId = 0; pathId < 4; ++pathId)
    {
      const GttPath path { uint8_t(pathId / 2), uint8_t(pathId % 2) };
      constexpr int golden[4][3] = { {1024, -256, 512}, {1024, 181, 543},
                                     {543, -905, 512}, {543, -278, 1002} };
      std::fill(source.begin(), source.end(), 10);
      for (int j = 0; j < length; ++j)
      {
        source[scan[layout.groupBegin[1] + j].idx] = 1024;
        source[scan[layout.groupBegin[2] + j].idx] = -256;
        source[scan[layout.groupBegin[3] + j].idx] = 512;
      }
      forwardGttPath(CCoeffBuf(source.data(), n, n), path, result, layout);
      for (int j = 0; j < length; ++j)
      {
        for (int group = 1; group < 4; ++group)
        {
          require(out[scan[layout.groupBegin[group] + j].idx] == golden[pathId][group - 1],
                  "Two-level full-path golden result mismatch");
        }
      }
      for (int i = 0; i < n * n; ++i) { source[i] = TCoeff(i * 19 % 2049 - 1024); }
      forwardGttPath(CCoeffBuf(source.data(), n, n), path, result, layout);
      std::vector<TCoeff> alias = source;
      CoeffBuf aliasBuf(alias.data(), n, n);
      forwardGttPath(aliasBuf, path, aliasBuf, layout);
      require(alias == out, "Forward path aliasing changed coefficients");
      inverseGttPath(result, path, inverse, layout);
      inverseGttPath(aliasBuf, path, aliasBuf, layout);
      require(alias == inv, "Inverse path aliasing changed coefficients");
      if (n == 4)
      {
        std::array<TCoeff, 16> legacy;
        CoeffBuf old(legacy.data(), 4, 4);
        forwardGtt4x4Path(CCoeffBuf(source.data(), 4, 4), path, old);
        require(std::equal(legacy.begin(), legacy.end(), out.begin()), "4x4 forward migration changed coefficients");
        inverseGtt4x4Path(old, path, old);
        require(std::equal(legacy.begin(), legacy.end(), inv.begin()), "4x4 inverse migration changed coefficients");
      }
    }
    std::fill(source.begin(), source.end(), 0);
    source[scan[0].idx] = std::numeric_limits<TCoeff>::min();
    source[scan[layout.groupEnd[0] - 1].idx] = -1;
    source[scan[layout.groupBegin[1]].idx] = -3;
    const auto path = deriveGttPath(CCoeffBuf(source.data(), n, n), layout);
    require(path.k2 == 1 && path.k3 == 1, "Final-level parity or boundary mismatch");
    requireThrows([&] { forwardGttPath(result, {2, 0}, result, layout); }, "Invalid kernel accepted");
    std::fill(out.begin(), out.end(), std::numeric_limits<TCoeff>::max());
    requireThrows([&] { forwardGttPath(result, {1, 0}, result, layout); }, "Rotation overflow not rejected");
  }
  const int expectedCg16[] = {0, 4, 1, 8, 5, 2, 12, 9, 6, 3, 13, 10, 7, 14, 11, 15};
  const auto *scan = scanFor(16);
  for (int cg = 0; cg < 16; ++cg)
  {
    const int pos = scan[cg * 16].idx;
    require((pos / 16 / 4) * 4 + pos % 16 / 4 == expectedCg16[cg], "16x16 CG diagonal order mismatch");
  }
  requireThrows([] { makeGttLayout(4, 1); }, "Fixed-first-CG accepted for 4x4");
  requireThrows([] { makeGttLayout(64, 0); }, "Unspecified 64x64 accepted");
  requireThrows([] { makeGttLayout(16, 2); }, "Unknown grouping accepted");
  const auto equal8 = makeGttLayout(8, 0), firstCg8 = makeGttLayout(8, 1);
  require(equal8.groupBegin == firstCg8.groupBegin && equal8.groupEnd == firstCg8.groupEnd,
          "8x8 grouping modes should have identical boundaries");
}

void testWideDct()
{
  std::mt19937 random(0x47545433);
  for (const int n: {4, 8, 16, 32})
  {
    const int stride = n + 3;
    std::vector<Pel> residual(stride * n, Pel(12345)), reconstruction(stride * n, Pel(23456));
    std::vector<TCoeff> coeff(stride * n, TCoeff(456789));
    CPelBuf input(residual.data(), stride, n, n);
    CoeffBuf output(coeff.data(), stride, n, n);
    PelBuf recon(reconstruction.data(), stride, n, n);
    for (int pattern = 0; pattern < 18; ++pattern)
    {
      for (int y = 0; y < n; ++y)
      {
        for (int x = 0; x < n; ++x)
        {
          const int index = y * stride + x;
          if (pattern == 0) { residual[index] = 0; }
          else if (pattern == 1) { residual[index] = std::numeric_limits<Pel>::min(); }
          else if (pattern == 2) { residual[index] = std::numeric_limits<Pel>::max(); }
          else if (pattern == 3) { residual[index] = (x + y) & 1 ? 32767 : -32768; }
          else if (pattern == 4) { residual[index] = (x == n / 2 && y == n / 2) ? -32768 : 0; }
          else { residual[index] = Pel(int(random() & 65535) - 32768); }
        }
      }
      const auto expected = referenceForward(input);
      forwardGttDctWide(input, output, 10, 15);
      for (int y = 0; y < n; ++y)
      {
        for (int x = 0; x < n; ++x)
        {
          require(coeff[y * stride + x] == expected[y * n + x], "Wide butterfly forward/reference mismatch");
        }
        for (int x = n; x < stride; ++x) { require(coeff[y * stride + x] == 456789, "DCT changed output row padding"); }
      }
      if (n == 4)
      {
        TCoeff block[16], tmp[16], legacy[16];
        for (int y = 0; y < n; ++y)
          for (int x = 0; x < n; ++x) { block[y * n + x] = residual[y * stride + x]; }
        fastForwardDCT2_B4(block, tmp, 3, 4, 0, 0);
        fastForwardDCT2_B4(tmp, legacy, 8, 4, 0, 0);
        require(std::equal(legacy, legacy + 16, expected.begin()), "4x4 DCT migration changed forward result");
      }
      for (int y = 0; y < n; ++y)
      {
        for (int x = 0; x < n; ++x)
        {
          coeff[y * stride + x] = pattern < 2 ? (pattern ? 55929 : -55929) : TCoeff(int(random() % 111859) - 55929);
        }
      }
      const auto expectedRecon = referenceInverse(output);
      inverseGttDctWide(output, recon, 10, 15);
      for (int y = 0; y < n; ++y)
      {
        for (int x = 0; x < n; ++x)
        {
          require(reconstruction[y * stride + x] == expectedRecon[y * n + x], "Wide butterfly inverse/reference mismatch");
        }
        for (int x = n; x < stride; ++x) { require(reconstruction[y * stride + x] == 23456, "Inverse changed row padding"); }
      }
    }
    coeff[0] = 55930;
    requireThrows([&] { inverseGttDctWide(output, recon, 10, 15); }, "Out-of-contract inverse input accepted");
    requireThrows([&] { forwardGttDctWide(input, output, 12, 15); }, "Unsupported DCT numeric contract accepted");
  }
}
#endif
}

int main()
{
  try
  {
    testModes();
#if HHI_GTT_COMMON
    initROM();
    testLayoutsAndPaths();
    testWideDct();
    destroyROM();
    std::cout << "GTT common: " << checks << " checks passed\n";
    return 0;
#else
    std::cout << "GTT mode table: " << checks << " checks passed; shared algorithm is compiled out\n";
    return 77;
#endif
  }
  catch (const std::exception &error)
  {
    std::cerr << "GTT common failure: " << error.what() << '\n';
    return 1;
  }
}
