import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide11() {
  return (
    <SlideShell dark>
      <Header section="11 / AUGUST SNAPSHOT" title="August demand coverage" dark />
      <div className="grid grid-cols-4 gap-[1.1vw] px-[7vw] pt-[7vh]">
        <div className="panel-dark"><div className="stat-number">1.24M</div><div className="body-copy mt-[2vh]">Total requested demand</div><div className="label mt-[2vh]">1,241,675 pieces</div></div>
        <div className="panel-dark"><div className="stat-number">233K</div><div className="body-copy mt-[2vh]">Fallback-scheduled demand</div><div className="label mt-[2vh]">233,014 pieces</div></div>
        <div className="panel-dark"><div className="stat-number">18.77%</div><div className="body-copy mt-[2vh]">Fallback share</div><div className="label mt-[2vh]">of requested demand</div></div>
        <div className="panel-dark"><div className="stat-number">5,143</div><div className="body-copy mt-[2vh]">Data-limited demand</div><div className="label mt-[2vh]">pieces</div></div>
      </div>
      <div className="absolute bottom-[12vh] left-[7vw] right-[7vw]">
        <div className="label">Fallback coverage</div>
        <div className="body-copy mt-[1.5vh]">75 items across pipe and fittings</div>
        <div className="bar bar-dark mt-[2.5vh]"><span className="w-[18.77%]" /></div>
      </div>
      <Footer page="11" />
    </SlideShell>
  );
}