#include "CommonLib/CodingStructure.h"
#include "CommonLib/Gtt.h"
#include "CommonLib/Rom.h"
#include "CommonLib/UnitTools.h"
#include "EncoderLib/CABACWriter.h"
#include "EncoderLib/VLCWriter.h"
#include "DecoderLib/CABACReader.h"
#include "DecoderLib/VLCReader.h"

#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>

namespace
{
unsigned checks = 0;
void require(bool value, const char *message)
{
  ++checks;
  if (!value) { throw std::runtime_error(message); }
}
template<class Fn> void rejects(Fn fn, const char *message)
{
  bool rejected = false;
  try { fn(); } catch (const std::exception &) { rejected = true; }
  require(rejected, message);
}

SPS makeSps()
{
  SPS sps;
  sps.m_vpsId = 1;
  sps.m_ptlDpbHrdParamsPresentFlag = false;
  sps.m_chromaFormatIdc = ChromaFormat::_400;
  sps.m_ctuSize = sps.m_maxCuWidth = sps.m_maxCuHeight = 64;
  sps.m_maxWidthInLumaSamples = sps.m_maxHeightInLumaSamples = 64;
  sps.m_bitDepths[ChannelType::LUMA] = sps.m_bitDepths[ChannelType::CHROMA] = 10;
  sps.m_qpBDOffset[ChannelType::LUMA] = sps.m_qpBDOffset[ChannelType::CHROMA] = 12;
  sps.m_verCollocatedChromaFlag = true;
  sps.m_depQuantEnabledFlag = true;
  sps.m_tempCabacInitMode = false;
  for (int i = 0; i < 3; ++i)
  {
    sps.m_minQT[i] = 4;
    sps.m_maxBTSize[i] = sps.m_maxTTSize[i] = 4;
    sps.m_maxMTTHierarchyDepth[i] = 0;
  }
  return sps;
}

OutputBitstream serialize(const SPS &sps)
{
  OutputBitstream bits;
  HLSWriter writer;
  writer.setBitstream(&bits);
  writer.codeSPS(&sps);
  return bits;
}

void parse(const OutputBitstream &bits, SPS &sps)
{
  InputBitstream input;
  input.getFifo() = bits.getFifo();
  input.resetToStart();
  HLSyntaxReader reader;
  reader.setBitstream(&input);
  reader.parseSPS(&sps);
  require(input.getNumBitsLeft() == 0, "SPS parser left unread bits");
}

// Independent bit-level fixture also tests macro-zero decoder rejection.
OutputBitstream extension(const OutputBitstream &legacy, unsigned version, unsigned mask, unsigned group)
{
  const auto &bytes = legacy.getFifo();
  auto bit = [&](size_t pos) { return (bytes[pos / 8] >> (7 - pos % 8)) & 1; };
  size_t trailing = bytes.size() * 8 - 1;
  while (!bit(trailing)) { --trailing; }
  require(trailing && !bit(trailing - 1), "Expected extension-free SPS");
  OutputBitstream result;
  for (size_t i = 0; i < trailing - 1; ++i) { result.write(bit(i), 1); }
  result.write(1, 1);
  result.write(0x40, 8);
  result.write(version, 8);
  if (version == 3)
  {
    result.write(mask, 8);
    result.write(group, 1);
  }
  result.writeByteAlignment();
  return result;
}

void spsTests()
{
  SPS off = makeSps();
  const auto legacy = serialize(off);
  SPS decoded;
  decoded.m_gttEnabled = true;
  decoded.m_gttVersion = 3;
  decoded.m_gttSizeMask = 8;
  decoded.m_gttGroupMode = 1;
  parse(legacy, decoded);
  require(!decoded.m_gttEnabled && !decoded.m_gttVersion && !decoded.m_gttSizeMask && !decoded.m_gttGroupMode,
          "Absent extension retained GTT fields");
  require(serialize(decoded).getFifo() == legacy.getFifo(), "Disabled SPS bytes changed");
  for (int mode = 1; mode <= 6; ++mode)
  {
    const auto &definition = getGttMode(mode);
    const auto bits = extension(legacy, 3, definition.sizeMask, definition.groupMode);
#if HHI_GTT_COMMON
    parse(bits, decoded);
    require(decoded.m_gttEnabled && decoded.m_gttVersion == 3 && decoded.m_gttSizeMask == definition.sizeMask &&
              decoded.m_gttGroupMode == definition.groupMode, "v3 mode SPS fields changed");
    require(serialize(decoded).getFifo() == bits.getFifo(), "v3 Writer differs from independent payload");
    auto invalid = decoded;
    invalid.m_log2MaxTbSize = 1;
    rejects([&] { invalid.validateGtt(); }, "SPS accepted oversized GTT mode");
#else
    rejects([&] { parse(bits, decoded); }, "Macro-zero decoder accepted v3");
#endif
  }
  for (unsigned mask: { 0U, 0x10U, 0x82U })
  {
    rejects([&] { parse(extension(legacy, 3, mask, 0), decoded); }, "Illegal size mask accepted");
  }
  rejects([&] { parse(extension(legacy, 3, 1, 1), decoded); }, "4x4 first-CG group accepted");
  rejects([&] { parse(extension(legacy, 3, 15, 1), decoded); }, "Mixed mask including 4x4 first-CG accepted");
  rejects([&] { parse(extension(legacy, 1, 0, 0), decoded); }, "Obsolete version accepted");
  rejects([&] { parse(extension(legacy, 4, 0, 0), decoded); }, "Unknown version accepted");
#if HHI_GTT_COMMON
  parse(extension(legacy, 3, 15, 0), decoded);
  require(decoded.m_gttSizeMask == 15, "Legal joint-size decoder mask rejected");
  parse(extension(legacy, 3, 14, 1), decoded);
  require(decoded.m_gttGroupMode == 1, "Legal joint first-CG decoder mask rejected");
  parse(extension(legacy, 2, 0, 0), decoded);
  require(decoded.m_gttVersion == 2 && decoded.m_gttSizeMask == 1 && !decoded.m_gttGroupMode,
          "v2 inferred layout changed");
#else
  rejects([&] { parse(extension(legacy, 2, 0, 0), decoded); }, "Macro-zero decoder accepted v2");
#endif
  rejects([&] { getGttMode(-1); }, "Negative experiment mode accepted");
  rejects([&] { getGttMode(7); }, "Unregistered experiment mode accepted");
}

#if HHI_GTT_COMMON
struct Fixture
{
  XuPool pool;
  CodingStructure cs { pool };
  SPS sps = makeSps();
  PPS pps;
  std::unique_ptr<PreCalcValues> pcv;
  Slice slice;
  CodingUnit cu;
  TransformUnit tu;
  std::vector<TCoeff> levels;
  std::vector<uint8_t> signs;
  explicit Fixture(int mode)
    : cu(ChromaFormat::_400, Area(0, 0, getGttMode(mode).size, getGttMode(mode).size))
    , tu(ChromaFormat::_400, Area(0, 0, getGttMode(mode).size, getGttMode(mode).size))
    , levels(getGttMode(mode).size * getGttMode(mode).size)
    , signs(levels.size())
  {
    const auto &definition = getGttMode(mode);
    sps.m_gttEnabled = true;
    sps.m_gttVersion = 3;
    sps.m_gttSizeMask = definition.sizeMask;
    sps.m_gttGroupMode = definition.groupMode;
    pps.m_picWidthInLumaSamples = pps.m_picHeightInLumaSamples = 64;
    pcv = std::make_unique<PreCalcValues>(sps, pps, true);
    cs.area = UnitArea(ChromaFormat::_400, Area(0, 0, definition.size, definition.size));
    cs.sps = &sps;
    cs.pps = &pps;
    cs.pcv = pcv.get();
    cs.slice = &slice;
    slice.m_sps = &sps;
    slice.m_pps = &pps;
    slice.m_depQuantEnabledIdc = 1;
    cu.cs = &cs;
    cu.slice = &slice;
    cu.predMode = MODE_INTRA;
    cu.chType = ChannelType::LUMA;
    cu.firstTU = cu.lastTU = &tu;
    tu.cs = &cs;
    tu.cu = &cu;
    TCoeff *coeffs[MAX_NUM_TBLOCKS] = { levels.data() };
    uint8_t *signPtrs[MAX_NUM_TBLOCKS] = { signs.data() };
    EnumArray<Pel *, ChannelType> palette;
    EnumArray<bool *, ChannelType> run;
    palette.fill(nullptr);
    run.fill(nullptr);
    tu.init(coeffs, signPtrs, palette, run);
    TU::setCbfAtDepth(tu, COMP_Y, 0, true);
    const auto layout = getGttLayout(tu);
    const auto *scan = g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
      [gp_sizeIdxInfo->idxFrom(definition.size)][gp_sizeIdxInfo->idxFrom(definition.size)];
    levels[0] = 1;
    levels[scan[layout.groupBegin[3]].idx] = 2;
    tu.lastPos[COMP_Y] = layout.groupBegin[3];
  }
};

void flagTests()
{
  require(Ctx::GttFlagBySize.Size == 4 && Ctx::GttFlagBySize.Offset == Ctx::GttFlag.Offset + 1,
          "v3 contexts did not append after v2");
  require(Ctx::NumberOfContexts == Ctx::GttFlagBySize.Offset + 4, "Unexpected context count");
  for (unsigned tableId = 0; tableId < 20; ++tableId)
  {
    const auto &table = ContextSetCfg::getInitTable(tableId);
    for (unsigned sizeId = 0; sizeId < 4; ++sizeId)
    {
      require(table[Ctx::GttFlagBySize(sizeId)] == table[Ctx::GttFlag()], "Size context initialization differs");
    }
  }
  const std::vector<bool> flags { false, true, false, true, true, false, false, true };
  for (int mode = 1; mode <= 6; ++mode)
  {
    Fixture fixture(mode);
    const int sizeId = TU::gttSizeId(fixture.tu);
    require(TU::isGttApplicable(fixture.tu, COMP_Y), "Registered mode is not eligible");
    require(TU::gttFlagCtxId(fixture.tu) == Ctx::GttFlagBySize(sizeId), "Incorrect size context");
    for (int initId = B_SLICE; initId < NUMBER_OF_SLICE_TYPES; ++initId)
    {
      OutputBitstream bits, plain;
      BinEncoder_Std bins, plainBins;
      CABACWriter writer(bins, nullptr), plainWriter(plainBins, nullptr);
      GttFlagStats stats;
      writer.setGttFlagStats(&stats);
      writer.initBitstream(&bits);
      plainWriter.initBitstream(&plain);
      bins.reset(32, initId);
      plainBins.reset(32, initId);
      uint64_t expectedBits[2] {};
      for (bool flag: flags)
      {
        fixture.tu.gttFlag = flag;
        expectedBits[flag] += writer.getCtx().getFracBitsAcess().getFracBitsArray(TU::gttFlagCtxId(fixture.tu)).intBits[flag];
        writer.gtt_flag(fixture.tu, COMP_Y);
        plainWriter.gtt_flag(fixture.tu, COMP_Y);
      }
      writer.end_of_slice();
      plainWriter.end_of_slice();
      bits.writeByteAlignment();
      plain.writeByteAlignment();
      require(bits.getFifo() == plain.getFifo(), "Statistics modified emitted bits");
      const auto &bySize = stats.bySize[sizeId];
      require(stats.total == 8 && stats.ones == 4 && bySize.total == 8 && bySize.ones == 4, "Flag counts incorrect");
      require(bySize.fracBits[0] == expectedBits[0] && bySize.fracBits[1] == expectedBits[1], "Flag probability sampled after update");
      require(bySize.tau[3] == 4 && bySize.paths[2] == 4 && bySize.prefixRTus == 4 && bySize.nonzeroLevels == 8,
              "Final path statistics incorrect");
      require(bySize.lastPositions[fixture.tu.lastPos[COMP_Y]] == 4, "Last position histogram incorrect");

      InputBitstream input;
      input.getFifo() = bits.getFifo();
      input.resetToStart();
      BinDecoder_Std decoder;
      CABACReader reader(decoder, nullptr);
      reader.initBitstream(&input);
      decoder.reset(32, initId);
      for (bool flag: flags)
      {
        reader.gtt_flag(fixture.tu, COMP_Y);
        require(fixture.tu.gttFlag == flag, "Flag round-trip failed");
      }
      require(reader.terminating_bit(), "CABAC termination failed");
      std::vector<std::pair<uint16_t, uint16_t>> encStates, decStates;
      writer.getCtx().savePStates(encStates);
      reader.getCtx().savePStates(decStates);
      require(encStates == decStates, "Writer and Reader context states differ");

      BitEstimator_Std estimateBins;
      CABACWriter estimator(estimateBins, nullptr);
      GttFlagStats estimatedStats;
      estimator.setGttFlagStats(&estimatedStats);
      estimateBins.reset(32, initId);
      estimator.gtt_flag(fixture.tu, COMP_Y);
      estimator.collectGttTuStats(fixture.cu);
      require(!estimatedStats.total && !estimatedStats.bySize[sizeId].tuCount, "Estimator polluted final statistics");
      fixture.cu.rootCbf = false;
      fixture.cu.skip = true;
      TU::setCbfAtDepth(fixture.tu, COMP_Y, 0, false);
      writer.collectGttTuStats(fixture.cu);
      require(bySize.tuCount == 1 && bySize.eligibleCount == 1, "Pre-flag population omitted root-CBF/skip TU");
      fixture.cu.chType = ChannelType::CHROMA;
      writer.collectGttTuStats(fixture.cu);
      require(bySize.tuCount == 1, "Chroma traversal counted luma population twice");
      fixture.cu.chType = ChannelType::LUMA;
      fixture.cu.rootCbf = true;
      fixture.cu.skip = false;
      TU::setCbfAtDepth(fixture.tu, COMP_Y, 0, true);
    }
    fixture.tu.gttFlag = false;
    BitEstimator_Std bins;
    CABACWriter estimator(bins, nullptr);
    bins.reset(32, I_SLICE);
    fixture.sps.m_gttSizeMask ^= 15;
    require(!TU::isGttApplicable(fixture.tu, COMP_Y), "Disabled size remained eligible");
    estimator.gtt_flag(fixture.tu, COMP_Y);
    require(!estimator.getEstFracBits(), "Disabled size emitted flag=0");
  }
}
#endif
}

int main()
{
  try
  {
    initROM();
    spsTests();
#if HHI_GTT_COMMON
    flagTests();
#endif
    destroyROM();
    std::cout << "GTT syntax tests passed: " << checks << " checks (compile=" << HHI_GTT_COMMON << ")\n";
    return 0;
  }
  catch (const std::exception &error)
  {
    std::cerr << "GTT syntax test failure: " << error.what() << '\n';
    return 1;
  }
}
