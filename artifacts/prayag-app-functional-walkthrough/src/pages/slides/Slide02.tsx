import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide02() {
  return (
    <SlideShell>
      <Header section="02 / HOME" title="Home is the operating launchpad" />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[5vh]">
        <div className="panel h-[19vh]"><div className="label">01</div><div className="body-copy mt-[2vh]">Machine Performance</div></div>
        <div className="panel h-[19vh]"><div className="label">02</div><div className="body-copy mt-[2vh]">Machine Planning</div></div>
        <div className="panel h-[19vh]"><div className="label">03</div><div className="body-copy mt-[2vh]">Machine Planning Follow Up</div></div>
        <div className="panel h-[19vh]"><div className="label">04</div><div className="body-copy mt-[2vh]">Costing Analysis</div></div>
        <div className="panel h-[19vh]"><div className="label">05</div><div className="body-copy mt-[2vh]">Costing</div></div>
      </div>
      <div className="absolute bottom-[11vh] left-[7vw] signal-line" />
      <Footer page="02" />
    </SlideShell>
  );
}