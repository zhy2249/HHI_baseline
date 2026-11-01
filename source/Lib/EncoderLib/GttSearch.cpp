#include "GttSearch.h"

#if HHI_GTT_COMMON
#include "CommonLib/UnitTools.h"
#include "CommonLib/Rom.h"
#include <limits>
#include <cmath>

GttSearchSizeStats *getGttSearchSizeStats(GttSearchStats *stats, int size)
{
  if (!stats) { return nullptr; }
  CHECK(size != 4 && size != 8 && size != 16 && size != 32, "Invalid GTT statistics size");
  return &stats->bySize[size == 4 ? 0 : size == 8 ? 1 : size == 16 ? 2 : 3];
}

void recordGttSearchOutcome(GttSearchSizeStats *stats, double bestNonzeroCost, double legacyCost)
{
  if (stats && bestNonzeroCost < MAX_DOUBLE && legacyCost < MAX_DOUBLE &&
      std::isfinite(bestNonzeroCost) && std::isfinite(legacyCost))
  {
    ++stats->comparisons;
    stats->localWins += bestNonzeroCost < legacyCost;
    stats->deltaJSum += static_cast<long double>(bestNonzeroCost) - legacyCost;
  }
}

void printGttSearchStats(const GttSearchStats &stats, int layer, int sub)
{
  for (unsigned i = 0; i < stats.bySize.size(); ++i)
  {
    const auto &s = stats.bySize[i];
    msg(NOTICE, "GTT_SEARCH_STATS experiment=2 sub=%d layer=%d size=%u calls=%llu dq_calls=%llu "
        "feasible=%llu scored=%llu comparisons=%llu local_wins=%llu dct_ms=%.6f dq_ms=%.6f score_ms=%.6f "
        "delta_j_sum=%.9Lg population=search_calls\n", sub, layer, 4u << i,
        (unsigned long long)s.calls, (unsigned long long)s.dqCalls,
        (unsigned long long)s.feasibleCandidates, (unsigned long long)s.scoredCandidates,
        (unsigned long long)s.comparisons, (unsigned long long)s.localWins,
        double(s.dctNs) / 1e6, double(s.dqNs) / 1e6, double(s.scoreNs) / 1e6, s.deltaJSum);
    if (s.allEnergy > 0)
    {
      msg(NOTICE, "GTT_SEARCH_ENERGY experiment=2 sub=%d layer=%d size=%u g1_ratio=%.9Lg "
          "population=search_calls weighting=base_dct_energy\n", sub, layer, 4u << i, s.g1Energy / s.allEnergy);
    }
    else
    {
      msg(NOTICE, "GTT_SEARCH_ENERGY experiment=2 sub=%d layer=%d size=%u g1_ratio=N/A "
          "population=search_calls weighting=base_dct_energy\n", sub, layer, 4u << i);
    }
  }
}

std::vector<Gtt4x4QuantResult> generateGtt4x4Candidates(
  TrQuant &trQuant, const TransformUnit &tu, const CPelBuf &residual, const Ctx &startCtx)
{
  CHECK(!tu.gttFlag || !TU::isGttApplicable(tu, COMP_Y), "Invalid GTT search input");
  CHECK(tu.mtsIdx[COMP_Y] != MtsType::DCT2_DCT2, "GTT requires fixed DCT-II");
  std::array<TCoeff, 16> base{};
  CoeffBuf baseBuf(base.data(), 4, 4);
  trQuant.forwardGtt4x4Dct(tu, residual, baseBuf);
  std::array<std::array<TCoeff, 16>, 4> paths{};
  for (uint8_t k2 = 0; k2 < 2; ++k2)
  {
    for (uint8_t k3 = 0; k3 < 2; ++k3)
    {
      CoeffBuf dst(paths[2 * k2 + k3].data(), 4, 4);
      forwardGtt4x4Path(baseBuf, Gtt4x4Path{k2, k3}, dst);
    }
  }

  const QpParam qp(tu, COMP_Y);
  std::vector<Gtt4x4QuantResult> candidates;
  candidates.reserve(11);
  for (int tau = 1; tau <= 4; ++tau)
  {
    for (uint8_t k2 = 0; k2 < (tau >= 2 ? 2 : 1); ++k2)
    {
      for (uint8_t k3 = 0; k3 < (tau >= 3 ? 2 : 1); ++k3)
      {
        Gtt4x4Constraints constraints;
        constraints.minLastScan = 4 * (tau - 1);
        constraints.maxLastScan = 4 * tau - 1;
        if (tau >= 2) { constraints.requiredParity[0] = k2; }
        if (tau >= 3) { constraints.requiredParity[1] = k3; }
        Gtt4x4QuantResult result;
        const CCoeffBuf path(paths[2 * k2 + k3].data(), 4, 4);
        if (trQuant.quantGtt4x4(tu, COMP_Y, path, qp, startCtx, constraints, result))
        {
          CHECK(!result.feasible || result.absSum <= 0, "Invalid nonzero GTT candidate");
          CHECK(result.lastScan < constraints.minLastScan || result.lastScan > constraints.maxLastScan,
                "GTT last position outside its terminal group");
          const auto derived = deriveGtt4x4Path(CCoeffBuf(result.qRaster.data(), 4, 4));
          CHECK((tau >= 2 && derived.k2 != k2) || (tau >= 3 && derived.k3 != k3),
                "GTT levels do not support the searched kernel prefix");
          candidates.push_back(result);
        }
      }
    }
  }
  return candidates;
}

void installGtt4x4Candidate(TransformUnit &tu, const Gtt4x4QuantResult &result)
{
  CHECK(!result.feasible || tu.Y().width != 4 || tu.Y().height != 4, "Invalid GTT candidate installation");
  const auto *scan = g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
                               [gp_sizeIdxInfo->idxFrom(4)][gp_sizeIdxInfo->idxFrom(4)];
  int last = -1;
  int64_t sum = 0;
  for (int i = 0; i < 16; ++i)
  {
    const int64_t level = result.qRaster[scan[i].idx];
    sum += level < 0 ? -level : level;
    if (level) { last = i; }
  }
  CHECK(last != result.lastScan || sum != result.absSum, "Inconsistent GTT candidate levels/metadata");
  CHECK(sum && !TU::isGttApplicable(tu, COMP_Y), "Cannot install GTT on an unsupported TU");
  tu.getCoeffs(COMP_Y).copyFrom(CCoeffBuf(result.qRaster.data(), 4, 4));
  tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
  tu.gttFlag = result.absSum != 0;
  tu.lastPos[COMP_Y] = tu.gttFlag ? result.lastScan : -1;
  TU::setCbfAtDepth(tu, COMP_Y, tu.depth, tu.gttFlag);
}

std::vector<GttQuantResult> generateGttCandidates(
  TrQuant &trQuant, const TransformUnit &tu, const CPelBuf &residual, const Ctx &startCtx,
  GttSearchStats *stats)
{
  CHECK(!tu.gttFlag || !TU::isGttApplicable(tu, COMP_Y), "Invalid GTT search input");
  CHECK(tu.mtsIdx[COMP_Y] != MtsType::DCT2_DCT2, "GTT requires fixed DCT-II");
  std::vector<GttQuantResult> candidates;
  candidates.reserve(11);
  if (tu.cs->sps->m_gttVersion == GTT4X4_VERSION)
  {
    for (const auto &old : generateGtt4x4Candidates(trQuant, tu, residual, startCtx))
    {
      GttQuantResult result;
      result.feasible = old.feasible;
      result.qRaster.assign(old.qRaster.begin(), old.qRaster.end());
      result.lastScan = old.lastScan;
      result.absSum = old.absSum;
      result.proxyCost = old.proxyCost;
      candidates.push_back(std::move(result));
    }
    return candidates;
  }

  const GttLayout layout = getGttLayout(tu);
  auto *sizeStats = getGttSearchSizeStats(stats, layout.width);
  if (sizeStats) { ++sizeStats->calls; }
  std::vector<TCoeff> base(layout.numCoeff);
  CoeffBuf baseBuf(base.data(), layout.width, layout.height);
  {
    GttSearchTimer timer(sizeStats ? &sizeStats->dctNs : nullptr);
    trQuant.forwardGttDct(tu, residual, baseBuf);
  }
  if (sizeStats)
  {
    const auto *scan = g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
                                 [gp_sizeIdxInfo->idxFrom(layout.width)][gp_sizeIdxInfo->idxFrom(layout.height)];
    for (int i = 0; i < layout.numCoeff; ++i)
    {
      const long double value = base[scan[i].idx];
      sizeStats->allEnergy += value * value;
      if (i < layout.groupEnd[0]) { sizeStats->g1Energy += value * value; }
    }
  }
  std::array<std::vector<TCoeff>, 4> paths;
  for (uint8_t k2 = 0; k2 < 2; ++k2)
  {
    for (uint8_t k3 = 0; k3 < 2; ++k3)
    {
      auto &path = paths[2 * k2 + k3];
      path.resize(layout.numCoeff);
      CoeffBuf dst(path.data(), layout.width, layout.height);
      forwardGttPath(baseBuf, GttPath{k2, k3}, dst, layout);
    }
  }

  const QpParam qp(tu, COMP_Y);
  for (int tau = 1; tau <= 4; ++tau)
  {
    for (uint8_t k2 = 0; k2 < (tau >= 2 ? 2 : 1); ++k2)
    {
      for (uint8_t k3 = 0; k3 < (tau >= 3 ? 2 : 1); ++k3)
      {
        GttConstraints constraints;
        constraints.minLastScan = layout.groupBegin[tau - 1];
        constraints.maxLastScan = layout.groupEnd[tau - 1] - 1;
        if (tau >= 2) { constraints.requiredParity[0] = k2; }
        if (tau >= 3) { constraints.requiredParity[1] = k3; }
        GttQuantResult result;
        const CCoeffBuf path(paths[2 * k2 + k3].data(), layout.width, layout.height);
        bool feasible;
        {
          GttSearchTimer timer(sizeStats ? &sizeStats->dqNs : nullptr);
          feasible = trQuant.quantGtt(tu, COMP_Y, path, qp, startCtx, layout, constraints, result);
        }
        if (sizeStats)
        {
          ++sizeStats->dqCalls;
          sizeStats->feasibleCandidates += feasible;
        }
        if (feasible)
        {
          CHECK(!result.feasible || result.absSum <= 0 || int(result.qRaster.size()) != layout.numCoeff,
                "Invalid nonzero GTT candidate");
          CHECK(result.lastScan < constraints.minLastScan || result.lastScan > constraints.maxLastScan,
                "GTT last position outside its terminal group");
          const auto derived = deriveGttPath(
            CCoeffBuf(result.qRaster.data(), layout.width, layout.height), layout);
          CHECK((tau >= 2 && derived.k2 != k2) || (tau >= 3 && derived.k3 != k3),
                "GTT levels do not support the searched kernel prefix");
          candidates.push_back(std::move(result));
        }
      }
    }
  }
  return candidates;
}

void installGttCandidate(TransformUnit &tu, const GttQuantResult &result)
{
  CHECK(!result.feasible, "Cannot install an infeasible GTT candidate");
  const GttLayout layout = getGttLayout(tu);
  CHECK(int(result.qRaster.size()) != layout.numCoeff, "GTT candidate has the wrong buffer length");
  const auto *scan = g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
                               [gp_sizeIdxInfo->idxFrom(layout.width)][gp_sizeIdxInfo->idxFrom(layout.height)];
  int last = -1;
  int64_t sum = 0;
  for (int i = 0; i < layout.numCoeff; ++i)
  {
    const int64_t level = result.qRaster[scan[i].idx];
    sum += level < 0 ? -level : level;
    if (level) { last = i; }
  }
  CHECK(last != result.lastScan || sum != result.absSum, "Inconsistent GTT candidate levels/metadata");
  CHECK(sum > std::numeric_limits<TCoeff>::max(), "GTT absolute sum exceeds the caller's coefficient range");
  CHECK(sum && !TU::isGttApplicable(tu, COMP_Y), "Cannot install GTT on an unsupported TU");
  tu.getCoeffs(COMP_Y).copyFrom(CCoeffBuf(result.qRaster.data(), layout.width, layout.height));
  tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
  tu.gttFlag = sum != 0;
  tu.lastPos[COMP_Y] = tu.gttFlag ? last : -1;
  TU::setCbfAtDepth(tu, COMP_Y, tu.depth, tu.gttFlag);
}
#endif
