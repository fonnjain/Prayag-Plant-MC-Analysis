import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide09() {
  return (
    <SlideShell>
      <Header section="09 / MACHINE PLANNING" title="Machine Planning is a guided workspace" />
      <div className="grid grid-cols-[.9fr_1.1fr] gap-[5vw] px-[7vw] pt-[4vh]">
        <div className="panel">
          <div className="label">Run lifecycle</div>
          <div className="mt-[3vh] flex items-center justify-between">
            <div className="text-center"><div className="stat-number text-[3.2vw]">01</div><div className="small-copy mt-[1vh]">SET UP</div></div>
            <div className="text-[2vw] text-[var(--rust)]">→</div>
            <div className="text-center"><div className="stat-number text-[3.2vw]">02</div><div className="small-copy mt-[1vh]">PLAN</div></div>
            <div className="text-[2vw] text-[var(--rust)]">→</div>
            <div className="text-center"><div className="stat-number text-[3.2vw]">03</div><div className="small-copy mt-[1vh]">REVIEW</div></div>
          </div>
        </div>
        <div className="flex flex-col gap-[2.4vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">Upload release plans</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">Set up demand and master data</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Configure machines, routing, BOM, per-hour rates, and compounds</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Configure working days and planning parameters</div></div>
          <div className="bullet"><div className="bullet-mark">05</div><div className="body-copy">Create, freeze, re-freeze, and finalize runs</div></div>
        </div>
      </div>
      <Footer page="09" />
    </SlideShell>
  );
}