import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide12() {
  return (
    <SlideShell>
      <Header section="12 / PIPE FALLBACK" title="Pipe fallback: limited reach, measurable spread" />
      <div className="grid grid-cols-[.75fr_1.25fr] gap-[6vw] px-[7vw] pt-[5vh]">
        <div>
          <div className="stat-number">6,951</div>
          <div className="body-copy mt-[1.8vh]">pieces were fallback-scheduled</div>
          <div className="label mt-[3vh]">7 pipe items</div>
          <div className="small-copy mt-[1vh]">Every pipe fallback used material-average data</div>
        </div>
        <div className="panel">
          <div className="label">Holdout comparison</div>
          <div className="mt-[3vh] flex items-end gap-[1.2vw]">
            <div className="w-[8vw]"><div className="bar h-[15vh]"><span className="h-[41%]" /></div><div className="label mt-[1vh]">SIGNED</div><div className="body-copy mt-[.7vh]">+19.88%</div></div>
            <div className="w-[8vw]"><div className="bar h-[15vh]"><span className="h-[44%]" /></div><div className="label mt-[1vh]">ABSOLUTE</div><div className="body-copy mt-[.7vh]">21.22%</div></div>
            <div className="w-[8vw]"><div className="bar h-[15vh]"><span className="h-full" /></div><div className="label mt-[1vh]">MAX</div><div className="body-copy mt-[.7vh]">53.26%</div></div>
          </div>
          <div className="small-copy mt-[3vh]">Positive signed divergence means the fallback is faster and potentially optimistic.</div>
        </div>
      </div>
      <Footer page="12" />
    </SlideShell>
  );
}