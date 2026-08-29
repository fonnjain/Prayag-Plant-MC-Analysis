import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide08() {
  return (
    <SlideShell>
      <Header section="08 / COVERAGE" title="Every demand line gets a modelling verdict" />
      <div className="grid grid-cols-3 gap-[1.3vw] px-[7vw] pt-[5vh]">
        <div className="panel h-[30vh] border-t-[.45vh] border-t-[var(--rust)]"><div className="label">schedulable</div><div className="body-copy mt-[3vh]">Direct BOM, route, and usable direct rate</div></div>
        <div className="panel h-[30vh] border-t-[.45vh] border-t-[var(--amber)]"><div className="label">partial</div><div className="body-copy mt-[3vh]">BOM exists, but route or rate uses documented fallback</div></div>
        <div className="panel h-[30vh] border-t-[.45vh] border-t-[var(--steel)]"><div className="label">not_modellable</div><div className="body-copy mt-[3vh]">BOM weight is missing</div></div>
      </div>
      <div className="grid grid-cols-2 gap-[5vw] px-[7vw] pt-[4vh]">
        <div><div className="label">Independent decision</div><div className="body-copy mt-[1.2vh]">can_schedule is reported independently from the status label</div></div>
        <div><div className="label">Capacity honesty</div><div className="body-copy mt-[1.2vh]">Data-limited lines never disappear into unfinished capacity</div></div>
      </div>
      <Footer page="08" />
    </SlideShell>
  );
}