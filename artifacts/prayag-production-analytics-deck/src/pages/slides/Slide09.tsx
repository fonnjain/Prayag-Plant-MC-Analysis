import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide09() {
  return (
    <SlideShell dark>
      <Header section="09 / FALLBACKS" title="Fallbacks are now explicit" dark />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[3.5vh]">
        <div className="panel-dark h-[18vh]"><div className="label">Route</div><div className="body-copy mt-[2vh]">direct or material fallback</div></div>
        <div className="panel-dark h-[18vh]"><div className="label">Pipe rate</div><div className="body-copy mt-[2vh]">direct item, material average, or overall average</div></div>
        <div className="panel-dark h-[18vh]"><div className="label">Fitting rate</div><div className="body-copy mt-[2vh]">direct fitting standard, cycle time, material average, or overall average</div></div>
        <div className="panel-dark h-[18vh]"><div className="label">No usable source</div><div className="body-copy mt-[2vh]">missing and not_evaluated are distinct outcomes</div></div>
      </div>
      <div className="absolute bottom-[11vh] left-[7vw] right-[7vw] flex items-center justify-between panel-dark">
        <div><div className="label">Rate units</div><div className="body-copy mt-[1vh]">kg/hr for pipe and pcs/hr for fittings</div></div>
        <div className="flex gap-[2vw] text-[2vw] font-semibold"><span className="text-[var(--amber)]">kg/hr</span><span className="text-[rgba(246,241,232,.35)]">/</span><span className="text-[var(--amber)]">pcs/hr</span></div>
      </div>
      <Footer page="09" />
    </SlideShell>
  );
}