#include "CommonLib/CodingStructure.h"
#include "CommonLib/SequenceParameterSet.h"
#include "CommonLib/UnitTools.h"
#include "EncoderLib/CABACWriter.h"
#include "EncoderLib/VLCWriter.h"
#include "DecoderLib/CABACReader.h"
#include "DecoderLib/VLCReader.h"

#include <iostream>
#include <stdexcept>
#include <vector>

namespace
{
void require(bool ok, const char *message)
{
  if (!ok)
  {
    throw std::runtime_error(message);
  }
}

template<class Fn> void requireReject(Fn fn, const char *message)
{
  bool rejected = false;
  try { fn(); }
  catch (const std::exception &) { rejected = true; }
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

// Construct a private-extension SPS independently of the experimental Writer.
// This also supplies a new-stream rejection fixture to a macro-zero decoder.
OutputBitstream withGttExtension(const OutputBitstream &legacy, unsigned version)
{
  const auto &bytes = legacy.getFifo();
  auto bit = [&](size_t pos) { return (bytes[pos / 8] >> (7 - pos % 8)) & 1; };
  size_t trailingOne = bytes.size() * 8 - 1;
  while (!bit(trailingOne)) { --trailingOne; }
  require(trailingOne > 0 && !bit(trailingOne - 1), "Expected an SPS without extensions");
  OutputBitstream out;
  for (size_t i = 0; i < trailingOne - 1; ++i) { out.write(bit(i), 1); }
  out.write(1, 1);      // sps_extension_present_flag
  out.write(0x40, 8);   // only extension slot 1
  out.write(version, 8);
  out.writeByteAlignment();
  return out;
}

void testSps()
{
  SPS sps = makeSps();
  const auto off = serialize(sps);
  SPS decoded;
  // Explicitly test reuse of the new fields while baseline fields start clean.
  decoded.m_gttEnabled = true;
  decoded.m_gttVersion = 99;
  parse(off, decoded);
  require(!decoded.m_gttEnabled && decoded.m_gttVersion == 0, "Absent GTT extension was not cleared");
  require(serialize(decoded).getFifo() == off.getFifo(), "Disabled SPS did not round-trip byte exactly");

  const auto contextStream = withGttExtension(off, 2);
#if HHI_GTT_COMMON
  sps.m_gttEnabled = true;
  sps.m_gttVersion = 2;
    sps.m_gttSizeMask = 1;
  sps.validateGtt4x4();
  require(serialize(sps).getFifo() == contextStream.getFifo(), "Unexpected GTT SPS extension placement/payload");
  parse(contextStream, decoded);
  require(decoded.m_gttEnabled && decoded.m_gttVersion == 2, "GTT v2 SPS did not round-trip");
  parse(off, decoded);
  require(!decoded.m_gttEnabled && decoded.m_gttVersion == 0, "GTT SPS state survived subsequent off SPS");
  require(serialize(decoded).getFifo() == off.getFifo(), "GTT off SPS gained payload");

  SPS invalid = sps;
  invalid.m_bitDepths[ChannelType::LUMA] = 8;
  requireReject([&] { invalid.validateGtt4x4(); }, "Invalid GTT bit depth accepted");
  invalid = sps;
  invalid.m_scalingListEnabledFlag = true;
  requireReject([&] { invalid.validateGtt4x4(); }, "GTT scaling lists accepted");
  invalid = sps;
  invalid.m_depQuantEnabledFlag = false;
  requireReject([&] { invalid.validateGtt4x4(); }, "GTT without DQ capability accepted");
  invalid = sps;
  invalid.m_spsRangeExtension.m_extendedPrecisionProcessingFlag = true;
  requireReject([&] { invalid.validateGtt4x4(); }, "GTT extended precision accepted");
#else
  requireReject([&] { parse(contextStream, decoded); }, "Unsupported build accepted GTT SPS");
  sps.m_gttEnabled = true;
  sps.m_gttVersion = 2;
    sps.m_gttSizeMask = 1;
  requireReject([&] { serialize(sps); }, "Unsupported build emitted GTT SPS");
#endif
  requireReject([&] { parse(withGttExtension(off, 1), decoded); }, "Obsolete bypass version accepted");
  requireReject([&] { parse(withGttExtension(off, 99), decoded); }, "Unknown GTT version accepted");

  // Legacy range extension remains supported when the experiment is disabled.
  SPS rext = makeSps();
  rext.m_bitDepths[ChannelType::LUMA] = rext.m_bitDepths[ChannelType::CHROMA] = 12;
  rext.m_spsRangeExtension.m_rrcRiceExtensionEnableFlag = true;
  const auto oldRext = serialize(rext);
  SPS decodedRext;
  parse(oldRext, decodedRext);
  require(!decodedRext.m_gttEnabled && decodedRext.m_spsRangeExtension.m_rrcRiceExtensionEnableFlag,
          "Legacy REXT SPS state changed");
  require(serialize(decodedRext).getFifo() == oldRext.getFifo(), "Legacy REXT SPS did not round-trip");
}

#if HHI_GTT_COMMON
struct TuFixture
{
  XuPool pool;
  CodingStructure cs { pool };
  SPS sps = makeSps();
  Slice slice;
  CodingUnit cu { ChromaFormat::_400, Area(0, 0, 4, 4) };
  TransformUnit tu { ChromaFormat::_400, Area(0, 0, 4, 4) };
  TuFixture()
  {
    sps.m_gttEnabled = true;
    sps.m_gttVersion = 2;
    sps.m_gttSizeMask = 1;
    cs.sps = &sps;
    cs.slice = &slice;
    slice.m_sps = &sps;
    slice.m_depQuantEnabledIdc = 1;
    slice.m_signDataHidingEnabledFlag = false;
    cu.cs = &cs;
    cu.slice = &slice;
    cu.predMode = MODE_INTRA;
    tu.cs = &cs;
    tu.cu = &cu;
    TU::setCbfAtDepth(tu, COMP_Y, 0, true);
  }
};

void testContextFlag()
{
  require(Ctx::GttFlag.Size == 1, "Expected one shared GTT context");
  require(Ctx::GttFlag.Offset == Ctx::LfCccmFlag.Offset + Ctx::LfCccmFlag.Size,
          "GTT context must follow all baseline contexts");
  require(Ctx::NumberOfContexts == Ctx::GttFlag.Offset + 5, "Unexpected context count");
  for (unsigned i = 0; i < 20; ++i)
  {
    const auto &table = ContextSetCfg::getInitTable(i);
    require(table.size() == Ctx::NumberOfContexts, "Incomplete GTT context initialization table");
    const unsigned expected = i < 4 ? 35 : i < 8 ? DWS : i < 12 ? DWE : DWO;
    require(table.back() == expected, "GTT context initialization changed without revision");
  }

  TuFixture fixture;
  const std::vector<bool> flags { false, false, true, false, true, true, false, false, false, true };
  for (int initId = B_SLICE; initId < NUMBER_OF_SLICE_TYPES; ++initId)
  {
    OutputBitstream bits;
    BinEncoder_Std bins;
    CABACWriter writer(bins, nullptr);
    GttFlagStats stats;
    writer.setGttFlagStats(&stats);
    writer.initBitstream(&bits);
    bins.reset(32, initId);
    for (bool flag : flags)
    {
      fixture.tu.gttFlag = flag;
      writer.gtt_flag(fixture.tu, COMP_Y);
    }
    writer.end_of_slice();
    bits.writeByteAlignment();
    require(stats.total == flags.size() && stats.ones == 4, "Final flag statistics count is wrong");

    OutputBitstream withoutStats;
    BinEncoder_Std plainBins;
    CABACWriter plainWriter(plainBins, nullptr);
    plainWriter.initBitstream(&withoutStats);
    plainBins.reset(32, initId);
    for (bool flag : flags)
    {
      fixture.tu.gttFlag = flag;
      plainWriter.gtt_flag(fixture.tu, COMP_Y);
    }
    plainWriter.end_of_slice();
    withoutStats.writeByteAlignment();
    require(bits.getFifo() == withoutStats.getFifo(), "Statistics changed the emitted CABAC bits");

    InputBitstream input;
    input.getFifo() = bits.getFifo();
    input.resetToStart();
    BinDecoder_Std decoder;
    CABACReader reader(decoder, nullptr);
    reader.initBitstream(&input);
    decoder.reset(32, initId);
    for (bool flag : flags)
    {
      reader.gtt_flag(fixture.tu, COMP_Y);
      require(fixture.tu.gttFlag == flag, "Context-coded flag failed round-trip");
    }
    require(reader.terminating_bit(), "CABAC termination failed after GTT flags");
    std::vector<std::pair<uint16_t, uint16_t>> encStates, decStates;
    writer.getCtx().savePStates(encStates);
    reader.getCtx().savePStates(decStates);
    require(encStates == decStates, "Encoder/decoder context states diverged");

    const auto consumed = input.getNumBitsRead();
    fixture.tu.gttFlag = true;
    reader.gtt_flag(fixture.tu, COMP_Cb);
    require(fixture.tu.gttFlag, "Chroma decoding cleared luma GTT state");
    fixture.tu.mtsIdx[COMP_Y] = MtsType::SKIP;
    reader.gtt_flag(fixture.tu, COMP_Y);
    require(!fixture.tu.gttFlag && input.getNumBitsRead() == consumed, "Absent flag read bits or retained GTT state");
    fixture.tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
  }

  BitEstimator_Std estimatorBins;
  CABACWriter estimator(estimatorBins, nullptr);
  GttFlagStats stats;
  estimator.setGttFlagStats(&stats); // Even a non-null pointer must not count estimates.
  estimatorBins.reset(32, I_SLICE);
  Ctx start = estimator.getCtx();
  fixture.tu.gttFlag = true;
  estimator.gtt_flag(fixture.tu, COMP_Y);
  const auto oneCost = estimator.getEstFracBits();
  require(oneCost > 0, "GTT context bin was not priced");
  require(stats.total == 0 && stats.ones == 0, "Estimator contaminated final statistics");
  estimator.getCtx() = start;
  estimator.resetBits();
  estimator.gtt_flag(fixture.tu, COMP_Y);
  require(estimator.getEstFracBits() == oneCost, "Candidate context rollback failed");

  auto before = estimator.getEstFracBits();
  estimator.gtt_flag(fixture.tu, COMP_Cb);
  require(fixture.tu.gttFlag && estimator.getEstFracBits() == before, "Chroma modified luma GTT state or syntax");
  fixture.tu.mtsIdx[COMP_Y] = MtsType::SKIP;
  fixture.tu.gttFlag = false;
  estimator.gtt_flag(fixture.tu, COMP_Y);
  require(estimator.getEstFracBits() == before, "Transform skip emitted a GTT flag");
  fixture.tu.mtsIdx[COMP_Y] = MtsType::DCT2_DCT2;
  TU::setCbfAtDepth(fixture.tu, COMP_Y, 0, false);
  estimator.gtt_flag(fixture.tu, COMP_Y);
  require(estimator.getEstFracBits() == before, "CBF-zero emitted a GTT flag");
  fixture.tu.gttFlag = true;
  requireReject([&] { estimator.gtt_flag(fixture.tu, COMP_Y); }, "Invalid GTT state accepted when syntax absent");
}
#endif
} // namespace

int main()
{
  try
  {
    testSps();
#if HHI_GTT_COMMON
    testContextFlag();
#endif
    std::cout << "GTT4x4 syntax tests passed (compile=" << HHI_GTT4X4 << ")\n";
    return 0;
  }
  catch (const std::exception &e)
  {
    std::cerr << "GTT4x4 syntax test failure: " << e.what() << '\n';
    return 1;
  }
}
