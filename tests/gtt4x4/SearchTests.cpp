#include "EncoderLib/GttSearch.h"
#include "EncoderLib/CABACWriter.h"
#include "DecoderLib/CABACReader.h"
#include "CommonLib/CodingStructure.h"
#include "CommonLib/UnitTools.h"

#include <array>
#include <iostream>
#include <memory>
#include <stdexcept>

#if HHI_GTT4X4
namespace
{
unsigned checks = 0;
void require(bool value, const char *message)
{
  ++checks;
  if (!value) { throw std::runtime_error(message); }
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
  TrQuant transform;
  Fixture(bool encoder)
  {
    sps.m_chromaFormatIdc = ChromaFormat::_400;
    sps.m_bitDepths[ChannelType::LUMA] = sps.m_bitDepths[ChannelType::CHROMA] = 10;
    sps.m_qpBDOffset[ChannelType::LUMA] = 12;
    sps.m_ctuSize = 64;
    sps.m_gtt4x4Enabled = true;
    sps.m_gtt4x4Version = GTT4X4_VERSION;
    sps.m_depQuantEnabledFlag = true;
    sps.m_numPredSign = 4;
    pps.m_picWidthInLumaSamples = pps.m_picHeightInLumaSamples = 64;
    pcv = std::make_unique<PreCalcValues>(sps, pps, true);
    slice.m_sps = &sps;
    slice.m_pps = &pps;
    slice.m_depQuantEnabledIdc = 1;
    slice.m_eSliceType = I_SLICE;
    slice.m_iSliceQp = 27;
    cs.area = UnitArea(ChromaFormat::_400, Area(0, 0, 4, 4));
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
    transform.init(nullptr, 4, true, true, false, encoder);
    transform.getQuant()->setUseScalingList(false);
    transform.setLambda(12.0);
  }
};

void roundTrip(Fixture &enc, Fixture &dec, const Gtt4x4QuantResult &candidate)
{
  installGtt4x4Candidate(enc.tu, candidate);
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

  dec.levels.fill(0);
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

  std::array<Pel, 16> encResidual {}, decResidual {};
  PelBuf encBuf(encResidual.data(), 4, 4), decBuf(decResidual.data(), 4, 4);
  enc.transform.invTransformNxN(enc.tu, COMP_Y, encBuf, QpParam(enc.tu, COMP_Y));
  dec.transform.invTransformNxN(dec.tu, COMP_Y, decBuf, QpParam(dec.tu, COMP_Y));
  require(encResidual == decResidual, "Independent decoder inverse differs from encoder inverse");
}

void run()
{
  Fixture encoder(true), decoder(false);
  Ctx start(static_cast<const BinProbModel_Std *>(nullptr));
  std::array<Pel, 16> residual {};
  for (int mode = 0; mode < 2; ++mode)
  {
    encoder.cu.predMode = decoder.cu.predMode = mode ? MODE_INTER : MODE_INTRA;
    encoder.slice.m_eSliceType = decoder.slice.m_eSliceType = mode ? B_SLICE : I_SLICE;
    for (int pattern = 0; pattern < 4; ++pattern)
    {
      for (int i = 0; i < 16; ++i)
      {
        residual[i] = pattern == 0 ? 0 : pattern == 1 ? 23 :
          pattern == 2 ? ((i & 1) ? -511 : 511) : ((i * 137 + 29) % 1001 - 500);
      }
      start.init(encoder.cu.qp, encoder.slice.m_eSliceType);
      start.setBaseLevel(4);
      encoder.tu.gttFlag = true;
      encoder.tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
      const auto oldLevels = encoder.levels;
      const auto candidates = generateGtt4x4Candidates(
        encoder.transform, encoder.tu, CPelBuf(residual.data(), 4, 4), start);
      require(candidates.size() == 11, "The unpruned GTT schedule must evaluate all 11 legal classes");
      require(encoder.levels == oldLevels, "Generating candidates modified final TU levels");
      auto invalid = candidates.front();
      invalid.lastScan = (invalid.lastScan + 1) % 16;
      bool rejected = false;
      try { installGtt4x4Candidate(encoder.tu, invalid); }
      catch (const std::exception &) { rejected = true; }
      require(rejected && encoder.levels == oldLevels, "Invalid last metadata was installed into the TU");
      for (const auto &candidate : candidates) { roundTrip(encoder, decoder, candidate); }

      Gtt4x4QuantResult zero;
      zero.feasible = true;
      installGtt4x4Candidate(encoder.tu, zero);
      require(!encoder.tu.gttFlag && encoder.tu.lastPos[COMP_Y] == -1 &&
                !TU::getCbfAtDepth(encoder.tu, COMP_Y, encoder.tu.depth), "Zero candidate metadata is stale");
      require(encoder.tu.getCoeffs(COMP_Y).computeAbsSum() == 0, "Zero candidate retained coefficients");
    }
  }
}
}
#endif

int main()
{
#if HHI_GTT4X4
  try
  {
    initROM();
    run();
    destroyROM();
    std::cout << "GTT4x4 search/syntax/inverse integration: " << checks << " checks passed\n";
    return 0;
  }
  catch (const std::exception &error)
  {
    std::cerr << "GTT4x4 integration failure: " << error.what() << '\n';
    return 1;
  }
#else
  std::cout << "GTT4x4 search tests require the explicitly enabled source macro\n";
  return 77;
#endif
}
