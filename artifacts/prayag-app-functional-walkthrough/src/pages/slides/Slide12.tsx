import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide12() {
  return (
    <SlideShell>
      <Header section="12 / RUNS" title="Runs create a reviewable planning history" />
      <div className="grid grid-cols-[1.1fr_.9fr] gap-[5vw] px-[7vw] pt-[4vh]">
        <div className="grid grid-cols-2 gap-[1.2vw]">
          <div className="panel h-[18vh]"><div className="label">01</div><div className="body-copy mt-[2vh]">Inspect run results and run details</div></div>
          <div className="panel h-[18vh]"><div className="label">02</div><div className="body-copy mt-[2vh]">Freeze a selected run when the plan is ready</div></div>
          <div className="panel h-[18vh]"><div className="label">03</div><div className="body-copy mt-[2vh]">Re-freeze or finalize as the operating state changes</div></div>
          <div className="panel h-[18vh]"><div className="label">04</div><div className="body-copy mt-[2vh]">Generate capacity-feasible, consolidated, corrective, and revised reports</div></div>
        </div>
        <div className="panel mt-[1vh]">
          <div className="label">Before action</div>
          <div className="headline mt-[3vh] text-[3vw]">Compare machine plans before committing to action</div>
          <div className="mt-[4vh] signal-line" />
        </div>
      </div>
      <Footer page="12" />
    </SlideShell>
  );
}