import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide13() {
  return (
    <SlideShell>
      <Header section="13 / FITTING FALLBACK" title="Fitting fallback: schedulable, but high outlier risk" />
      <div className="grid grid-cols-[.75fr_1.25fr] gap-[6vw] px-[7vw] pt-[5vh]">
        <div>
          <div className="stat-number">226,063</div>
          <div className="body-copy mt-[1.8vh]">pieces were fallback-scheduled</div>
          <div className="label mt-[3vh]">68 fitting items</div>
          <div className="small-copy mt-[1vh]">Every fitting fallback used material-average data</div>
        </div>
        <div className="panel">
          <div className="label">Holdout comparison</div>
          <div className="mt-[3vh] flex items-end gap-[1.2vw]">
            <div className="w-[8vw]"><div className="bar h-[15vh]"><span className="h-[18%]" /></div><div className="label mt-[1vh]">SIGNED</div><div className="body-copy mt-[.7vh]">+18.19%</div></div>
            <div className="w-[8vw]"><div className="bar h-[15vh]"><span className="h-[77%]" /></div><div className="label mt-[1vh]">ABSOLUTE</div><div className="body-copy mt-[.7vh]">76.74%</div></div>
            <div className="w-[8vw]"><div className="bar h-[15vh]"><span className="h-full" /></div><div className="label mt-[1vh]">MAX</div><div className="body-copy mt-[.7vh]">1,432.21%</div></div>
          </div>
          <div className="small-copy mt-[3vh]">The spread is a warning about rate quality, not a reason to hide the demand.</div>
        </div>
      </div>
      <Footer page="13" />
    </SlideShell>
  );
}