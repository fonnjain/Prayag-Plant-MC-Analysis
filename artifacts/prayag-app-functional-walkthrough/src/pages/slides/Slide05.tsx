import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide05() {
  return (
    <SlideShell>
      <Header section="05 / DRILL-DOWN" title="Performance is drillable by operating grain" />
      <div className="grid grid-cols-3 gap-[1.2vw] px-[7vw] pt-[5vh]">
        <div className="panel h-[22vh]"><div className="stat-number text-[4.3vw]">01</div><div className="body-copy mt-[2vh]">Plant rollups</div></div>
        <div className="panel h-[22vh]"><div className="stat-number text-[4.3vw]">02</div><div className="body-copy mt-[2vh]">Machine rollups</div></div>
        <div className="panel h-[22vh]"><div className="stat-number text-[4.3vw]">03</div><div className="body-copy mt-[2vh]">Segment rollups</div></div>
        <div className="panel h-[22vh]"><div className="stat-number text-[4.3vw]">04</div><div className="body-copy mt-[2vh]">Date and tonnage views</div></div>
        <div className="panel h-[22vh]"><div className="stat-number text-[4.3vw]">05</div><div className="body-copy mt-[2vh]">Location views</div></div>
        <div className="panel h-[22vh]"><div className="stat-number text-[4.3vw]">06</div><div className="body-copy mt-[2vh]">Charts and tables for the selected period</div></div>
      </div>
      <Footer page="05" />
    </SlideShell>
  );
}