import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide10() {
  return (
    <SlideShell>
      <Header section="10 / PLANNING DATA" title="Planning data is inspectable before it is used" />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[3.5vh]">
        <div className="panel h-[17vh]"><div className="label">BOM</div><div className="body-copy mt-[1.7vh]">Review and edit BOM data</div></div>
        <div className="panel h-[17vh]"><div className="label">Rates</div><div className="body-copy mt-[1.7vh]">Review and edit per-hour data</div></div>
        <div className="panel h-[17vh]"><div className="label">Routing</div><div className="body-copy mt-[1.7vh]">Review and edit machine and routing data</div></div>
        <div className="panel h-[17vh]"><div className="label">Compound</div><div className="body-copy mt-[1.7vh]">Maintain compound inputs</div></div>
        <div className="panel h-[17vh]"><div className="label">Downtime</div><div className="body-copy mt-[1.7vh]">Add, resolve, restore, and inspect downtime</div></div>
        <div className="panel h-[17vh]"><div className="label">Reset</div><div className="body-copy mt-[1.7vh]">Reset seeded planning data when a clean setup is needed</div></div>
      </div>
      <Footer page="10" />
    </SlideShell>
  );
}