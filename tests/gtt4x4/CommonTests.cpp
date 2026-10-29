#include "CodingStructure.h"
#include "Gtt4x4.h"
#include "Rom.h"
#include "TrQuant.h"
#include "UnitTools.h"

#include <array>
#include <cmath>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>

#if HHI_GTT4X4

namespace
{
int checks = 0;
constexpr std::array<unsigned, 16> scan { 0, 4, 1, 8, 5, 2, 12, 9, 6, 3, 13, 10, 7, 14, 11, 15 };
constexpr int matrix[4][4] = { { 64, 64, 64, 64 }, { 83, 36, -36, -83 },
                               { 64, -64, -64, 64 }, { 36, -83, 83, -36 } };

void require(bool condition, const char *message)
{
  ++checks;
  if (!condition)
  {
    throw std::runtime_error(message);
  }
}

template<typename Fn> void requireThrows(Fn fn, const char *message)
{
  bool threw = false;
  try
  {
    fn();
  }
  catch (const std::exception &)
  {
    threw = true;
  }
  require(threw, message);
}

std::array<TCoeff, 16> raster(const std::array<TCoeff, 16> &values)
{
  std::array<TCoeff, 16> result {};
  for (unsigned i = 0; i < 16; ++i)
  {
    result[scan[i]] = values[i];
  }
  return result;
}

int64_t originalRound(int64_t value, int shift)
{
  const int64_t divisor = int64_t(1) << shift;
  value += divisor / 2;
  return value >= 0 ? value / divisor : -((-value + divisor - 1) / divisor);
}

void referenceSynthesis(std::array<TCoeff, 16> &coeff, Gtt4x4Path path)
{
  for (int stage = 1; stage >= 0; --stage)
  {
    if (!(stage ? path.k3 : path.k2))
    {
      continue;
    }
    const int first = stage ? 8 : 4;
    const int second = stage ? 12 : 8;
    for (int i = 0; i < 4; ++i)
    {
      const double u = coeff[scan[first + i]];
      const double v = coeff[scan[second + i]];
      coeff[scan[first + i]] = TCoeff(std::llround((u - v) * (724.0 / 1024.0)));
      coeff[scan[second + i]] = TCoeff(std::llround((u + v) * (724.0 / 1024.0)));
    }
  }
}

std::array<Pel, 16> referenceInverse(const std::array<TCoeff, 16> &coeff)
{
  int64_t tmp[16] {};
  std::array<Pel, 16> result {};
  for (int y = 0; y < 4; ++y)
  {
    for (int x = 0; x < 4; ++x)
    {
      int64_t sum = 0;
      for (int k = 0; k < 4; ++k)
      {
        sum += int64_t(matrix[k][y]) * coeff[k * 4 + x];
      }
      tmp[y * 4 + x] = Clip3<int64_t>(-32768, 32767, originalRound(sum, 7));
    }
  }
  for (int y = 0; y < 4; ++y)
  {
    for (int x = 0; x < 4; ++x)
    {
      int64_t sum = 0;
      for (int k = 0; k < 4; ++k)
      {
        sum += int64_t(matrix[k][x]) * tmp[y * 4 + k];
      }
      result[y * 4 + x] = Pel(Clip3<int64_t>(std::numeric_limits<Pel>::min(), std::numeric_limits<Pel>::max(),
                                             originalRound(sum, 10)));
    }
  }
  return result;
}

struct Fixture
{
  SPS sps;
  PPS pps;
  Slice slice;
  CodingStructure cs { g_xuPool };
  CodingUnit cu { ChromaFormat::_444, Area(0, 0, 4, 4) };
  TransformUnit tu { ChromaFormat::_444, Area(0, 0, 4, 4) };
  TransformUnit copy { ChromaFormat::_444, Area(0, 0, 4, 4) };
  std::unique_ptr<PreCalcValues> pcv;
  std::array<std::array<TCoeff, 16>, 3> levels {};
  std::array<std::array<TCoeff, 16>, 3> copiedLevels {};
  std::array<std::array<uint8_t, 16>, 3> signs {};
  std::array<std::array<uint8_t, 16>, 3> copiedSigns {};
  TrQuant trQuant;

  Fixture()
  {
    sps.m_chromaFormatIdc = ChromaFormat::_444;
    sps.m_bitDepths[ChannelType::LUMA] = sps.m_bitDepths[ChannelType::CHROMA] = 10;
    sps.m_qpBDOffset[ChannelType::LUMA] = sps.m_qpBDOffset[ChannelType::CHROMA] = 12;
    sps.m_gtt4x4Enabled = true;
    sps.m_gtt4x4Version = GTT4X4_VERSION;
    sps.m_depQuantEnabledFlag = true;
    sps.m_numPredSign = 4;
    sps.m_ctuSize = 64;
    pps.m_picWidthInLumaSamples = pps.m_picHeightInLumaSamples = 64;
    pcv = std::make_unique<PreCalcValues>(sps, pps, true);
    slice.m_sps = &sps;
    slice.m_pps = &pps;
    slice.m_depQuantEnabledIdc = 1;
    slice.m_eSliceType = I_SLICE;
    cs.area = UnitArea(ChromaFormat::_444, Area(0, 0, 4, 4));
    cs.sps = &sps;
    cs.pps = &pps;
    cs.slice = &slice;
    cs.pcv = pcv.get();
    cu.cs = &cs;
    cu.slice = &slice;
    cu.predMode = MODE_INTRA;
    cu.qp = 27;
    cu.firstTU = cu.lastTU = &tu;
    bind(tu, levels, signs);
    bind(copy, copiedLevels, copiedSigns);
    trQuant.init(nullptr, 4, true, true, false, true);
    trQuant.getQuant()->setUseScalingList(false);
    trQuant.setLambda(1.0);
  }

  void bind(TransformUnit &unit, std::array<std::array<TCoeff, 16>, 3> &coeff,
            std::array<std::array<uint8_t, 16>, 3> &sign)
  {
    unit.cs = &cs;
    unit.cu = &cu;
    TCoeff *coeffPointers[MAX_NUM_TBLOCKS] = { coeff[0].data(), coeff[1].data(), coeff[2].data() };
    uint8_t *signPointers[MAX_NUM_TBLOCKS] = { sign[0].data(), sign[1].data(), sign[2].data() };
    EnumArray<Pel *, ChannelType> palette;
    EnumArray<bool *, ChannelType> run;
    palette.fill(nullptr);
    run.fill(nullptr);
    unit.init(coeffPointers, signPointers, palette, run);
  }
};

void testIntegerOperators()
{
  const auto *actualScan = g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
                                    [gp_sizeIdxInfo->idxFrom(4)][gp_sizeIdxInfo->idxFrom(4)];
  for (unsigned i = 0; i < 16; ++i)
  {
    require(actualScan[i].idx == scan[i], "Unexpected 4x4 diagonal scan");
  }
  constexpr int golden[][6] = {
    { 0, 0, 0, 0, 0, 0 }, { 1024, 0, 724, -724, 1024, 0 }, { 0, 1024, 724, 724, 0, 1024 },
    { 2048, 2048, 2896, 0, 2048, 2048 }, { 2048, -2048, 0, -2896, 2048, -2048 },
    { -2048, -2048, -2896, 0, -2048, -2048 }, { 1, 0, 1, -1, 1, 0 }, { 2, 0, 1, -1, 1, 0 },
    { 128, 0, 91, -91, 129, 0 }, { -128, 0, -91, 91, -129, 0 }
  };
  for (const auto &row: golden)
  {
    std::array<TCoeff, 16> input {}, output {}, inverse {};
    input[scan[4]] = row[0];
    input[scan[8]] = row[1];
    CoeffBuf out(output.data(), 4, 4), inv(inverse.data(), 4, 4);
    forwardGtt4x4Path(CCoeffBuf(input.data(), 4, 4), { 1, 0 }, out);
    require(output[scan[4]] == row[2] && output[scan[8]] == row[3], "Q10 forward golden vector failed");
    inverseGtt4x4Path(out, { 1, 0 }, inv);
    require(inverse[scan[4]] == row[4] && inverse[scan[8]] == row[5], "Q10 synthesis golden vector failed");
  }

  const std::array<TCoeff, 16> rootScan { 10, -20, 30, -40, 1024, -512, 2, 128, -256, 1024, -1, 0,
                                        512, -1024, 3, -128 };
  const std::array<std::array<TCoeff, 16>, 4> expectedScan {{
    rootScan,
    { 10, -20, 30, -40, 1024, -512, 2, 128, 181, 0, 1, -91, 543, -1448, 3, -91 },
    { 10, -20, 30, -40, 543, 362, 1, 91, -905, 1086, -2, -91, 512, -1024, 3, -128 },
    { 10, -20, 30, -40, 543, 362, 1, 91, -278, 44, 1, -155, 1002, -1492, 4, -26 }
  }};
  const auto input = raster(rootScan);
  for (unsigned pathId = 0; pathId < 4; ++pathId)
  {
    const Gtt4x4Path path { uint8_t(pathId / 2), uint8_t(pathId % 2) };
    std::array<TCoeff, 16> output {};
    CoeffBuf out(output.data(), 4, 4);
    forwardGtt4x4Path(CCoeffBuf(input.data(), 4, 4), path, out);
    require(output == raster(expectedScan[pathId]), "Four-path forward golden vector failed");
    auto expected = output;
    referenceSynthesis(expected, path);
    inverseGtt4x4Path(out, path, out);
    require(output == expected, "Synthesis order or aliasing mismatch");
    require(input == raster(rootScan), "Path generation mutated the root buffer");

    std::array<TCoeff, 16> zeroSuffix {};
    zeroSuffix[scan[0]] = 1024;
    const auto before = zeroSuffix;
    CoeffBuf suffix(zeroSuffix.data(), 4, 4);
    inverseGtt4x4Path(suffix, path, suffix);
    require(zeroSuffix == before, "Unused kernels changed a zero suffix");
  }

  for (uint8_t k2 = 0; k2 < 2; ++k2)
  {
    std::array<TCoeff, 16> stopped {};
    for (unsigned i = 0; i < 8; ++i)
    {
      stopped[scan[i]] = TCoeff(100 + i);
    }
    auto continued = stopped;
    CoeffBuf stopBuf(stopped.data(), 4, 4), continueBuf(continued.data(), 4, 4);
    inverseGtt4x4Path(stopBuf, { k2, 0 }, stopBuf);
    inverseGtt4x4Path(continueBuf, { k2, 1 }, continueBuf);
    require(stopped == continued, "Unused K3 changed a tau2 reconstruction");
  }

  std::array<TCoeff, 24> padded;
  padded.fill(777);
  for (unsigned i = 0; i < 16; ++i)
  {
    padded[i / 4 * 6 + i % 4] = input[i];
  }
  CoeffBuf strided(padded.data(), 6, 4, 4);
  forwardGtt4x4Path(strided, { 1, 1 }, strided);
  const auto expected = raster(expectedScan[3]);
  for (unsigned i = 0; i < 16; ++i)
  {
    require(padded[i / 4 * 6 + i % 4] == expected[i], "Strided path differs from contiguous path");
  }
  for (int y = 0; y < 4; ++y)
  {
    require(padded[y * 6 + 4] == 777 && padded[y * 6 + 5] == 777, "Path modified row padding");
  }

  std::array<TCoeff, 16> levels {};
  levels[scan[0]] = std::numeric_limits<TCoeff>::min();
  levels[scan[1]] = -1;
  levels[scan[4]] = 3;
  const auto path = deriveGtt4x4Path(CCoeffBuf(levels.data(), 4, 4));
  require(path.k2 == 1 && path.k3 == 1, "Level parity failed at signed coefficient boundary");
  CoeffBuf levelBuffer(levels.data(), 4, 4);
  requireThrows([&] { forwardGtt4x4Path(levelBuffer, { 2, 0 }, levelBuffer); }, "Invalid path was accepted");
  levels.fill(std::numeric_limits<TCoeff>::max());
  requireThrows([&] { forwardGtt4x4Path(levelBuffer, { 1, 0 }, levelBuffer); }, "TCoeff overflow was not rejected");
}

void testLifecycleAndEligibility(Fixture &fixture)
{
  auto &tu = fixture.tu;
  require(!tu.gttFlag && !fixture.copy.gttFlag, "New TU must clear the GTT flag");
  require(TU::isGttApplicable(tu, COMP_Y), "Supported synthetic TU is ineligible");
  require(!TU::isGttFlagPresent(tu, COMP_Y), "CBF0 must omit the flag");
  TU::setCbfAtDepth(tu, COMP_Y, tu.depth, true);
  require(TU::isGttFlagPresent(tu, COMP_Y), "Supported nonzero legacy TU must carry flag zero");
  tu.gttFlag = true;
  fixture.levels[0][0] = 7;
  fixture.copy = tu;
  require(fixture.copy.gttFlag && fixture.copiedLevels[0][0] == 7, "Whole-TU copy lost GTT state");
  fixture.copy.gttFlag = false;
  tu.copyComponentFrom(fixture.copy, COMP_Cb);
  require(tu.gttFlag, "Copying Cb cleared the luma GTT flag");
  tu.copyComponentFrom(fixture.copy, COMP_Cr);
  require(tu.gttFlag, "Copying Cr cleared the luma GTT flag");
  tu.copyComponentFrom(fixture.copy, COMP_Y);
  require(!tu.gttFlag, "Copying Y did not copy its GTT flag");
  tu.gttFlag = true;
  TU::setCbfAtDepth(tu, COMP_Cb, tu.depth, false);
  require(tu.gttFlag, "Chroma CBF0 cleared the luma GTT flag");
  require(!TU::getDelayedSignCoding(tu, COMP_Y) && !TU::getUseSignPred(tu, COMP_Y),
          "GTT luma entered delayed sign prediction");
  require(TU::getDelayedSignCoding(tu, COMP_Cb), "GTT luma disabled chroma sign prediction");
  require(!TU::isMTSAllowed(tu, COMP_Y) && !TU::lfnstAllowed(tu, COMP_Y), "GTT permits old transform syntax");
  tu.mtsIdx[COMP_Y] = MtsType::DCT2_NST1;
  require(TU::getNstIdx(tu, COMP_Y) == 0, "GTT leaked a stale NST index");
  tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
  TU::setCbfAtDepth(tu, COMP_Y, tu.depth, false);
  require(!tu.gttFlag, "Luma CBF0 left GTT active");
  require(TU::getDelayedSignCoding(tu, COMP_Y), "Legacy luma lost sign prediction");
  TU::setCbfAtDepth(tu, COMP_Y, tu.depth, true);
  tu.mtsIdx[COMP_Y] = MtsType::SKIP;
  require(TU::isGttApplicable(tu, COMP_Y) && !TU::isGttFlagPresent(tu, COMP_Y),
          "TS winner should omit syntax without suppressing independent GTT search");
  tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
  for (const auto mode: { MODE_IBC, MODE_PLT })
  {
    fixture.cu.predMode = mode;
    require(!TU::isGttApplicable(tu, COMP_Y), "Unsupported prediction mode is eligible");
  }
  fixture.cu.predMode = MODE_INTER;
  require(TU::isGttApplicable(tu, COMP_Y), "Ordinary inter TU is ineligible");
  fixture.cu.predMode = MODE_INTRA;
  fixture.slice.m_depQuantEnabledIdc = 2;
  require(!TU::isGttApplicable(tu, COMP_Y), "DQ2 is eligible");
  fixture.slice.m_depQuantEnabledIdc = 1;
  fixture.sps.m_gtt4x4Version = 1;
  require(!TU::isGttApplicable(tu, COMP_Y), "Obsolete EP syntax version is eligible");
  fixture.sps.m_gtt4x4Version = GTT4X4_VERSION;
  fixture.sps.m_spsRangeExtension.m_rrcRiceExtensionEnableFlag = true;
  require(!TU::isGttApplicable(tu, COMP_Y), "Unsupported synthetic REXT TU is eligible");
  fixture.sps.m_spsRangeExtension.m_rrcRiceExtensionEnableFlag = false;
  tu.gttFlag = true;
  tu.initData();
  require(!tu.gttFlag && tu.lastPos[COMP_Y] == -1 && !TU::getCbf(tu, COMP_Y), "Reinitialized TU retained state");
}

void testFixedDctAndInverse(Fixture &fixture)
{
  // Full Pel extremes cover overshooting prediction blends as well as normal
  // 10-bit residuals. The expected transform uses an independent matrix loop.
  for (int pattern = 0; pattern < 36; ++pattern)
  {
    std::array<Pel, 16> residual {};
    for (int i = 0; i < 16; ++i)
    {
      residual[i] = pattern < 16 ? (i == pattern ? 1023 : 0)
        : pattern < 32 ? (i == pattern - 16 ? -1023 : 0)
        : pattern == 32 ? 1023 : pattern == 33 ? -1023
        : pattern == 34 ? std::numeric_limits<Pel>::min()
        : (i & 1) ? std::numeric_limits<Pel>::max() : std::numeric_limits<Pel>::min();
    }
    std::array<TCoeff, 16> actual {}, expected {};
    int64_t tmp[16] {};
    for (int y = 0; y < 4; ++y)
    {
      for (int x = 0; x < 4; ++x)
      {
        int64_t sum = 0;
        for (int k = 0; k < 4; ++k)
        {
          sum += int64_t(matrix[x][k]) * residual[y * 4 + k];
        }
        tmp[y * 4 + x] = originalRound(sum, 3);
      }
    }
    for (int y = 0; y < 4; ++y)
    {
      for (int x = 0; x < 4; ++x)
      {
        int64_t sum = 0;
        for (int k = 0; k < 4; ++k)
        {
          sum += int64_t(matrix[y][k]) * tmp[k * 4 + x];
        }
        expected[y * 4 + x] = TCoeff(originalRound(sum, 8));
      }
    }
    CoeffBuf actualBuf(actual.data(), 4, 4);
    fixture.trQuant.forwardGtt4x4Dct(fixture.tu, CPelBuf(residual.data(), 4, 4), actualBuf);
    require(actual == expected, "Fixed GTT DCT differs from the original integer matrix and shifts");
  }

  fixture.tu.gttFlag = true;
  TU::setCbfAtDepth(fixture.tu, COMP_Y, 0, true);
  for (int high = 0; high < 2; ++high)
  {
    for (unsigned pathId = 0; pathId < 4; ++pathId)
    {
      const Gtt4x4Path path { uint8_t(pathId / 2), uint8_t(pathId % 2) };
      auto &levels = fixture.levels[0];
      levels.fill(high ? 16000 : 2);
      levels[scan[0]] += path.k2;
      levels[scan[4]] += path.k3;
      for (int i = 1; i < 16; i += 2)
      {
        levels[scan[i]] = -levels[scan[i]];
      }
      fixture.tu.lastPos[COMP_Y] = 15;
      std::array<TCoeff, 16> dequant {};
      CoeffBuf dequantBuf(dequant.data(), 4, 4);
      const QpParam qp(fixture.tu, COMP_Y);
      fixture.trQuant.getQuant()->dequant(fixture.tu, dequantBuf, COMP_Y, qp);
      referenceSynthesis(dequant, path);
      const auto expected = referenceInverse(dequant);
      std::array<Pel, 16> actual {};
      PelBuf actualBuf(actual.data(), 4, 4);
      fixture.trQuant.invTransformNxN(fixture.tu, COMP_Y, actualBuf, qp);
      require(actual == expected, "Public GTT inverse differs from full DQ plus reference synthesis/DCT");
      requireThrows([&] { fixture.trQuant.invTransformNxN(fixture.tu, COMP_Y, actualBuf, qp, true); },
                    "GTT accepted a stale dequantization buffer");
    }
  }
}
}

int main()
{
  try
  {
    initROM();
    {
      testIntegerOperators();
      Fixture fixture;
      testLifecycleAndEligibility(fixture);
      testFixedDctAndInverse(fixture);
    }
    destroyROM();
    std::cout << "GTT common synthetic checks passed: " << checks << '\n';
    return 0;
  }
  catch (const std::exception &error)
  {
    std::cerr << "GTT common synthetic test failed: " << error.what() << '\n';
    return 1;
  }
}

#else
int main()
{
  std::cout << "GTT4x4 common tests require the explicitly enabled source macro\n";
  return 77;
}
#endif
