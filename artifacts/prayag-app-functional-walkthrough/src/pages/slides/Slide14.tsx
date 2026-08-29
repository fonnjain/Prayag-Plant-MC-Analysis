import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide14() {
  return (
    <SlideShell>
      <Header section="14 / COSTING" title="Costing connects production to its drivers" />
      <div className="grid grid-cols-[.9fr_1.1fr] gap-[5vw] px-[7vw] pt-[4vh]">
        <div className="panel flex h-[36vh] flex-col justify-between">
          <div className="label">Costing views</div>
          <div className="headline text-[3.35vw]">Labour<br />plus<br />raw material.</div>
          <div className="signal-line" />
        </div>
        <div className="flex flex-col gap-[2.8vh] pt-[1vh]">
          <div className="bullet"><div className="bullet-mark">01</div><div className="body-copy">Costing Analysis provides the analytical view</div></div>
          <div className="bullet"><div className="bullet-mark">02</div><div className="body-copy">Labour costing separates workforce impact</div></div>
          <div className="bullet"><div className="bullet-mark">03</div><div className="body-copy">Raw-material costing exposes RM impact</div></div>
          <div className="bullet"><div className="bullet-mark">04</div><div className="body-copy">Source workbooks and costing masters feed the calculations</div></div>
          <div className="bullet"><div className="bullet-mark">05</div><div className="body-copy">The module keeps plant and period assumptions visible</div></div>
        </div>
      </div>
      <Footer page="14" />
    </SlideShell>
  );
}