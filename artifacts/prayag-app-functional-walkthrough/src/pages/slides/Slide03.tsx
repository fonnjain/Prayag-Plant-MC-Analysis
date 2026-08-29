import { Footer, Header, SlideShell } from '@/components/Primitives';

export default function Slide03() {
  return (
    <SlideShell dark>
      <Header section="03 / NAVIGATION" title="The navigation follows the operating rhythm" />
      <div className="grid grid-cols-2 gap-[1.2vw] px-[7vw] pt-[4vh]">
        <div className="panel-dark h-[19vh]"><div className="label">01</div><div className="body-copy mt-[2vh]">Machine Performance: understand what happened</div></div>
        <div className="panel-dark h-[19vh]"><div className="label">02</div><div className="body-copy mt-[2vh]">Machine Planning: decide what to run</div></div>
        <div className="panel-dark h-[19vh]"><div className="label">03</div><div className="body-copy mt-[2vh]">Costing: understand labour and raw-material impact</div></div>
        <div className="panel-dark h-[19vh]"><div className="label">04</div><div className="body-copy mt-[2vh]">Capacity Planning: test available capacity</div></div>
        <div className="panel-dark h-[19vh]"><div className="label">05</div><div className="body-copy mt-[2vh]">Operations: inspect supporting processes</div></div>
        <div className="panel-dark h-[19vh]"><div className="label">06</div><div className="body-copy mt-[2vh]">Data &amp; Audit: confirm what can be trusted</div></div>
      </div>
      <Footer page="03" />
    </SlideShell>
  );
}