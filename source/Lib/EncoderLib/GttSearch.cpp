#include "GttSearch.h"

#if HHI_GTT4X4
#include "CommonLib/UnitTools.h"
#include "CommonLib/Rom.h"

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
#endif
