#include "CodingStructure.h"
#include "DepQuant.h"
#include "Gtt4x4.h"
#include "Gtt4x4QuantRouting.h"
#include "Rom.h"
#include "UnitTools.h"

#include <array>
#include <cmath>
#include <iostream>
#include <memory>
#include <stdexcept>

#if HHI_GTT_COMMON

namespace
{
int checks = 0;

void require(bool condition, const char *message)
{
  ++checks;
  if (!condition)
  {
    throw std::runtime_error(message);
  }
}

struct Fixture
{
  SPS sps;
  PPS pps;
  Slice slice;
  CodingStructure cs { g_xuPool };
  CodingUnit cu { ChromaFormat::_400, Area(0, 0, 4, 4) };
  TransformUnit tu { ChromaFormat::_400, Area(0, 0, 4, 4) };
  std::unique_ptr<PreCalcValues> pcv;
  std::array<TCoeff, 16> levels {};
  std::array<uint8_t, 16> signs {};
  DepQuant quant { nullptr, true };
  Ctx ctx { static_cast<const BinProbModel_Std *>(nullptr) };

  Fixture()
  {
    sps.m_chromaFormatIdc = ChromaFormat::_400;
    sps.m_bitDepths[ChannelType::LUMA] = 10;
    sps.m_bitDepths[ChannelType::CHROMA] = 10;
    sps.m_qpBDOffset[ChannelType::LUMA] = 12;
    sps.m_internalMinusInputBitDepth[ChannelType::LUMA] = 0;
    sps.m_ctuSize = 64;
    sps.m_gttEnabled = true;
    sps.m_gttVersion = GTT4X4_VERSION;
    sps.m_gttSizeMask = 1;
    sps.m_depQuantEnabledFlag = true;
    pps.m_picWidthInLumaSamples = 64;
    pps.m_picHeightInLumaSamples = 64;
    pcv = std::make_unique<PreCalcValues>(sps, pps, true);
    slice.m_sps = &sps;
    slice.m_pps = &pps;
    slice.m_depQuantEnabledIdc = 1;
    slice.m_eSliceType = I_SLICE;
    cs.area = UnitArea(ChromaFormat::_400, Area(0, 0, 4, 4));
    cs.sps = &sps;
    cs.pps = &pps;
    cs.slice = &slice;
    cs.pcv = pcv.get();
    cu.cs = &cs;
    cu.slice = &slice;
    cu.predMode = MODE_INTRA;
    cu.firstTU = cu.lastTU = &tu;
    tu.cs = &cs;
    tu.cu = &cu;
    tu.gttFlag = true;
    TCoeff *coeffPointers[MAX_NUM_TBLOCKS] = { levels.data() };
    uint8_t *signPointers[MAX_NUM_TBLOCKS] = { signs.data() };
    EnumArray<Pel *, ChannelType> palette;
    EnumArray<bool *, ChannelType> run;
    palette.fill(nullptr);
    run.fill(nullptr);
    tu.init(coeffPointers, signPointers, palette, run);
    quant.init(4, true, true, false);
    quant.setUseScalingList(false);
    setQp(27);
  }

  void setQp(int qp)
  {
    cu.qp = qp;
    slice.m_iSliceQp = qp;
    ctx.init(Clip3(0, MAX_QP, qp), slice.m_eSliceType);
    ctx.setBaseLevel(4);
    quant.setLambda(0.57 * std::pow(2.0, (qp - 12.0) / 3.0));
  }

  const ScanElement *scan() const
  {
    return g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG][gp_sizeIdxInfo->idxFrom(4)][gp_sizeIdxInfo->idxFrom(4)];
  }

  Gtt4x4QuantResult solve(const CCoeffBuf &source, const Gtt4x4Constraints &constraints)
  {
    // Quantization is a pure candidate generator, including its failure path.
    const auto oldLevels = levels;
    std::array<uint8_t, MAX_NUM_TBLOCKS> oldCbf;
    std::array<int, MAX_NUM_TBLOCKS> oldLast;
    std::copy_n(tu.cbf, MAX_NUM_TBLOCKS, oldCbf.begin());
    std::copy_n(tu.lastPos, MAX_NUM_TBLOCKS, oldLast.begin());
    const auto oldMts = tu.mtsIdx;
    const bool oldGtt = tu.gttFlag;
    Gtt4x4QuantResult result;
    const bool feasible = quant.quantGtt4x4(tu, COMP_Y, source, QpParam(tu, COMP_Y), ctx, constraints, result);
    require(feasible == result.feasible, "Return value differs from feasible metadata");
    require(levels == oldLevels && std::equal(oldCbf.begin(), oldCbf.end(), tu.cbf) &&
              std::equal(oldLast.begin(), oldLast.end(), tu.lastPos) && tu.mtsIdx == oldMts &&
              tu.gttFlag == oldGtt,
            "Candidate quantization mutated the caller TU");
    return result;
  }

  void verify(const Gtt4x4QuantResult &result, const Gtt4x4Constraints &constraints)
  {
    require(result.feasible, "Expected a feasible constrained candidate");
    require(result.lastScan >= constraints.minLastScan && result.lastScan <= constraints.maxLastScan,
            "Last coefficient outside the requested interval");
    int64_t absSum = 0;
    int actualLast = -1;
    for (int g = 0; g < 4; g++)
    {
      int parity = 0;
      for (int i = g * 4; i < g * 4 + 4; i++)
      {
        const int64_t q = result.qRaster[scan()[i].idx];
        const int64_t magnitude = q < 0 ? -q : q;
        absSum += magnitude;
        parity ^= int(magnitude & 1);
        if (q)
        {
          actualLast = i;
        }
      }
      require(constraints.requiredParity[g] < 0 || constraints.requiredParity[g] == parity,
              "Group parity target was not satisfied");
    }
    require(absSum == result.absSum && absSum > 0, "Incorrect absolute sum");
    require(actualLast == result.lastScan, "Incorrect last-position metadata or nonzero suffix");
  }

  void verifyDequant(const std::array<TCoeff, 16> &input)
  {
    levels = input;
    std::array<TCoeff, 16> actual {};
    CoeffBuf destination(actual.data(), 4, 4);
    const QpParam qp(tu, COMP_Y);
    quant.dequant(tu, destination, COMP_Y, qp);

    // Independent four-state trajectory; the small-level reconstruction offset
    // is generated as 63/qAbs instead of copying DepQuant's lookup table.
    constexpr int transitions[4][2] = { { 0, 1 }, { 2, 3 }, { 1, 0 }, { 3, 2 } };
    const int qpDq = qp.Qp(false) + 1;
    const int shift = IQUANT_SHIFT + 1 - qpDq / 6 - 3; // 15 - 10 - log2(4) = 3
    const int64_t scale = int64_t(g_invQuantScales[0][qpDq % 6]) * (int64_t(1) << std::max(0, -shift));
    const int64_t add = shift > 0 ? int64_t(1) << (shift - 1) : 0;
    int last = 15;
    while (last >= 0 && !input[scan()[last].idx])
    {
      --last;
    }
    std::array<TCoeff, 16> expected {};
    int state = 0;
    for (int i = last; i >= 0; i--)
    {
      const int raster = scan()[i].idx;
      const int64_t level = input[raster];
      if (level)
      {
        const int sign = level < 0 ? -1 : 1;
        const int64_t qAbs = 2 * std::abs(level) - (state & 1);
        int64_t magnitude = (qAbs * scale + add) >> std::max(0, shift);
        if (qAbs < 64)
        {
          const int64_t nextMagnitude = ((qAbs + 1) * scale + add) >> std::max(0, shift);
          const int64_t weight = 63 / qAbs;
          magnitude = ((1024 - weight) * magnitude + weight * nextMagnitude) >> 10;
        }
        expected[raster] = TCoeff(Clip3<int64_t>(-32768, 32767, sign * magnitude));
      }
      state = transitions[state][std::abs(level) & 1];
    }
    require(actual == expected, "Original dequant differs from the independent state trajectory");
  }
};

void testLastAndParity(Fixture &fixture)
{
  std::array<TCoeff, 16> zero {};
  std::array<TCoeff, 16> signal { 1300, -910, 0, 41, -700, 350, 1, -1, 70, 29, -390, 0, 222, -45, 130, -11 };
  for (const int qp: { -12, 0, 22, 27, 32, 37, 63 })
  {
    fixture.setQp(qp);
    for (const bool reverseLast: { false, true })
    {
      fixture.slice.m_reverseLastSigCoeffFlag = reverseLast;
      for (int last = 0; last < 16; last++)
      {
        Gtt4x4Constraints constraints;
        constraints.minLastScan = constraints.maxLastScan = uint8_t(last);
        for (const auto *source: { &zero, &signal })
        {
          const auto result = fixture.solve(CCoeffBuf(source->data(), 4, 4), constraints);
          fixture.verify(result, constraints);
          fixture.verifyDequant(result.qRaster);
        }
      }
    }
    fixture.slice.m_reverseLastSigCoeffFlag = false;
    for (int tau = 1; tau <= 4; tau++)
    {
      const int prefixCount = tau == 1 ? 1 : tau == 2 ? 2 : 4;
      for (int prefix = 0; prefix < prefixCount; prefix++)
      {
        Gtt4x4Constraints constraints;
        constraints.minLastScan = uint8_t(4 * (tau - 1));
        constraints.maxLastScan = uint8_t(4 * tau - 1);
        if (tau >= 2)
        {
          constraints.requiredParity[0] = int8_t(tau == 2 ? prefix : prefix / 2);
        }
        if (tau >= 3)
        {
          constraints.requiredParity[1] = int8_t(prefix & 1);
        }
        for (const auto *source: { &zero, &signal })
        {
          const auto result = fixture.solve(CCoeffBuf(source->data(), 4, 4), constraints);
          fixture.verify(result, constraints);
          const auto repeated = fixture.solve(CCoeffBuf(source->data(), 4, 4), constraints);
          require(result.qRaster == repeated.qRaster && result.proxyCost == repeated.proxyCost,
                  "Repeated candidate depends on previous solver state");
          fixture.verifyDequant(result.qRaster);
          if (source == &zero)
          {
            require(result.proxyCost > 0, "Zero-source nonzero candidate should survive a positive proxy cost");
            for (const auto q: result.qRaster)
            {
              require(q >= 0, "A zero source must use the specified positive sign");
            }
          }
        }
      }
    }
  }
}

void testFailureAndBuffers(Fixture &fixture)
{
  fixture.setQp(27);
  fixture.levels.fill(17);
  fixture.tu.lastPos[COMP_Y] = 15;
  TU::setCbfAtDepth(fixture.tu, COMP_Y, 0, true);
  std::array<TCoeff, 16> zero {};
  Gtt4x4Constraints impossible;
  impossible.requiredParity[3] = 1; // All positions in group 4 are forced to zero.
  const auto failed = fixture.solve(CCoeffBuf(zero.data(), 4, 4), impossible);
  require(!failed.feasible && failed.lastScan == -1 && failed.absSum == 0 && failed.qRaster == zero,
          "Infeasible result contains stale candidate data");

  Gtt4x4Constraints constraints;
  constraints.minLastScan = 12;
  constraints.maxLastScan = 15;
  constraints.requiredParity[0] = 1;
  constraints.requiredParity[1] = 1;
  std::array<TCoeff, 16> signal;
  for (int i = 0; i < 16; i++)
  {
    signal[i] = (i & 1) ? -32000 + i : 32000 - i;
  }
  const auto normal = fixture.solve(CCoeffBuf(signal.data(), 4, 4), constraints);
  fixture.verify(normal, constraints);
  fixture.verifyDequant(normal.qRaster);
  std::array<TCoeff, 24> padded;
  padded.fill(999999); // Deliberately large padding must never enter the solver.
  for (int y = 0; y < 4; y++)
  {
    for (int x = 0; x < 4; x++)
    {
      padded[y * 6 + x] = signal[y * 4 + x];
    }
  }
  const auto strided = fixture.solve(CCoeffBuf(padded.data(), 6, 4, 4), constraints);
  require(normal.qRaster == strided.qRaster && normal.proxyCost == strided.proxyCost,
          "Strided source produced different coefficients");

  for (auto &value: signal)
  {
    value = -value;
  }
  const auto negative = fixture.solve(CCoeffBuf(signal.data(), 4, 4), constraints);
  for (int i = 0; i < 16; i++)
  {
    require(negative.qRaster[i] == -normal.qRaster[i], "Sign inversion changed magnitude or parity decisions");
  }

  bool rejected = false;
  try
  {
    fixture.slice.m_depQuantEnabledIdc = 2;
    fixture.solve(CCoeffBuf(signal.data(), 4, 4), constraints);
  }
  catch (const std::exception &)
  {
    rejected = true;
  }
  fixture.slice.m_depQuantEnabledIdc = 1;
  require(rejected, "Unsupported DQ mode was silently accepted");

  // Unclipped blended prediction can produce full-Pel-range residuals. Its
  // transform coefficients must be handled without clipping or int64 overflow.
  for (const int qp: { -12, 0, 22, 37, 63 })
  {
    fixture.setQp(qp);
    for (const int magnitude: { 32768, 1048577, 2097151 })
    {
      for (int i = 0; i < 16; i++)
      {
        signal[i] = (i & 1) ? -magnitude + i : magnitude - i;
      }
      const auto wide = fixture.solve(CCoeffBuf(signal.data(), 4, 4), constraints);
      fixture.verify(wide, constraints);
      fixture.verifyDequant(wide.qRaster);
    }
  }
}

// This intentionally controlled model has no CABAC neighborhood/history: future
// cost depends only on physical DQ state, position, and the next level. It can
// therefore validate joint-slot routing against true exhaustive enumeration.
// It does not claim that the production CABAC survivor search is globally optimal.
struct ControlledModel
{
  int length = 8;
  std::array<std::array<int, 3>, 8> levelCost {};
  std::array<std::array<int, 3>, 4> stateCost {};
  std::array<unsigned, 8> allowedLevels {{ 7, 7, 7, 7, 7, 7, 7, 7 }};
  std::array<int8_t, 4> parity {{ -1, -1, -1, -1 }};
};

constexpr int controlledInfinity = 1000000000;

int controlledDynamicProgram(const ControlledModel &model, bool preserveParity, bool &keptCostlierParity)
{
  struct Survivor
  {
    int slot = -1;
    int cost = controlledInfinity;
  };
  std::array<Survivor, 8> previous;
  previous[0] = { 0, 0 };
  if (!Gtt4x4QuantRouting::lastStartAllowed(model.length - 1, model.parity))
  {
    return controlledInfinity;
  }
  for (int position = model.length - 1; position >= 0; --position)
  {
    std::array<Survivor, 8> current;
    for (const auto &survivor: previous)
    {
      if (survivor.slot < 0)
      {
        continue;
      }
      for (int level = 0; level <= 2; ++level)
      {
        if (!(model.allowedLevels[position] & (1u << level)) || (position == model.length - 1 && !level))
        {
          continue;
        }
        const int next = Gtt4x4QuantRouting::nextSlot(survivor.slot, level & 1, position,
                                                     model.parity[position / 4], g_stateTransTab[1]);
        if (next < 0)
        {
          continue;
        }
        const int cost = survivor.cost + model.levelCost[position][level] + model.stateCost[survivor.slot / 2][level];
        const int key = preserveParity ? next : next / 2;
        if (cost < current[key].cost)
        {
          current[key] = { next, cost };
        }
      }
    }
    if (preserveParity)
    {
      for (int state = 0; state < 4; ++state)
      {
        const auto &even = current[2 * state];
        const auto &odd = current[2 * state + 1];
        keptCostlierParity |= even.slot >= 0 && odd.slot >= 0 && even.cost != odd.cost;
      }
    }
    previous = current;
  }
  int best = controlledInfinity;
  for (const auto &survivor: previous)
  {
    best = std::min(best, survivor.cost);
  }
  return best;
}

int controlledExhaustiveReference(const ControlledModel &model)
{
  // Independent reference: enumerate complete integer vectors, test whole-group
  // parity afterwards, and traverse a literal physical-state table. Neither the
  // production routing helper nor its boundary logic is reused here.
  constexpr int transitions[4][2] = { { 0, 1 }, { 2, 3 }, { 1, 0 }, { 3, 2 } };
  int combinations = 1;
  for (int i = 0; i < model.length; ++i)
  {
    combinations *= 3;
  }
  int best = controlledInfinity;
  for (int ordinal = 0; ordinal < combinations; ++ordinal)
  {
    std::array<int, 16> levels {};
    int digits = ordinal;
    bool allowed = true;
    for (int i = 0; i < model.length; ++i)
    {
      levels[i] = digits % 3;
      digits /= 3;
      allowed &= bool(model.allowedLevels[i] & (1u << levels[i]));
    }
    if (!allowed || levels[model.length - 1] == 0)
    {
      continue;
    }
    for (int group = 0; group < 4; ++group)
    {
      const int sum = levels[4 * group] + levels[4 * group + 1] + levels[4 * group + 2] + levels[4 * group + 3];
      allowed &= model.parity[group] < 0 || (sum % 2) == model.parity[group];
    }
    if (!allowed)
    {
      continue;
    }
    int cost = 0;
    int state = 0;
    for (int i = model.length - 1; i >= 0; --i)
    {
      cost += model.levelCost[i][levels[i]] + model.stateCost[state][levels[i]];
      state = transitions[state][levels[i] % 2];
    }
    best = std::min(best, cost);
  }
  return best;
}

void testControlledExhaustiveAndParitySurvivors()
{
  // Vary the last position, rate ordering, and constrained/unconstrained groups.
  // Three-level complete-vector enumeration is small enough to remain exhaustive.
  for (int seed = 0; seed < 4; ++seed)
  {
    for (int length = 1; length <= 8; ++length)
    {
      ControlledModel model;
      model.length = length;
      for (int i = 0; i < 8; ++i)
      {
        for (int level = 0; level <= 2; ++level)
        {
          model.levelCost[i][level] = ((i + 3) * (level + 5) * (seed + 7) + level * level * 3) % 47;
        }
      }
      for (int state = 0; state < 4; ++state)
      {
        for (int level = 0; level <= 2; ++level)
        {
          model.stateCost[state][level] = ((state + 2) * (level + 1) + seed * 3) % 11;
        }
      }
      for (int parity0 = -1; parity0 <= 1; ++parity0)
      {
        for (int parity1 = -1; parity1 <= 1; ++parity1)
        {
          model.parity[0] = int8_t(parity0);
          model.parity[1] = int8_t(parity1);
          bool keptCostlierParity = false;
          require(controlledDynamicProgram(model, true, keptCostlierParity) == controlledExhaustiveReference(model),
                  "Joint-slot DP differs from exhaustive enumeration of a history-independent model");
        }
      }
    }
  }

  ControlledModel model;
  constexpr int weights[8] = { 16, 41, 65, 66, 83, 14, 29, 77 };
  model.parity[0] = 1;
  for (int i = 0; i < 8; ++i)
  {
    model.allowedLevels[i] = 3; // Levels 0 or 1.
    model.levelCost[i][1] = weights[i];
  }
  model.allowedLevels[0] = 1; // The final decision cannot repair an incorrect parity.
  bool keptCostlierParity = false;
  const int jointCost = controlledDynamicProgram(model, true, keptCostlierParity);
  require(keptCostlierParity, "The survivor example did not retain a more expensive parity history");
  require(jointCost == 118 && jointCost == controlledExhaustiveReference(model),
          "Parity survivor example did not recover the known feasible optimum");
  bool ignored = false;
  require(controlledDynamicProgram(model, false, ignored) == controlledInfinity,
          "The parity example no longer demonstrates failure from discarding a costlier parity survivor");
}
}

int main()
{
  try
  {
    initROM();
    {
      Fixture fixture;
      testLastAndParity(fixture);
      fixture.cu.predMode = MODE_INTER;
      fixture.slice.m_eSliceType = B_SLICE;
      testLastAndParity(fixture);
      fixture.cu.predMode = MODE_INTRA; // Context-switch path for intra in a B slice.
      testFailureAndBuffers(fixture);
      testControlledExhaustiveAndParitySurvivors();
    }
    destroyROM();
    std::cout << "GTT constrained DQ synthetic checks passed: " << checks << '\n';
    return 0;
  }
  catch (const std::exception &error)
  {
    std::cerr << "GTT constrained DQ test failed: " << error.what() << '\n';
    return 1;
  }
}
#else
int main()
{
  std::cout << "GTT constrained DQ tests require compiled GTT support; skipped.\n";
  return 77;
}
#endif
