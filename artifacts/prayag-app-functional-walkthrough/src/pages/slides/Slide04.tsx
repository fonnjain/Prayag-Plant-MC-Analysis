import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide04() {
  return (
    <SlideShell>
      <Header section="04 / PERFORMANCE" title="The Performance dashboard starts with the headline" />
      <div className="grid grid-cols-[1.1fr_.9fr] gap-[5vw] px-[7vw] pt-[3.5vh]">
        <div className="flex flex-col gap-[2.7vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">Period selector and refresh control</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">Live-source status and parser or rejection-drift warnings</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">OEE, output efficiency, utilisation, and rating gauge</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Confirmation-gated needs-review or published sign-off state</div></div>
          <div className="bullet"><div className="bullet-mark">05</div><div className="body-copy">Freshness, source-change, and AI narrative panels</div></div>
        </div>
        <div className="panel relative h-[33vh]">
          <div className="label">Headline view</div>
          <div className="mt-[3vh] grid grid-cols-2 gap-[1vw]">
            <div className="border-b-[.3vh] border-[var(--rust)] pb-[2vh]"><div className="stat-number text-[4vw]">OEE</div><div className="small-copy mt-[1vh]">headline KPI</div></div>
            <div className="border-b-[.3vh] border-[var(--amber)] pb-[2vh]"><div className="stat-number text-[4vw]">RATE</div><div className="small-copy mt-[1vh]">rating gauge</div></div>
          </div>
          <div className="absolute bottom-[2vw] left-[2.3vw] right-[2.3vw] signal-line" />
        </div>
      </div>
      <Footer page="04" />
    </SlideShell>
  );
}