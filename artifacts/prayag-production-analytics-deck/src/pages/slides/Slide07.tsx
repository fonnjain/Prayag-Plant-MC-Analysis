import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide07() {
  return (
    <SlideShell>
      <Header section="07 / PLANNING PREVIEW" title="Planning is a safe preview, not a hidden write" />
      <div className="grid grid-cols-[1.1fr_.9fr] gap-[5vw] px-[7vw] pt-[3vh]">
        <div className="flex flex-col gap-[2.4vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">POST /data-api/v1/schedule returns a non-persistent capacity preview</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">Reads current machine master, routing, BOM, rates, downtime, and rejection inputs</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Does not freeze demand or write plan lines</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Returns blocks, weekly fill, unfinished demand, and downtime totals</div></div>
          <div className="bullet"><div className="bullet-mark">05</div><div className="body-copy">Pipe and fitting pools remain independently mergeable only when disjoint</div></div>
        </div>
        <div className="panel mt-[1vh]">
          <div className="label">Preview boundary</div>
          <div className="mt-[3vh] flex flex-col gap-[1vh]">
            <div className="flex items-center justify-between border-b border-[var(--line)] pb-[1.1vh]"><span className="small-copy">READ</span><span className="body-copy">master data</span></div>
            <div className="flex items-center justify-between border-b border-[var(--line)] pb-[1.1vh]"><span className="small-copy">MODEL</span><span className="body-copy">capacity blocks</span></div>
            <div className="flex items-center justify-between border-b border-[var(--line)] pb-[1.1vh]"><span className="small-copy">RETURN</span><span className="body-copy">coverage + fill</span></div>
            <div className="flex items-center justify-between"><span className="small-copy">WRITE</span><span className="body-copy text-[var(--rust)]">none</span></div>
          </div>
        </div>
      </div>
      <Footer page="07" />
    </SlideShell>
  );
}