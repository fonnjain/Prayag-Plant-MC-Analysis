import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide05() {
  return (
    <SlideShell>
      <Header section="05 / PERFORMANCE" title="Metrics that explain performance, not just output" />
      <div className="grid grid-cols-3 gap-[1.2vw] px-[7vw] pt-[4vh]">
        <div className="panel h-[20vh]"><div className="label">Efficiency</div><div className="body-copy mt-[2vh]">OEE, availability, performance, and quality</div></div>
        <div className="panel h-[20vh]"><div className="label">Capacity</div><div className="body-copy mt-[2vh]">Utilisation and machine efficiency</div></div>
        <div className="panel h-[20vh]"><div className="label">Baselines</div><div className="body-copy mt-[2vh]">Output efficiency against real ideal-output baselines</div></div>
      </div>
      <div className="grid grid-cols-[.9fr_1.1fr] gap-[5vw] px-[7vw] pt-[5vh]">
        <div>
          <div className="label">Operating facts</div>
          <div className="body-copy mt-[2vh]">Good output, rejection, actual hours, and ideal hours</div>
        </div>
        <div>
          <div className="label">Drill-down grain</div>
          <div className="body-copy mt-[2vh]">Plant, machine, segment, date, and period drill-downs</div>
          <div className="mt-[3vh] signal-line" />
        </div>
      </div>
      <Footer page="05" />
    </SlideShell>
  );
}