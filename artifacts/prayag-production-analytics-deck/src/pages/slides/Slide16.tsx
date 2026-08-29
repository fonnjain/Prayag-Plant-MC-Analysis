import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide16() {
  return (
    <SlideShell>
      <Header section="16 / OPERATING LOOP" title="A decision-ready planning loop" />
      <div className="grid grid-cols-5 gap-[1vw] px-[7vw] pt-[6vh]">
        <div className="panel h-[26vh]"><div className="stat-number">01</div><div className="body-copy mt-[3vh]">Start with confirmed demand and source provenance</div></div>
        <div className="panel mt-[3vh] h-[26vh]"><div className="stat-number">02</div><div className="body-copy mt-[3vh]">Inspect coverage by item, route method, and rate method</div></div>
        <div className="panel mt-[6vh] h-[26vh]"><div className="stat-number">03</div><div className="body-copy mt-[3vh]">Review optimistic outliers and capacity impact</div></div>
        <div className="panel mt-[3vh] h-[26vh]"><div className="stat-number">04</div><div className="body-copy mt-[3vh]">Use the preview to allocate feasible blocks</div></div>
        <div className="panel h-[26vh]"><div className="stat-number">05</div><div className="body-copy mt-[3vh]">Escalate missing BOMs and weak standards as data work, not hidden assumptions</div></div>
      </div>
      <Footer page="16" />
    </SlideShell>
  );
}