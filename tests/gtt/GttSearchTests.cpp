#include "EncoderLib/GttSearch.h"
#include "EncoderLib/CABACWriter.h"
#include "DecoderLib/CABACReader.h"
#include "CommonLib/CodingStructure.h"
#include "CommonLib/UnitTools.h"
#include "CommonLib/Rom.h"

#include <iostream>
#include <memory>
#include <stdexcept>

#if HHI_GTT_COMMON
namespace
{
unsigned checks = 0;
unsigned emptyFirstCgCases = 0;
void require(bool value, const char *message)
{
  ++checks;
  if (!value) { throw std::runtime_error(message); }
}

struct Fixture
{
  int size;
  SPS sps;
  PPS pps;
  Slice slice;
  CodingStructure cs { g_xuPool };
  CodingUnit cu;
  TransformUnit tu;
  std::unique_ptr<PreCalcValues> pcv;
  std::vector<TCoeff> levels;
  std::vector<uint8_t> signs;
  TrQuant transform;
  Fixture(int sub, bool encoder)
    : size(getGttMode(sub).size), cu(ChromaFormat::_400, Area(0, 0, size, size)),
      tu(ChromaFormat::_400, Area(0, 0, size, size)), levels(size * size), signs(size * size)
  {
    sps.m_chromaFormatIdc = ChromaFormat::_400;
    sps.m_bitDepths[ChannelType::LUMA] = sps.m_bitDepths[ChannelType::CHROMA] = 10;
    sps.m_qpBDOffset[ChannelType::LUMA] = 12;
    sps.m_ctuSize = 64;
    sps.m_gttEnabled = true;
    sps.m_gttVersion = 3;
    sps.m_gttSizeMask = getGttMode(sub).sizeMask;
    sps.m_gttGroupMode = getGttMode(sub).groupMode;
    sps.m_depQuantEnabledFlag = true;
    sps.m_numPredSign = 4;
    pps.m_picWidthInLumaSamples = pps.m_picHeightInLumaSamples = 64;
    pcv = std::make_unique<PreCalcValues>(sps, pps, true);
    slice.m_sps = &sps;
    slice.m_pps = &pps;
    slice.m_depQuantEnabledIdc = 1;
    slice.m_eSliceType = I_SLICE;
    slice.m_iSliceQp = 27;
    cs.area = UnitArea(ChromaFormat::_400, Area(0, 0, size, size));
    cs.sps = &sps;
    cs.pps = &pps;
    cs.slice = &slice;
    cs.pcv = pcv.get();
    cu.cs = &cs;
    cu.slice = &slice;
    cu.predMode = MODE_INTRA;
    cu.qp = 27;
    cu.firstTU = cu.lastTU = &tu;
    tu.cs = &cs;
    tu.cu = &cu;
    tu.gttFlag = true;
    TCoeff *coeffs[MAX_NUM_TBLOCKS] = { levels.data() };
    uint8_t *signsPtr[MAX_NUM_TBLOCKS] = { signs.data() };
    EnumArray<Pel *, ChannelType> palette;
    EnumArray<bool *, ChannelType> run;
    palette.fill(nullptr);
    run.fill(nullptr);
    tu.init(coeffs, signsPtr, palette, run);
    transform.init(nullptr, size, true, true, false, encoder);
    transform.getQuant()->setUseScalingList(false);
    transform.setLambda(12.0);
  }
};

void roundTrip(Fixture &enc, Fixture &dec, const GttQuantResult &candidate)
{
  installGttCandidate(enc.tu, candidate);
  if (candidate.lastScan >= 16)
  {
    const auto *scan = g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
                                 [gp_sizeIdxInfo->idxFrom(enc.size)][gp_sizeIdxInfo->idxFrom(enc.size)];
    bool empty = true;
    for (int i = 0; i < 16; ++i) { empty &= candidate.qRaster[scan[i].idx] == 0; }
    emptyFirstCgCases += empty;
  }
  OutputBitstream output;
  BinEncoder_Std bins;
  CABACWriter writer(bins, nullptr);
  GttFlagStats stats;
  writer.setGttFlagStats(&stats);
  writer.initBitstream(&output);
  bins.reset(enc.cu.qp, enc.slice.m_eSliceType);
  writer.getCtx().setBaseLevel(4);
  CUCtx encCtx;
  encCtx.isDQPCoded = encCtx.isChromaQpAdjCoded = true;
  writer.residual_coding_last(enc.tu, COMP_Y, &encCtx);
  writer.nst_idx(enc.tu, encCtx);
  writer.residual_coding_coef(enc.tu, COMP_Y, &encCtx);
  writer.mts_idx(enc.tu, encCtx);
  writer.residual_coding_sign(enc.tu, COMP_Y);
  writer.end_of_slice();
  output.writeByteAlignment();
  require(stats.total == 1 && stats.ones == 1, "Actual candidate flag not counted exactly once");

  std::fill(dec.levels.begin(), dec.levels.end(), TCoeff(0));
  dec.tu.gttFlag = false;
  dec.tu.lastPos[COMP_Y] = -1;
  dec.tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
  TU::setCbfAtDepth(dec.tu, COMP_Y, dec.tu.depth, true);
  InputBitstream input;
  input.getFifo() = output.getFifo();
  BinDecoder_Std decoderBins;
  CABACReader reader(decoderBins, nullptr);
  reader.initBitstream(&input);
  decoderBins.reset(dec.cu.qp, dec.slice.m_eSliceType);
  reader.getCtx().setBaseLevel(4);
  CUCtx decCtx;
  decCtx.isDQPCoded = decCtx.isChromaQpAdjCoded = true;
  reader.residual_coding_last(dec.tu, COMP_Y, decCtx);
  reader.nst_idx(dec.tu, decCtx);
  reader.residual_coding_coef(dec.tu, COMP_Y, decCtx);
  reader.mts_idx(dec.tu, decCtx);
  reader.residual_coding_sign(dec.tu, COMP_Y);
  require(reader.terminating_bit(), "Candidate CABAC stream did not terminate");
  require(dec.tu.gttFlag && dec.levels == enc.levels, "Candidate levels did not round-trip");
  require(dec.tu.lastPos[COMP_Y] == enc.tu.lastPos[COMP_Y], "Decoded last differs from searched last");
  require(!TU::getDelayedSignCoding(dec.tu, COMP_Y), "GTT must use immediate signs");
  std::vector<std::pair<uint16_t, uint16_t>> encStates, decStates;
  writer.getCtx().savePStates(encStates);
  reader.getCtx().savePStates(decStates);
  require(encStates == decStates, "Coefficient syntax context states differ");

  std::vector<Pel> encResidual(enc.size * enc.size), decResidual(dec.size * dec.size);
  PelBuf encBuf(encResidual.data(), enc.size, enc.size), decBuf(decResidual.data(), dec.size, dec.size);
  enc.transform.invTransformNxN(enc.tu, COMP_Y, encBuf, QpParam(enc.tu, COMP_Y));
  dec.transform.invTransformNxN(dec.tu, COMP_Y, decBuf, QpParam(dec.tu, COMP_Y));
  require(encResidual == decResidual, "Independent decoder inverse differs from encoder inverse");
}

void run()
{
  for (int sub = 1; sub <= 6; ++sub)
  {
    Fixture encoder(sub, true), decoder(sub, false);
    Ctx start(static_cast<const BinProbModel_Std *>(nullptr));
    std::vector<Pel> residual(encoder.size * encoder.size);
    for (int mode = 0; mode < 2; ++mode)
    {
      encoder.cu.predMode = decoder.cu.predMode = mode ? MODE_INTER : MODE_INTRA;
      encoder.slice.m_eSliceType = decoder.slice.m_eSliceType = mode ? B_SLICE : I_SLICE;
      for (int pattern = 0; pattern < 4; ++pattern)
      {
        for (unsigned i = 0; i < residual.size(); ++i)
        {
          residual[i] = pattern == 0 ? 0 : pattern == 1 ? 23 :
            pattern == 2 ? ((i & 1) ? -2047 : 2047) : (int((i * 137 + 29) % 1001) - 500);
        }
        start.init(encoder.cu.qp, encoder.slice.m_eSliceType);
        start.setBaseLevel(4);
        encoder.tu.gttFlag = true;
        encoder.tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
        const auto oldLevels = encoder.levels;
        const CPelBuf residualBuf(residual.data(), encoder.size, encoder.size);
        const auto candidates = generateGttCandidates(encoder.transform, encoder.tu, residualBuf, start);
        require(candidates.size() == 11, "GTT schedule must retain all 11 legal classes");
        require(encoder.levels == oldLevels, "Generating candidates modified final TU levels");
        GttSearchStats searchStats;
        const auto observed = generateGttCandidates(encoder.transform, encoder.tu, residualBuf, start, &searchStats);
        require(observed.size() == candidates.size(), "Statistics changed candidate count");
        const auto *sizeStats = getGttSearchSizeStats(&searchStats, encoder.size);
        require(sizeStats->calls == 1 && sizeStats->dqCalls == 11 && sizeStats->feasibleCandidates == 11,
                "Search statistics count is wrong");
        for (unsigned i = 0; i < candidates.size(); ++i)
        {
          require(candidates[i].qRaster == observed[i].qRaster &&
                  candidates[i].proxyCost == observed[i].proxyCost, "Statistics changed a candidate");
        }
        if (sub == 6)
        {
          const auto old = generateGtt4x4Candidates(encoder.transform, encoder.tu, residualBuf, start);
          require(old.size() == candidates.size(), "4x4 migration changed candidate count");
          for (unsigned i = 0; i < old.size(); ++i)
          {
            require(std::equal(old[i].qRaster.begin(), old[i].qRaster.end(), candidates[i].qRaster.begin()) &&
                    old[i].feasible == candidates[i].feasible && old[i].lastScan == candidates[i].lastScan &&
                    old[i].absSum == candidates[i].absSum && old[i].proxyCost == candidates[i].proxyCost,
                    "4x4 migration changed a candidate or tie order");
          }
        }
        auto invalid = candidates.front();
        invalid.qRaster.pop_back();
        bool rejected = false;
        try { installGttCandidate(encoder.tu, invalid); }
        catch (const std::exception &) { rejected = true; }
        require(rejected && encoder.levels == oldLevels, "Invalid length modified the TU");
        for (const auto &candidate : candidates) { roundTrip(encoder, decoder, candidate); }

        GttQuantResult zero;
        zero.feasible = true;
        zero.qRaster.assign(residual.size(), TCoeff(0));
        installGttCandidate(encoder.tu, zero);
        require(!encoder.tu.gttFlag && encoder.tu.lastPos[COMP_Y] == -1 &&
                !TU::getCbfAtDepth(encoder.tu, COMP_Y, encoder.tu.depth), "Zero candidate metadata is stale");
        require(encoder.tu.getCoeffs(COMP_Y).computeAbsSum() == 0, "Zero candidate retained coefficients");
      }
    }
  }
  require(emptyFirstCgCases > 0, "Integration cases must cover a zero-sign first CG");
}
}
#endif

int main()
{
#if HHI_GTT_COMMON
  try
  {
    initROM();
    run();
    destroyROM();
    std::cout << "GTT multi-size candidate/syntax/inverse: " << checks << " checks passed\n";
    return 0;
  }
  catch (const std::exception &error)
  {
    std::cerr << "GTT integration failure: " << error.what() << '\n';
    return 1;
  }
#else
  std::cout << "GTT search tests require compiled GTT support\n";
  return 77;
#endif
}
