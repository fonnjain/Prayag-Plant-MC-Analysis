import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide11() {
  return (
    <SlideShell dark>
      <Header section="11 / SCHEDULE" title="The schedule preview shows feasible capacity" />
      <div className="grid grid-cols-[1.05fr_.95fr] gap-[5vw] px-[7vw] pt-[3.5vh]">
        <div className="flex flex-col gap-[2.4vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">Read current machine, routing, BOM, rate, downtime, and rejection inputs</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">Return a non-persistent capacity preview</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Show shift blocks and weekly fill</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Show unfinished demand and downtime totals</div></div>
          <div className="bullet"><div className="bullet-mark">05</div><div className="body-copy">Keep pipe and fitting pools independently visible</div></div>
          <div className="bullet"><div className="bullet-mark">06</div><div className="body-copy">Do not freeze demand or write plan lines</div></div>
        </div>
        <div className="panel-dark mt-[1vh]">
          <div className="label">Preview boundary</div>
          <div className="mt-[3vh] flex flex-col gap-[1.3vh]">
            <div className="flex justify-between border-b border-[rgba(246,241,232,.18)] pb-[1.2vh]"><span className="small-copy">READ</span><span className="body-copy">source inputs</span></div>
            <div className="flex justify-between border-b border-[rgba(246,241,232,.18)] pb-[1.2vh]"><span className="small-copy">MODEL</span><span className="body-copy">capacity</span></div>
            <div className="flex justify-between border-b border-[rgba(246,241,232,.18)] pb-[1.2vh]"><span className="small-copy">RETURN</span><span className="body-copy">fill + blocks</span></div>
            <div className="flex justify-between"><span className="small-copy">WRITE</span><span className="body-copy text-[var(--amber)]">none</span></div>
          </div>
        </div>
      </div>
      <Footer page="11" />
    </SlideShell>
  );
}