import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide04() {
  return (
    <SlideShell dark>
      <Header section="04 / PIPELINE" title="From raw sheets to an operator-ready answer" dark />
      <div className="grid grid-cols-5 gap-[1vw] px-[7vw] pt-[6vh]">
        <div className="panel-dark h-[29vh]"><div className="stat-number">01</div><div className="body-copy mt-[3vh]">Ingest source workbooks</div></div>
        <div className="panel-dark mt-[3vh] h-[29vh]"><div className="stat-number">02</div><div className="body-copy mt-[3vh]">Parse daily and monthly production records</div></div>
        <div className="panel-dark mt-[6vh] h-[29vh]"><div className="stat-number">03</div><div className="body-copy mt-[3vh]">Reconcile output, rejection, run hours, and ideal hours</div></div>
        <div className="panel-dark mt-[3vh] h-[29vh]"><div className="stat-number">04</div><div className="body-copy mt-[3vh]">Apply confirmation checks and data gates</div></div>
        <div className="panel-dark h-[29vh]"><div className="stat-number">05</div><div className="body-copy mt-[3vh]">Surface reports, exports, and planning previews</div></div>
      </div>
      <div className="absolute bottom-[9vh] left-[7vw] right-[7vw] signal-line" />
      <Footer page="04" />
    </SlideShell>
  );
}