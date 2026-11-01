#include "CodingStructure.h"
#include "DepQuant.h"
#include "Gtt.h"
#include "GttDct.h"
#include "GttQuantRouting.h"
#include "Rom.h"
#include "UnitTools.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <iostream>
#include <memory>
#include <set>
#include <stdexcept>
#include <vector>

#if HHI_GTT_COMMON
namespace
{
int checks = 0;
void require(bool condition, const char *message)
{
  ++checks;
  if (!condition) { throw std::runtime_error(message); }
}

struct Fixture
{
  SPS sps;
  PPS pps;
  Slice slice;
  CodingStructure cs { g_xuPool };
  CodingUnit cu;
  TransformUnit tu;
  std::unique_ptr<PreCalcValues> pcv;
  std::vector<TCoeff> levels;
  std::vector<uint8_t> signs;
  DepQuant quant { nullptr, true };
  Ctx ctx { static_cast<const BinProbModel_Std *>(nullptr) };
  GttLayout layout;

  explicit Fixture(int size, uint8_t grouping = 0)
    : cu(ChromaFormat::_400, Area(0, 0, size, size))
    , tu(ChromaFormat::_400, Area(0, 0, size, size))
    , levels(size * size), signs(size * size), layout(makeGttLayout(size, grouping))
  {
    sps.m_chromaFormatIdc = ChromaFormat::_400;
    sps.m_bitDepths[ChannelType::LUMA] = sps.m_bitDepths[ChannelType::CHROMA] = 10;
    sps.m_qpBDOffset[ChannelType::LUMA] = 12;
    sps.m_ctuSize = 64;
    sps.m_gttEnabled = true;
    sps.m_gttVersion = GTT_VERSION;
    sps.m_gttSizeMask = uint8_t(1 << (floorLog2(size) - 2));
    sps.m_gttGroupMode = grouping;
    sps.m_depQuantEnabledFlag = true;
    pps.m_picWidthInLumaSamples = pps.m_picHeightInLumaSamples = 64;
    pcv = std::make_unique<PreCalcValues>(sps, pps, true);
    slice.m_sps = &sps;
    slice.m_pps = &pps;
    slice.m_depQuantEnabledIdc = 1;
    slice.m_eSliceType = I_SLICE;
    cs.area = UnitArea(ChromaFormat::_400, Area(0, 0, size, size));
    cs.sps = &sps; cs.pps = &pps; cs.slice = &slice; cs.pcv = pcv.get();
    cu.cs = &cs; cu.slice = &slice; cu.predMode = MODE_INTRA;
    cu.firstTU = cu.lastTU = &tu;
    tu.cs = &cs; tu.cu = &cu; tu.gttFlag = true;
    TCoeff *coeffPointers[MAX_NUM_TBLOCKS] = { levels.data() };
    uint8_t *signPointers[MAX_NUM_TBLOCKS] = { signs.data() };
    EnumArray<Pel *, ChannelType> palette;
    EnumArray<bool *, ChannelType> run;
    palette.fill(nullptr); run.fill(nullptr);
    tu.init(coeffPointers, signPointers, palette, run);
    quant.init(size, true, true, false);
    quant.setUseScalingList(false);
    setQp(27);
  }

  void setQp(int qp)
  {
    cu.qp = slice.m_iSliceQp = qp;
    ctx.init(Clip3(0, MAX_QP, qp), slice.m_eSliceType);
    ctx.setBaseLevel(4);
    quant.setLambda(0.57 * std::pow(2.0, (qp - 12.0) / 3.0));
  }

  const ScanElement *scan() const
  {
    return g_scanOrder[SCAN_GROUPED_4x4][CoeffScanType::DIAG]
      [gp_sizeIdxInfo->idxFrom(layout.width)][gp_sizeIdxInfo->idxFrom(layout.height)];
  }

  GttQuantResult solve(const std::vector<TCoeff> &source, const GttConstraints &constraints)
  {
    const auto savedLevels = levels;
    std::array<uint8_t, MAX_NUM_TBLOCKS> savedCbf;
    std::array<int, MAX_NUM_TBLOCKS> savedLast;
    std::copy_n(tu.cbf, MAX_NUM_TBLOCKS, savedCbf.begin());
    std::copy_n(tu.lastPos, MAX_NUM_TBLOCKS, savedLast.begin());
    const auto savedMts = tu.mtsIdx;
    const bool savedFlag = tu.gttFlag;
    GttQuantResult result;
    const bool feasible = quant.quantGtt(tu, COMP_Y, CCoeffBuf(source.data(), layout.width, layout.height),
                                         QpParam(tu, COMP_Y), ctx, layout, constraints, result);
    require(feasible == result.feasible, "Feasible return differs from result");
    require(levels == savedLevels && std::equal(savedCbf.begin(), savedCbf.end(), tu.cbf) &&
              std::equal(savedLast.begin(), savedLast.end(), tu.lastPos) && tu.mtsIdx == savedMts &&
              tu.gttFlag == savedFlag, "Constrained search mutated the TU");
    return result;
  }

  void verify(const GttQuantResult &result, const GttConstraints &constraints)
  {
    require(result.feasible && result.qRaster.size() == size_t(layout.numCoeff), "Incomplete GTT result");
    int actualLast = -1;
    int64_t actualSum = 0;
    for (int g = 0; g < 4; ++g)
    {
      int parity = 0;
      for (int i = layout.groupBegin[g]; i < layout.groupEnd[g]; ++i)
      {
        const int64_t level = result.qRaster[scan()[i].idx];
        const int64_t magnitude = level < 0 ? -level : level;
        actualSum += magnitude; parity ^= int(magnitude & 1);
        if (level) { actualLast = i; }
      }
      require(constraints.requiredParity[g] < 0 || parity == constraints.requiredParity[g],
              "Final transmitted levels violate a group parity");
    }
    require(actualLast == result.lastScan && actualLast >= constraints.minLastScan &&
              actualLast <= constraints.maxLastScan, "Incorrect last or nonzero suffix");
    require(actualSum == result.absSum && actualSum > 0, "Incorrect absolute sum");
  }

  void verifyDequant(const GttQuantResult &result)
  {
    levels = result.qRaster;
    std::vector<TCoeff> actual(layout.numCoeff), expected(layout.numCoeff);
    CoeffBuf destination(actual.data(), layout.width, layout.height);
    const QpParam qp(tu, COMP_Y);
    quant.dequant(tu, destination, COMP_Y, qp);
    constexpr int transitions[4][2] = {{0,1},{2,3},{1,0},{3,2}};
    const int qpDq = qp.Qp(false) + 1;
    const int transformShift = 15 - 10 - floorLog2(layout.width);
    const int shift = IQUANT_SHIFT + 1 - qpDq / 6 - transformShift;
    const int64_t scale = int64_t(g_invQuantScales[0][qpDq % 6]) * (int64_t(1) << std::max(0, -shift));
    const int64_t add = shift > 0 ? int64_t(1) << (shift - 1) : 0;
    int state = 0;
    for (int i = result.lastScan; i >= 0; --i)
    {
      const int raster = scan()[i].idx;
      const int64_t level = levels[raster];
      if (level)
      {
        const int64_t qAbs = 2 * std::abs(level) - (state & 1);
        int64_t magnitude = (qAbs * scale + add) >> std::max(0, shift);
        if (qAbs < 64)
        {
          const int64_t next = ((qAbs + 1) * scale + add) >> std::max(0, shift);
          const int64_t weight = 63 / qAbs;
          magnitude = ((1024 - weight) * magnitude + weight * next) >> 10;
        }
        expected[raster] = TCoeff(Clip3<int64_t>(-32768, 32767, (level < 0 ? -1 : 1) * magnitude));
      }
      state = transitions[state][std::abs(level) & 1];
    }
    require(actual == expected, "Whole-TU DQ reconstruction reset state at a CG/logical boundary");
  }
};

void testRouting()
{
  constexpr int transition[4][2] = {{0,1},{2,3},{1,0},{3,2}};
  for (int n: {8,16,32}) for (int mode: {0,1})
  {
    const GttLayout layout = makeGttLayout(n, uint8_t(mode));
    for (int target: {-1,0,1})
    {
      GttConstraints constraints;
      constraints.requiredParity.fill(int8_t(target));
      for (int i = 0; i < n*n; ++i)
      {
        int group = 0;
        while (i >= layout.groupEnd[group]) { ++group; }
        require(GttQuantRouting::groupAt(layout, i) == group, "Incorrect group lookup");
        for (int slot = 0; slot < 8; ++slot) for (int level = 0; level < 2; ++level)
        {
          int parity = target < 0 ? 0 : ((slot & 1) ^ level);
          int expected = 2 * transition[slot/2][level] + parity;
          if (i == layout.groupBegin[group])
          {
            expected = target >= 0 && parity != target ? -1 : 2 * transition[slot/2][level];
          }
          require(GttQuantRouting::nextSlot(slot, level, i, layout, constraints, g_stateTransTab[1]) == expected,
                  "Incorrect four-state/parity transition");
        }
        if (i % 16 == 0)
        {
          for (int slot = 0; slot < 8; ++slot)
          {
            int physical = slot/2;
            for (int k = 0; k < 16; ++k) { physical = transition[physical][0]; }
            int parity = target < 0 ? 0 : slot & 1;
            int expected = 2 * physical + parity;
            if (i == layout.groupBegin[group])
            {
              expected = target >= 0 && parity != target ? -1 : 2 * physical;
            }
            require(GttQuantRouting::skipSlot(slot, i, layout, constraints) == expected,
                    "CG skip changed physical state or reset parity inside a logical group");
          }
        }
      }
    }
  }
}

void testCandidates(Fixture &f)
{
  std::vector<TCoeff> zero(f.layout.numCoeff), signal(f.layout.numCoeff);
  for (int i = 0; i < f.layout.numCoeff; ++i)
  {
    signal[f.scan()[i].idx] = i % 7 ? TCoeff((i * 587 % 4093) - 2046) : 0;
  }
  for (const int qp: {-12,27,63}) for (const bool reverse: {false,true})
  {
    f.setQp(qp); f.slice.m_reverseLastSigCoeffFlag = reverse;
    for (int tau = 1; tau <= 4; ++tau)
    {
      const int count = tau == 1 ? 1 : tau == 2 ? 2 : 4;
      for (int prefix = 0; prefix < count; ++prefix)
      {
        GttConstraints c;
        c.minLastScan = f.layout.groupBegin[tau-1];
        c.maxLastScan = f.layout.groupEnd[tau-1]-1;
        if (tau >= 2) { c.requiredParity[0] = int8_t(tau == 2 ? prefix : prefix/2); }
        if (tau >= 3) { c.requiredParity[1] = int8_t(prefix & 1); }
        for (const auto *source: {&zero,&signal})
        {
          const auto result = f.solve(*source,c);
          f.verify(result,c); f.verifyDequant(result);
          const auto repeated = f.solve(*source,c);
          require(repeated.qRaster == result.qRaster && repeated.proxyCost == result.proxyCost,
                  "Previous candidate leaked CG history into the next solve");
          if (source == &zero) { require(result.proxyCost > 0, "Nonzero positive-cost candidate was pruned"); }
          if (f.layout.width == 4)
          {
            Gtt4x4Constraints old;
            old.minLastScan=uint8_t(c.minLastScan); old.maxLastScan=uint8_t(c.maxLastScan);
            old.requiredParity=c.requiredParity;
            Gtt4x4QuantResult reference;
            f.quant.quantGtt4x4(f.tu,COMP_Y,CCoeffBuf(source->data(),4,4),QpParam(f.tu,COMP_Y),f.ctx,old,reference);
            require(reference.feasible == result.feasible && reference.proxyCost == result.proxyCost &&
                      reference.lastScan == result.lastScan && reference.absSum == result.absSum &&
                      std::equal(reference.qRaster.begin(),reference.qRaster.end(),result.qRaster.begin()),
                    "4x4 migration changed a frozen candidate");
          }
        }
      }
    }
  }
  f.setQp(27);
  std::set<int> positions {0,15,16,255,256,f.layout.numCoeff-1};
  for (int g=0; g<4; ++g) { positions.insert(f.layout.groupBegin[g]); positions.insert(f.layout.groupEnd[g]-1); }
  for (int last: positions)
  {
    if (last >= f.layout.numCoeff) { continue; }
    GttConstraints c; c.minLastScan=c.maxLastScan=last;
    const auto result=f.solve(signal,c); f.verify(result,c); f.verifyDequant(result);
  }
  GttConstraints impossible; impossible.minLastScan=0; impossible.maxLastScan=f.layout.groupEnd[0]-1;
  impossible.requiredParity[3]=1;
  const auto failed=f.solve(signal,impossible);
  require(!failed.feasible && failed.qRaster.empty(), "Odd all-zero suffix incorrectly admitted a candidate");
  GttConstraints recovery; recovery.maxLastScan=f.layout.numCoeff-1; recovery.requiredParity[0]=1;
  f.verify(f.solve(signal,recovery),recovery);

  // Interleave ordinary DQ with constrained DQ on the same instance. Both
  // callers must completely reset their history, budgets and skip snapshots.
  const auto before=f.solve(signal,recovery);
  f.tu.gttFlag=false;
  TCoeff sum=0;
  f.quant.quant(f.tu,COMP_Y,CCoeffBuf(signal.data(),f.layout.width,f.layout.height),sum,QpParam(f.tu,COMP_Y),f.ctx);
  f.tu.gttFlag=true;
  const auto after=f.solve(signal,recovery);
  require(before.qRaster==after.qRaster && before.proxyCost==after.proxyCost, "Legacy DQ contaminated GTT history");
}

void testSparseCodingGroups()
{
  for (int n: {16,32}) for (int mode: {0,1})
  {
    Fixture f(n,uint8_t(mode));
    f.setQp(27);
    std::vector<TCoeff> source(n*n);
    source[f.scan()[0].idx]=12000;
    source[f.scan()[n*n-1].idx]=-16000;
    GttConstraints c; c.minLastScan=c.maxLastScan=n*n-1; c.requiredParity[0]=1;
    const auto result=f.solve(source,c); f.verify(result,c); f.verifyDequant(result);
    int zeroCgs=0;
    for (int begin=16; begin<n*n-16; begin+=16)
    {
      bool nonzero=false;
      for (int j=0;j<16;++j) { nonzero |= result.qRaster[f.scan()[begin+j].idx]!=0; }
      zeroCgs += !nonzero;
    }
    require(zeroCgs>0, "Sparse multi-CG input did not preserve any all-zero middle CG");
    const auto repeated=f.solve(source,c);
    require(result.qRaster==repeated.qRaster && result.proxyCost==repeated.proxyCost,
            "Sparse CG skip traceback is nondeterministic");
  }
}

void testUnconstrainedLegacyEquivalence()
{
  for (int n: {8,16,32}) for (int mode: {0,1})
  {
    Fixture f(n,uint8_t(mode));
    std::vector<TCoeff> source(n*n);
    for (int i=0;i<n*n;++i)
    {
      source[f.scan()[i].idx]=(i/16)%3 == 1 ? 0 : TCoeff((i*587%3001)+1000);
      if (i&1) { source[f.scan()[i].idx] = -source[f.scan()[i].idx]; }
    }
    for (int qp: {22,27,37}) for (bool reverse: {false,true})
    {
      f.setQp(qp); f.slice.m_reverseLastSigCoeffFlag=reverse;
      GttConstraints c; c.maxLastScan=n*n-1;
      const auto constrained=f.solve(source,c); f.verify(constrained,c);
      f.tu.gttFlag=false;
      TCoeff sum=0;
      f.quant.quant(f.tu,COMP_Y,CCoeffBuf(source.data(),n,n),sum,QpParam(f.tu,COMP_Y),f.ctx);
      require(sum>0, "Legacy equivalence fixture must select a nonzero candidate");
      if (constrained.qRaster!=f.levels || constrained.absSum!=sum || constrained.lastScan!=f.tu.lastPos[COMP_Y])
      {
        std::cerr << "Legacy difference n=" << n << " group=" << mode << " qp=" << qp << " reverse=" << reverse
                  << " newLast=" << constrained.lastScan << " oldLast=" << f.tu.lastPos[COMP_Y]
                  << " newSum=" << constrained.absSum << " oldSum=" << sum
                  << " newCost=" << constrained.proxyCost << '\n';
        int differences=0;
        for (int i=0;i<n*n && differences<16;++i)
        {
          const int raster=f.scan()[i].idx;
          if (constrained.qRaster[raster]!=f.levels[raster])
          {
            std::cerr << "scan=" << i << " source=" << source[raster] << " new=" << constrained.qRaster[raster]
                      << " old=" << f.levels[raster] << '\n';
            ++differences;
          }
        }
      }
      require(constrained.qRaster==f.levels && constrained.absSum==sum &&
                constrained.lastScan==f.tu.lastPos[COMP_Y],
              "Unconstrained joint solver differs from original multi-CG DQ");
      f.tu.gttFlag=true;
    }
  }
}

void testFullPelTransformSources()
{
  uint32_t seed=0x75a39d21;
  for (int n: {8,16,32}) for (int mode: {0,1})
  {
    Fixture f(n,uint8_t(mode));
    std::vector<Pel> residual(n*n);
    std::vector<TCoeff> base(n*n), source(n*n);
    for (int pattern=0;pattern<4;++pattern)
    {
      for (int i=0;i<n*n;++i)
      {
        seed=1664525U*seed+1013904223U;
        residual[i]=pattern==0 ? 32767 : pattern==1 ? ((i+i/n)&1 ? -32768 : 32767) :
                    pattern==2 ? (seed>>31 ? -32768 : 32767) : Pel(int(seed>>16)-32768);
      }
      CoeffBuf baseBuf(base.data(),n,n);
      forwardGttDctWide(CPelBuf(residual.data(),n,n),baseBuf,10,15);
      for (uint8_t k2=0;k2<2;++k2) for (uint8_t k3=0;k3<2;++k3)
      {
        CoeffBuf sourceBuf(source.data(),n,n);
        forwardGttPath(baseBuf,GttPath{k2,k3},sourceBuf,f.layout);
        for (int qp: {-12,0,22,63})
        {
          f.setQp(qp);
          GttConstraints c; c.maxLastScan=n*n-1;
          c.requiredParity[0]=k2; c.requiredParity[1]=k3;
          const auto result=f.solve(source,c);
          f.verify(result,c); f.verifyDequant(result);
        }
      }
    }
  }
}
}

int main()
{
  try
  {
    initROM();
    testRouting();
    for (int mode=1; mode<=6; ++mode)
    {
      const auto &entry=getGttMode(mode);
      Fixture f(entry.size,entry.groupMode);
      testCandidates(f);
      f.cu.predMode=MODE_INTER; f.slice.m_eSliceType=B_SLICE;
      testCandidates(f);
    }
    testSparseCodingGroups();
    testUnconstrainedLegacyEquivalence();
    testFullPelTransformSources();
    destroyROM();
    std::cout << "GTT multi-CG DQ synthetic checks passed: " << checks << '\n';
    return 0;
  }
  catch (const std::exception &error)
  {
    std::cerr << "GTT multi-CG DQ test failed: " << error.what() << '\n';
    return 1;
  }
}
#else
int main()
{
  std::cout << "GTT multi-CG DQ tests require GTT common capability; skipped.\n";
  return 77;
}
#endif
